import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel, ValidationError
from src.agents import _prompt, run_structured, response_prefix, AgentOutputError
from src.models import (CaseStatus, CouncilDecision, ManagedPlan, ManagerReview, ReviewResult,
    RequirementSpec, OrganizationState, JourneyResponse, TravelCase)
from src.policy import owner_approval_reasons
from src.result_cache import cache_key, get_result, store_result, CACHE_TTL
from src.stage_schemas import ProjectDraft, ProjectReview, ProjectPlan, decode_response, compact_schema
from src.workflow import TravelAgencyWorkflow


def finish(coro):
    try:
        coro.send(None)
    except StopIteration as result:
        return result.value
    coro.close()
    raise AssertionError("Unexpected external IO")


def review():
    return ManagerReview(operations=ReviewResult(reviewer_name="运营", passed=True),
        risk=ReviewResult(reviewer_name="风险", passed=True), completion_conditions=["本轮已核对"])


def plan_payload():
    return {"theme": "轻量活动", "summary": "保留休息", "schedule": ["09:00轻量共创", "12:00午餐"],
        "basis": ["符合低强度要求"], "completion_conditions": ["完整方案已形成，资源尚待核实"],
        "pending_resources": ["场地容量"], "customer_questions": ["确认餐饮偏好"]}


class CacheStageTests(unittest.TestCase):
    def test_structured_reply_uses_unique_valid_object_and_rejects_conflicts(self):
        payload = json.dumps(plan_payload(), ensure_ascii=False)
        agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
            text=f"方案如下：\n```json\n{payload}\n```\n{{\"note\": \"已核对\"}}")))
        result = asyncio.run(run_structured(agent, "生成方案", CouncilDecision))
        self.assertEqual(result.theme, "轻量活动")
        other = json.dumps(dict(plan_payload(), theme="另一份方案"), ensure_ascii=False)
        agent.run.return_value.text = f"{payload}\n{other}"
        with self.assertRaisesRegex(AgentOutputError, "多个不同的有效结果"):
            asyncio.run(run_structured(agent, "生成方案", CouncilDecision))

    def setUp(self):
        for name, value in [("src.workflow.load_organization_state", OrganizationState()),
            ("src.workflow.TravelAgencyWorkflow._load_history", None),
            ("src.workflow.TravelAgencyWorkflow._load_learning", None)]:
            p = patch(name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.workflow = TravelAgencyWorkflow(SimpleNamespace(manager="manager", planner="planner", fulfillment="executor"))
        self.case = self.workflow.create_case("南京30人低强度")
        self.case.status = CaseStatus.REVIEWING
        self.case.requirements = RequirementSpec(city="南京", participant_count=30, budget_yuan=9000)

    def call(self, prompt="核对当前方案", schema=ManagerReview):
        return finish(self.workflow._call(self.case, "manager", prompt, schema))

    def test_identical_retry_and_json_reload_skip_model_with_explicit_cache_record(self):
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=review())) as model:
            first = self.call()
            self.case = TravelCase.model_validate_json(self.case.model_dump_json())
            second = self.call()
        self.assertEqual(first, second)
        self.assertEqual(model.await_count, 1)
        self.assertEqual(self.case.handoffs[-1].source, "validated_cache")
        self.assertEqual(self.case.handoffs[-1].elapsed_seconds, 0)
        self.assertEqual(len(self.case.result_cache), 1)

    def test_budget_rule_version_model_instruction_and_stage_changes_miss(self):
        mutations = [lambda c: setattr(c.requirements, "budget_yuan", 8000),
            lambda c: c.applied_constraints.append("自愿参与"),
            lambda c: setattr(c, "plan_version", 2),
            lambda c: setattr(c, "status", CaseStatus.DELIBERATION)]
        for mutate in mutations:
            self.case.result_cache = []
            with patch("src.workflow.run_structured", new=AsyncMock(return_value=review())) as model:
                self.call()
                mutate(self.case)
                self.call()
                self.assertEqual(model.await_count, 2)
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=review())) as model:
            self.call("新任务")
            self.workflow.policy.max_revision_count += 1
            self.call("新任务")
            with patch.dict(os.environ, {"LLM_MODEL": "different-test-model"}):
                self.call("新任务")
            with patch("src.workflow.role_instructions", return_value="new instructions"):
                self.call("新任务")
            self.assertEqual(model.await_count, 4)

    def test_expired_corrupt_and_failed_results_are_not_reused(self):
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=review())) as model:
            self.call()
            self.case.result_cache[0].expires_at = 0
            self.call()
            self.case.result_cache[-1].payload = {"broken": True}
            self.call()
            self.assertEqual(model.await_count, 3)
        self.case.result_cache = []
        with patch("src.workflow.run_structured", new=AsyncMock(side_effect=RuntimeError("offline"))):
            with self.assertRaises(RuntimeError):
                self.call()
        self.assertEqual(self.case.result_cache, [])

    def test_cache_is_per_case_bounded_and_returns_detached_objects(self):
        value = review()
        for number in range(30):
            store_result(self.case, str(number), value, now=100)
        self.assertEqual(len(self.case.result_cache), 24)
        found = get_result(self.case, "29", ManagerReview, now=101)
        found.operations.passed = False
        self.assertTrue(get_result(self.case, "29", ManagerReview, now=101).operations.passed)
        self.assertIsNone(get_result(self.case, "29", ManagerReview, now=100 + CACHE_TTL))
        other = self.workflow.create_case("另一单")
        self.assertIsNone(get_result(other, "29", ManagerReview, now=101))

    def test_external_actions_and_empty_plan_are_not_cached(self):
        self.assertIsNone(cache_key(self.case, "manager", "现场", JourneyResponse, self.workflow.policy, "rules"))
        empty = CouncilDecision(theme="空", summary="空", schedule=[], merged_ideas=[], rationale=[])
        store_result(self.case, "bad", empty)
        self.assertEqual(self.case.result_cache, [])

    def test_prefix_is_stable_and_schema_precedes_dynamic_data(self):
        first = _prompt("订单甲资料", ManagedPlan)
        second = _prompt("订单乙资料", ManagedPlan)
        self.assertEqual(first.split("<task_data>")[0], second.split("<task_data>")[0])
        self.assertLess(first.index("JSON Schema"), first.index("订单甲资料"))
        self.assertNotIn("订单甲资料", response_prefix(ManagedPlan))
        self.assertLess(len(json.dumps(compact_schema(ProjectDraft.model_json_schema()), ensure_ascii=False)),
                        len(json.dumps(ManagedPlan.model_json_schema(), ensure_ascii=False)))

    def test_schema_compaction_preserves_properties_named_title_and_default(self):
        class NamedFields(BaseModel):
            title: str
            default: int
        schema = compact_schema(NamedFields.model_json_schema())
        self.assertEqual(set(schema["properties"]), {"title", "default"})
        self.assertEqual(schema["required"], ["title", "default"])

    def test_draft_adapter_preserves_authority_evidence_conditions_and_pending_items(self):
        payload = {"plan": plan_payload(), "findings": [{"domain": "risk", "severity": "high",
            "description": "活动条件未满足", "evidence": "原方案含高风险活动", "release_condition": "老板核查风险",
            "decision_owner": "owner", "can_auto_fix": False, "blocking": False}]}
        value = decode_response(ManagedPlan, payload)
        self.assertFalse(value.checks.risk.passed)
        self.assertTrue(owner_approval_reasons([value.checks.risk], self.workflow.policy))
        self.assertEqual(value.checks.risk.findings[0].evidence, "原方案含高风险活动")
        self.assertEqual(value.checks.risk.findings[0].release_condition, "老板核查风险")
        self.assertEqual(value.plan.items_to_verify, ["场地容量"])
        self.assertEqual(value.plan.unresolved_customer_decisions, ["确认餐饮偏好"])
        self.assertEqual(value.resolutions, value.plan.rationale)
        self.assertTrue(value.plan.completion_conditions)

    def test_check_only_contract_cannot_return_a_new_schedule(self):
        with self.assertRaises(ValidationError):
            ProjectReview.model_validate({"findings": [], "completion_conditions": ["核查完成"], "schedule": ["越权"]})
        value = decode_response(ManagerReview, {"findings": [], "completion_conditions": ["核查完成"]})
        self.assertTrue(value.operations.passed and value.risk.passed)

    def test_new_draft_requires_evidence_and_completion_conditions(self):
        payload = {"plan": plan_payload(), "findings": [{"domain": "operations", "severity": "medium",
            "description": "时间冲突", "release_condition": "调整时间"}]}
        with self.assertRaises(ValidationError):
            decode_response(ManagedPlan, payload)
        payload = plan_payload()
        payload.pop("completion_conditions")
        with self.assertRaises(ValidationError):
            decode_response(CouncilDecision, payload)

    def test_old_valid_results_remain_readable_without_a_repair_call(self):
        plan = CouncilDecision(theme="旧", summary="旧", schedule=["活动"], merged_ideas=[], rationale=["适合"])
        for value in [plan, review(), ManagedPlan(plan=plan, checks=review())]:
            self.assertEqual(decode_response(type(value), value.model_dump(mode="json")), value)

    def test_actual_model_call_uses_stage_schema_and_decodes_once(self):
        fake = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(text=json.dumps({"plan": plan_payload(), "findings": []}))))
        result = asyncio.run(run_structured(fake, "业务资料", ManagedPlan))
        self.assertIsInstance(result, ManagedPlan)
        self.assertEqual(fake.run.await_count, 1)
        self.assertIn('"basis"', fake.run.call_args.args[0])
        self.assertNotIn('"resolutions"', fake.run.call_args.args[0])

    def test_cached_model_check_does_not_approve_empty_plan(self):
        self.case.council_decision = CouncilDecision(theme="空", summary="空", schedule=[], merged_ideas=[], rationale=[])
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=review())) as model:
            self.call()
            self.call()
            result = finish(self.workflow._run_reviews(self.case))
        self.assertFalse(result[-1].passed)
        self.assertTrue(any(f.description == "程序检查：缺少可执行行程" for f in result[-1].findings))


if __name__ == "__main__":
    unittest.main()
