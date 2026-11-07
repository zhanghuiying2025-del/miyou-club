import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.models import (
    CaseStatus, CouncilDecision, PlanAcceptanceFeedback, ReviewFinding, ReviewResult,
    TravelCase,
    RequirementSpec, CustomerStyleFeedback, IntakeResult, CreativeOpinion, CreativeRound,
)
from src.performance import company_lessons, set_lesson_active
from src.policy import CompanyPolicy, owner_approval_reasons
from src.workflow import TravelAgencyWorkflow, InvalidStateError


def complete(coroutine):
    """Run mocked, immediately resolved awaits without opening a network event loop."""
    try:
        coroutine.send(None)
    except StopIteration as finished:
        return finished.value
    coroutine.close()
    raise AssertionError("A mocked test unexpectedly attempted external IO")


def plan():
    return CouncilDecision(
        theme="测试方案", summary="城市探索", schedule=["上午城市活动"],
        merged_ideas=[], rationale=["满足客户目标"],
    )


class WorkflowRegressions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch("src.performance.PERFORMANCE_FILE", Path(self.directory.name) / "performance.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workflow = TravelAgencyWorkflow(SimpleNamespace(manager=object(), planner=object(), fulfillment=object()))

    def case(self):
        case = self.workflow.create_case("测试客户需求", planning_mode="single_agent_roles")
        case.council_decision = plan()
        case.lead_agent = "宋野"
        case.status = CaseStatus.READY_FOR_CUSTOMER
        case.plan_feedback_options = self.workflow._fallback_plan_feedback_options()
        return case

    def review(self, decision_owner=None, severity="medium"):
        return [ReviewResult(
            reviewer_name="运营审核员", passed=False,
            findings=[ReviewFinding(
                severity=severity, description="仍有待解决问题",
                decision_owner=decision_owner, can_auto_fix=False,
            )],
        )]

    def test_failed_review_at_limit_requires_owner(self):
        case = self.case()
        case.status = CaseStatus.REVIEWING
        case.revision_count = self.workflow.policy.max_revision_count
        self.workflow._run_reviews = AsyncMock(return_value=self.review())
        complete(self.workflow._review_and_revise(case))
        self.assertEqual(case.status, CaseStatus.WAITING_OWNER)
        self.assertIsNotNone(case.approval)

    def test_passed_review_releases_plan(self):
        case = self.case()
        self.workflow._run_reviews = AsyncMock(return_value=[ReviewResult(reviewer_name="运营", passed=True)])
        complete(self.workflow._review_and_revise(case))
        self.assertEqual(case.status, CaseStatus.READY_FOR_CUSTOMER)

    def test_clarification_limit_needs_authorization_then_customer(self):
        case = self.case()
        case.clarification_rounds = self.workflow.policy.max_clarification_rounds
        self.workflow._run_reviews = AsyncMock(return_value=self.review("customer"))
        complete(self.workflow._review_and_revise(case))
        self.assertEqual(case.status, CaseStatus.WAITING_OWNER)
        complete(self.workflow.submit_owner_decision(case, True))
        self.assertEqual(case.status, CaseStatus.WAITING_CUSTOMER)
        self.assertTrue(case.customer_questions)

    def test_high_customer_choice_is_not_owner_approval(self):
        self.assertEqual(owner_approval_reasons(self.review("customer", "high"), CompanyPolicy()), [])

    def test_mixed_risks_cannot_skip_customer_choice(self):
        case = self.case()
        case.reviews = self.review("owner", "high") + self.review("customer", "high")
        self.workflow._run_reviews = AsyncMock(return_value=case.reviews)
        complete(self.workflow._review_and_revise(case))
        complete(self.workflow.submit_owner_decision(case, True))
        self.assertEqual(case.status, CaseStatus.WAITING_CUSTOMER)

    def test_timeout_preserves_step_and_can_resume(self):
        case = self.case()
        case.status = CaseStatus.CROSS_REVIEW
        self.workflow._run_cross_review = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            complete(self.workflow.advance(case))
        restored = TravelCase.model_validate_json(case.model_dump_json())
        self.assertEqual(restored.resume_status, CaseStatus.CROSS_REVIEW)

        async def reviewed(current):
            current.status = CaseStatus.READY_FOR_CUSTOMER

        self.workflow._run_cross_review = AsyncMock(side_effect=reviewed)
        complete(self.workflow.resume_case(restored))
        self.assertEqual(restored.status, CaseStatus.READY_FOR_CUSTOMER)
        self.assertIsNone(restored.error_message)

    def test_learning_survives_new_workflow_without_agent_scorecards(self):
        case = self.case()
        self.workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(outcome="satisfied"))
        new_workflow = TravelAgencyWorkflow(SimpleNamespace())
        second = new_workflow.create_case("第二位客户")
        self.assertEqual(second.scorecards, [])
        self.assertEqual([lesson.source_case_id for lesson in company_lessons()], [case.id])

    def test_retry_from_stale_case_does_not_duplicate_learning(self):
        case = self.case()
        stale = case.model_copy(deep=True)
        feedback = PlanAcceptanceFeedback(outcome="satisfied")
        self.workflow.submit_plan_acceptance(case, feedback)
        self.workflow.submit_plan_acceptance(stale, feedback)
        self.assertEqual(len(company_lessons()), 1)
        with self.assertRaises(InvalidStateError):
            self.workflow.submit_plan_acceptance(case, feedback)

    def test_new_feedback_preserves_old_learning_store_fields_without_using_them(self):
        store = Path(self.directory.name) / "performance.json"
        historical_cards = [{"agent_name": "宋野", "completed_cases": 12}]
        store.write_text(json.dumps({"scorecards": historical_cards}), encoding="utf-8")
        case = self.case()
        self.workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(outcome="satisfied"))
        lesson, = company_lessons()
        set_lesson_active(lesson.id, False)
        self.assertEqual(json.loads(store.read_text(encoding="utf-8"))["scorecards"], historical_cards)
        self.assertEqual(case.scorecards, [])
        self.assertFalse(company_lessons()[0].active)

    def reject(self, case):
        self.workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(
            outcome="unacceptable", selected_option_ids=["bad_style"], comment="活动过于单调",
        ))

    def test_rework_generation_failure_keeps_original_feedback(self):
        case = self.case()
        self.reject(case)
        with patch("src.workflow.run_structured", new=AsyncMock(side_effect=RuntimeError("offline"))):
            with self.assertRaises(RuntimeError):
                complete(self.workflow.rework_rejected_plan(case))
        self.assertEqual(case.status, CaseStatus.NEEDS_REWORK)
        self.assertIsNotNone(case.plan_acceptance_feedback)
        self.assertEqual(len(case.feedback_history), 1)

    def test_rework_review_failure_retains_history_and_resumes_review(self):
        case = self.case()
        self.reject(case)
        self.workflow._run_reviews = AsyncMock(side_effect=RuntimeError("offline"))
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=plan())):
            complete(self.workflow.rework_rejected_plan(case))
        restored = TravelCase.model_validate_json(case.model_dump_json())
        self.assertEqual(restored.status, CaseStatus.FAILED)
        self.assertEqual(restored.resume_status, CaseStatus.REVIEWING)
        self.assertEqual(restored.feedback_history[0].feedback.comment, "活动过于单调")
        self.assertIsNotNone(restored.feedback_history[0].plan)

    def test_reworked_version_can_receive_separate_feedback(self):
        case = self.case()
        self.reject(case)
        self.workflow._run_reviews = AsyncMock(return_value=[ReviewResult(reviewer_name="运营", passed=True)])
        self.workflow._prepare_plan_feedback_options = AsyncMock()
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=plan())):
            complete(self.workflow.rework_rejected_plan(case))
        self.assertEqual(case.plan_version, 2)
        self.workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(outcome="satisfied"))
        self.assertEqual(len(case.feedback_history), 2)
        self.assertEqual([lesson.source_version for lesson in company_lessons()], [1, 2])

    def test_customer_and_owner_pages_render_new_states(self):
        from streamlit.testing.v1 import AppTest

        case = self.case()
        application = Path(__file__).resolve().parents[1] / "app.py"
        from src.storage import save_case_to_disk
        with patch("src.agents.build_agent_team", return_value=SimpleNamespace()), patch(
            "src.storage.CASE_FILE", Path(self.directory.name) / "cases.json"
        ):
            app = AppTest.from_file(str(application), default_timeout=30).run()
            app.session_state["case"] = case
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any(item.label == "方案结论" for item in app.radio))
            case.status = CaseStatus.FAILED
            case.resume_status = CaseStatus.CROSS_REVIEW
            save_case_to_disk(case)
            app.sidebar.radio[0].set_value("公司老板").run()
            app.sidebar.radio(key="work_area").set_value("团建策划与交付").run()
            next(item for item in app.selectbox if item.label == "历史案件与未完成进度").set_value(case.id).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any(item.label == "从中断步骤继续处理" for item in app.button))

    def test_structured_requirements_confirmation_needs_no_model(self):
        case = self.case()
        case.requirements = RequirementSpec(city="南京", event_date="2026-11-10", participant_count=30)
        case.status = CaseStatus.WAITING_REQUIREMENTS
        self.workflow.confirm_requirements(case)
        self.assertEqual(case.status, CaseStatus.CREATIVE_COUNCIL)

    def test_customer_rejection_starts_new_version_and_keeps_feedback(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import load_case_from_disk

        case = self.case()
        original = case.council_decision.model_copy(deep=True)
        updated = original.model_copy(update={"theme": "室内活动", "schedule": ["在同一场地小组交流"]})
        model = AsyncMock(return_value=updated)
        with patch("src.agents.build_agent_team", return_value=self.workflow.team), \
             patch("src.storage.CASE_FILE", Path(self.directory.name) / "cases.json"), \
             patch("src.workflow.run_structured", new=model), \
             patch.object(TravelAgencyWorkflow, "_run_reviews", new=AsyncMock(return_value=[ReviewResult(reviewer_name="运营", passed=True)])), \
             patch.object(TravelAgencyWorkflow, "_prepare_plan_feedback_options", new=AsyncMock()):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
            app.session_state["case"] = case
            app.run()
            next(item for item in app.radio if item.label == "方案结论").set_value("unacceptable").run()
            next(item for item in app.text_area if item.label == "哪里不满意？").set_value("转场太多，希望集中在同一个场地")
            next(item for item in app.button if item.label == "提交意见并重新生成").click().run()
            self.assertFalse(app.exception)
            saved = load_case_from_disk(case.id)
        self.assertEqual(saved.plan_version, 2)
        self.assertEqual(saved.council_decision.schedule, updated.schedule)
        self.assertEqual(saved.feedback_history[0].plan, original)
        self.assertIn("转场太多", saved.feedback_history[0].feedback.comment)
        self.assertTrue(any("已学习情景" in item.value for item in app.success))
        model.assert_awaited_once()

    def test_intake_stops_for_customer_confirmation(self):
        case = self.case()
        self.workflow.team.manager = object()
        result = IntakeResult(requirements=RequirementSpec(city="南京", event_date="2026-11-10", participant_count=30), summary="需求已提取")
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=result)) as call:
            complete(self.workflow._run_intake(case))
        self.assertEqual(case.status, CaseStatus.WAITING_REQUIREMENTS)
        self.assertEqual(call.await_count, 1)

    def test_pure_preference_does_not_call_feedback_model(self):
        case = self.case()
        case.status = CaseStatus.WAITING_STYLE_FEEDBACK
        self.workflow.advance = AsyncMock(return_value=case)
        with patch("src.workflow.run_structured", new=AsyncMock()) as call:
            complete(self.workflow.submit_style_feedback(case, CustomerStyleFeedback(preferred_agent="宋野", preference_reason="节奏更轻松")))
        call.assert_not_awaited()
        self.assertEqual(case.status, CaseStatus.CROSS_REVIEW)
        self.assertFalse(case.feedback_assessment.complaint_present)
        self.assertIn("本案客户偏好：节奏更轻松", case.applied_constraints)

    def test_draft_adjustment_retains_customer_checkpoint(self):
        case = self.case()
        case.status = CaseStatus.WAITING_DRAFT
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=plan())):
            complete(self.workflow.submit_draft_feedback(case, ["增加休息"]))
        self.assertEqual(case.status, CaseStatus.WAITING_DRAFT)
        self.assertEqual(case.draft_revision_count, 1)
        self.assertIsNone(case.plan_acceptance_feedback)

    def test_draft_confirmation_reviews_before_release(self):
        case = self.case()
        case.status = CaseStatus.WAITING_DRAFT
        self.workflow._run_reviews = AsyncMock(return_value=[ReviewResult(reviewer_name="运营", passed=True)])
        complete(self.workflow.submit_draft_feedback(case, []))
        self.assertEqual(case.status, CaseStatus.READY_FOR_CUSTOMER)
        self.workflow._run_reviews.assert_awaited_once()

    def test_draft_generation_failure_preserves_previous_draft(self):
        case = self.case()
        case.status = CaseStatus.WAITING_DRAFT
        original = case.council_decision.model_dump()
        with patch("src.workflow.run_structured", new=AsyncMock(side_effect=RuntimeError("offline"))):
            with self.assertRaises(RuntimeError):
                complete(self.workflow.submit_draft_feedback(case, ["增加休息"]))
        self.assertEqual(case.council_decision.model_dump(), original)
        self.assertEqual(case.draft_revision_count, 0)

    def test_customer_question_resumes_draft_without_restarting_proposals(self):
        case = self.case()
        case.requirements = RequirementSpec(city="南京")
        case.pending_customer_step = CaseStatus.DELIBERATION
        self.workflow._ask_customer(case, ["选择室内还是室外？"])
        self.workflow.record_customer_answer(case, "室内")
        self.assertEqual(case.status, CaseStatus.DELIBERATION)
        self.assertIn("客户补充决策：室内", case.applied_constraints)

    def test_customer_checkpoints_render(self):
        from streamlit.testing.v1 import AppTest
        case = self.case()
        case.requirements = RequirementSpec(city="南京", event_date="2026-11-10", participant_count=30)
        application = Path(__file__).resolve().parents[1] / "app.py"
        with patch("src.agents.build_agent_team", return_value=SimpleNamespace()), patch("src.storage.load_case_from_disk", return_value=None):
            app = AppTest.from_file(str(application), default_timeout=30).run()
            for status in (CaseStatus.WAITING_REQUIREMENTS, CaseStatus.WAITING_DRAFT):
                case.status = status
                app.session_state["case"] = case
                app.run()
                self.assertEqual(len(app.exception), 0)

    def test_confirm_button_runs_single_planner_with_progress(self):
        from streamlit.testing.v1 import AppTest
        case = self.case()
        case.status = CaseStatus.WAITING_REQUIREMENTS
        case.requirements = RequirementSpec(city="南京", event_date="2026-11-10", participant_count=30)
        team = SimpleNamespace(planner=object())
        opinions = [CreativeOpinion(
            agent_name=name, persona_title="策划师", concept_name="轻松探索",
            one_sentence_pitch="轻松且全员参与", recommended_activities=["城市寻宝"],
            reasons=["适合团队"], confidence=0.8,
        ) for name in ("宋野", "沈知行", "夏橙")]
        application = Path(__file__).resolve().parents[1] / "app.py"
        round_result = CreativeRound(song_ye=opinions[0], shen_zhixing=opinions[1], xia_cheng=opinions[2])
        with patch("src.agents.build_agent_team", return_value=team), patch("src.storage.load_case_from_disk", return_value=None), patch("src.storage.save_case_to_disk"), patch("src.workflow.run_structured", new=AsyncMock(return_value=round_result)):
            app = AppTest.from_file(str(application), default_timeout=30).run()
            app.session_state["case"] = case
            app.run()
            confirm = next(button for button in app.button if button.label == "确认需求，让三位策划师提案")
            confirm.click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.session_state["case"].status, CaseStatus.WAITING_STYLE_FEEDBACK)
            self.assertEqual(len(app.session_state["case"].creative_opinions), 3)


if __name__ == "__main__":
    unittest.main()
