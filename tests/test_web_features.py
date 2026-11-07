"""Exercise the added role-specific React capabilities with isolated stores."""
from datetime import date, timedelta
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src import company_store, performance, storage
from src.agents import AgentOutputError
from src.models import ApprovalRequest, CaseStatus, JourneyResponse, ReviewFinding, ReviewResult
from src.web_api import create_app
from src.workflow import TravelAgencyWorkflow
from test_activity_review import SOURCE, audit, execution, paragraph, word_file
from test_document_activity_management import document_case
from test_style_request_flow import TEAM, brief


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
            self.assertEqual(self.customer.get(owner_path).status_code, 401)
            owner_case = self.read(self.owner.get(owner_path))
            self.assertIn("retry", owner_case["actions"])
            self.action(self.owner, owner_path, owner_case, "retry")
            self.wait(path, lambda item: item["status"] == "waiting_style_feedback")
        self.assertEqual(model.await_count, 2)
        key = "case:" + activity["id"]
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
