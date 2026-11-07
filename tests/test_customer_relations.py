import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.customer_relations import record_customer_objection
from src.models import CustomerConcern, CreativeOpinion, CreativeRound, OrganizationState, PersonalityState, RequirementSpec, TravelCase
from src.workflow import TravelAgencyWorkflow


class CustomerRelationsTests(unittest.TestCase):
    def case(self):
        return TravelCase(customer_request="要求全员低强度，又要求所有人参加高强度竞技",
            personality_snapshots={"宋野": PersonalityState()}, requirements=RequirementSpec(city="南京"))

    def concern(self):
        return CustomerConcern(kind="requirement_conflict", evidence=["全员低强度", "所有人参加高强度竞技"],
            alternatives=["保留低强度，竞技自愿", "调整强度要求，确认参加意愿"])

    def record(self, case, event="initial", concern=None):
        return record_customer_objection(case, "宋野", concern or self.concern(), event,
            "初始需求", case.customer_request, [], "测试中的真实角色输出")

    def test_direct_response_has_evidence_and_retry_does_not_accumulate(self):
        case = self.case()
        first = self.record(case)
        again = self.record(case)
        self.assertEqual(first, again)
        self.assertEqual(case.customer_frustration["宋野"], 25)
        self.assertEqual(len(case.customer_objections), 1)
        self.assertIn("你提出了", first.message)
        self.assertIn("全员低强度", first.message)
        self.assertIn("我", first.message)
        second = self.record(case, "draft:1")
        self.assertEqual(case.customer_frustration["宋野"], 50)
        self.assertGreater(second.tone.probability, first.tone.probability)

    def test_preference_or_fabricated_evidence_does_not_raise_frustration(self):
        case = self.case()
        self.assertIsNone(self.record(case, concern=CustomerConcern(kind="preference", evidence=["全员低强度", "所有人参加高强度竞技"], alternatives=["城市", "户外"])))
        invalid = self.concern().model_copy(update={"evidence": ["全员低强度", "客户恶意不停刁难员工"]})
        self.assertIsNone(self.record(case, concern=invalid))
        self.assertEqual(case.customer_frustration, {})

    def test_previous_customer_mood_is_not_assigned_to_current_customer(self):
        case = self.case()
        fresh = self.record(case)
        another = self.case()
        another.id = case.id
        another.personality_snapshots["宋野"].frustration = 100
        result = self.record(another)
        self.assertEqual(fresh.tone.probability, result.tone.probability)

    def test_repeated_revision_is_subjective_not_a_conflict_verdict(self):
        case = self.case()
        case.draft_change_requests = ["请增加城市游玩", "请改成文化参观"]
        concern = CustomerConcern(kind="repeated_revision", evidence=case.draft_change_requests,
            alternatives=["保持城市方向", "固定文化方向"])
        result = record_customer_objection(case, "宋野", concern, "draft:2", "草案调整",
            "请改成文化参观", [], "角色回应模板")
        self.assertIsNotNone(result)
        self.assertNotIn("两项要求之间有个矛盾", result.message)
        self.assertEqual(case.customer_frustration["宋野"], 15)
        first_change = self.case()
        first_change.draft_change_requests = ["请增加城市游玩"]
        self.assertIsNone(record_customer_objection(first_change, "宋野", concern, "draft:1", "调整", "请增加城市游玩", [], "测试"))

    def test_proposal_publishes_customer_response_under_actual_speaker(self):
        async def exercise():
            workflow = TravelAgencyWorkflow(SimpleNamespace(planner=1))
            workflow.organization = OrganizationState()
            case = self.case()
            events = []
            workflow.public_update_callback = events.append

            async def model(agent, prompt, response_model):
                opinion = CreativeOpinion(agent_name="模型写错", persona_title="策划师", concept_name="团建",
                    one_sentence_pitch="测试", recommended_activities=["轻松活动"], reasons=["匹配需求"], confidence=0.8,
                    customer_concerns=[])
                song = opinion.model_copy(deep=True)
                song.customer_concerns = [self.concern()]
                return CreativeRound(song_ye=song, shen_zhixing=opinion.model_copy(deep=True),
                                     xia_cheng=opinion.model_copy(deep=True))

            with patch("src.workflow.run_structured", side_effect=model):
                await workflow._run_creative_council(case)
            first = next(x for x in events if x["actor"] == "宋野")
            self.assertIn("直接回应客户", first["stage"])
            self.assertIn("你提出了", first["message"])
            self.assertTrue(case.creative_opinions[0].customer_response)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
