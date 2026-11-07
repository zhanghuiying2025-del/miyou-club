import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.compact_planning import input_fingerprint
from src.feedback_learning import (match_reason, scope_for, select_lessons,
    verified_complaint_evidence, lessons_from_complaint)
from src.models import (CaseStatus, CouncilDecision, CreativeOpinion, CustomerStyleFeedback,
    FeedbackAssessment, LearningResponse, OrganizationState, OutcomeFeedback,
    PlanAcceptanceFeedback, RequirementSpec, TravelCase)
from src.performance import company_lessons, set_lesson_active
from src.workflow import TravelAgencyWorkflow


class FeedbackLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, kwargs in [
            ("src.performance.PERFORMANCE_FILE", {"new": Path(self.temp.name) / "performance.json"}),
            ("src.workflow.load_organization_state", {"side_effect": OrganizationState}),
        ]:
            patcher = patch(name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.workflow = TravelAgencyWorkflow(SimpleNamespace(manager="manager", planner="planner", fulfillment="executor"))

    def case(self):
        case = self.workflow.create_case("南京混龄团队，30人，要求全程低强度、自愿参加",
                                         planning_mode="single_agent_roles")
        case.requirements = RequirementSpec(city="南京", participant_count=30, budget_yuan=9000,
            preferred_style="户外活动", team_profile="混龄团队", duration="一天",
            hard_constraints=["全程低强度", "自愿参加"])
        case.status = CaseStatus.READY_FOR_CUSTOMER
        case.lead_agent = "夏橙"
        case.council_decision = CouncilDecision(theme="分组体验", summary="运动与休闲并行",
            schedule=["上午公园自愿分组，运动组与休闲组并行", "中午共同集合用餐"],
            merged_ideas=[], rationale=["照顾不同体能"])
        case.plan_feedback_options = self.workflow._fallback_plan_feedback_options()
        return case

    def positive(self, case=None):
        case = case or self.case()
        return self.workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(outcome="satisfied",
            selected_option_ids=["sat_style"], comment="分组很好，不勉强任何人"))

    def test_explicit_positive_feedback_is_scoped_candidate_not_salary(self):
        case = self.positive()
        lesson = company_lessons()[0]
        self.assertEqual(lesson.kind, "success")
        self.assertEqual(lesson.source_stage, "方案评价")
        self.assertEqual(lesson.scope.city, "南京")
        self.assertIn("分组很好，不勉强任何人", lesson.evidence)
        self.assertEqual(lesson.source_schedule, case.council_decision.schedule)
        self.assertEqual(case.feedback_history[0].lessons[0].id, lesson.id)
        self.assertEqual(case.pay_adjustments, [])
        self.assertEqual(case.feedback_history[0].personality_after, {})
        self.assertEqual(case.scorecards, [])
        self.assertIsNone(case.feedback_history[0].ratings)

    def test_bare_satisfaction_saves_template_without_inventing_reasons(self):
        case = self.case()
        self.workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(outcome="satisfied"))
        lesson, = company_lessons()
        self.assertEqual(lesson.evidence, ["客户对本版整体方案评价为满意"])
        self.assertEqual(lesson.source_schedule, case.council_decision.schedule)
        self.assertEqual(case.feedback_history[0].lessons[0].id, lesson.id)
        self.assertTrue(case.audit_log[-1].detail.startswith("已学习情景"))

    def test_replay_does_not_duplicate_lessons(self):
        case = self.case()
        stale = case.model_copy(deep=True)
        self.positive(case)
        self.positive(stale)
        self.assertEqual(len(company_lessons()), 1)
        self.assertEqual(stale.feedback_history[0].lessons, case.feedback_history[0].lessons)

    def test_delivery_feedback_keeps_original_plan_evaluation_and_records_timestamp(self):
        from test_three_agent_flow import prepare
        from src.workflow import InvalidStateError
        case = self.positive()
        order = prepare(case, nights=0)
        feedback = OutcomeFeedback(overall_rating=3, novelty_rating=4, culture_rating=4,
            detail_rating=4, engagement_rating=4, feasibility_rating=4, comment="现场休息时间太短", incidents=["休息不足"])
        with self.assertRaises(InvalidStateError):
            self.workflow.submit_outcome_feedback(case, feedback, after_delivery=True)
        order.settled = True
        self.workflow.submit_outcome_feedback(case, feedback, after_delivery=True)
        self.assertNotEqual(case.plan_acceptance_feedback.comment, case.delivery_feedback.comment)
        lesson = company_lessons()[-1]
        self.assertEqual(lesson.source_stage, "履约后反馈")
        self.assertIsNotNone(lesson.created_at)
        self.assertIn("已学习情景", case.audit_log[-1].detail)

    def test_similar_next_order_receives_record_but_same_order_does_not(self):
        case = self.positive()
        lesson = company_lessons()[0]
        self.assertIsNone(match_reason(lesson, case))
        second = self.case()
        self.workflow._load_learning(second)
        self.workflow._load_learning(second)
        self.assertEqual(len(second.learning_applications), 1)
        self.assertIn("人群", second.learning_applications[0].match_reason)
        self.assertEqual(company_lessons()[0].supplied_cases, 1)

    def test_other_city_style_group_and_missing_conditions_are_not_matched(self):
        self.positive()
        lesson = company_lessons()[0]
        for field, value in [("city", "杭州"), ("preferred_style", "博物馆古迹参观"),
                             ("team_profile", "小学生"), ("hard_constraints", []), ("city", None)]:
            second = self.case()
            setattr(second.requirements, field, value)
            self.assertIsNone(match_reason(lesson, second), field)

    def test_no_scope_does_not_silently_become_global_rule(self):
        self.positive()
        lesson = company_lessons()[0]
        lesson.scope = scope_for(TravelCase(customer_request="未知条件"))
        self.assertIsNone(match_reason(lesson, self.case()))

    def test_changed_requirements_remove_previous_reference(self):
        self.positive()
        second = self.case()
        self.workflow._load_learning(second)
        self.assertEqual(len(second.learning_applications), 1)
        second.requirements.team_profile = "小学生"
        self.workflow._load_learning(second)
        self.assertEqual(second.learning_applications, [])

    def test_negative_feedback_is_check_not_confirmed_fault(self):
        case = self.case()
        self.workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(outcome="unacceptable",
            selected_option_ids=["bad_constraint"], comment="担心活动强度太大"))
        self.assertEqual(case.status, CaseStatus.NEEDS_REWORK)
        self.assertEqual(company_lessons()[0].kind, "check")
        self.assertIn("不代表已认定违规", company_lessons()[0].action)
        self.assertEqual(self.workflow.organization.constraints, [])

    def test_acceptable_specific_concern_is_saved_as_check(self):
        case = self.case()
        self.workflow.submit_plan_acceptance(case, PlanAcceptanceFeedback(outcome="acceptable",
            selected_option_ids=["acc_pace"]))
        self.assertEqual(company_lessons()[0].kind, "check")
        self.assertEqual(case.status, CaseStatus.COMPLETED)

    def test_manager_prompt_and_usage_link_to_real_record_and_schedule(self):
        self.positive()
        lesson = company_lessons()[0]
        second = self.case()
        plan = second.council_decision.model_copy(deep=True)
        plan.learning_responses = [
            LearningResponse(lesson_id=lesson.id, decision="adapted", detail="保留分组，增加共同集合", schedule_index=0),
            LearningResponse(lesson_id="invented", decision="adopted", detail="伪造", schedule_index=0),
            LearningResponse(lesson_id=lesson.id, decision="adopted", detail="越界", schedule_index=99),
        ]
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=plan)) as model:
            result = asyncio.run(self.workflow._call(second, "manager", "形成方案", CouncilDecision))
        self.assertEqual(model.await_count, 1)
        prompt = model.call_args.args[1]
        self.assertIn(lesson.id, prompt)
        self.assertIn("分组很好", prompt)
        self.assertIn("不机械照搬", prompt)
        self.assertEqual(len(result.learning_responses), 1)
        self.assertEqual(result.learning_responses[0].detail, "保留分组，增加共同集合")
        self.assertEqual(second.learning_applications[0].supplied_to, ["项目经理"])

    def test_disabled_record_is_not_supplied_and_invalidates_check_cache(self):
        self.positive()
        second = self.case()
        self.workflow._load_learning(second)
        before = input_fingerprint(second)
        second.learning_applications[0].supplied_to.append("项目经理")
        self.assertEqual(before, input_fingerprint(second))
        set_lesson_active(company_lessons()[0].id, False)
        self.workflow._load_learning(second)
        self.assertEqual(second.learning_applications, [])
        self.assertNotEqual(before, input_fingerprint(second))

    def complaint(self, case, valid=True):
        case.creative_opinions = [CreativeOpinion(agent_name="夏橙", persona_title="户外策划", concept_name="挑战",
            one_sentence_pitch="挑战", recommended_activities=["全员强制两小时徒步"], reasons=["团队参与"], confidence=.8)]
        return FeedbackAssessment(feedback_kind="complaint", complaint_present=True, complaint_reasonable=True,
            rationale="要求低强度，提案却强制徒步", responsible_agents=["夏橙"], customer_response="将调整",
            evidence=["全程低强度", "全员强制两小时徒步"] if valid else ["不存在的客户要求", "不存在的安排"],
            organization_constraints=["检查是否提供自愿与低强度替代"])

    def test_complaint_needs_literal_customer_and_proposal_evidence(self):
        case = self.case()
        assessment = self.complaint(case)
        self.assertEqual(len(verified_complaint_evidence(case, assessment)), 2)
        lessons = lessons_from_complaint(case, assessment, "complaint-1")
        self.assertEqual(len(lessons), 1)
        assessment.evidence = ["不存在的客户要求", "全员强制两小时徒步"]
        self.assertEqual(lessons_from_complaint(case, assessment, "complaint-2"), [])

    def test_complaint_creates_scoped_check_without_personality_penalty(self):
        case = self.case()
        case.status = CaseStatus.WAITING_STYLE_FEEDBACK
        assessment = self.complaint(case)
        self.workflow.advance = AsyncMock(side_effect=lambda value: value)
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=assessment)):
            asyncio.run(self.workflow.submit_style_feedback(case, CustomerStyleFeedback(complaint="违反低强度要求")))
        self.assertEqual(len(company_lessons()), 1)
        self.assertIn("本案投诉检查", "；".join(case.applied_constraints))
        self.assertEqual(self.workflow.organization.constraints, [])
        self.assertEqual(case.personality_snapshots["夏橙"].history, [])

    def test_fabricated_complaint_does_not_create_check(self):
        case = self.case()
        case.status = CaseStatus.WAITING_STYLE_FEEDBACK
        assessment = self.complaint(case, False)
        self.workflow.advance = AsyncMock(side_effect=lambda value: value)
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=assessment)):
            asyncio.run(self.workflow.submit_style_feedback(case, CustomerStyleFeedback(complaint="不满意")))
        self.assertIsNone(case.feedback_assessment.complaint_reasonable)
        self.assertEqual(company_lessons(), [])

    def test_legacy_case_preserves_archived_fields_and_gets_new_defaults(self):
        case = TravelCase.model_validate({"customer_request": "旧案", "pay_adjustments": [
            {"agent_name": "宋野", "amount_yuan": 300, "reason": "旧建议"}]})
        self.assertEqual(case.pay_adjustments[0].amount_yuan, 300)
        self.assertFalse(case.learning_loaded)
        self.assertEqual(case.learning_applications, [])
        self.assertEqual(TravelCase.model_validate_json(case.model_dump_json()), case)

    def test_old_review_entry_saves_evidence_without_claiming_delivery(self):
        case = self.case()
        case.status = CaseStatus.COMPLETED
        self.workflow.submit_outcome_feedback(case, OutcomeFeedback(overall_rating=5, novelty_rating=4,
            culture_rating=4, detail_rating=5, engagement_rating=5, feasibility_rating=4, comment="分组安排好"))
        self.assertIn("尚无完单证据", company_lessons()[0].action)
        self.assertEqual(company_lessons()[0].kind, "success")

    def test_owner_page_shows_evidence_and_no_salary_or_trust_widgets(self):
        from streamlit.testing.v1 import AppTest
        case = self.positive()
        with patch("src.agents.build_agent_team", return_value=SimpleNamespace()), patch("src.storage.load_case_from_disk", return_value=None):
            app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
            app.session_state["case"] = case
            app.sidebar.radio[0].set_value("公司老板").run()
            app.sidebar.radio(key="work_area").set_value("团建策划与交付").run()
            self.assertEqual(len(app.exception), 0)
            self.assertIn("反馈与改进", [tab.label for tab in app.tabs])
            self.assertNotIn("信任分", [metric.label for metric in app.metric])
            self.assertFalse(any("贡献比例" in item.value.columns for item in app.dataframe))
            self.assertTrue(any("经验候选" in item.value for item in app.markdown))
            self.assertFalse(any("累计模拟浮动薪酬" in item.value for item in app.caption))


if __name__ == "__main__":
    unittest.main()
