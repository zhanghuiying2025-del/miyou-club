import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from dataclasses import fields
from unittest.mock import AsyncMock, patch

from src.agents import AgentTeam, AgentOutputError
from src.models import (ApprovalRequest, BookingSpeech, CaseStatus, CouncilDecision, ExecutionTriage,
    JourneyAdjustment, JourneyResponse, ManagerReview, ReviewResult, TravelCase)
from src.workflow import TravelAgencyWorkflow
from src.procurement import (start_procurement, advance_supplier, request_booking_speech,
    finish_turn, owner_decide, customer_decide, pay_suppliers, settle_order, cash_summary, turn_context, financials, get_package)
from src.journey import start_demo, tick_demo, pending_event, coordinate_response, apply_response, report_event
from src.orchestration import handoffs_for, lifecycle


def current_case():
    return TravelCase(customer_request="南京30人团建", status=CaseStatus.COMPLETED,
        council_decision=CouncilDecision(theme="探索", summary="轻松体验", schedule=[
            "集合", "公园户外协作", "午餐", "街区步行", "团队分享"], merged_ideas=[], rationale=[]))


def prepare(case, nights=1):
    order = start_procurement(case, 9000, nights, 2)
    for _ in range(3):
        advance_supplier(order, now=order.last_step_at + 2)
    return order


class ThreeAgentFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch("src.performance.PERFORMANCE_FILE", Path(self.temp.name) / "performance.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.team = SimpleNamespace(manager="manager", planner="planner", fulfillment="fulfillment")

    def test_only_three_agent_instances_and_old_case_defaults(self):
        self.assertEqual({f.name for f in fields(AgentTeam)}, {"manager", "planner", "fulfillment"})
        case = TravelCase.model_validate({"customer_request": "旧需求"})
        self.assertEqual(case.handoffs, [])

    def test_manager_combined_review_keeps_program_rule_gate(self):
        case = current_case()
        case.council_decision.schedule = []
        workflow = TravelAgencyWorkflow(self.team)
        result = ManagerReview(operations=ReviewResult(reviewer_name="x", passed=True),
            risk=ReviewResult(reviewer_name="x", passed=True))
        with patch("src.workflow.run_structured", new=AsyncMock(return_value=result)) as model:
            asyncio.run(workflow._run_reviews(case))
            asyncio.run(workflow._review_and_revise(case))
        self.assertEqual(case.status, CaseStatus.WAITING_OWNER)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(case.handoffs[-1].source, "validated_cache")
        self.assertEqual(case.handoffs[0].receiver, "项目经理")
        self.assertTrue(all(x.status == "completed" for x in case.handoffs))

    def test_booking_actual_choices_cross_roles_and_change_execution_plan(self):
        case = current_case()
        order = prepare(case)
        outputs = [BookingSpeech(message="我选择同级远端酒店", package_id="far"),
            BookingSpeech(message="我建议减少赶场", adjustments=[JourneyAdjustment(schedule_index=1, replacement="室内轻量共创")]),
            BookingSpeech(message="我建议优化车辆排班", package_id="far_optimized")]
        async def exercise():
            with patch("src.procurement.build_agent_team", return_value=self.team), patch(
                "src.procurement.run_structured", new=AsyncMock(side_effect=outputs)) as model:
                for step in range(3, 7):
                    speech, source = await request_booking_speech(order)
                    self.assertEqual(source, "rule" if step == 5 else "agent")
                    finish_turn(order, step, speech, source)
            self.assertEqual([c.args[0] for c in model.call_args_list], ["fulfillment", "planner", "manager"])
        asyncio.run(exercise())
        self.assertEqual(order.status, "waiting_owner")
        owner_decide(order, True, "far_optimized")
        self.assertEqual(order.status, "waiting_customer")
        self.assertEqual(order.reservations[2].status, "partial_available")
        customer_decide(order, True)
        self.assertEqual(order.status, "ready")
        self.assertTrue(all(r.confirmation_id for r in order.reservations))
        self.assertEqual(start_demo(case).active_schedule[1], "室内轻量共创")
        self.assertEqual(case.council_decision.schedule[1], "公园户外协作")
        self.assertEqual(len(handoffs_for(case)), 4)

    def test_unknown_package_is_recorded_as_explicit_fallback(self):
        order = prepare(current_case())
        with patch("src.procurement.build_agent_team", return_value=self.team), patch(
            "src.procurement.run_structured", new=AsyncMock(return_value=BookingSpeech(message="已订好", package_id="invented"))):
            speech, source = asyncio.run(request_booking_speech(order))
        self.assertEqual(source, "rule_fallback")
        self.assertEqual(speech.handoffs[0].status, "failed")
        self.assertEqual(order.step, 3)
        finish_turn(order, 3, speech, source)
        with self.assertRaises(ValueError):
            finish_turn(order, 3, speech, source)

    def test_activity_exception_uses_executor_planner_manager_and_approval(self):
        case = current_case()
        demo = start_demo(case, now=0)
        tick_demo(demo, now=8)
        event = pending_event(demo)
        suggested = JourneyResponse(summary="我建议室内替代", needs_change=True,
            adjustments=[JourneyAdjustment(schedule_index=2, replacement="室内共创")], organizer_notice="请确认")
        outputs = [ExecutionTriage(summary="户外受影响", needs_replan=True, affected_indices=[2], requires_owner=True),
            suggested, suggested.model_copy(deep=True)]
        with patch("src.journey.run_structured", new=AsyncMock(side_effect=outputs)) as model:
            response, source, error = asyncio.run(coordinate_response(self.team, demo, event))
        self.assertEqual([c.args[0] for c in model.call_args_list], ["fulfillment", "planner", "manager"])
        self.assertEqual(source, "agent")
        self.assertTrue(response.requires_owner)
        apply_response(demo, event.id, response, source)
        self.assertEqual(demo.status, "awaiting_owner")
        self.assertEqual(demo.active_schedule, demo.original_schedule)
        self.assertEqual(len(demo.handoffs), 3)

    def test_failed_activity_review_cannot_release_changes(self):
        demo = start_demo(current_case(), now=0)
        event = report_event(demo, "delay")
        with patch("src.journey.run_structured", new=AsyncMock(side_effect=AgentOutputError("offline"))):
            response, source, error = asyncio.run(coordinate_response(self.team, demo, event))
        apply_response(demo, event.id, response, source, error)
        self.assertEqual(source, "rule_fallback")
        self.assertEqual(demo.status, "awaiting_owner")
        self.assertEqual(demo.handoffs[0].status, "failed")
        self.assertEqual(event.source, "organizer_report")

    def test_no_change_skips_planner_call(self):
        demo = start_demo(current_case(), now=0)
        event = report_event(demo, "member")
        outputs = [ExecutionTriage(summary="无需修改", needs_replan=False), JourneyResponse(
            summary="我会继续跟进", needs_change=False, organizer_notice="保留安排")]
        with patch("src.journey.run_structured", new=AsyncMock(side_effect=outputs)) as model:
            response, source, _ = asyncio.run(coordinate_response(self.team, demo, event))
        self.assertEqual(model.call_count, 2)
        self.assertEqual([c.args[0] for c in model.call_args_list], ["fulfillment", "manager"])
        self.assertEqual(source, "agent")

    def test_cash_settlement_is_idempotent_and_not_deposit_profit(self):
        case = current_case()
        order = prepare(case, nights=0)
        self.assertEqual(cash_summary(order)['revenue'], 0)
        self.assertEqual(cash_summary(order)['advance_obligation'], 5400)
        with self.assertRaises(ValueError):
            settle_order(case)
        pay_suppliers(order)
        payments = len(order.payments)
        pay_suppliers(order)
        self.assertEqual(len(order.payments), payments)
        demo = start_demo(case)
        demo.status = "finished"
        settle_order(case)
        settle_order(case)
        cash = cash_summary(order)
        self.assertEqual(cash['revenue'], 9000)
        self.assertEqual(cash['customer_due'], 0)
        self.assertEqual(cash['supplier_due'], 0)
        self.assertEqual(cash['advance_obligation'], 0)
        self.assertEqual(cash['cash'], round(cash['opening_cash'] + cash['profit'], 2))
        self.assertEqual(sum(p.kind == "customer_balance" for p in order.payments), 1)
        self.assertEqual(case.scorecards, [])
        self.assertEqual(lifecycle(case), "订单完成并结算")

    def test_payments_cannot_spend_reserve(self):
        order = prepare(current_case(), nights=0)
        order.opening_cash_yuan = 0
        with self.assertRaises(ValueError):
            pay_suppliers(order)
        self.assertEqual(len(order.payments), 1)

    def test_financial_review_uses_actual_selected_package(self):
        order = prepare(current_case())
        finish_turn(order, 3, BookingSpeech(message="我选择近端候选", package_id="near_budget"), "agent")
        finish_turn(order, 4, BookingSpeech(message="行程暂不修改"), "agent")
        facts = turn_context(order)[3]
        self.assertIn("near_budget", facts)
        self.assertIn(f"{financials(order, get_package(order))['cost']:.2f}", facts)

    def test_clarification_approval_without_plan_still_returns_to_customer(self):
        workflow = TravelAgencyWorkflow(self.team)
        case = TravelCase(customer_request="信息缺失", status=CaseStatus.WAITING_OWNER,
            customer_answer_pending=True, customer_questions=["请选择日期"],
            approval=ApprovalRequest(title="需要继续沟通", reasons=["日期缺失"]))
        asyncio.run(workflow.submit_owner_decision(case, True))
        self.assertEqual(case.status, CaseStatus.WAITING_CUSTOMER)

    def test_new_owner_and_customer_payment_controls_render_and_persist(self):
        from streamlit.testing.v1 import AppTest
        case = current_case()
        order = prepare(case, nights=0)
        with self.assertRaises(ValueError):
            settle_order(case)
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with patch("src.agents.build_agent_team", return_value=self.team), patch("src.storage.load_case_from_disk", side_effect=lambda case_id=None: case if case_id in {None,case.id} else None), patch("src.storage.save_case_to_disk"):
            app = AppTest.from_file(str(app_path), default_timeout=30).run()
            app.session_state['case'] = case
            app.sidebar.radio[0].set_value("公司老板").run()
            app.sidebar.radio(key="work_area").set_value("团建策划与交付").run()
            self.assertEqual(len(app.exception), 0)
            next(b for b in app.button if b.label == "授权模拟供应商支出").click().run()
            self.assertEqual(len(app.exception), 0)
            next(b for b in app.button if b.label=="在客户页面继续此案件").click().run()
            self.assertFalse(any(b.label == "开始活动" for b in app.button))
            next(b for b in app.button if b.label == "确认活动完成并支付尾款").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertIsNone(case.journey_demo)
            self.assertTrue(order.settled)
            self.assertEqual(len([p for p in order.payments if p.kind == 'customer_balance']), 1)
