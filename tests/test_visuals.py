import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from src.models import CreativeOpinion, CreativeRound, OrganizationState, RequirementSpec, TravelCase
from src.visuals import BUNDLED_FONT_PATHS, font, proposal_poster, itinerary_diagram, collaboration_diagram


class VisualTests(unittest.TestCase):
    def test_pillow_uses_bundled_chinese_fonts(self):
        for bold in (False, True):
            face = font(24, bold=bold)
            self.assertEqual(Path(face.path).resolve(), BUNDLED_FONT_PATHS[bold].resolve())
            self.assertNotEqual(bytes(face.getmask("中")), bytes(face.getmask("\U000F0000")))

    def test_posters_support_long_chinese_and_are_real_pngs(self):
        for name in ("宋野", "沈知行", "夏橙"):
            data = proposal_poster(name, "南京探索" * 20, "轻松共创" * 50,
                                   ("城市寻宝" * 30, "午间休息", "团队共创"),
                                   "全员参与" * 20, "需要控制步行" * 20, "南京 · 30人 · 1天")
            with Image.open(BytesIO(data)) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (780, 1120))

    def test_diagrams_handle_empty_and_full_schedule(self):
        for schedule in ((), tuple(f"第{i}站：休息与交流" for i in range(12))):
            with Image.open(BytesIO(itinerary_diagram("团建", schedule))) as image:
                self.assertGreater(image.height, 200)
        with Image.open(BytesIO(collaboration_diagram(2))) as image:
            self.assertEqual(image.size, (1300, 290))

    def test_one_round_publishes_three_roles_without_fake_finish_order(self):
        from src.workflow import TravelAgencyWorkflow

        async def exercise():
            team = SimpleNamespace(planner="planner")
            workflow = TravelAgencyWorkflow(team)
            workflow.organization = OrganizationState()
            case = TravelCase(customer_request="南京团建", requirements=RequirementSpec(city="南京"))
            seen = []

            def receive(event):
                if 'opinion' in event:
                    seen.append(event)
                    self.assertEqual(len(case.creative_opinions), 3)

            workflow.public_update_callback = receive

            async def model(agent, prompt, response_model):
                self.assertEqual(agent, "planner")
                self.assertIs(response_model, CreativeRound)
                opinion = CreativeOpinion(agent_name="模型可能写错的人名", persona_title="策划师",
                                       concept_name="轻松探索", one_sentence_pitch="减少赶场",
                                       recommended_activities=["城市探索"], reasons=["适合团队"], confidence=0.8)
                return CreativeRound(song_ye=opinion.model_copy(deep=True),
                    shen_zhixing=opinion.model_copy(deep=True), xia_cheng=opinion.model_copy(deep=True))

            with patch("src.workflow.run_structured", side_effect=model) as generate:
                await asyncio.wait_for(workflow._run_creative_council(case), timeout=5)
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(case.proposal_completion_order, [])
            self.assertEqual(case.planning_mode, "single_agent_roles")
            self.assertEqual(len(seen), 3)
            self.assertEqual(seen[0]["opinion"]["agent_name"], "宋野")
            self.assertEqual(seen[0]["stage"], "提案发言")
            self.assertTrue(seen[0]["message"].startswith("我的提案"))
            restored = TravelCase.model_validate_json(case.model_dump_json())
            self.assertEqual(len([x for x in restored.public_updates if x.source != 'workflow_status']), 3)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
