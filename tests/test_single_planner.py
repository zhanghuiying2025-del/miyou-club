import asyncio
import os
import unittest
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from src.agents import AgentOutputError, AgentTeam, build_agent_team
from src.models import (
    CaseStatus, CreativeOpinion, CreativeRound, OrganizationState,
    PeerChallenge, PeerReview, PeerReviewRound, TravelCase,
)
from src.personalities import build_planning_instructions
from src.workflow import TravelAgencyWorkflow


def opinion(name):
    return CreativeOpinion(agent_name=name, persona_title="策划师", concept_name=name,
        one_sentence_pitch="我建议轻松探索", recommended_activities=["城市探索"],
        reasons=["控制步行"], confidence=0.8)


class SinglePlannerTests(unittest.TestCase):
    def workflow(self):
        with patch("src.workflow.load_organization_state", return_value=OrganizationState()):
            return TravelAgencyWorkflow(SimpleNamespace(planner=object()))

    def test_builds_exactly_one_planner_without_fixed_profiles(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "test", "LLM_BASE_URL": "https://example.com", "LLM_MODEL": "test"}), patch(
            "src.agents.OpenAIChatCompletionClient"
        ), patch("src.agents.Agent") as factory:
            build_agent_team()
        planners = [c for c in factory.call_args_list if c.kwargs["name"] == "multi_role_planner"]
        self.assertEqual(len(planners), 1)
        self.assertNotIn("urban_explorer", {f.name for f in fields(AgentTeam)})
        instructions = planners[0].kwargs["instructions"]
        for name in ("宋野", "沈知行", "夏橙"):
            self.assertNotIn(name, instructions)
        self.assertIn("第一人称", instructions)

    def test_missing_role_rejected_and_old_case_still_loads(self):
        with self.assertRaises(ValidationError):
            CreativeRound(song_ye=opinion("宋野"), shen_zhixing=opinion("沈知行"))
        legacy = TravelCase.model_validate({"customer_request": "旧需求"})
        self.assertEqual(legacy.planning_mode, "legacy_independent")
        self.assertEqual(self.workflow().create_case("新需求").planning_mode, "style_branch")

    def test_peer_round_is_one_call_preserves_identity_and_legacy_proposals(self):
        workflow = self.workflow()
        case = TravelCase(customer_request="旧需求", creative_opinions=[
            opinion(n) for n in ("宋野", "沈知行", "夏橙")])
        def review():
            return PeerReview(reviewer_name="错误姓名", updated_position="我接受减少赶场",
                challenges=[PeerChallenge(target_agent="宋野", issue="太赶",
                    why_it_matters="体力限制", evidence_or_change_needed="减少地点", severity="low")])
        generated = PeerReviewRound(song_ye=review(), shen_zhixing=review(), xia_cheng=review())
        model = AsyncMock(return_value=generated)
        with patch("src.workflow.run_structured", new=model):
            asyncio.run(workflow._run_cross_review(case))
        model.assert_awaited_once()
        self.assertIs(model.call_args.args[0], workflow.team.planner)
        self.assertEqual([r.reviewer_name for r in case.peer_reviews], ["宋野", "沈知行", "夏橙"])
        self.assertEqual(case.peer_reviews[0].challenges[0].target_agent, "委员会")
        self.assertEqual(case.peer_reviews[1].challenges[0].target_agent, "宋野")
        self.assertEqual(case.status, CaseStatus.DELIBERATION)
        restored = TravelCase.model_validate_json(case.model_dump_json())
        self.assertEqual(restored.planning_mode, "single_agent_roles")
        self.assertEqual(len([x for x in restored.public_updates if x.source != 'workflow_status']), 3)

    def test_failed_peer_round_can_resume_without_partial_role_results(self):
        workflow = self.workflow()
        case = TravelCase(customer_request="测试", status=CaseStatus.CROSS_REVIEW,
            creative_opinions=[opinion(n) for n in ("宋野", "沈知行", "夏橙")])
        with patch("src.workflow.run_structured", new=AsyncMock(side_effect=AgentOutputError("模型返回不完整"))):
            asyncio.run(workflow.advance(case))
        self.assertEqual(case.status, CaseStatus.FAILED)
        self.assertEqual(case.resume_status, CaseStatus.CROSS_REVIEW)
        self.assertEqual(case.peer_reviews, [])
        self.assertTrue(all(x.source == 'workflow_status' for x in case.public_updates))
