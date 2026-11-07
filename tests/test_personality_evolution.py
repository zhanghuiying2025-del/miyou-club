"""Personas stay recognizable while feedback is handled by the learning module."""
import unittest

from src.models import PersonalityChange
from src.personality_evolution import initial_persona_states, persona_instructions


class StablePersonaTests(unittest.TestCase):
    def test_profiles_remain_distinct(self):
        states = initial_persona_states()
        self.assertEqual(len(states), 3)
        self.assertIn("叛逆", persona_instructions("宋野"))
        self.assertIn("自省", persona_instructions("沈知行"))
        self.assertIn("自愿", persona_instructions("夏橙"))

    def test_new_order_starts_with_fresh_persona_state(self):
        previous = initial_persona_states()["宋野"]
        previous.frustration = 100
        previous.history.append(PersonalityChange(event_id="old", signal="reward", reason="旧奖励",
                                                   before={}, after={}, response="旧记录"))
        frozen = initial_persona_states()["宋野"]
        self.assertEqual(frozen.frustration, 0)
        self.assertEqual(frozen.style_intensity, 85)
        self.assertEqual(frozen.history, [])
        self.assertEqual(previous.history[0].event_id, "old")


if __name__ == "__main__":
    unittest.main()
