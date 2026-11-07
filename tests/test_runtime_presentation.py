import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from threading import Event
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.runtime import Job, submit_once, consume, progress_events, emit_progress
from src.models import (CaseStatus, CouncilDecision, JourneyResponse, JourneyAdjustment,
    ManagerReview, ReviewResult, ReviewFinding, TravelCase, RequirementSpec)
from src.workflow import TravelAgencyWorkflow
from src.journey import start_demo, report_event, apply_response, confirm_event
from src.procurement import start_procurement, advance_supplier, pay_suppliers, settle_order, confirm_activity_cost, cash_summary
from src.orchestration import assign


def case():
    return TravelCase(customer_request='团建', status=CaseStatus.COMPLETED,
        requirements=RequirementSpec(city='南京', event_date='2026-11-07', participant_count=30),
        council_decision=CouncilDecision(theme='轻松', summary='测试', schedule=['集合', '户外协作'],
            merged_ideas=[], rationale=[]))


class RuntimePresentationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch('src.performance.PERFORMANCE_FILE', Path(self.directory.name) / 'performance.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.team = SimpleNamespace(manager=object(), planner=object(), fulfillment=object())

    def test_two_sessions_submit_once_and_commit_once(self):
        key = ('test', uuid.uuid4().hex)
        gate, started = Event(), Event()
        calls = []
        def worker():
            calls.append(1)
            emit_progress('履约执行', '核对资源')
            started.set()
            gate.wait(2)
            return 'result'
        with ThreadPoolExecutor(max_workers=2) as clients:
            one = clients.submit(submit_once, key, 'test-case', worker)
            two = clients.submit(submit_once, key, 'test-case', worker)
            a, b = one.result(), two.result()
        self.assertIs(a, b)
        self.assertTrue(started.wait(2))
        self.assertIn('核对资源', progress_events(a)[0]['message'])
        gate.set(); self.assertEqual(a.future.result(timeout=3), 'result')
        applied = []
        self.assertTrue(consume(a, applied.append))
        self.assertFalse(consume(b, applied.append))
        self.assertEqual(calls, [1]); self.assertEqual(applied, ['result'])

    def test_failed_commit_retains_result_for_retry(self):
        f = Future(); f.set_result('saved-result')
        job = Job(('test', uuid.uuid4().hex), 'case', f)
        def fail(value): raise RuntimeError('version conflict')
        with self.assertRaises(RuntimeError): consume(job, fail)
        self.assertFalse(job.applied)
        applied = []
        self.assertTrue(consume(job, applied.append))
        self.assertEqual(applied, ['saved-result'])

    def test_low_nonblocking_notice_does_not_trigger_rework(self):
        current = case(); current.status = CaseStatus.REVIEWING
        result = ManagerReview(operations=ReviewResult(reviewer_name='经理', passed=True,
            findings=[ReviewFinding(severity='low', description='开放时间仍待核实')]),
            risk=ReviewResult(reviewer_name='经理', passed=True))
        wf = TravelAgencyWorkflow(self.team)
        with patch('src.workflow.run_structured', new=AsyncMock(return_value=result)) as call:
            asyncio.run(wf._review_and_revise(current))
        self.assertEqual(call.await_count, 1)
        self.assertEqual(current.status, CaseStatus.READY_FOR_CUSTOMER)
        self.assertEqual(current.revision_count, 0)
        self.assertIn('开放时间仍待核实', current.council_decision.items_to_verify)

    def test_high_risk_cannot_be_labeled_nonblocking(self):
        current = case(); current.status = CaseStatus.REVIEWING
        result = ManagerReview(operations=ReviewResult(reviewer_name='经理', passed=True),
            risk=ReviewResult(reviewer_name='经理', passed=True, findings=[ReviewFinding(
                severity='high', description='重大风险需要授权', blocking=False)]))
        with patch('src.workflow.run_structured', new=AsyncMock(return_value=result)):
            asyncio.run(TravelAgencyWorkflow(self.team)._review_and_revise(current))
        self.assertEqual(current.status, CaseStatus.WAITING_OWNER)

    def test_expense_and_unknown_fee_require_owner_even_when_model_says_no(self):
        for amount, note in [(5000, '需要场租'), (0, '新增5000元场租'), (None, '费用未确定')]:
            demo = start_demo(case(), now=0); event = report_event(demo, 'weather')
            response = JourneyResponse(summary='调整', needs_change=True,
                adjustments=[JourneyAdjustment(schedule_index=1, replacement='室内活动')],
                organizer_notice='请确认', requires_owner=False, additional_cost_yuan=amount, cost_note=note)
            apply_response(demo, event.id, response, 'agent')
            self.assertEqual(demo.status, 'awaiting_owner')

    def test_zero_cost_change_can_go_to_customer(self):
        demo = start_demo(case(), now=0); event = report_event(demo, 'member')
        response = JourneyResponse(summary='原地休息', needs_change=True, additional_cost_yuan=0,
            adjustments=[JourneyAdjustment(schedule_index=1, replacement='自愿参与或原地休息')],
            organizer_notice='请确认', cost_note='无新增费用')
        apply_response(demo, event.id, response, 'agent')
        self.assertEqual(demo.status, 'awaiting_organizer')

    def test_empty_schedule_cannot_collect_deposit(self):
        current = case(); current.council_decision.schedule = []
        with self.assertRaises(ValueError): start_procurement(current, 9000, 0, 2)
        self.assertIsNone(current.procurement_order)

    def test_waiting_status_is_persisted_before_model_finishes(self):
        current = case()
        wf = TravelAgencyWorkflow(self.team)
        saved = []
        wf.checkpoint_callback = lambda c: saved.append(c.model_copy(deep=True))
        async def model(*args):
            self.assertTrue(any(r.status == 'running' for r in saved[-1].handoffs))
            self.assertEqual(saved[-1].public_updates[-1].source, 'workflow_status')
            return current.council_decision
        with patch('src.workflow.run_structured', side_effect=model):
            asyncio.run(wf._call(current, 'manager', 'test', CouncilDecision))
        self.assertEqual(saved[-1].handoffs[-1].status, 'completed')
        self.assertEqual(saved[-1].public_updates[-1].stage, '环节完成')

    def test_cancellation_resistant_late_result_is_not_success(self):
        records = []
        async def late(*args):
            try: await asyncio.sleep(.1)
            except asyncio.CancelledError: await asyncio.sleep(.02)
            return case().council_decision
        async def exercise():
            with self.assertRaises(TimeoutError):
                await assign(object(), 'test', CouncilDecision, records, 'test', '经理', '策划',
                    '截止时间测试', 'test', timeout=.005, runner=late)
        asyncio.run(exercise())
        self.assertEqual(records[-1].status, 'failed')

    def test_booking_worker_failure_is_retryable_on_page(self):
        from streamlit.testing.v1 import AppTest
        current = case(); order = start_procurement(current, 9000, 1, 2)
        for _ in range(3): advance_supplier(order, now=order.last_step_at + 2)
        f = Future(); f.set_exception(RuntimeError('injected worker failure'))
        job = Job(('booking', current.id, order.id, 1, 3, 0), current.id, f)
        with patch('src.agents.build_agent_team', return_value=self.team), patch('src.storage.load_case_from_disk', return_value=None), patch('src.storage.save_case_to_disk'), patch('src.procurement_ui.submit_once', return_value=job):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
            app.session_state['case'] = current
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertIn('injected', order.runtime_error)
            self.assertTrue(any(b.label == '重试当前预订步骤' for b in app.button))
            self.assertEqual(order.step, 3); self.assertEqual(len(order.payments), 1)

    @patch("src.activity_order.TRACKING_ENABLED", True)
    def test_activity_worker_exception_does_not_crash_page(self):
        from streamlit.testing.v1 import AppTest
        current = case(); order = start_procurement(current, 9000, 0, 2)
        for _ in range(3): advance_supplier(order, now=order.last_step_at + 2)
        demo = start_demo(current, now=0); event = report_event(demo, 'weather')
        f = Future(); f.set_exception(RuntimeError('injected activity failure'))
        job = Job(('journey', current.id, demo.order_id, event.id, 0), current.id, f)
        with patch('src.agents.build_agent_team', return_value=self.team), patch('src.storage.load_case_from_disk', return_value=None), patch('src.storage.save_case_to_disk'), patch('src.journey_ui.submit_once', return_value=job):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
            app.session_state['case'] = current; app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(demo.status, 'awaiting_agent')
            self.assertIn('injected activity', demo.runtime_error)
            self.assertTrue(any(b.label == '重试本次活动响应' for b in app.button))

    def test_approved_activity_expense_must_be_accounted_before_settlement(self):
        current = case(); order = start_procurement(current, 9000, 0, 2)
        for _ in range(3): advance_supplier(order, now=order.last_step_at + 2)
        pay_suppliers(order)
        demo = start_demo(current, now=0); event = report_event(demo, 'weather')
        response = JourneyResponse(summary='改为室内', needs_change=True, additional_cost_yuan=500,
            adjustments=[JourneyAdjustment(schedule_index=1, replacement='室内协作')], organizer_notice='确认调整')
        apply_response(demo, event.id, response, 'agent')
        confirm_event(demo, True, owner=True); confirm_event(demo, True)
        demo.status = 'finished'
        with self.assertRaises(ValueError): settle_order(current)
        before = cash_summary(order)['cash']
        confirm_activity_cost(current, event.id, 500)
        confirm_activity_cost(current, event.id, 500)
        self.assertEqual(cash_summary(order)['cash'], before - 500)
        settle_order(current)
        self.assertTrue(order.settled)
        self.assertEqual(cash_summary(order)['profit'], 9000 - cash_summary(order)['paid'])
