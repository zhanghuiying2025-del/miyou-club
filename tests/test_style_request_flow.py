import asyncio
from datetime import date
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src.models import (CaseStatus, CouncilDecision, CreativeOpinion, FeasibilityReview, FeedbackAssessment, IntakeResult, ManagedPlan,
                        ProposalBrief, RequirementSpec, ReviewFinding, ReviewResult)
from src.style_planning import matching_style_template
from src.runtime import pause_planning, submit_once
from src.workflow import TravelAgencyWorkflow, WorkflowError
from test_compact_planning import checks


TEAM = SimpleNamespace(manager="manager", planner="planner", fulfillment="fulfillment")
APP = Path(__file__).resolve().parents[1] / "app.py"


def brief():
    return ProposalBrief(concept_name="轻量户外协作", one_sentence_pitch="沿平缓路线分组探索",
                         recommended_activities=["分组探索"], reasons=["适合混龄成员"])


class StyleRequestFlowTests(unittest.TestCase):
    def test_direction_question_and_revision_are_separate_actions(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_case_to_disk
        case = TravelAgencyWorkflow(TEAM).create_case("北京团队室内协作")
        case.requirements = RequirementSpec(city="北京", event_date="2026-11-20", participant_count=30)
        case.status = CaseStatus.WAITING_STYLE_FEEDBACK
        case.creative_opinions = [CreativeOpinion(**brief().model_copy(
            update={"recommended_activities": ["北京鼓楼", "什刹海"]}).model_dump(),
            agent_name="策划", persona_title="城市游玩")]
        save_case_to_disk(case)
        assessment = FeedbackAssessment(feedback_kind="question", rationale="客户补充了游览经历",
            customer_response="可以更换地点", requires_owner=True)
        model = AsyncMock(side_effect=[assessment, brief()])
        with patch("src.agents.build_agent_team", return_value=TEAM), patch("src.workflow.run_structured", new=model):
            app = AppTest.from_file(str(APP), default_timeout=30)
            app.session_state["customer_case_id"] = case.id
            app.run()
            reason = "多数团队成员已去过鼓楼和什刹海，希望更换"
            next(t for t in app.text_area if t.label=="质疑内容").set_value(reason)
            next(b for b in app.button if b.label=="提交质疑").click().run()
            updated = app.session_state["case"]
            self.assertEqual(updated.status, CaseStatus.WAITING_STYLE_FEEDBACK)
            self.assertIsNone(updated.approval)
            self.assertEqual(updated.creative_opinions[0].recommended_activities, ["北京鼓楼", "什刹海"])
            next(t for t in app.text_area if t.label=="修改理由").set_value(reason)
            next(b for b in app.button if b.label=="提交修改").click().run()
            updated = app.session_state["case"]
            self.assertFalse(app.exception)
            self.assertEqual(updated.status, CaseStatus.WAITING_STYLE_FEEDBACK)
            self.assertEqual(updated.plan_version, 2)
            self.assertIn(reason, updated.applied_constraints[-1])
            self.assertEqual(updated.creative_opinions[0].recommended_activities, brief().recommended_activities)
            self.assertIn(reason, model.call_args_list[1].args[1])

    def test_optional_budget_and_style_questions_do_not_block_plan(self):
        workflow = TravelAgencyWorkflow(TEAM)
        case = workflow.create_case("南京户外团建，预算5000元")
        case.requirements = RequirementSpec(city="南京", event_date="2026-11-20",
            participant_count=30, budget_yuan=5000, preferred_style="户外活动")
        case.status = CaseStatus.CROSS_REVIEW
        questions = ["是否认可轻度户外协作？", "住宿与交通的预算优先级？"]
        plan = CouncilDecision(theme="户外协作", summary="保留总预算", schedule=["户外协作", "午餐"],
            merged_ideas=[], rationale=["低强度"], unresolved_customer_decisions=questions)
        model = AsyncMock(side_effect=[FeasibilityReview(summary="可行"),
            ManagedPlan(plan=plan, checks=checks()), checks()])
        with patch("src.workflow.run_structured", new=model):
            asyncio.run(workflow.advance(case))
            self.assertEqual(case.status, CaseStatus.WAITING_DRAFT)
            self.assertEqual(case.council_decision.items_to_verify, questions)
            self.assertEqual(case.customer_questions, [])
            asyncio.run(workflow.submit_draft_feedback(case, []))
        self.assertEqual(case.status, CaseStatus.READY_FOR_CUSTOMER)
        self.assertEqual(model.await_count, 3)

    def test_saved_wait_recovers_in_customer_view_without_rerunning_model(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_case_to_disk, load_case_from_disk

        workflow = TravelAgencyWorkflow(TEAM)
        case = workflow.create_case("南京团建")
        case.requirements = RequirementSpec(city="南京", event_date="2026-11-20", participant_count=30)
        case.council_decision = CouncilDecision(theme="午餐与协作", summary="草案", schedule=["午餐"],
            merged_ideas=[], rationale=[], unresolved_customer_decisions=["午餐喜欢什么口味？"])
        case.pending_customer_step = CaseStatus.DELIBERATION
        workflow._ask_customer(case, case.council_decision.unresolved_customer_decisions)
        save_case_to_disk(case)
        model = AsyncMock()
        with patch("src.agents.build_agent_team", return_value=TEAM), patch("src.workflow.run_structured", new=model):
            app = AppTest.from_file(str(APP), default_timeout=30)
            app.session_state["customer_case_id"] = case.id
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(load_case_from_disk(case.id).status, CaseStatus.WAITING_DRAFT)
        self.assertTrue(any(b.label=="确认草案，进入最终检查" for b in app.button))
        self.assertFalse(any(b.label=="提交补充信息" for b in app.button))
        model.assert_not_awaited()

    def test_required_decision_keeps_block_and_cannot_restart_question_loop(self):
        workflow = TravelAgencyWorkflow(TEAM)
        case = workflow.create_case("必须全员无障碍")
        case.requirements = RequirementSpec(city="南京", event_date="2026-11-20", participant_count=30)
        case.council_decision = CouncilDecision(theme="活动", summary="草案", schedule=["台阶路线"],
            merged_ideas=[], rationale=[])
        finding = ReviewFinding(severity="high", description="路线不满足已确认的无障碍要求",
            decision_owner="customer", evidence="活动路线需走台阶", release_condition="客户确认替代路线")
        workflow._run_reviews = AsyncMock(return_value=[ReviewResult(
            reviewer_name="履约", passed=False, findings=[finding])])
        asyncio.run(workflow._review_and_revise(case))
        self.assertEqual(case.status, CaseStatus.WAITING_CUSTOMER)
        self.assertFalse(workflow.restore_optional_question_wait(case))
        workflow.record_customer_answer(case, "需要无台阶路线")
        self.assertEqual(case.status, CaseStatus.SUPPLEMENTING)
        case.clarification_rounds = 1
        asyncio.run(workflow._review_and_revise(case))
        self.assertEqual(case.status, CaseStatus.WAITING_OWNER)
        with self.assertRaises(WorkflowError):
            asyncio.run(workflow.submit_owner_decision(case, True))
        self.assertEqual(case.status, CaseStatus.WAITING_OWNER)

    def test_matching_template_uses_one_satisfied_order_and_deduplicates_sources(self):
        req = RequirementSpec(preferred_style="户外活动", team_profile="混龄", desired_outcomes=["团队合作"])
        current = TravelAgencyWorkflow(TEAM).create_case("户外团建")
        current.requirements = req
        records = [
            {"case": {"id": f"case_{i}", "status": "completed", "service_mode": "travel_planning",
                      "plan_acceptance_feedback": {"outcome": "satisfied"},
                      "requirements": req.model_dump(),
                      "council_decision": {"schedule": ["分组探索", "午餐后安排固定休息点"]}}}
            for i in range(3)
        ]
        with patch("src.style_planning.saved_case_snapshots", return_value=[*records[:2], records[0]]):
            result = matching_style_template(current)
            self.assertEqual([item["case_id"] for item in result["examples"]], ["case_0", "case_1"])
        with patch("src.style_planning.saved_case_snapshots", return_value=records[:1]):
            self.assertEqual(matching_style_template(current)["examples"][0]["case_id"], "case_0")
        with patch("src.style_planning.saved_case_snapshots", return_value=records):
            result = matching_style_template(current)
            self.assertEqual(result["methods"]["用分组任务带动参与"], ["case_0", "case_1", "case_2"])
            self.assertIn("安排明确的休息点或休息时段", result["methods"])
            current.requirements.team_profile = "青年"
            self.assertIsNone(matching_style_template(current))

    def test_wizard_style_branch_and_targeted_draft_supplement(self):
        from streamlit.testing.v1 import AppTest
        initial = CouncilDecision(theme="轻量户外协作", summary="先协作再午餐",
            schedule=["09:00台阶路线探索", "12:00午餐"], merged_ideas=[], rationale=["低强度"])
        revised = initial.model_copy(update={"schedule": ["09:00无台阶路线探索", "12:00午餐"]})
        updated = RequirementSpec(city="南京", event_date="2026-11-20", participant_count=30,
            duration="1天", preferred_style="轻松户外探索，保留休息点", hard_constraints=["两位成员不能走楼梯"])
        model = AsyncMock(side_effect=[brief(), FeasibilityReview(summary="核对步行和休息条件"),
            ManagedPlan(plan=initial, checks=checks()), IntakeResult(requirements=updated, summary="增加无障碍要求"),
            revised, checks()])
        with patch("src.agents.build_agent_team", return_value=TEAM), patch("src.workflow.run_structured", new=model):
            app = AppTest.from_file(str(APP), default_timeout=30).run()
            next(x for x in app.text_input if x.label=="活动城市 *").set_value("南京")
            app.date_input[0].set_value(date(2026, 11, 20))
            next(b for b in app.button if b.label=="下一步").click().run()
            style = "轻松户外探索，保留休息点"
            self.assertFalse(any(s.label=="活动风格（其他）" for s in app.text_input))
            app.pills(key="trip_style:choice").set_value("其他").run()
            next(b for b in app.button if b.label=="下一步").click().run()
            self.assertTrue(any("填写其他风格" in item.value for item in app.error))
            app.pills(key="trip_team:choice").set_value("其他").run()
            next(s for s in app.text_input if s.label=="活动风格（其他）").set_value(style)
            next(s for s in app.text_input if s.label=="团队情况（其他）").set_value("实习生与导师")
            app.multiselect(key="trip_dietary:choice").set_value(["需要素食选择", "其他"]).run()
            next(s for s in app.text_area if s.label=="餐饮需求（其他）").set_value("不要芒果")
            next(b for b in app.button if b.label=="下一步").click().run()
            next(b for b in app.button if b.label=="上一步").click().run()
            self.assertEqual(next(s for s in app.text_input if s.label=="活动风格（其他）").value, style)
            self.assertEqual(next(s for s in app.text_input if s.label=="团队情况（其他）").value, "实习生与导师")
            self.assertEqual(next(s for s in app.text_area if s.label=="餐饮需求（其他）").value, "不要芒果")
            next(b for b in app.button if b.label=="下一步").click().run()
            self.assertEqual(model.await_count, 0)
            next(b for b in app.button if b.label=="提交团建需求").click().run()
            self.assertFalse(app.exception)
            case = app.session_state["case"]
            self.assertEqual(case.status, CaseStatus.WAITING_STYLE_FEEDBACK)
            self.assertEqual([p.agent_name for p in case.creative_opinions], ["策划"])
            self.assertIn(f"活动方向：{style}", model.call_args_list[0].args[1])
            self.assertEqual(case.requirements.preferred_style, style)
            self.assertEqual(case.requirements.team_profile, "实习生与导师")
            self.assertIn("餐饮要求：不要芒果", case.requirements.hard_constraints)
            next(b for b in app.button if b.label=="确认方向，查看草案").click().run()
            self.assertEqual(app.session_state["case"].status, CaseStatus.WAITING_DRAFT)
            details = next(item for item in app.expander if item.label=="查看行程细节")
            self.assertFalse(details.proto.expanded)
            next(b for b in app.button if b.label=="我想补充关键信息").click().run()
            next(x for x in app.text_area if x.label=="本次补充").set_value("两位成员不能走楼梯")
            next(b for b in app.button if b.label=="提交补充").click().run()
            self.assertFalse(app.exception)
            case = app.session_state["case"]
            self.assertEqual(case.status, CaseStatus.WAITING_DRAFT)
            self.assertEqual(case.plan_version, 2)
            self.assertEqual(case.council_decision.schedule, revised.schedule)
            next(b for b in app.button if b.label=="确认草案，进入最终检查").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["case"].status, CaseStatus.READY_FOR_CUSTOMER)
        self.assertEqual([call.args[0] for call in model.call_args_list],
                         ["planner", "fulfillment", "manager", "manager", "planner", "fulfillment"])

    def test_existing_booking_supplement_preserves_order(self):
        from test_three_agent_flow import prepare
        workflow = TravelAgencyWorkflow(TEAM)
        case = workflow.create_case("已预订案例")
        case.requirements = RequirementSpec(city="南京", event_date="2026-11-20", participant_count=30)
        case.council_decision = CouncilDecision(theme="团建", summary="团建", schedule=["午餐"], merged_ideas=[], rationale=[])
        case.status = CaseStatus.COMPLETED
        order = prepare(case, nights=0)
        saved = order.model_dump()
        workflow.record_supplement(case, "有人新增餐饮忌口")
        self.assertEqual(case.procurement_order.model_dump(), saved)
        self.assertEqual(case.status, CaseStatus.COMPLETED)
        self.assertEqual(case.audit_log[-1].action, "booking_change_requested")

    def test_pause_finishes_current_call_and_stops_before_next_handoff(self):
        started, release = Event(), Event()
        workflow = TravelAgencyWorkflow(TEAM)
        case = workflow.create_case("户外团建")
        case.requirements = RequirementSpec(city="南京", preferred_style="户外活动")
        case.status = CaseStatus.CROSS_REVIEW

        async def executor(*args):
            started.set()
            if not release.wait(10):
                raise RuntimeError("测试等待超时")
            return FeasibilityReview(summary="执行条件已经核对")

        model = AsyncMock(side_effect=executor)
        with patch("src.workflow.run_structured", new=model):
            job = submit_once(("planning", case.id, "test"), case.id,
                              lambda: asyncio.run(workflow.advance(case)))
            try:
                self.assertTrue(started.wait(10))
                pause_planning(case.id)
            finally:
                release.set()
            self.assertEqual(job.future.result(timeout=10).status, CaseStatus.DELIBERATION)
        self.assertEqual(model.await_count, 1)
        self.assertIsNotNone(case.feasibility_review)
