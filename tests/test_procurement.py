import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import AsyncMock, patch

from src.models import BookingSpeech, CaseStatus, CouncilDecision, RequirementSpec, TravelCase
from src.procurement import (start_procurement, advance_supplier, finish_turn, get_package, financials,
    owner_decide, customer_decide, route_order, customer_reasons, request_booking_speech, resume_order)
from src.journey import start_demo


def current_case():
    return TravelCase(customer_request="团建", status=CaseStatus.COMPLETED,
        requirements=RequirementSpec(participant_count=30, budget_yuan=9000),
        council_decision=CouncilDecision(theme="测试", summary="测试", schedule=["集合", "公园活动"], merged_ideas=[], rationale=[]))


def suppliers(order):
    for _ in range(3):
        advance_supplier(order, now=order.last_step_at + 2)


def council(order):
    for step in range(3, 7):
        finish_turn(order, step, BookingSpeech(message="测试阶段的业务意见"), "rule_fallback")


class ProcurementTests(unittest.TestCase):
    def test_deposit_is_idempotent_and_not_a_revenue_event(self):
        case = current_case()
        order = start_procurement(case, 9000, 1, 2)
        self.assertIs(order, start_procurement(case, 9000, 1, 2))
        self.assertEqual(order.deposit_yuan, 5400)
        self.assertEqual(len(order.payments), 1)
        self.assertEqual(order.payments[0].kind, "customer_deposit")
        self.assertIn("收入和已实现利润均为0", order.discussion[0].owner_message)

    def test_supplier_returns_partial_hotel_and_unfinished_order_blocks_trip(self):
        case = current_case()
        order = start_procurement(case, 9000, 1, 2)
        suppliers(order)
        self.assertEqual([x.status for x in order.reservations], ["confirmed", "confirmed", "partial_available"])
        self.assertLess(order.reservations[2].available_quantity, order.room_count)
        with self.assertRaises(ValueError):
            start_demo(case)
        with self.assertRaises(ValueError):
            route_order(order)

    def test_team_optimizes_before_owner_and_finance_is_computed(self):
        order = start_procurement(current_case(), 9000, 1, 2)
        suppliers(order)
        with self.assertRaises(ValueError):
            owner_decide(order, True, "near_budget")
        council(order)
        self.assertEqual(order.status, "waiting_owner")
        self.assertEqual(order.selected_package, "far_optimized")
        self.assertAlmostEqual(financials(order, get_package(order, "far"))["margin"], -.04)
        self.assertAlmostEqual(financials(order, get_package(order))["margin"], -.01)
        self.assertTrue(any("优化" in x.owner_message for x in order.discussion))
        self.assertEqual(order.reservations[2].status, "partial_available")

    def test_owner_profit_override_preserves_standard_without_silent_downgrade(self):
        case = current_case()
        order = start_procurement(case, 9000, 1, 2)
        suppliers(order)
        council(order)
        owner_decide(order, True, "far_optimized")
        self.assertEqual(order.status, "ready")
        self.assertEqual(get_package(order).hotel_grade, 2)
        self.assertEqual(order.reservations[2].status, "confirmed")
        self.assertEqual(start_demo(case).order_id, order.id)

    def test_owner_approval_cannot_replace_customer_hotel_consent(self):
        order = start_procurement(current_case(), 9000, 1, 2)
        suppliers(order)
        council(order)
        owner_decide(order, True, "near_budget")
        self.assertEqual(order.status, "waiting_customer")
        self.assertNotEqual(order.reservations[2].status, "confirmed")
        customer_decide(order, True)
        self.assertEqual(order.status, "ready")
        self.assertEqual(order.customer_approved_package, "near_budget")
        with self.assertRaises(ValueError):
            customer_decide(order, True)

    def test_customer_rejects_conditions_then_new_package_requires_new_authority(self):
        order = start_procurement(current_case(), 9000, 1, 2)
        suppliers(order)
        council(order)
        owner_decide(order, True, "near_budget")
        customer_decide(order, False)
        self.assertEqual(order.status, "waiting_owner")
        self.assertIsNone(order.owner_approved_package)
        self.assertEqual(order.reservations[2].status, "partial_available")
        owner_decide(order, True, "far_optimized")
        self.assertEqual(order.status, "ready")

    def test_confirmed_transfer_limit_also_requires_customer(self):
        order = start_procurement(current_case(), 9000, 1, 2, max_transfer=30)
        suppliers(order)
        council(order)
        owner_decide(order, True, "far_optimized")
        self.assertEqual(order.status, "waiting_customer")
        self.assertTrue(any("接驳" in x for x in customer_reasons(order, get_package(order))))

    def test_no_hotel_and_owner_rejection_are_valid_end_states(self):
        order = start_procurement(current_case(), 9000, 0, 2)
        suppliers(order)
        self.assertEqual(order.status, "ready")
        self.assertEqual(order.reservations[2].status, "not_required")
        another = start_procurement(current_case(), 9000, 1, 2)
        suppliers(another)
        council(another)
        owner_decide(another, False)
        self.assertEqual(another.status, "held")
        self.assertEqual(len(another.payments), 1)

    def test_api_failure_is_explicit_rule_fallback(self):
        order = start_procurement(current_case(), 9000, 1, 2)
        suppliers(order)
        with patch("src.procurement.build_agent_team", side_effect=RuntimeError("offline")):
            speech, source = asyncio.run(request_booking_speech(order))
        self.assertEqual(source, "rule_fallback")
        self.assertTrue(speech.message)

    def test_held_order_resumes_without_collecting_deposit_again(self):
        order = start_procurement(current_case(), 9000, 1, 2)
        suppliers(order)
        council(order)
        owner_decide(order, False)
        payments = list(order.payments)
        resume_order(order)
        self.assertEqual(order.status, "waiting_owner")
        owner_decide(order, True, "near_budget")
        self.assertEqual(order.status, "waiting_customer")
        customer_decide(order, True)
        self.assertEqual(order.status, "ready")
        self.assertEqual(order.payments, payments)

    def test_customer_booking_form_and_standard_change_buttons_render(self):
        from streamlit.testing.v1 import AppTest
        case = current_case()
        application = Path(__file__).resolve().parents[1] / "app.py"
        with patch("src.agents.build_agent_team", return_value=SimpleNamespace()), patch("src.storage.load_case_from_disk", return_value=None), patch("src.storage.save_case_to_disk"):
            app = AppTest.from_file(str(application), default_timeout=30).run()
            app.session_state["case"] = case
            app.run()
            self.assertTrue(any(x.label == "确认订单并支付预付款" for x in app.button))
            order = start_procurement(case, 9000, 1, 2)
            suppliers(order)
            council(order)
            owner_decide(order, True, "near_budget")
            app.run()
            self.assertEqual(len(app.exception), 0)
            next(x for x in app.button if x.label == "我同意上述条件变更").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(case.procurement_order.status, "ready")

    def test_customer_and_owner_sessions_reload_each_others_approval(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_case_to_disk, load_case_from_disk
        case = current_case()
        order = start_procurement(case, 9000, 1, 2)
        suppliers(order)
        council(order)
        application = Path(__file__).resolve().parents[1] / "app.py"
        with tempfile.TemporaryDirectory() as directory, patch("src.storage.DATA_DIR", Path(directory)), patch("src.storage.CASE_FILE", Path(directory) / "case.json"), patch("src.agents.build_agent_team", return_value=SimpleNamespace()):
            save_case_to_disk(case)
            client = AppTest.from_file(str(application), default_timeout=30).run()
            client.session_state["customer_case_id"]=case.id
            client.run()
            owner = AppTest.from_file(str(application), default_timeout=30).run()
            owner.sidebar.radio[0].set_value("公司老板").run()
            next(x for x in owner.selectbox if x.label == "历史案件与未完成进度").set_value(case.id).run()
            next(x for x in owner.selectbox if x.label == "老板选择资源组合").set_value("near_budget")
            next(x for x in owner.button if x.label == "批准所选商业方案").click().run()
            self.assertEqual(len(owner.exception), 0)
            client.run()
            self.assertEqual(client.session_state["case"].procurement_order.status, "waiting_customer")
            next(x for x in client.button if x.label == "我同意上述条件变更").click().run()
            self.assertEqual(len(client.exception), 0)
            owner.run()
            self.assertEqual(owner.session_state["case"].procurement_order.status, "ready")
            self.assertEqual(load_case_from_disk().storage_revision, 3)

    def test_automatic_ready_requests_full_redraw(self):
        from src.procurement_ui import render_procurement
        case = current_case()
        order = start_procurement(case, 9000, 0, 2)
        for _ in range(2):
            advance_supplier(order, now=order.last_step_at + 2)
        order.last_step_at = 0
        class FullRedraw(BaseException):
            pass
        with patch("src.procurement_ui.st.subheader"), patch("src.procurement_ui.st.caption"), patch("src.procurement_ui.st.rerun", side_effect=FullRedraw):
            with self.assertRaises(FullRedraw):
                render_procurement.__wrapped__(case, lambda value: None)
        self.assertEqual(order.status, "ready")


if __name__ == "__main__":
    unittest.main()
