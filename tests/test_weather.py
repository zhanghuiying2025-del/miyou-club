from datetime import date, timedelta
import unittest
from unittest.mock import patch

from src import weather
from src.models import WeatherForecast
from src.web_api import resolved_event_end_date
from src.web_flow import Brief, customer_actions
from src.models import CaseStatus, CouncilDecision, RequirementSpec, TravelCase, WeatherForecastDay
from src.workflow import weather_plan_context


class WeatherTests(unittest.TestCase):
    def test_fetch_weather_builds_planning_advice(self):
        day = date.today() + timedelta(days=3)
        payloads = [
            {"results": [{"latitude": 31.2, "longitude": 121.5, "name": "上海", "admin1": "上海市", "country": "中国"}]},
            {"daily": {"time": [day.isoformat()], "weather_code": [61], "temperature_2m_min": [12],
                       "temperature_2m_max": [18], "precipitation_probability_max": [80],
                       "wind_speed_10m_max": [4]}},
        ]
        with patch.object(weather, "_get_json", side_effect=payloads):
            result = weather.fetch_weather("上海市区", day, "上海")
        self.assertIsInstance(result, WeatherForecast)
        self.assertEqual(result.condition, "小雨")
        self.assertEqual(result.risk_level, "medium")
        self.assertTrue(any("伞" in item for item in result.tips))
        self.assertTrue(any("鞋" in item for item in result.clothing_advice))

    def test_fetch_weather_covers_departure_through_return_date(self):
        start = date.today() + timedelta(days=3)
        end = start + timedelta(days=2)
        times = [(start + timedelta(days=offset)).isoformat() for offset in range(3)]
        payloads = [
            {"results": [{"latitude": 31.2, "longitude": 121.5, "name": "上海", "admin1": "上海市", "country": "中国"}]},
            {"daily": {"time": times, "weather_code": [0, 61, 2], "temperature_2m_min": [12, 10, 11],
                       "temperature_2m_max": [22, 16, 19], "precipitation_probability_max": [10, 80, 30],
                       "wind_speed_10m_max": [3, 5, 4]}},
        ]
        with patch.object(weather, "_get_json", side_effect=payloads) as get_json:
            result = weather.fetch_weather("上海", start, "上海", end)
        forecast_params = get_json.call_args_list[1].args[1]
        self.assertEqual(forecast_params["wind_speed_unit"], "ms")
        self.assertEqual(forecast_params["start_date"], start.isoformat())
        self.assertEqual(forecast_params["end_date"], end.isoformat())
        self.assertEqual([item.date for item in result.days], times)
        self.assertEqual(result.end_date, end.isoformat())
        self.assertIn("小雨", result.condition)
        self.assertEqual(result.risk_level, "medium")

    def test_old_activity_duration_recovers_multi_day_weather_range(self):
        start = date.today() + timedelta(days=2)
        req = RequirementSpec(city="南京", event_date=start.isoformat(), duration="3天2晚")
        self.assertEqual(resolved_event_end_date(req), start + timedelta(days=2))

    def test_legacy_brief_preserves_activity_city_and_duration(self):
        start = date.today()
        brief = Brief(name="跨城团建", people=20, city="南京", event_city="上海",
                      date=start, duration="3天2晚", budget=500)
        requirements = brief.requirements()
        self.assertEqual(requirements.city, "上海")
        self.assertEqual(requirements.duration, "3天2晚")
        self.assertEqual(requirements.end_date, (start + timedelta(days=2)).isoformat())

    def test_weather_replan_is_available_before_booking(self):
        case = TravelCase(customer_request="团建", status=CaseStatus.WAITING_DRAFT,
                          council_decision=CouncilDecision(theme="团建", summary="待确认草案", schedule=[], merged_ideas=[], rationale=[]))
        with patch("src.web_flow.planning_active", return_value=False):
            self.assertIn("weather_replan", customer_actions(case, None))
            self.assertNotIn("weather_replan", customer_actions(case, object()))

    def test_weather_context_requires_clothing_and_warm_tip_in_ai_plan(self):
        day = date.today() + timedelta(days=2)
        second = day + timedelta(days=1)
        forecast = WeatherForecast(
            location="北京", destination="北京", event_date=day.isoformat(), end_date=second.isoformat(),
            condition="小雨；晴", weather_code=61, temperature_min_c=8, temperature_max_c=12,
            precipitation_probability=75, wind_speed_max=3, clothing_advice=["防滑鞋"], tips=["带伞"],
            fetched_at="2026-11-08T10:00:00+08:00", days=[
                WeatherForecastDay(date=day.isoformat(), condition="小雨", weather_code=61),
                WeatherForecastDay(date=second.isoformat(), condition="晴", weather_code=0),
            ],
        )
        case = TravelCase(customer_request="测试", requirements=RequirementSpec(
            city="北京", event_date=day.isoformat(), end_date=second.isoformat(), weather=forecast))
        context = weather_plan_context(case)
        self.assertIn("穿衣建议", context)
        self.assertIn("温馨提示", context)
        self.assertIn(day.isoformat(), context)
        self.assertIn(second.isoformat(), context)

    def test_brief_keeps_destination_and_weather_without_activity_city(self):
        day = date.today() + timedelta(days=2)
        forecast = WeatherForecast(
            location="北京", destination="北京近郊", event_date=day.isoformat(), condition="晴",
            weather_code=0, temperature_min_c=10, temperature_max_c=20, precipitation_probability=0,
            wind_speed_max=2, clothing_advice=["薄外套"], tips=["补水"], fetched_at="2026-11-08T10:00:00+08:00",
        )
        brief = Brief(name="测试", people=20, city="北京", destination="北京近郊",
                      event_city="", date=day, end_date=day + timedelta(days=1), budget=500, weather=forecast)
        req = brief.requirements()
        self.assertEqual(req.city, "北京")
        self.assertEqual(req.destination, "北京近郊")
        self.assertEqual(req.end_date, (day + timedelta(days=1)).isoformat())
        self.assertEqual(req.duration, "2天1晚")
        self.assertEqual(req.weather.condition, "晴")


if __name__ == "__main__":
    unittest.main()
