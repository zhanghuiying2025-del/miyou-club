import asyncio
from datetime import date
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src.models import CaseStatus, CouncilDecision, FeasibilityReview, IntakeResult, ManagedPlan, ProposalBrief, RequirementSpec
from src.style_planning import matching_style_template
from src.runtime import pause_planning, submit_once
from src.workflow import TravelAgencyWorkflow
from test_compact_planning import checks


TEAM = SimpleNamespace(manager="manager", planner="planner", fulfillment="fulfillment")
APP = Path(__file__).resolve().parents[1] / "app.py"


def brief():
    return ProposalBrief(concept_name="轻量户外协作", one_sentence_pitch="沿平缓路线分组探索",
                         recommended_activities=["分组探索"], reasons=["适合混龄成员"])


class StyleRequestFlowTests(unittest.TestCase):
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
            next(s for s in app.text_input if s.label=="活动风格").set_value(style).run()
            next(b for b in app.button if b.label=="下一步").click().run()
            next(b for b in app.button if b.label=="上一步").click().run()
            self.assertEqual(next(s for s in app.text_input if s.label=="活动风格").value, style)
            next(b for b in app.button if b.label=="下一步").click().run()
            self.assertEqual(model.await_count, 0)
            next(b for b in app.button if b.label=="提交团建需求").click().run()
            self.assertFalse(app.exception)
            case = app.session_state["case"]
            self.assertEqual(case.status, CaseStatus.WAITING_STYLE_FEEDBACK)
            self.assertEqual([p.agent_name for p in case.creative_opinions], ["策划"])
            self.assertIn(f"活动方向：{style}", model.call_args_list[0].args[1])
            self.assertEqual(case.requirements.preferred_style, style)
            next(b for b in app.button if b.label=="确认方向，查看草案").click().run()
            self.assertEqual(app.session_state["case"].status, CaseStatus.WAITING_DRAFT)
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
