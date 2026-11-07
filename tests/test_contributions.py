import unittest

from src.contributions import adoption_evidence
from src.models import (
    AdoptedContribution, CouncilDecision, CreativeOpinion, TravelCase,
)


def sample_case():
    case = TravelCase(customer_request="测试")
    case.creative_opinions = [CreativeOpinion(
        agent_name=name, persona_title="策划师", concept_name="方案", one_sentence_pitch="提案",
        recommended_activities=[activity], reasons=["适合"], confidence=0.8,
    ) for name, activity in [("宋野", "城市探索"), ("沈知行", "博物馆"), ("夏橙", "公园")]]
    case.council_decision = CouncilDecision(theme="组合", summary="组合", schedule=["城市探索", "博物馆"], merged_ideas=[], rationale=[])
    return case


class ContributionTests(unittest.TestCase):
    def test_only_valid_and_distinct_adoptions_are_shown(self):
        case = sample_case()
        valid = AdoptedContribution(agent_name="宋野", source_index=0, schedule_index=0, reason="纳入上午")
        case.council_decision.adopted_contributions = [valid, valid.model_copy(),
            AdoptedContribution(agent_name="夏橙", source_index=99, schedule_index=0, reason="伪造来源"),
            AdoptedContribution(agent_name="沈知行", source_index=0, schedule_index=99, reason="不存在的行程")]
        self.assertEqual(adoption_evidence(case), [{"来源角色": "宋野", "原建议": "城市探索",
            "对应行程": "城市探索", "采纳原因": "纳入上午"}])

    def test_completion_order_does_not_change_evidence(self):
        case = sample_case()
        case.council_decision.adopted_contributions = [AdoptedContribution(
            agent_name="宋野", source_index=0, schedule_index=0, reason="纳入上午")]
        first = adoption_evidence(case)
        case.proposal_completion_order = ["夏橙", "宋野", "沈知行"]
        self.assertEqual(first, adoption_evidence(case))


if __name__ == "__main__":
    unittest.main()
