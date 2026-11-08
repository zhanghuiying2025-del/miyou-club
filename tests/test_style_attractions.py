from datetime import date, timedelta
import unittest

from src.web_flow import Brief


class StyleAttractionTests(unittest.TestCase):
    def test_styles_and_attractions_reach_planning_requirements(self):
        brief = Brief(
            name="城市探索", people=20, city="北京", destination="北京", date=date.today() + timedelta(days=3),
            end_date=date.today() + timedelta(days=4), budget=500,
            goals=["城市游玩", "美食体验"], attractions=["故宫", "南锣鼓巷"],
        )
        req = brief.requirements()
        self.assertEqual(req.preferred_style, "城市游玩、美食体验")
        self.assertEqual(req.attractions, ["故宫", "南锣鼓巷"])
        self.assertTrue(any("偏好景点：故宫、南锣鼓巷" in item for item in req.preferences))


if __name__ == "__main__":
    unittest.main()
