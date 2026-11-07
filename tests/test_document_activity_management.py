import asyncio
from datetime import date
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.document_intake import intake_documents, missing_fields, update_document_fields, confirm_document_route
from src.activity_order import create_activity_order, start_activity_order, advance_activity_order, report_activity_incident
from src.models import (
    TravelCase, RequirementSpec, DocumentIntakeResult, ActivitySource, ActivityReviewReport,
    CaseStatus, ExecutionTriage, JourneyResponse, JourneyAdjustment,
)
from src.journey import apply_response, confirm_event, tick_demo, coordinate_response
from test_activity_review import SOURCE, audit, execution


def document_case(count=None, kind='existing_plan'):
    req = RequirementSpec(event_name='青年讲演', event_date='2026-04-23', participant_count=count)
    return TravelCase(customer_request='客户上传文档', service_mode='document_intake',
        status=CaseStatus.WAITING_REQUIREMENTS, requirements=req,
        document_venue='五楼大会议室',
        document_intake=DocumentIntakeResult(document_kind=kind, summary='原文已包含日期与地点，总人数未知',
            requirements=req, venue='五楼大会议室'),
        activity_sources=[ActivitySource(file_name='原活动.docx', text=SOURCE)])


def managed_case():
    case = document_case(42)
    case.service_mode = 'activity_review'
    case.status = CaseStatus.COMPLETED
    plan = execution()
    plan.run_sheet = ['签到与设备检查', '选手演讲及计分', '颁奖与离场']
    case.activity_review = ActivityReviewReport(audit=audit(), execution=plan)
    return case


class DocumentIntakeTests(unittest.TestCase):
    def test_extract_once_then_collect_only_missing_essentials(self):
        result = document_case().document_intake
        workflow = SimpleNamespace(_call=AsyncMock(return_value=result))
        case = TravelCase(customer_request='上传需求', activity_sources=[ActivitySource(file_name='方案.docx', text=SOURCE)])
        asyncio.run(intake_documents(workflow, case))
        self.assertEqual(missing_fields(case, 'activity'), ['participant_count'])
        self.assertEqual(missing_fields(case, 'travel'), ['city', 'participant_count'])
        update_document_fields(case, {'participant_count': 42})
        self.assertEqual(confirm_document_route(case, 'activity'), 'activity_review')
        self.assertEqual(case.requirements.event_date, '2026-04-23')
        workflow._call.assert_awaited_once()

    def test_required_information_cannot_be_replaced_by_placeholders(self):
        case = document_case()
        case.requirements.event_date = '日期待定'
        self.assertEqual(missing_fields(case, 'activity'), ['event_date', 'participant_count'])
        with self.assertRaisesRegex(ValueError, '仍需确认'):
            confirm_document_route(case, 'activity')
        with self.assertRaises(ValueError):
            update_document_fields(case, {'participant_count': 0})

    def test_manual_answers_update_fields_without_new_extraction(self):
        case = document_case()
        update_document_fields(case, {'event_date': date(2026, 11, 7), 'participant_count': 32})
        self.assertEqual(case.requirements.event_date, '2026-11-07')
        self.assertEqual(case.document_venue, '五楼大会议室')
        self.assertEqual(case.requirements.participant_count, 32)

    def test_travel_route_still_requires_city_but_not_activity_venue(self):
        case = document_case(20, kind='requirements')
        case.document_venue = ''
        with self.assertRaisesRegex(ValueError, '活动城市'):
            confirm_document_route(case, 'travel')
        update_document_fields(case, {'city': '南京'})
        self.assertEqual(confirm_document_route(case, 'travel'), 'document_travel')
        self.assertEqual(case.service_mode, 'travel_planning')

    def test_active_order_blocks_new_document_extraction_before_model_call(self):
        case = managed_case()
        create_activity_order(case, case.activity_review.execution.run_sheet)
        workflow = SimpleNamespace(_call=AsyncMock())
        with self.assertRaisesRegex(ValueError, '不能通过重新读取'):
            asyncio.run(intake_documents(workflow, case))
        workflow._call.assert_not_awaited()


class ActivityOrderTests(unittest.TestCase):
    def test_order_needs_acknowledgement_and_explicit_selected_schedule(self):
        case = managed_case()
        case.status = CaseStatus.READY_FOR_CUSTOMER
        with self.assertRaises(ValueError):
            create_activity_order(case, case.activity_review.execution.run_sheet)
        case.status = CaseStatus.COMPLETED
        with self.assertRaises(ValueError):
            create_activity_order(case, ['凭空新增环节'])
        demo = create_activity_order(case, list(reversed(case.activity_review.execution.run_sheet)))
        self.assertEqual(demo.active_schedule, case.activity_review.execution.run_sheet)
        self.assertIsNone(case.procurement_order)
        with self.assertRaisesRegex(ValueError, '不能重复创建'):
            create_activity_order(case, demo.active_schedule)

    def test_activity_does_not_auto_advance_or_inject_travel_weather(self):
        case = managed_case()
        demo = create_activity_order(case, case.activity_review.execution.run_sheet)
        start_activity_order(case)
        self.assertFalse(tick_demo(demo, now=99999999999))
        self.assertEqual(demo.events, [])
        self.assertEqual(demo.manual_index, 0)
        advance_activity_order(case)
        self.assertEqual(demo.manual_index, 1)

    def test_extra_cost_cannot_skip_owner_or_organizer_and_past_is_immutable(self):
        case = managed_case()
        demo = create_activity_order(case, case.activity_review.execution.run_sheet)
        start_activity_order(case)
        advance_activity_order(case)
        event = report_activity_incident(case, 'equipment', '投影故障，备用设备未知')
        with self.assertRaises(ValueError):
            advance_activity_order(case)
        invalid = JourneyResponse(summary='错误修改已结束签到', needs_change=True,
            adjustments=[JourneyAdjustment(schedule_index=0, replacement='修改签到')], organizer_notice='确认', additional_cost_yuan=0)
        with self.assertRaises(ValueError):
            apply_response(demo, event.id, invalid, 'agent')
        response = JourneyResponse(summary='建议租用备用设备', needs_change=True,
            adjustments=[JourneyAdjustment(schedule_index=1, replacement='设备确认后恢复演讲，计分规则不变')],
            organizer_notice='请确认备件与处理', requires_owner=False, additional_cost_yuan=500)
        apply_response(demo, event.id, response, 'agent')
        self.assertEqual(demo.status, 'awaiting_owner')
        with self.assertRaises(ValueError):
            confirm_event(demo, True)
        confirm_event(demo, True, owner=True)
        self.assertEqual(demo.status, 'awaiting_organizer')
        self.assertEqual(demo.active_schedule[1], '选手演讲及计分')
        confirm_event(demo, True)
        self.assertEqual(demo.active_schedule[0], '签到与设备检查')
        self.assertEqual(demo.original_schedule[1], '选手演讲及计分')
        self.assertIn('设备确认', demo.active_schedule[1])

    def test_even_no_schedule_change_waits_for_organizer_to_verify_site_recovery(self):
        case = managed_case()
        demo = create_activity_order(case, case.activity_review.execution.run_sheet)
        start_activity_order(case)
        event = report_activity_incident(case, 'absence', '一名选手未到场')
        response = JourneyResponse(summary='建议先核实到场状态，不修改赛制', needs_change=False,
            organizer_notice='现场确认后继续', additional_cost_yuan=0)
        apply_response(demo, event.id, response, 'agent')
        self.assertEqual(demo.status, 'awaiting_organizer')
        confirm_event(demo, True)
        self.assertEqual(demo.status, 'running')

    def test_demo_and_real_reports_have_distinct_sources(self):
        case = managed_case()
        demo = create_activity_order(case, case.activity_review.execution.run_sheet)
        start_activity_order(case)
        event = report_activity_incident(case, 'equipment', simulated=True)
        self.assertEqual(event.source, 'simulated_event')
        self.assertEqual(demo.status, 'awaiting_agent')

    def test_coordinator_receives_original_rules_and_manual_current_index(self):
        case = managed_case()
        demo = create_activity_order(case, case.activity_review.execution.run_sheet)
        start_activity_order(case)
        advance_activity_order(case)
        event = report_activity_incident(case, 'delay')
        triage = ExecutionTriage(summary='核实延迟', needs_replan=False, affected_indices=[1])
        response = JourneyResponse(summary='记录延迟，待现场确认衔接', needs_change=False,
            organizer_notice='请现场核查', additional_cost_yuan=0)
        with patch('src.journey.run_structured', new=AsyncMock(side_effect=[triage, response])) as model:
            result, source, _ = asyncio.run(coordinate_response(SimpleNamespace(fulfillment='f', manager='m', planner='p'), demo, event))
        self.assertEqual(source, 'agent')
        self.assertIn('去掉一个最高分', model.call_args_list[0].args[1])
        self.assertIn('当前索引：1', model.call_args_list[0].args[1])
        self.assertEqual(model.await_count, 2)


class DocumentAndOrderPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in (
            ('src.storage.CASE_FILE', Path(self.temp.name) / 'case.json'),
            ('src.organization.ORGANIZATION_FILE', Path(self.temp.name) / 'organization.json'),
            ('src.performance.PERFORMANCE_FILE', Path(self.temp.name) / 'performance.json'),
        ):
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        p = patch('src.agents.build_agent_team', return_value=SimpleNamespace(manager=object(), planner=object(), fulfillment=object()))
        p.start(); self.addCleanup(p.stop)
        self.path = str(Path(__file__).resolve().parents[1] / 'app.py')

    def test_only_missing_count_is_asked_and_answer_does_not_call_model(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_case_to_disk, load_case_from_disk
        save_case_to_disk(document_case())
        app = AppTest.from_file(self.path, default_timeout=30).run()
        app.session_state["customer_case_id"]=load_case_from_disk().id
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual([x.label for x in app.number_input], ['参与总人数'])
        self.assertEqual(len(app.date_input), 0)
        with patch('src.workflow.run_structured', new=AsyncMock()) as model:
            app.number_input[0].set_value(42)
            next(b for b in app.button if b.label == '保存补充信息').click().run()
        model.assert_not_awaited()
        self.assertEqual(load_case_from_disk().requirements.participant_count, 42)
        self.assertEqual(len(app.number_input), 0)
        with patch('src.workflow.run_structured', new=AsyncMock(side_effect=[audit(), execution()])) as model:
            next(b for b in app.button if b.label == '确认信息并继续').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(model.await_count, 2)
        self.assertEqual(load_case_from_disk().service_mode, 'activity_review')

    @patch("src.activity_order.TRACKING_ENABLED", True)
    def test_activity_board_creates_order_and_routes_incident_approval(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_case_to_disk, load_case_from_disk
        save_case_to_disk(managed_case())
        app = AppTest.from_file(self.path, default_timeout=30).run()
        app.session_state["customer_case_id"]=load_case_from_disk().id
        app.run()
        self.assertFalse(app.exception)
        next(c for c in app.checkbox if c.label.startswith('我已核对')).set_value(True)
        next(b for b in app.button if b.label == '确认执行信息并创建管理订单').click().run()
        self.assertFalse(app.exception)
        next(b for b in app.button if b.label == '开始活动').click().run()
        self.assertEqual(load_case_from_disk().journey_demo.status, 'running')
        next(b for b in app.button if b.label == '完成当前环节，进入下一环节').click().run()
        response = JourneyResponse(summary='建议更换备用投影，新增500元待核实', needs_change=True,
            adjustments=[JourneyAdjustment(schedule_index=1, replacement='确认备用设备后按原规则演讲')],
            organizer_notice='请确认', additional_cost_yuan=500)
        with patch('src.activity_order_ui.request_response', return_value=(response, 'agent', '')):
            next(b for b in app.button if b.label == '演示一次投影故障').click().run()
            if load_case_from_disk().journey_demo.status == 'awaiting_agent':
                app.run()
        self.assertFalse(app.exception)
        self.assertEqual(load_case_from_disk().journey_demo.status, 'awaiting_owner')
        app.sidebar.radio[0].set_value('公司老板').run()
        next(s for s in app.selectbox if s.label == '历史案件与未完成进度').set_value(load_case_from_disk().id).run()
        next(b for b in app.button if b.label == '批准建议，交组织者确认').click().run()
        self.assertEqual(load_case_from_disk().journey_demo.status, 'awaiting_organizer')
        app.sidebar.radio[0].set_value('客户').run()
        next(b for b in app.button if b.label == '确认本次处理并继续').click().run()
        self.assertFalse(app.exception)
        saved = load_case_from_disk()
        self.assertEqual(saved.journey_demo.status, 'running')
        self.assertEqual(saved.journey_demo.events[0].source, 'simulated_event')
        self.assertIsNone(saved.procurement_order)

    def test_document_travel_route_preserves_full_source_for_the_planner(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_case_to_disk, load_case_from_disk
        from src.models import CreativeRound
        from test_single_planner import opinion
        case = document_case(20, kind='requirements')
        case.requirements.city = '南京'
        case.document_intake.requirements.city = '南京'
        case.activity_sources[0].text = '南京20人一天；不安排连续两小时步行，必须提供休息替代。'
        save_case_to_disk(case)
        app = AppTest.from_file(self.path, default_timeout=30).run()
        app.session_state["customer_case_id"]=case.id
        app.run()
        generated = CreativeRound(song_ye=opinion('宋野'), shen_zhixing=opinion('沈知行'), xia_cheng=opinion('夏橙'))
        with patch('src.workflow.run_structured', new=AsyncMock(return_value=generated)) as model:
            next(b for b in app.button if b.label == '确认信息并继续').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(model.await_count, 1)
        self.assertIn('连续两小时步行', model.call_args.args[1])
        self.assertEqual(load_case_from_disk().status, CaseStatus.WAITING_STYLE_FEEDBACK)
        self.assertIsNone(load_case_from_disk().journey_demo)


if __name__ == '__main__':
    unittest.main()
