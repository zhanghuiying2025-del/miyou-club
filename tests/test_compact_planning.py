import asyncio
import unittest
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.models import (BriefCreativeRound, ProposalBrief, FeasibilityReview, FeasibilityFinding,
    ManagedPlan, ManagerReview, ReviewResult, RequirementSpec, CaseStatus, CustomerStyleFeedback,
    CouncilDecision, TravelCase, OrganizationState, ReviewFinding)
from src.workflow import TravelAgencyWorkflow
from src.compact_planning import plan_fingerprint, input_fingerprint


def checks():
    return ManagerReview(operations=ReviewResult(reviewer_name="运营", passed=True),
                         risk=ReviewResult(reviewer_name="风险", passed=True))


def draft():
    return CouncilDecision(theme="轻松交流", summary="轻量活动并保留休息", schedule=["09:00室内共创", "12:00午餐休息"],
        merged_ideas=["自愿参与"], rationale=["符合低强度要求"])


def proposals():
    return BriefCreativeRound(**{key: ProposalBrief(concept_name=name, one_sentence_pitch="轻松交流",
        recommended_activities=[activity], reasons=["低强度"], style_strengths=["易参与"], style_tradeoffs=["刺激性较低"])
        for key, name, activity in [("song_ye", "城市探索", "街区交流"),
            ("shen_zhixing", "文化慢游", "短讲体验"), ("xia_cheng", "轻量协作", "室内共创")]})


class CompactPlanningTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("src.workflow.load_organization_state", return_value=OrganizationState())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.team = SimpleNamespace(manager="manager", planner="planner", fulfillment="fulfillment")
        self.workflow = TravelAgencyWorkflow(self.team)

    def case(self):
        case = self.workflow.create_case("虚构需求：南京30人轻松团建", planning_mode="single_agent_roles")
        case.requirements = RequirementSpec(city="南京", event_date="2026-11-15", participant_count=30,
            budget_yuan=9000, hard_constraints=["低强度", "自愿参与"])
        case.status = CaseStatus.WAITING_REQUIREMENTS
        self.workflow.confirm_requirements(case)
        return case

    async def reach_draft(self, case, feasibility=None, managed=None):
        await self.workflow.advance(case)
        await self.workflow.submit_style_feedback(case, CustomerStyleFeedback(preferred_agent="宋野"))
        self.assertEqual(case.status, CaseStatus.WAITING_DRAFT)

    def test_ordinary_flow_is_exactly_three_real_roles_and_program_gate(self):
        case = self.case()
        model = AsyncMock(side_effect=[proposals(), FeasibilityReview(summary="无阻塞，场地待确认"),
            ManagedPlan(plan=draft(), checks=checks(), resolutions=["采用轻量共创，减少转场"])])
        async def exercise():
            with patch("src.workflow.run_structured", new=model):
                await self.reach_draft(case)
                await self.workflow.submit_draft_feedback(case, [])
                await self.workflow._run_reviews(case)
        asyncio.run(exercise())
        self.assertEqual(model.await_count, 3)
        self.assertEqual([c.args[0] for c in model.call_args_list], ["planner", "fulfillment", "manager"])
        self.assertEqual([c.args[2] for c in model.call_args_list], [BriefCreativeRound, FeasibilityReview, ManagedPlan])
        self.assertEqual(case.status, CaseStatus.READY_FOR_CUSTOMER)
        self.assertEqual(case.peer_reviews, [])
        self.assertEqual(case.creative_opinions[0].agent_name, "宋野")
        self.assertTrue(any(r.reviewer_name == "程序硬性检查" for r in case.reviews))
        self.assertEqual(len(case.handoffs), 3)
        self.assertEqual(case.handoffs[2].sender, "履约执行")

    def reviewed_case(self, finding=None):
        case = self.case()
        case.feasibility_review = FeasibilityReview(summary="检查", findings=[finding] if finding else [])
        case.feasibility_fingerprint = input_fingerprint(case)
        case.feasibility_cached_at = time.time()
        case.council_decision = draft()
        case.manager_checks = checks()
        case.manager_check_fingerprint = plan_fingerprint(case)
        case.manager_checks_cached_at = time.time()
        case.status = CaseStatus.REVIEWING
        return case

    def test_changed_people_invalidates_check_and_reuses_new_result(self):
        case = self.reviewed_case()
        case.requirements.participant_count = 80
        model = AsyncMock(return_value=checks())
        async def exercise():
            with patch("src.workflow.run_structured", new=model):
                await self.workflow._review_and_revise(case)
                await self.workflow._run_reviews(case)
        asyncio.run(exercise())
        model.assert_awaited_once()
        self.assertEqual(model.call_args.args[0], "fulfillment")
        self.assertEqual(case.status, CaseStatus.READY_FOR_CUSTOMER)

    def test_plan_budget_constraints_and_version_change_fingerprint(self):
        for change in (lambda c: c.council_decision.schedule.append("新增活动"),
                       lambda c: setattr(c.requirements, "budget_yuan", 100),
                       lambda c: c.applied_constraints.append("不能长时间步行"),
                       lambda c: setattr(c, "plan_version", c.plan_version + 1)):
            case = self.reviewed_case()
            before = plan_fingerprint(case)
            change(case)
            self.assertNotEqual(before, plan_fingerprint(case))

    def test_executor_owner_gate_cannot_be_removed_by_manager(self):
        case = self.reviewed_case(FeasibilityFinding(severity="high", description="明确危险活动",
            evidence="提案安排高风险活动", release_condition="老板处理风险", decision_owner="老板", can_auto_fix=False))
        model = AsyncMock()
        with patch("src.workflow.run_structured", new=model):
            asyncio.run(self.workflow._review_and_revise(case))
        model.assert_not_awaited()
        self.assertEqual(case.status, CaseStatus.WAITING_OWNER)

    def test_executor_customer_gate_cannot_be_replaced_by_owner(self):
        case = self.reviewed_case(FeasibilityFinding(severity="medium", description="需要改变客户已确认条件",
            evidence="客户要求全员参与，提案只允许部分参加", release_condition="客户选择", decision_owner="客户"))
        with patch("src.workflow.run_structured", new=AsyncMock()) as model:
            asyncio.run(self.workflow._review_and_revise(case))
        model.assert_not_awaited()
        self.assertEqual(case.status, CaseStatus.WAITING_CUSTOMER)

    def test_claimed_repair_needs_executor_check_of_final_plan(self):
        case = self.reviewed_case(FeasibilityFinding(severity="medium", description="活动时间冲突",
            evidence="两项活动重叠", release_condition="调整时间", can_auto_fix=True, blocking=True))
        case.final_recheck_required = True
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=checks())) as model:
            asyncio.run(self.workflow._review_and_revise(case))
        model.assert_awaited_once()
        self.assertEqual(model.call_args.args[0], "fulfillment")
        self.assertEqual(case.status, CaseStatus.READY_FOR_CUSTOMER)

    def test_cached_manager_cannot_override_empty_schedule_program_check(self):
        case = self.reviewed_case()
        case.council_decision.schedule = []
        case.manager_check_fingerprint = plan_fingerprint(case)
        with patch("src.workflow.run_structured", new=AsyncMock()) as model:
            asyncio.run(self.workflow._review_and_revise(case))
        model.assert_not_awaited()
        self.assertEqual(case.status, CaseStatus.WAITING_OWNER)

    def test_failed_executor_resume_does_not_regenerate_proposals(self):
        from src.agents import AgentOutputError
        case = self.case()
        async def exercise():
            with patch("src.workflow.run_structured", new=AsyncMock(return_value=proposals())):
                await self.workflow.advance(case)
            with patch("src.workflow.run_structured", new=AsyncMock(side_effect=AgentOutputError("临时失败"))):
                await self.workflow.submit_style_feedback(case, CustomerStyleFeedback(preferred_agent="宋野"))
            self.assertEqual(case.resume_status, CaseStatus.CROSS_REVIEW)
            restored = TravelCase.model_validate_json(case.model_dump_json())
            with patch("src.workflow.run_structured", new=AsyncMock(side_effect=[FeasibilityReview(summary="通过"),
                ManagedPlan(plan=draft(), checks=checks())])) as retry:
                await self.workflow.resume_case(restored)
            self.assertEqual([c.args[0] for c in retry.call_args_list], ["fulfillment", "manager"])
            self.assertEqual(restored.status, CaseStatus.WAITING_DRAFT)
        asyncio.run(exercise())

    def test_legacy_case_keeps_legacy_protocol_and_new_fields_default(self):
        old = TravelCase.model_validate({"customer_request": "旧案件"})
        self.assertEqual(old.planning_protocol, "legacy")
        self.assertIsNone(old.feasibility_review)
        self.assertEqual(self.case().planning_protocol, "compact_v1")

    def test_successful_checks_survive_json_reload_without_another_call(self):
        case = TravelCase.model_validate_json(self.reviewed_case().model_dump_json())
        with patch("src.workflow.run_structured", new=AsyncMock()) as model:
            asyncio.run(self.workflow._review_and_revise(case))
        model.assert_not_awaited()
        self.assertEqual(case.status, CaseStatus.READY_FOR_CUSTOMER)

    def test_expired_checks_are_revalidated(self):
        case = self.reviewed_case()
        case.manager_checks_cached_at = time.time() - 901
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=checks())) as model:
            asyncio.run(self.workflow._run_reviews(case))
        model.assert_awaited_once()

    def test_policy_change_invalidates_fast_check_cache(self):
        case = self.reviewed_case()
        self.workflow.policy.max_revision_count += 1
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=checks())) as model:
            asyncio.run(self.workflow._run_reviews(case))
        model.assert_awaited_once()

    def test_model_and_history_changes_invalidate_fast_check_cache(self):
        import os
        from src.compact_planning import cached_reviews
        case = self.reviewed_case()
        with patch.dict(os.environ, {"LLM_MODEL": "new-model-for-test"}):
            self.assertIsNone(cached_reviews(case))
        case.history_references.append({"id": "changed-reference", "text": "new facts"})
        self.assertIsNone(cached_reviews(case))

    def test_manager_retry_reuses_saved_executor_result(self):
        from src.agents import AgentOutputError
        case = self.case()
        async def exercise():
            with patch("src.workflow.run_structured", new=AsyncMock(return_value=proposals())):
                await self.workflow.advance(case)
            with patch("src.workflow.run_structured", new=AsyncMock(side_effect=[FeasibilityReview(summary="通过"),
                    AgentOutputError("经理暂时失败")])):
                await self.workflow.submit_style_feedback(case, CustomerStyleFeedback(preferred_agent="宋野"))
            self.assertEqual(case.resume_status, CaseStatus.DELIBERATION)
            restored = TravelCase.model_validate_json(case.model_dump_json())
            with patch("src.workflow.run_structured", new=AsyncMock(return_value=ManagedPlan(plan=draft(),checks=checks()))) as model:
                await self.workflow.resume_case(restored)
            model.assert_awaited_once()
            self.assertEqual(model.call_args.args[0], "manager")
        asyncio.run(exercise())

    def test_program_source_cannot_be_used_to_select_resources(self):
        from src.models import BookingSpeech
        from src.procurement import start_procurement, advance_supplier, finish_turn
        case = TravelCase(customer_request="虚构", status=CaseStatus.COMPLETED, council_decision=draft())
        order = start_procurement(case, 9000, 1, 2)
        for _ in range(3):
            advance_supplier(order, now=order.last_step_at+2)
        with self.assertRaises(ValueError):
            finish_turn(order, 3, BookingSpeech(message="冒用程序", package_id="far"), "rule")
        self.assertEqual(order.step, 3)

    def test_compact_owner_page_shows_executor_evidence(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        case = self.reviewed_case(FeasibilityFinding(severity="low", description="待核实容量",
            evidence="尚无容量回执", release_condition="执行前核实", blocking=False))
        case.creative_opinions = []
        case.status = CaseStatus.WAITING_DRAFT
        # Use the actual proposal conversion through the planner path.
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=proposals())):
            asyncio.run(self.workflow._run_creative_council(case))
        with patch("src.agents.build_agent_team", return_value=self.team), patch("src.storage.load_case_from_disk", return_value=None):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
            app.session_state["case"] = case
            app.sidebar.radio[0].set_value("公司老板").run()
            app.sidebar.radio(key="work_area").set_value("团建策划与交付").run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("履约执行检查" in item.value for item in app.subheader))
