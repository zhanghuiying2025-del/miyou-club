from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.models import ApprovalRequest,TravelCase,CaseStatus,RequirementSpec,OrganizationState,CouncilDecision
from src.storage import (save_case_to_disk,load_case_from_disk,list_saved_cases,save_visit,load_visit,
    delete_case_from_disk,CaseConflictError)


def case(title):
    return TravelCase(customer_request=title,status=CaseStatus.WAITING_REQUIREMENTS,
        requirements=RequirementSpec(event_name=title,city="南京",event_date="2026-11-15",participant_count=30))


class CaseVisitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for target,value in [("src.storage.CASE_FILE",self.root/"cases.json"),
            ("src.company_store.COMPANY_DB",self.root/"company.sqlite3"),
            ("src.performance.PERFORMANCE_FILE",self.root/"performance.json")]:
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        for target,value in [("src.agents.build_agent_team",SimpleNamespace(manager=object(),planner=object(),fulfillment=object())),
            ("src.workflow.load_organization_state",OrganizationState())]:
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
        self.path=str(Path(__file__).resolve().parents[1]/"app.py")

    def app(self,visit):
        from streamlit.testing.v1 import AppTest
        with patch("src.visits._browser_visit",return_value=SimpleNamespace(visit={"id":visit,"navigation":"reload"})):
            return AppTest.from_file(self.path,default_timeout=30).run()

    def rerun_app(self,app,visit):
        with patch("src.visits._browser_visit",return_value=SimpleNamespace(visit={"id":visit,"navigation":"reload"})):
            return app.run()

    def test_new_case_keeps_previous_snapshot_and_separate_revisions(self):
        first,second=case("第一单"),case("第二单")
        save_case_to_disk(first);save_case_to_disk(second)
        first.customer_request="第一单补充"
        save_case_to_disk(first)
        self.assertEqual(load_case_from_disk(first.id).customer_request,"第一单补充")
        self.assertEqual(load_case_from_disk(second.id).storage_revision,1)
        self.assertEqual(len(list_saved_cases()),2)

    def test_waiting_customer_shows_answer_without_opening_optional_supplement(self):
        pending = case("待确认风格")
        pending.status = CaseStatus.WAITING_CUSTOMER
        pending.planning_mode = "style_branch"
        pending.pending_customer_step = CaseStatus.DELIBERATION
        pending.customer_questions = ["是否认可低强度共创？"]
        save_case_to_disk(pending)
        save_visit("pending-answer-visit", {"customer_case_id": pending.id})
        app = self.app("pending-answer-visit")
        self.assertFalse(app.exception)
        self.assertTrue(any(item.value == pending.customer_questions[0] for item in app.info))
        self.assertTrue(any(item.label == "你的答复" for item in app.text_area))
        self.assertTrue(any(item.label == "提交补充信息" for item in app.button))

    def test_independent_cases_can_be_saved_concurrently(self):
        items=[case("第一单"),case("第二单")]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(save_case_to_disk,items))
        self.assertEqual({r["id"] for r in list_saved_cases()},{c.id for c in items})

    def test_legacy_read_and_visit_write_keep_old_case(self):
        old=case("旧案件")
        file=self.root/"cases.json"
        file.write_text(old.model_dump_json(),encoding="utf-8")
        before=file.read_bytes()
        self.assertEqual(load_case_from_disk().id,old.id)
        self.assertEqual(file.read_bytes(),before)
        save_visit("browser-old",{"customer_case_id":old.id})
        self.assertEqual(json.loads(file.read_text(encoding="utf-8"))["format"],"case_book_v1")
        self.assertEqual(load_case_from_disk(old.id).model_dump(),old.model_dump())

    def test_delete_does_not_remove_other_case_and_stale_worker_cannot_resurrect(self):
        first,second=case("第一单"),case("第二单")
        save_case_to_disk(first);save_case_to_disk(second)
        save_visit("browser-first",{"customer_case_id":first.id,"owner_case_id":second.id})
        delete_case_from_disk(first)
        self.assertIsNone(load_case_from_disk(first.id))
        self.assertEqual(load_case_from_disk(second.id).id,second.id)
        self.assertIsNone(load_visit("browser-first")["customer_case_id"])
        with self.assertRaises(CaseConflictError):save_case_to_disk(first)

    def test_fresh_customer_is_empty_and_owner_can_restore_old_stage(self):
        old=case("保留未完成需求")
        save_case_to_disk(old)
        app=self.app("fresh-browser")
        self.assertFalse(app.exception)
        self.assertTrue(any(x.label=="活动城市 *" for x in app.text_input))
        app.sidebar.radio[0].set_value("公司老板")
        self.rerun_app(app,"fresh-browser")
        self.assertEqual(app.sidebar.radio(key="work_area").value,"经营驾驶舱")
        self.assertFalse(any(b.label=="在客户页面继续此案件" for b in app.button))
        with patch("src.visits._browser_visit",return_value=SimpleNamespace(visit={"id":"fresh-browser","navigation":"navigate"})):
            app.button(key="owner_dashboard_go_orders").click().run()
            app.button(key=f"owner_dashboard_open:case:{old.id}").click().run()
            self.assertEqual(app.session_state["case"].id,old.id)
            next(b for b in app.button if b.label=="在客户页面继续此案件").click().run()
        self.assertEqual(app.sidebar.radio[0].value,"客户")
        self.assertEqual(app.session_state["case"].status,CaseStatus.WAITING_REQUIREMENTS)
        app.sidebar.radio[0].set_value("公司老板")
        self.rerun_app(app,"fresh-browser")
        self.assertEqual(app.sidebar.radio(key="work_area").value,"经营驾驶舱")
        self.assertFalse(any(b.label=="在客户页面继续此案件" for b in app.button))
        self.assertEqual(load_case_from_disk(old.id).storage_revision,old.storage_revision)

    def test_refresh_restores_same_visit_while_another_visit_is_blank(self):
        old=case("刷新应恢复")
        save_case_to_disk(old)
        save_visit("same-browser",{"customer_case_id":old.id,"identity":"客户","work_area":"团建策划与交付"})
        first=self.app("same-browser")
        refreshed=self.app("same-browser")
        self.assertEqual(first.session_state["case"].id,old.id)
        self.assertEqual(refreshed.session_state["case"].id,old.id)
        fresh=self.app("different-browser")
        self.assertTrue(any(x.label=="活动城市 *" for x in fresh.text_input))
        self.assertEqual(len(list_saved_cases()),1)

    def test_empty_owner_dashboard_navigation_needs_no_model_or_company_database(self):
        visit = "empty-owner-dashboard"
        save_visit(visit, {"identity": "公司老板"})
        with patch("src.agents.build_agent_team", side_effect=AssertionError("dashboard must not build agents")):
            app = self.app(visit)
            navigation = app.segmented_control(key="owner_dashboard_page")
            self.assertEqual(navigation.value, "经营总览")
            self.assertEqual(navigation.options, ["经营总览", "订单与风险", "经营复盘", "AI 员工与制度"])
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["待老板处理"], "0 项")
            self.assertNotIn("累计模拟收款", metrics)
            app.button(key="owner_dashboard_go_review").click()
            self.rerun_app(app, visit)
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["累计模拟收款"], "¥0.00")
            self.assertEqual(metrics["累计模拟支出"], "¥0.00")
            self.assertEqual(metrics["模拟现金余额"], "¥46,000.00")
            app.segmented_control(key="owner_dashboard_page").set_value("经营总览")
            self.rerun_app(app, visit)
            app.button(key="owner_dashboard_go_orders").click()
            self.rerun_app(app, visit)
            self.assertEqual(app.segmented_control(key="owner_dashboard_page").value, "订单与风险")
            for page in ["经营复盘", "经营总览"]:
                app.segmented_control(key="owner_dashboard_page").set_value(page)
                self.rerun_app(app, visit)
                self.assertTrue(any(title.value == page for title in app.subheader))
            app.button(key="owner_dashboard_go_tasks").click()
            self.rerun_app(app, visit)
            self.assertEqual(app.segmented_control(key="owner_dashboard_page").value, "AI 员工与制度")
        self.assertFalse(app.exception)
        self.assertFalse((self.root / "company.sqlite3").exists())

    def test_owner_dashboard_leaves_exhausted_clarification_with_customer(self):
        pending = case("等待客户确认关键条件")
        pending.status = CaseStatus.WAITING_OWNER
        pending.planning_mode = "style_branch"
        pending.clarification_rounds = 1
        pending.customer_answer_pending = True
        pending.approval = ApprovalRequest(title="方案待核对", reasons=["客户还未确认关键条件"])
        save_case_to_disk(pending)
        visit = "owner-clarification-visit"
        save_visit(visit, {"identity": "公司老板"})
        with patch("src.agents.build_agent_team", side_effect=AssertionError("reading approval must not build agents")):
            app = self.app(visit)
            self.assertEqual(next(m for m in app.metric if m.label == "待老板处理").value, "0 项")
            app.button(key="owner_dashboard_go_orders").click()
            self.rerun_app(app, visit)
            self.assertEqual(app.dataframe[0].value.iloc[0]["接手人"], "客户")
            app.button(key=f"owner_dashboard_open:case:{pending.id}").click()
            self.rerun_app(app, visit)
            self.assertTrue(next(b for b in app.button if b.label == "批准").disabled)
        self.assertFalse(app.exception)

    def test_background_old_case_save_does_not_redirect_current_customer(self):
        first,second=case("后台第一单"),case("前台第二单")
        save_case_to_disk(first);save_case_to_disk(second)
        save_visit("second-browser",{"customer_case_id":second.id,"identity":"客户"})
        app=self.app("second-browser")
        first.customer_request="后台已继续完成部分步骤"
        save_case_to_disk(first)
        self.rerun_app(app,"second-browser")
        self.assertEqual(app.session_state["case"].id,second.id)

    def test_new_browser_document_clears_reused_server_session_without_copying_old_binding(self):
        old=case("原访问的案件")
        save_case_to_disk(old)
        save_visit("previous-document",{"customer_case_id":old.id,"identity":"客户"})
        app=self.app("previous-document")
        self.assertEqual(app.session_state["case"].id,old.id)
        self.rerun_app(app,"new-document")
        self.assertTrue(any(x.label=="活动城市 *" for x in app.text_input))
        self.assertIsNone(load_visit("new-document").get("customer_case_id"))
        self.assertEqual(load_case_from_disk(old.id).id,old.id)

    def test_owner_history_selection_resumes_exact_case(self):
        first,second=case("第一单"),case("第二单")
        second.status=CaseStatus.WAITING_DRAFT
        second.council_decision=CouncilDecision(theme="草案",summary="原草案",schedule=["休息"],merged_ideas=[],rationale=[])
        save_case_to_disk(first);save_case_to_disk(second)
        save_visit("owner-browser",{"identity":"公司老板","owner_case_id":first.id,
            "work_area":"团建策划与交付"})
        app=self.app("owner-browser")
        self.assertIsNone(next(x for x in app.selectbox if x.label=="历史案件与未完成进度").value)
        with patch("src.visits._browser_visit",return_value=SimpleNamespace(visit={"id":"owner-browser","navigation":"reload"})):
            next(s for s in app.selectbox if s.label=="历史案件与未完成进度").set_value(second.id).run()
            next(b for b in app.button if b.label=="在客户页面继续此案件").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["case"].id,second.id)
        self.assertEqual(app.session_state["case"].status,CaseStatus.WAITING_DRAFT)
        self.assertEqual(app.session_state["case"].council_decision.summary,"原草案")

    def test_start_new_request_preserves_old_case_in_owner_history(self):
        old=case("新建前旧单")
        save_case_to_disk(old)
        save_visit("new-request-browser",{"customer_case_id":old.id})
        app=self.app("new-request-browser")
        with patch("src.visits._browser_visit",return_value=SimpleNamespace(visit={"id":"new-request-browser","navigation":"reload"})):
            next(b for b in app.button if b.label=="保存进度并开始新需求").click().run()
        self.assertTrue(any(x.label=="活动城市 *" for x in app.text_input))
        self.assertIsNone(load_visit("new-request-browser")["customer_case_id"])
        self.assertEqual(load_case_from_disk(old.id).status,CaseStatus.WAITING_REQUIREMENTS)

    def test_supplier_customer_is_fresh_but_owner_can_restore_prebooking(self):
        from src.operations import register_lead
        from src.supplier_simulation import create_order
        from src.supplier_models import BookingNeed,MealSlot
        register_lead("demo-history","模拟需求")
        order=create_order(BookingNeed(city="模拟",check_in="2026-11-06",check_out="2026-11-08",men=100,women=50,
            meals=[MealSlot(day="2026-11-06",meal="午餐")]),"demo-history")
        save_visit("supplier-old-browser",{"customer_supplier_order_id":order.id,"work_area":"酒店餐饮模拟预订"})
        refreshed=self.app("supplier-old-browser")
        self.assertTrue(any(b.key==f"contact_rule:{order.id}" for b in refreshed.button))
        fresh=self.app("supplier-new-browser")
        self.assertEqual([r.label for r in fresh.sidebar.radio], ["当前身份"])
        with patch("src.visits._browser_visit",return_value=SimpleNamespace(visit={"id":"supplier-new-browser","navigation":"reload"})), \
             patch("src.agents.build_agent_team",return_value=SimpleNamespace()):
            next(r for r in fresh.radio if r.label=="需求类型").set_value("酒店餐饮预订").run()
        self.assertTrue(any(b.label=="下一步" for b in fresh.button))
        self.assertFalse(any(b.key==f"contact_rule:{order.id}" for b in fresh.button))
        fresh.sidebar.radio[0].set_value("公司老板")
        self.rerun_app(fresh,"supplier-new-browser")
        with patch("src.visits._browser_visit",return_value=SimpleNamespace(visit={"id":"supplier-new-browser","navigation":"reload"})):
            fresh.button(key="owner_dashboard_go_orders").click().run()
            fresh.button(key=f"owner_dashboard_open:supplier:{order.id}").click().run()
            next(b for b in fresh.button if b.label=="在客户页面继续此预订").click().run()
        self.assertFalse(fresh.exception)
        self.assertEqual(fresh.sidebar.radio[0].value,"客户")
        self.assertEqual([r.label for r in fresh.sidebar.radio], ["当前身份"])
        self.assertTrue(any(b.key==f"contact_rule:{order.id}" for b in fresh.button))
