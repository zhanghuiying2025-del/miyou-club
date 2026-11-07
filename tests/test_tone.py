import unittest

from src.models import PersonalityState, CreativeOpinion
from src.tone import decide_tone, apply_tone_constraints
from src.discussion import proposal_speech


class ToneTests(unittest.TestCase):
    def test_initial_state_is_not_randomly_dissatisfied(self):
        decision = decide_tone("case-1", "宋野", PersonalityState())
        self.assertFalse(decision.triggered)
        self.assertEqual(decision.probability, 0)
        self.assertEqual(decision.remark, "")

    def test_persistent_frustration_and_correction_change_probability(self):
        rebellious = PersonalityState(frustration=70)
        reflective = PersonalityState(frustration=70, self_correction=80)
        song = decide_tone("same-case", "宋野", rebellious)
        shen = decide_tone("same-case", "沈知行", rebellious)
        restrained = decide_tone("same-case", "宋野", reflective)
        self.assertGreater(song.probability, shen.probability)
        self.assertGreater(song.probability, restrained.probability)
        self.assertEqual(song, decide_tone("same-case", "宋野", rebellious))

    def test_triggered_line_survives_short_discussion_and_constraints_override_it(self):
        state = PersonalityState(frustration=90)
        decision = next(decide_tone(f"case-{i}", "宋野", state) for i in range(100)
                        if decide_tone(f"case-{i}", "宋野", state).triggered)
        opinion = CreativeOpinion(agent_name="宋野", persona_title="策划师", concept_name="城市探索",
            one_sentence_pitch="有记忆点", recommended_activities=["街区探索"], reasons=["符合目标"],
            confidence=0.8, discussion_message="我建议上午探索街区，下午留出自由活动。",
            sharp_remark=decision.remark)
        self.assertIn(decision.remark, proposal_speech(opinion))
        blocked = apply_tone_constraints(decision, ["以后面向客户的措辞必须温和克制"])
        self.assertFalse(blocked.triggered)
        self.assertEqual(blocked.remark, "")
        self.assertIn("约束屏蔽", blocked.reason)


if __name__ == "__main__":
    unittest.main()
