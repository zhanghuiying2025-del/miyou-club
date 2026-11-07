import unittest

from src.discussion import proposal_speech, peer_speech
from src.models import CreativeOpinion, PeerReview, PeerChallenge


class DiscussionTests(unittest.TestCase):
    def opinion(self, message=""):
        return CreativeOpinion(agent_name="宋野", persona_title="城市策划", concept_name="城市探索",
            one_sentence_pitch="减少赶场", recommended_activities=["街区探索"], reasons=["适合团队"],
            confidence=0.8, discussion_message=message)

    def test_generated_first_person_speech_is_preserved(self):
        message = "我想保留街区探索，不想把这次团建做成赶景点。"
        self.assertEqual(proposal_speech(self.opinion(message)), message)

    def test_missing_or_third_person_speech_falls_back_to_actual_proposal(self):
        for message in ("", "宋野认为应该增加城市探索"):
            result = proposal_speech(self.opinion(message))
            self.assertTrue(result.startswith("我的提案"))
            self.assertIn("街区探索", result)
            self.assertNotIn("已经同意", result)

    def test_peer_speech_addresses_the_named_colleague(self):
        review = PeerReview(reviewer_name="沈知行", updated_position="缩短步行",
            challenges=[PeerChallenge(target_agent="宋野", issue="连续步行时间过长", why_it_matters="体力差异",
                evidence_or_change_needed="增加休息", severity="medium")],
            proposed_compromises=["保留探索，增加休息"])
        result = peer_speech(review)
        self.assertTrue(result.startswith("宋野，我"))
        self.assertIn("我建议", result)
        self.assertIn("增加休息", result)
        self.assertNotIn("沈知行认为", result)


if __name__ == "__main__":
    unittest.main()
