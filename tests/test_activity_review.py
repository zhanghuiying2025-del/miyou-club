import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.activity_review import award_budget_checks, checked_facts, read_activity_documents, review_activity
from src.models import (
    ActivityAudit, ActivityExecutionPlan, ActivityFact, ActivityReviewReport,
    ActivitySource, ActivityTask, CaseStatus, TravelCase,
)
from src.workflow import TravelAgencyWorkflow
from src.activity_review_ui import report_markdown
from test_document_uploads import word_file, paragraph


SOURCE = """活动主题：青春与岗位担当
比赛时间：2026年4月23日下午；公司五楼大会议室。
演讲时长5-7分钟，采用10分制，去掉一个最高分、一个最低分后取平均分。
一等奖：1名，颁发荣誉证书+奖金1200元
二等奖：2名，颁发荣誉证书+奖金800元
三等奖：3名，颁发荣誉证书+奖金500元
（相关费用由公司行政支出总计4600元）"""


def audit():
    return ActivityAudit(event_name="青年讲演比赛", summary="保留原活动，核对筹备缺项",
        facts=[ActivityFact(label="地点", value="公司五楼大会议室", source_quote="公司五楼大会议室")])


def execution():
    return ActivityExecutionPlan(summary="按原活动细化执行，不另做旅行",
        tasks=[ActivityTask(phase="筹备", task="建议核实参赛和评委人数", suggested_owner="建议会务组",
            timing="报名结束后，具体时间待确认", deliverable="经组织者确认的名单")],
        run_sheet=["原方案：设备调试、签到、开场、致辞、演讲、计分、颁奖、收尾"],
        organizer_questions=["是否沿用原日期？", "参赛人数和评委人数分别是多少？"])


def activity_case():
    return TravelCase(customer_request="审核已有方案", service_mode="activity_review",
        activity_sources=[ActivitySource(file_name="讲演.docx", text=SOURCE)])


class ActivityReviewTests(unittest.TestCase):
    def test_budget_reconciliation_reports_unexplained_difference_without_changing_total(self):
        result = award_budget_checks(SOURCE)
        self.assertEqual(len(result), 1)
        self.assertIn("4300元", result[0].issue)
        self.assertIn("4600元", result[0].issue)
        self.assertIn("300元", result[0].issue)
        self.assertIn("不等于总额算错", result[0].suggestion)
        self.assertEqual(award_budget_checks(SOURCE.replace('4600元', '4300元')), [])

    def test_unknown_amounts_do_not_create_budget_findings(self):
        self.assertEqual(award_budget_checks("奖项和总预算待定"), [])

    def test_fact_with_nonexistent_quote_is_not_presented_as_verified(self):
        result = audit()
        result.facts.append(ActivityFact(label="人数", value="30人", source_quote="30人参加"))
        checked_facts(result, SOURCE)
        self.assertEqual(len(result.facts), 1)
        self.assertIn("原文引用未匹配", result.findings[0].issue)

    def test_sources_keep_full_text_including_late_rules(self):
        data = word_file(paragraph(SOURCE))
        source = read_activity_documents([('讲演.DOCX', 'application/octet-stream', data)])[0]
        self.assertIn('去掉一个最高分', source.text)
        self.assertIn('4600元', source.text)
        with self.assertRaisesRegex(ValueError, '没有可读取'):
            read_activity_documents([('空.docx', '', word_file(''))])
        with self.assertRaisesRegex(ValueError, '1至3'):
            read_activity_documents([])

    def test_two_roles_review_full_document_without_travel_requirements(self):
        workflow = SimpleNamespace(_call=AsyncMock(side_effect=[audit(), execution()]))
        case = activity_case()
        asyncio.run(review_activity(workflow, case, notes='保留比赛形式'))
        self.assertEqual([call.args[1] for call in workflow._call.call_args_list], ['manager', 'planner'])
        for call in workflow._call.call_args_list:
            self.assertIn(SOURCE, call.args[2])
            self.assertIn('保留比赛形式', call.args[2])
            self.assertIn('不要凭空增加人数', call.args[2])
        self.assertIsNone(case.requirements)
        self.assertEqual(case.creative_opinions, [])
        self.assertIsNone(case.procurement_order)
        self.assertEqual(case.status, CaseStatus.READY_FOR_CUSTOMER)
        self.assertIn('300元', case.activity_review.program_checks[0].issue)

    def test_retry_preserves_previous_report_when_model_fails(self):
        old = ActivityReviewReport(audit=audit(), execution=execution())
        case = activity_case()
        case.activity_review = old
        workflow = SimpleNamespace(_call=AsyncMock(side_effect=[audit(), RuntimeError('模拟失败')]))
        with self.assertRaisesRegex(RuntimeError, '模拟失败'):
            asyncio.run(review_activity(workflow, case))
        self.assertIs(case.activity_review, old)
        self.assertTrue(case.activity_sources)

    def test_revision_keeps_history_and_passes_new_information_to_both_roles(self):
        case = activity_case()
        case.activity_review = ActivityReviewReport(audit=audit(), execution=execution())
        case.activity_review_notes = '5位评委'
        workflow = SimpleNamespace(_call=AsyncMock(side_effect=[audit(), execution()]))
        asyncio.run(review_activity(workflow, case, notes='12位选手，300元用于证书'))
        self.assertEqual(len(case.activity_review_history), 1)
        self.assertIn('5位评委', case.activity_review_notes)
        self.assertIn('12位选手', workflow._call.call_args_list[0].args[2])

    def test_booking_order_cannot_be_repurposed_as_review(self):
        case = activity_case()
        case.procurement_order = SimpleNamespace()
        workflow = SimpleNamespace(_call=AsyncMock())
        with self.assertRaisesRegex(ValueError, '不能切换'):
            asyncio.run(review_activity(workflow, case))
        workflow._call.assert_not_awaited()

    def test_saved_legacy_case_has_no_event_mode_and_report_round_trips(self):
        self.assertEqual(TravelCase(customer_request='旧旅行案件').service_mode, 'travel_planning')
        case = activity_case()
        case.activity_review = ActivityReviewReport(audit=audit(), execution=execution())
        restored = TravelCase.model_validate_json(case.model_dump_json())
        self.assertEqual(restored.activity_sources[0].text, SOURCE)
        self.assertIn('尚未派发', report_markdown(restored.activity_review))

    def test_missing_source_stops_before_any_model_request(self):
        workflow = SimpleNamespace(_call=AsyncMock())
        with self.assertRaisesRegex(ValueError, '先上传'):
            asyncio.run(review_activity(workflow, TravelCase(customer_request='空资料')))
        workflow._call.assert_not_awaited()


class ActivityReviewPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in (
            ('src.storage.CASE_FILE', Path(self.temp.name) / 'case.json'),
            ('src.organization.ORGANIZATION_FILE', Path(self.temp.name) / 'organization.json'),
            ('src.performance.PERFORMANCE_FILE', Path(self.temp.name) / 'performance.json'),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch('src.agents.build_agent_team', return_value=SimpleNamespace(manager=object(), planner=object(), fulfillment=object()))
        p.start()
        self.addCleanup(p.stop)
        self.path = str(Path(__file__).resolve().parents[1] / 'app.py')

    def test_new_service_does_not_require_trip_form(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(self.path, default_timeout=30).run()
        next(r for r in app.radio if r.label == '需求类型').set_value('上传已有资料').run()
        self.assertFalse(app.exception)
        self.assertFalse(any('活动城市' in t.label for t in app.text_input))
        self.assertEqual(set(app.get('file_uploader')[0].proto.type), {'.docx', '.pdf'})

    def test_review_retry_runs_two_roles_and_persists_after_refresh(self):
        from streamlit.testing.v1 import AppTest
        from src.storage import save_case_to_disk, load_case_from_disk
        save_case_to_disk(activity_case())
        app = AppTest.from_file(self.path, default_timeout=30).run()
        app.session_state["customer_case_id"]=load_case_from_disk().id
        app.run()
        self.assertFalse(app.exception)
        with patch('src.workflow.run_structured', new=AsyncMock(side_effect=[audit(), execution()])) as model:
            next(b for b in app.button if b.label == '重试审核').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(model.await_count, 2)
        restored = load_case_from_disk()
        self.assertIsNotNone(restored.activity_review)
        self.assertEqual([r.receiver for r in restored.handoffs], ['项目经理', '执行策划'])
        app.run()
        self.assertTrue(any('300元' in warning.value for warning in app.warning))
        self.assertFalse(any('预付款' in b.label for b in app.button))
        next(b for b in app.button if b.label == '确认收到审核与执行清单').click().run()
        self.assertEqual(load_case_from_disk().status, CaseStatus.COMPLETED)
        app.sidebar.radio[0].set_value('公司老板').run()
        self.assertFalse(app.exception)
        self.assertFalse(any('授权模拟供应商支出' == b.label for b in app.button))


if __name__ == '__main__':
    unittest.main()
