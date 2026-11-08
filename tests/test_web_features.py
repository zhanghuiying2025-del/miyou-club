"""Exercise the added role-specific React capabilities with isolated stores."""
from datetime import date, datetime, timedelta, timezone
import asyncio
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src import company_store, operations, performance, storage, supplier_simulation
from src.agents import AgentOutputError
from src.models import (ApprovalRequest, AuditEvent, CaseStatus, CouncilDecision, FeedbackAssessment, JourneyResponse,
                        PlanAcceptanceFeedback, RequirementSpec, ReviewFinding, ReviewResult, TaskHandoff)
from src.web_api import create_app
from src.workflow import TravelAgencyWorkflow
from test_activity_review import SOURCE, audit, execution, paragraph, word_file
from test_document_activity_management import document_case
from test_style_request_flow import TEAM, brief
from test_supplier_operations import need


class WebFeatureTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        root = Path(directory)
        self.enterContext(patch.object(storage, "CASE_FILE", root / "cases.json"))
        self.enterContext(patch.object(storage, "DATA_DIR", root))
        self.enterContext(patch.object(company_store, "COMPANY_DB", root / "company.sqlite3"))
        self.enterContext(patch.object(performance, "PERFORMANCE_FILE", root / "performance.json"))
        self.enterContext(patch.dict(os.environ, {"MIYOU_OWNER_PASSWORD": "local-test-password"}))
        self.enterContext(patch("src.web_flow.build_agent_team", return_value=TEAM))
        app = create_app()
        self.owner, self.customer = TestClient(app), TestClient(app)
        self.read(self.owner.post("/api/owner/session", json={"password": "local-test-password"}))
        self.read(self.customer.get("/api/customer/session"))

    def read(self, response):
        self.assertLess(response.status_code, 300, response.text)
        return response.json()

    def wait(self, path, state):
        for _ in range(150):
            item = self.read(self.customer.get(path))
            if not item["running"] and state(item):
                return item
            time.sleep(0.02)
        self.fail(item)

    def action(self, client, path, item, action, **fields):
        return self.read(client.post(path + "/actions", json={
            "action": action, "revision": item["revision"], **fields,
        }))

    def test_missing_or_whitespace_owner_password_does_not_allow_login(self):
        for password in ("", "   "):
            with self.subTest(password=password), patch.dict(os.environ, {"MIYOU_OWNER_PASSWORD": password}):
                visitor = TestClient(create_app())
                response = visitor.post("/api/owner/session", json={"password": password})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(visitor.get("/api/owner/dashboard").status_code, 401)

    def test_exhausted_clarification_returns_to_customer_without_bypassing_approval(self):
        case = TravelAgencyWorkflow(None).create_case("秋日团建")
        case.web_customer_id = sha256(self.customer.cookies["miyou_customer"].encode()).hexdigest()
        case.status = CaseStatus.WAITING_OWNER
        case.clarification_rounds = 1
        case.customer_answer_pending = True
        case.customer_questions = ["秋叶可能已经过季"]
        case.approval = ApprovalRequest(title="关键条件仍待确认", reasons=case.customer_questions)
        case.reviews = [ReviewResult(reviewer_name="项目经理", passed=False, findings=[
            ReviewFinding(severity="medium", description="秋叶可能已经过季", decision_owner="customer", blocking=False),
            ReviewFinding(severity="medium", description="请确认是否接受室内备选", decision_owner="customer", blocking=True),
        ])]
        storage.save_case_to_disk(case)
        owner_case = TravelAgencyWorkflow(None).create_case("待老板确认的风险")
        owner_case.web_customer_id = case.web_customer_id
        owner_case.status = CaseStatus.WAITING_OWNER
        owner_case.approval = ApprovalRequest(title="需确认费用", reasons=["新增支出需授权"])
        storage.save_case_to_disk(owner_case)

        path = "/api/customer/activities/" + case.id
        activity = self.read(self.customer.get(path))
        self.assertEqual(activity["status"], "waiting_customer")
        self.assertEqual(activity["actions"], ["supplement"])
        self.assertEqual(activity["questions"], ["请确认是否接受室内备选", "秋叶可能已经过季"])
        self.assertEqual(storage.load_case_from_disk(case.id).status, CaseStatus.WAITING_OWNER)
        activities = {row["id"]: row for row in self.read(self.customer.get("/api/customer/activities"))}
        self.assertEqual(activities[case.id]["status"], "waiting_customer")
        self.assertEqual(activities[owner_case.id]["status"], "waiting_owner")
        orders = {row["case_id"]: row for row in self.read(self.owner.get("/api/owner/dashboard"))["orders"]}
        self.assertFalse(orders[case.id]["needs_owner"])
        self.assertTrue(orders[owner_case.id]["needs_owner"])
        blocked = self.read(self.owner.get("/api/owner/orders/case:" + case.id))
        self.assertNotIn("approve_plan", blocked["actions"])

        with patch("src.web_flow.enqueue") as enqueue:
            updated = self.action(self.customer, path, activity, "supplement", note="同意天气不佳时改室内")
            self.assertEqual(updated["status"], "supplementing")
            self.assertEqual(storage.load_case_from_disk(case.id).pending_supplement, "同意天气不佳时改室内")
            enqueue.assert_called_once()

    def test_owner_recovers_agent_failure_and_reads_archive(self):
        model = AsyncMock(side_effect=[AgentOutputError("模型返回格式不正确：Extra data"), brief()])
        with patch("src.workflow.run_structured", new=model):
            activity = self.read(self.customer.post("/api/customer/activities", json={
                "request_id": str(uuid4()), "brief": {"name": "老板协助重试", "people": 15,
                    "city": "北京", "event_city": "北京", "date": (date.today() + timedelta(days=5)).isoformat(),
                    "duration": "1天", "budget": 700, "goals": ["增进默契"]},
            }))
            path = "/api/customer/activities/" + activity["id"]
            owner_path = "/api/owner/orders/case:" + activity["id"]
            failed = self.wait(path, lambda item: item["status"] == "failed")
            self.assertIn("retry", failed["actions"])
            key = "case:" + activity["id"]
            tasks = self.read(self.owner.get("/api/owner/dashboard"))["tasks"]
            self.assertTrue(any(task["order_key"] == key and task["status"] == "failed" and
                                not task["recovered"] for task in tasks))
            self.assertEqual(self.customer.get(owner_path).status_code, 401)
            owner_case = self.read(self.owner.get(owner_path))
            self.assertIn("retry", owner_case["actions"])
            self.action(self.owner, owner_path, owner_case, "retry")
            self.wait(path, lambda item: item["status"] == "waiting_style_feedback")
        self.assertEqual(model.await_count, 2)
        tasks = self.read(self.owner.get("/api/owner/dashboard"))["tasks"]
        self.assertTrue(any(task["order_key"] == key and task["status"] == "failed" and
                            task["recovered"] for task in tasks))
        self.assertEqual(self.customer.get("/api/owner/archive").status_code, 401)
        archives = self.read(self.owner.get("/api/owner/archive"))
        self.assertEqual(archives[0]["id"], key)
        path = "/api/owner/archive/" + key
        archive = self.read(self.owner.get(path))
        self.assertNotIn("web_customer_id", archive["snapshot"])
        self.assertIn("attachment", self.owner.get(path + "?download=true").headers["content-disposition"])
        self.read(self.owner.post(path, json={"active": False}))
        self.assertTrue(self.read(self.owner.get("/api/owner/archive"))[0]["hidden"])
        self.read(self.owner.post(path, json={"active": True}))
        self.assertFalse(self.read(self.owner.get("/api/owner/archive"))[0]["hidden"])

    def test_interrupted_planning_and_timeout_can_both_resume(self):
        case = TravelAgencyWorkflow(None).create_case("虚构的南京团队交流")
        case.web_customer_id = sha256(self.customer.cookies["miyou_customer"].encode()).hexdigest()
        case.status = CaseStatus.CREATIVE_COUNCIL
        case.requirements = RequirementSpec(city="南京", participant_count=15, budget_yuan=3500)
        case.handoffs.append(TaskHandoff(case_id=case.id, sender="项目经理", receiver="策划",
            task="creative_council · ProposalBrief", expected_result="ProposalBrief",
            input_summary="方案第1版；已确认需求和案件约束"))
        storage.save_case_to_disk(case)
        key = "case:" + case.id
        owner_path = "/api/owner/orders/" + key
        path = "/api/customer/activities/" + case.id
        tasks = self.read(self.owner.get("/api/owner/dashboard"))["tasks"]
        self.assertTrue(any(t["order_key"] == key and t["status"] == "interrupted" and not t["recovered"] for t in tasks))

        async def slow(*args):
            await asyncio.sleep(5)

        with patch("src.web_flow.PLANNING_TIMEOUT_SECONDS", 0.2), patch("src.workflow.run_structured", side_effect=slow):
            item = self.read(self.owner.get(owner_path))
            self.assertIn("retry", item["actions"])
            self.action(self.owner, owner_path, item, "retry")
            failed = self.wait(path, lambda item: item["status"] == "failed")
            self.assertIn("retry", failed["actions"])
            saved = storage.load_case_from_disk(case.id)
            self.assertIn("超过", saved.error_message or "")
            self.assertEqual(saved.resume_status, CaseStatus.CREATIVE_COUNCIL)

        with patch("src.workflow.run_structured", new=AsyncMock(return_value=brief())) as model:
            self.action(self.owner, owner_path, self.read(self.owner.get(owner_path)), "retry")
            self.wait(path, lambda item: item["status"] == "waiting_style_feedback")
        self.assertEqual(model.await_count, 1)
        tasks = self.read(self.owner.get("/api/owner/dashboard"))["tasks"]
        failures = [t for t in tasks if t["order_key"] == key and t["status"] in {"failed", "interrupted"}]
        self.assertEqual(len(failures), 2)
        self.assertTrue(all(t["recovered"] for t in failures))

    def test_existing_orders_use_saved_dates_in_dashboard_and_archive(self):
        case = TravelAgencyWorkflow(None).create_case("已有的两日活动")
        case.web_customer_id = sha256(self.customer.cookies["miyou_customer"].encode()).hexdigest()
        case.audit_log[0].timestamp = datetime(2026, 11, 7, 17, tzinfo=timezone.utc)
        case.requirements = RequirementSpec(event_date="2026-11-21", duration="2天1晚")
        storage.save_case_to_disk(case)
        key = "case:" + case.id
        number = "20261108-20261121~20261122-" + case.id.removeprefix("case_")
        self.assertEqual(self.read(self.owner.get("/api/owner/orders/" + key))["display_number"], number)
        archives = self.read(self.owner.get("/api/owner/archive"))
        self.assertEqual(archives[0]["id"], key)
        self.assertEqual(archives[0]["display_number"], number)
        customer_rows = self.read(self.customer.get("/api/customer/activities"))
        self.assertEqual((customer_rows[0]["id"], customer_rows[0]["display_number"]), (case.id, number))
        activity = self.read(self.customer.get("/api/customer/activities/" + case.id))
        self.assertEqual((activity["id"], activity["display_number"]), (case.id, number))
        self.assertEqual(storage.load_case_from_disk(case.id).storage_revision, 1)

    def test_customer_question_has_pending_state_and_separate_reply(self):
        case = TravelAgencyWorkflow(None).create_case("室内团建")
        case.web_customer_id = sha256(self.customer.cookies["miyou_customer"].encode()).hexdigest()
        case.status = CaseStatus.WAITING_STYLE_FEEDBACK
        storage.save_case_to_disk(case)
        path = "/api/customer/activities/" + case.id
        activity = self.read(self.customer.get(path))
        self.assertIsNone(activity["question_reply"])
        question = "默契游戏有哪些内容？"
        reply = "我们会安排两人同步作画和分组拼图。"
        release = Event()

        async def answer(*args):
            await asyncio.to_thread(release.wait, 3)
            return FeedbackAssessment(feedback_kind="question", rationale="说明活动内容", customer_response=reply)

        with patch("src.workflow.run_structured", side_effect=answer):
            try:
                submitted = self.action(self.customer, path, activity, "question", note=question)
                self.assertTrue(submitted["question_pending"])
                self.assertIsNone(submitted["question_reply"])
            finally:
                release.set()
            completed = self.wait(path, lambda item: item["question_reply"] is not None)
        self.assertFalse(completed["question_pending"])
        self.assertEqual(completed["question_reply"]["question"], question)
        self.assertEqual(completed["question_reply"]["answer"], reply)
        self.assertTrue(completed["question_reply"]["answered_at"])
        saved = storage.load_case_from_disk(case.id)
        saved.status = CaseStatus.WAITING_DRAFT
        saved.audit_log.append(AuditEvent(actor="项目经理", action="style_feedback_assessed", detail="已确认方向"))
        storage.save_case_to_disk(saved)
        self.assertEqual(self.read(self.customer.get(path))["question_reply"], completed["question_reply"])

    def test_failed_question_retry_resumes_the_original_submission(self):
        case = TravelAgencyWorkflow(None).create_case("质疑重试测试")
        case.web_customer_id = sha256(self.customer.cookies["miyou_customer"].encode()).hexdigest()
        case.status = CaseStatus.WAITING_STYLE_FEEDBACK
        storage.save_case_to_disk(case)
        path = "/api/customer/activities/" + case.id
        question = "下雨时破冰游戏在哪儿做？"
        answer = FeedbackAssessment(feedback_kind="question", rationale="说明雨天备选", customer_response="改到室内。")
        model = AsyncMock(side_effect=[AgentOutputError("模型接口等待超时"), answer])
        with patch("src.workflow.run_structured", new=model):
            current = self.read(self.customer.get(path))
            self.action(self.customer, path, current, "question", note=question)
            failed = self.wait(path, lambda item: item["status"] == "failed")
            self.assertIn("retry", failed["actions"])
            self.assertEqual(failed["pending_question"], question)
            stale = self.customer.post(path + "/actions", json={"action": "question", "revision": current["revision"], "note": "旧页面重复提交"})
            self.assertEqual(stale.status_code, 409)
            owner_path = "/api/owner/orders/case:" + case.id
            self.action(self.owner, owner_path, self.read(self.owner.get(owner_path)), "retry")
            completed = self.wait(path, lambda item: item["status"] == "waiting_style_feedback")
        self.assertEqual(model.await_count, 2)
        self.assertEqual(completed["question_reply"]["question"], question)
        self.assertEqual(completed["question_reply"]["answer"], answer.customer_response)
        self.assertEqual(completed["pending_question"], "")

    def test_failed_direction_revision_can_retry_the_saved_reason(self):
        workflow = TravelAgencyWorkflow(None)
        case = workflow.create_case("改方案重试测试")
        case.web_customer_id = sha256(self.customer.cookies["miyou_customer"].encode()).hexdigest()
        case.status = CaseStatus.WAITING_STYLE_FEEDBACK
        case.requirements = RequirementSpec(city="南京", participant_count=20, budget_yuan=8000)
        storage.save_case_to_disk(case)
        path = "/api/customer/activities/" + case.id
        reason = "下雨时活动要在室内开展"
        model = AsyncMock(side_effect=[AgentOutputError("模型返回格式不正确：Extra data"), brief()])
        with patch("src.workflow.run_structured", new=model):
            self.action(self.customer, path, self.read(self.customer.get(path)), "revise_direction", note=reason)
            failed = self.wait(path, lambda item: item["status"] == "failed")
            self.assertEqual(storage.load_case_from_disk(case.id).plan_version, 1)
            self.action(self.customer, path, failed, "retry")
            self.wait(path, lambda item: item["status"] == "waiting_style_feedback")
        self.assertEqual(model.await_count, 2)
        saved = storage.load_case_from_disk(case.id)
        self.assertEqual(saved.plan_version, 2)
        self.assertIn(f"客户要求调整方向：{reason}", saved.applied_constraints)

    def test_failed_final_rework_retry_creates_one_new_version(self):
        workflow = TravelAgencyWorkflow(None)
        case = workflow.create_case("最终方案返工测试")
        case.web_customer_id = sha256(self.customer.cookies["miyou_customer"].encode()).hexdigest()
        case.status = CaseStatus.READY_FOR_CUSTOMER
        original = CouncilDecision(theme="原方案", summary="旧行程", schedule=["户外活动"], merged_ideas=[], rationale=[])
        case.council_decision = original
        workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(outcome="unacceptable", comment="改成室内活动"))
        storage.save_case_to_disk(case)
        path = "/api/customer/activities/" + case.id
        revised = original.model_copy(update={"theme": "返工方案", "schedule": ["室内活动"]})
        model = AsyncMock(side_effect=[AgentOutputError("模型接口等待超时"), revised])
        with patch("src.workflow.run_structured", new=model), patch.object(TravelAgencyWorkflow, "_run_reviews",
                new=AsyncMock(return_value=[ReviewResult(reviewer_name="项目经理", passed=True)])):
            self.action(self.customer, path, self.read(self.customer.get(path)), "rework")
            failed = self.wait(path, lambda item: item["status"] == "failed")
            self.assertEqual(failed["plans"][0]["title"], "原方案")
            self.action(self.customer, path, failed, "retry")
            self.wait(path, lambda item: item["status"] in {"needs_rework", "ready_for_customer"})
        saved = storage.load_case_from_disk(case.id)
        self.assertEqual(model.await_count, 2)
        self.assertEqual(saved.status, CaseStatus.READY_FOR_CUSTOMER)
        self.assertEqual(saved.plan_version, 2)
        self.assertEqual(saved.council_decision.theme, "返工方案")
        self.assertEqual([record.feedback.comment for record in saved.feedback_history], ["改成室内活动"])

    def test_archive_delete_and_restore_keep_linked_booking_consistent(self):
        case = TravelAgencyWorkflow(None).create_case("有酒店预订的团建")
        storage.save_case_to_disk(case)
        operations.register_lead("archive-test", "测试")
        booking = supplier_simulation.create_order(need(), "archive-test", case_id=case.id)
        case_key, booking_key = "case:" + case.id, "supplier:" + booking.id
        archive = "/api/owner/archive"

        self.read(self.owner.post(f"{archive}/{case_key}", json={"active": False}))
        rows = {row["id"]: row for row in self.read(self.owner.get(archive))}
        self.assertTrue(rows[case_key]["hidden"])
        self.assertTrue(rows[booking_key]["hidden"])
        self.assertTrue(rows[booking_key]["hidden_by_case"])
        self.assertEqual(self.owner.post(f"{archive}/{booking_key}", json={"active": True}).status_code, 409)
        self.assertIsNotNone(storage.load_case_from_disk(case.id))
        self.assertEqual(supplier_simulation.load_order(booking.id)[0].status, "inquiring")

        self.read(self.owner.post(f"{archive}/{case_key}", json={"active": True}))
        rows = {row["id"]: row for row in self.read(self.owner.get(archive))}
        self.assertFalse(rows[case_key]["hidden"])
        self.assertFalse(rows[booking_key]["hidden"])
        self.read(self.owner.post(f"{archive}/{booking_key}", json={"active": False}))
        self.read(self.owner.post(f"{archive}/{booking_key}", json={"active": True}))
        self.assertFalse(next(row for row in self.read(self.owner.get(archive)) if row["id"] == booking_key)["hidden"])

    def test_upload_review_and_manage_incident_through_two_roles(self):
        result = document_case().document_intake
        model = AsyncMock(side_effect=[result, audit(), execution()])
        response = JourneyResponse(summary="暂停并核对设备", requires_owner=True,
                                   needs_change=False, organizer_notice="确认设备状态后继续")
        coordinate = AsyncMock(return_value=(response, "agent", ""))
        with patch("src.workflow.run_structured", new=model), patch("src.journey.coordinate_response", new=coordinate):
            invalid = self.customer.post("/api/customer/documents", data={"request_id": str(uuid4())},
                files=[("files", ("broken.pdf", b"invalid", "application/pdf"))])
            self.assertEqual(invalid.status_code, 422)
            created = self.read(self.customer.post("/api/customer/documents", data={"request_id": str(uuid4())},
                files=[("files", ("活动.docx", word_file(paragraph(SOURCE)),
                                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]))
            path = "/api/customer/activities/" + created["id"]
            item = self.wait(path, lambda activity: activity["status"] == "waiting_requirements")
            self.assertIn(SOURCE.splitlines()[0], item["documents"][0]["text"])
            self.action(self.customer, path, item, "confirm_document", fields={"participant_count": "42"}, purpose="activity")
            item = self.wait(path, lambda activity: activity["status"] == "ready_for_customer")
            self.assertTrue(item["review"]["execution"]["run_sheet"])
            self.assertIn("attachment", self.customer.get(path + "/report").headers["content-disposition"])
            item = self.action(self.customer, path, item, "receive_review")
            item = self.action(self.customer, path, item, "create_activity_order",
                               options=item["review"]["execution"]["run_sheet"])
            self.assertIn("start_activity", item["actions"])
            item = self.action(self.customer, path, item, "start_activity")
            self.action(self.customer, path, item, "report_incident", incident="equipment", simulated=True)
            item = self.wait(path, lambda activity: activity["tracking"]["status"] == "awaiting_owner")
            self.assertFalse(item["actions"])
            owner_path = "/api/owner/orders/case:" + created["id"]
            order = self.read(self.owner.get(owner_path))
            self.assertIn("approve_incident", order["actions"])
            self.assertTrue(order["needs_owner"])
            before = item["tracking"]["events"][-1]["description"]
            self.action(self.owner, owner_path, order, "hold_incident")
            held = self.read(self.customer.get(path))
            self.assertEqual(held["tracking"]["status"], "held")
            self.assertFalse(held["actions"])
            self.action(self.owner, owner_path, self.read(self.owner.get(owner_path)), "reopen_incident")
            reopened = self.wait(path, lambda activity: activity["tracking"]["status"] == "awaiting_owner")
            self.assertEqual(reopened["tracking"]["events"][-1]["description"], before)
            order = self.read(self.owner.get(owner_path))
            self.action(self.owner, owner_path, order, "approve_incident")
            item = self.read(self.customer.get(path))
            self.assertIn("confirm_incident", item["actions"])
            item = self.action(self.customer, path, item, "confirm_incident")
            self.assertEqual(item["tracking"]["status"], "running")
            while "next_activity_step" in item["actions"]:
                item = self.action(self.customer, path, item, "next_activity_step")
            self.assertEqual(item["tracking"]["status"], "finished")
            self.assertIsNone(item["booking"])
        self.assertEqual(model.await_count, 3)

    def test_simulated_operations_and_customer_coupon(self):
        route = "/api/owner/operations"
        self.assertEqual(self.customer.get(route).status_code, 401)
        self.read(self.owner.post(route, json={"action": "fixed_research", "title": "小团队团建"}))
        self.read(self.owner.post(route, json={"action": "create_campaign", "title": "模拟活动",
                                               "content": "欢迎咨询", "amount": 20000}))
        operations = self.read(self.owner.get(route))
        self.assertEqual(len(operations["research"]), 1)
        campaign = operations["campaign"][0]["id"]
        self.read(self.owner.post(route, json={"action": "activate_campaign", "id": campaign}))
        self.read(self.owner.post(route, json={"action": "campaign_spend", "id": campaign,
                                               "reference": "test-spend", "amount": 1500}))
        self.read(self.customer.post("/api/customer/marketing", json={"interest": "咨询团建", "campaign_id": campaign}))
        self.read(self.owner.post(route, json={"action": "create_policy", "title": "团建抵用券", "amount": 10000,
                                               "start": "2026-01-01", "end": "2027-12-31", "campaign_id": campaign}))
        offers = self.read(self.customer.get("/api/customer/marketing"))
        policy = offers["policies"][0]["id"]
        ticket = self.read(self.customer.post("/api/customer/marketing", json={"policy_id": policy}))["coupons"][0]
        self.assertEqual(ticket["status"], "issued")
        operations = self.read(self.owner.get(route))
        self.assertEqual(operations["summary"]["leads"], 1)
        self.assertEqual(operations["summary"]["finance"]["outgoing_cents"], 1500)

    def test_customer_can_refresh_an_expired_quote_before_paying(self):
        case = TravelAgencyWorkflow(None).create_case("过期报价恢复测试")
        case.web_customer_id = sha256(self.customer.cookies["miyou_customer"].encode()).hexdigest()
        case.status = CaseStatus.COMPLETED
        operations.register_lead("expired-quote-test", "测试")
        order = supplier_simulation.create_order(need(), "expired-quote-test", case_id=case.id)
        case.supplier_order_id = order.id
        storage.save_case_to_disk(case)
        order = supplier_simulation.contact_all(order.id)
        options = [q.id for q in order.quotes if q.supplier_id in {"hotel-b", "meal-b"}]
        order = supplier_simulation.select_quotes(order.id, options)
        supplier_simulation.confirm_contract(order.id, list(supplier_simulation.contract(order)["conditions"]))
        path = "/api/customer/activities/" + case.id
        signed = self.read(self.customer.get(path))

        supplier_simulation.simulate_change(order.id, "expire")
        refused = self.customer.post(path + "/actions", json={"action": "pay_deposit", "revision": signed["revision"],
            "order_revision": signed["booking"]["revision"], "signature": signed["booking"]["contract"]["signature"]})
        self.assertGreaterEqual(refused.status_code, 400)
        self.assertEqual(supplier_simulation.load_order(order.id)[0].customer_paid_cents, 0)

        expired = self.read(self.customer.get(path))
        self.assertIn("refresh_quotes", expired["actions"])
        self.assertNotIn("pay_deposit", expired["actions"])
        self.assertTrue(all(not quote["available"] for quote in expired["booking"]["quotes"]))
        self.assertTrue(any("报价已过期" in line for line in expired["booking"]["quotes"][0]["blockers"]))
        refreshed = self.action(self.customer, path, expired, "refresh_quotes", order_revision=expired["booking"]["revision"])
        replacement = {q["kind"]: q["id"] for q in refreshed["booking"]["quotes"] if q["available"] and q["id"] not in options}
        self.assertEqual(set(replacement), {"hotel", "restaurant"})
        self.assertIn("select_quotes", refreshed["actions"])
        self.assertNotIn("pay_deposit", refreshed["actions"])
        new = self.action(self.customer, path, refreshed, "select_quotes", options=list(replacement.values()),
                          order_revision=refreshed["booking"]["revision"])
        contract = new["booking"]["contract"]
        self.assertNotEqual(contract["signature"], signed["booking"]["contract"]["signature"])
        confirmed = self.action(self.customer, path, new, "confirm_contract", options=list(contract["conditions"]),
                                order_revision=new["booking"]["revision"], signature=contract["signature"])
        self.assertIn("pay_deposit", confirmed["actions"])
        paid = self.action(self.customer, path, confirmed, "pay_deposit",
                           order_revision=confirmed["booking"]["revision"], signature=contract["signature"])
        self.assertEqual(paid["booking"]["customer_paid_cents"], contract["deposit_cents"])
