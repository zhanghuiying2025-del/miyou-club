import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
from src.models import (CaseStatus, CouncilDecision, JourneyAdjustment, JourneyResponse, TravelCase)
from src.journey import (start_demo, replay_demo, tick_demo, pending_event, fallback_response,
                         apply_response, confirm_event, recommend, validate_response)
from src.visuals import journey_frame
from src.procurement import start_procurement, advance_supplier


def case():
    return TravelCase(customer_request="南京团建", status=CaseStatus.COMPLETED,
        council_decision=CouncilDecision(theme="城市探索", summary="轻松团建", merged_ideas=[], rationale=[],
            schedule=["集合破冰", "公园户外协作", "午餐休息", "街区步行", "团队分享"]))


def ready_booking(current):
    order = start_procurement(current, 9000, 0, 2)
    for _ in range(3):
        advance_supplier(order, now=order.last_step_at + 2)


class JourneyTests(unittest.TestCase):
    def test_requires_confirmed_plan_and_keeps_original_unchanged(self):
        current = case()
        current.status = CaseStatus.READY_FOR_CUSTOMER
        with self.assertRaises(ValueError):
            start_demo(current)
        current.status = CaseStatus.COMPLETED
        demo = start_demo(current, now=0)
        demo.active_schedule[0] = "仅演示修改"
        self.assertEqual(current.council_decision.schedule[0], "集合破冰")
        self.assertEqual(demo.original_schedule[0], "集合破冰")

    def test_time_catchup_stops_at_alert_and_waits_for_confirmation(self):
        current = case()
        demo = start_demo(current, now=0)
        tick_demo(demo, now=100)
        self.assertEqual(demo.tick, 4)
        self.assertEqual(demo.status, "awaiting_agent")
        self.assertFalse(tick_demo(demo, now=200))
        event = pending_event(demo)
        response = fallback_response(demo, event)
        self.assertTrue(response.needs_change)
        before = list(demo.active_schedule)
        apply_response(demo, event.id, response, "rule_fallback")
        self.assertEqual(demo.active_schedule, before)
        self.assertEqual(demo.status, "awaiting_owner")
        confirm_event(demo, True, owner=True)
        self.assertEqual(demo.status, "awaiting_organizer")
        with patch("src.journey.time.time", return_value=100):
            confirm_event(demo, True)
        self.assertEqual(demo.status, "running")
        self.assertNotEqual(demo.active_schedule, before)
        tick_demo(demo, now=200)
        self.assertEqual(demo.tick, 8)
        self.assertEqual(pending_event(demo).kind, "member")
        self.assertEqual(len(demo.events), 2)

    def test_rejection_cannot_resume_without_owner_and_organizer(self):
        demo = start_demo(case(), now=0)
        tick_demo(demo, now=8)
        event = pending_event(demo)
        apply_response(demo, event.id, fallback_response(demo, event), "rule_fallback")
        confirm_event(demo, True, owner=True)
        confirm_event(demo, False)
        self.assertEqual(demo.status, "awaiting_owner")
        self.assertFalse(tick_demo(demo, now=100))
        confirm_event(demo, True, owner=True)
        self.assertEqual(demo.status, "awaiting_organizer")
        confirm_event(demo, True)
        self.assertEqual(demo.status, "running")

    def test_no_affected_activity_does_not_force_changes(self):
        current = case()
        current.council_decision.schedule = ["室内分享", "午餐", "室内交流"]
        demo = start_demo(current, now=0)
        tick_demo(demo, now=8)
        event = pending_event(demo)
        response = fallback_response(demo, event)
        self.assertFalse(response.needs_change)
        apply_response(demo, event.id, response, "rule_fallback")
        self.assertEqual(demo.status, "running")

    def test_invalid_agent_edit_falls_back_and_stale_response_is_rejected(self):
        demo = start_demo(case(), now=0)
        tick_demo(demo, now=8)
        event = pending_event(demo)
        invalid = JourneyResponse(summary="调整已结束行程", needs_change=True,
            adjustments=[JourneyAdjustment(schedule_index=0, replacement="错误调整")], organizer_notice="测试")
        with patch("src.journey.run_structured", new=AsyncMock(return_value=invalid)):
            response, source, error = asyncio.run(recommend(object(), demo, event))
        self.assertEqual(source, "rule_fallback")
        self.assertTrue(error)
        apply_response(demo, event.id, response, source)
        with self.assertRaises(ValueError):
            apply_response(demo, event.id, response, source)

    def test_network_failure_is_explicit_and_replay_preserves_history(self):
        current = case()
        demo = start_demo(current, now=0)
        tick_demo(demo, now=8)
        event = pending_event(demo)
        with patch("src.journey.run_structured", new=AsyncMock(side_effect=RuntimeError("offline"))):
            response, source, error = asyncio.run(recommend(object(), demo, event))
        self.assertEqual(source, "rule_fallback")
        self.assertEqual(error, "offline")
        demo.status = "finished"
        old_id = demo.order_id
        replay_demo(current)
        self.assertEqual(current.journey_history[0].order_id, old_id)
        self.assertNotEqual(current.journey_demo.order_id, old_id)
        restored = TravelCase.model_validate_json(current.model_dump_json())
        self.assertEqual(restored.journey_history[0].events[0].id, event.id)

    def test_local_illustration_is_png(self):
        with Image.open(BytesIO(journey_frame("公园户外协作", 1, 5, 4, True, False))) as image:
            self.assertEqual(image.size, (1300, 640))

    @patch("src.activity_order.TRACKING_ENABLED", True)
    def test_customer_can_create_simulated_order_from_completed_plan(self):
        from streamlit.testing.v1 import AppTest
        current = case()
        ready_booking(current)
        application = Path(__file__).resolve().parents[1] / "app.py"
        with patch("src.agents.build_agent_team", return_value=SimpleNamespace()), patch("src.storage.load_case_from_disk", return_value=None), patch("src.storage.save_case_to_disk"):
            app = AppTest.from_file(str(application), default_timeout=30).run()
            app.session_state["case"] = current
            app.run()
            button = next(x for x in app.button if x.label == "开始活动")
            button.click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertIsNotNone(app.session_state["case"].journey_demo)
            self.assertEqual(app.session_state["case"].status, CaseStatus.COMPLETED)

    @patch("src.activity_order.TRACKING_ENABLED", True)
    def test_organizer_can_confirm_event_on_customer_page(self):
        from streamlit.testing.v1 import AppTest
        current = case()
        ready_booking(current)
        demo = start_demo(current, now=0)
        tick_demo(demo, now=8)
        event = pending_event(demo)
        apply_response(demo, event.id, fallback_response(demo, event), "rule_fallback")
        confirm_event(demo, True, owner=True)
        application = Path(__file__).resolve().parents[1] / "app.py"
        with patch("src.agents.build_agent_team", return_value=SimpleNamespace()), patch("src.storage.load_case_from_disk", return_value=None), patch("src.storage.save_case_to_disk"):
            app = AppTest.from_file(str(application), default_timeout=30).run()
            app.session_state["case"] = current
            app.run()
            button = next(x for x in app.button if x.label == "同意演示调整并继续")
            button.click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.session_state["case"].journey_demo.status, "running")
            self.assertEqual(event.status, "resolved")


if __name__ == "__main__":
    unittest.main()
