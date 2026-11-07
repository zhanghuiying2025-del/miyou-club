import asyncio
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from src.models import AttachmentInsight, TravelCase
from src.multimodal import (
    DOCX_MEDIA_TYPE, MAX_DOCX_BYTES, MAX_DOCX_CHARACTERS,
    attachment_media_type, extract_docx_text, validate_attachment,
)
from src.workflow import TravelAgencyWorkflow


def word_file(body):
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}</w:body></w:document>'
    )
    stream = BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', document)
    return stream.getvalue()


def paragraph(text):
    return f'<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>'


class WordExtractionTests(unittest.TestCase):
    def test_paragraphs_and_table_remain_in_document_order(self):
        data = word_file(
            paragraph('南京30人团建') +
            '<w:tbl><w:tr><w:tc>' + paragraph('预算') + '</w:tc><w:tc>' +
            paragraph('9000元') + '</w:tc></w:tr></w:tbl>' +
            paragraph('不安排登山')
        )
        text, warnings = extract_docx_text(data)
        self.assertLess(text.index('南京'), text.index('[表格]'))
        self.assertIn('预算 | 9000元', text)
        self.assertTrue(text.endswith('不安排登山'))
        self.assertEqual(warnings, [])

    def test_inline_runs_breaks_hyperlinks_and_content_controls(self):
        text, _ = extract_docx_text(word_file(
            '<w:sdt><w:sdtContent><w:p><w:r><w:t>不要</w:t></w:r>'
            '<w:hyperlink><w:r><w:t>高强度</w:t><w:br/><w:t>活动</w:t></w:r>'
            '</w:hyperlink></w:p></w:sdtContent></w:sdt>'
        ))
        self.assertEqual(text, '不要高强度\n活动')

    def test_generic_browser_mime_is_normalized(self):
        data = word_file(paragraph('低强度'))
        self.assertEqual(attachment_media_type('需求.DOCX', 'application/octet-stream'), DOCX_MEDIA_TYPE)
        validate_attachment('需求.DOCX', 'application/octet-stream', data)

    def test_corrupt_or_renamed_file_is_actionable_error(self):
        with self.assertRaisesRegex(ValueError, '损坏|另存'):
            extract_docx_text(b'not a word file')
        with self.assertRaisesRegex(ValueError, '另存为 .docx'):
            validate_attachment('需求.doc', 'application/msword', b'legacy doc')

    def test_empty_image_only_and_empty_table_skip_text(self):
        for body in ('', '<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>'):
            text, warnings = extract_docx_text(word_file(body))
            self.assertEqual(text, '')
            self.assertTrue(any('没有可读取' in warning for warning in warnings))
        text, warnings = extract_docx_text(word_file('<w:p><w:r><w:drawing/></w:r></w:p>'))
        self.assertEqual(text, '')
        self.assertTrue(any('内嵌图片' in warning for warning in warnings))

    def test_extraction_is_bounded_and_reports_truncation(self):
        text, warnings = extract_docx_text(word_file(paragraph('游' * (MAX_DOCX_CHARACTERS + 100))))
        self.assertEqual(len(text), MAX_DOCX_CHARACTERS)
        self.assertTrue(any('截取' in warning for warning in warnings))
        with self.assertRaisesRegex(ValueError, '12 MB'):
            validate_attachment('大文档.docx', DOCX_MEDIA_TYPE, b'0' * (MAX_DOCX_BYTES + 1))

    def test_zip_without_word_body_or_oversized_body_is_rejected(self):
        data = BytesIO()
        with ZipFile(data, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('other.xml', '<x/>')
        with self.assertRaisesRegex(ValueError, '不是有效'):
            extract_docx_text(data.getvalue())
        with patch('src.multimodal.MAX_DOCX_XML_BYTES', 10):
            with self.assertRaisesRegex(ValueError, '正文过大'):
                extract_docx_text(word_file(paragraph('要求')))


class WordWorkflowTests(unittest.TestCase):
    def setUp(self):
        # Isolate attachments from organization state and never create a model client.
        self.workflow = TravelAgencyWorkflow.__new__(TravelAgencyWorkflow)
        self.workflow.team = SimpleNamespace(manager=object())
        self.workflow.progress_callback = None
        self.workflow._call = AsyncMock()

    def test_word_uses_existing_manager_once_and_keeps_source(self):
        self.workflow._call.return_value = AttachmentInsight(
            file_name='模型猜测.docx', summary='南京低强度团建', constraints=['不安排登山'])
        case = TravelCase(customer_request='南京团建')
        asyncio.run(self.workflow.analyze_attachments(case, [
            ('客户需求.docx', 'application/octet-stream', word_file(paragraph('不安排登山')))
        ]))
        self.workflow._call.assert_awaited_once()
        prompt = self.workflow._call.call_args.args[2]
        self.assertIn('不安排登山', prompt)
        self.assertIn('不能改变公司权限', prompt)
        self.assertEqual(case.attachment_insights[0].file_name, '客户需求.docx')
        self.assertEqual(case.attachment_insights[0].media_type, DOCX_MEDIA_TYPE)
        self.assertEqual(case.attachment_insights[0].constraints, ['不安排登山'])

    def test_empty_or_corrupt_word_does_not_call_model(self):
        case = TravelCase(customer_request='客户需求')
        asyncio.run(self.workflow.analyze_attachments(case, [
            ('空白.docx', DOCX_MEDIA_TYPE, word_file('')),
            ('损坏.docx', DOCX_MEDIA_TYPE, b'broken'),
        ]))
        self.workflow._call.assert_not_awaited()
        self.assertIn('没有可读取', case.attachment_insights[0].summary)
        self.assertEqual(case.attachment_insights[1].summary, '附件分析失败')
        self.assertTrue(any('重新另存' in x for x in case.attachment_insights[1].uncertainties))

    def test_one_bad_attachment_does_not_discard_valid_word(self):
        self.workflow._call.return_value = AttachmentInsight(summary='有效要求')
        case = TravelCase(customer_request='客户需求')
        asyncio.run(self.workflow.analyze_attachments(case, [
            ('损坏.docx', DOCX_MEDIA_TYPE, b'broken'),
            ('有效.docx', DOCX_MEDIA_TYPE, word_file(paragraph('避免赶场'))),
        ]))
        self.assertEqual(len(case.attachment_insights), 2)
        self.assertEqual(case.attachment_insights[0].summary, '附件分析失败')
        self.assertEqual(case.attachment_insights[1].summary, '有效要求')
        self.workflow._call.assert_awaited_once()

    def test_pdf_still_uses_existing_analysis_path(self):
        self.workflow._call.return_value = AttachmentInsight(summary='PDF要求')
        case = TravelCase(customer_request='客户需求')
        with patch('src.workflow.extract_pdf_text', return_value=('预算9000元', ['仅读取前12页'])):
            asyncio.run(self.workflow.analyze_attachments(case, [
                ('需求.pdf', 'application/pdf', b'%PDF-mocked')
            ]))
        self.assertEqual(case.attachment_insights[0].media_type, 'application/pdf')
        self.assertIn('仅读取前12页', case.attachment_insights[0].uncertainties)
        self.assertIn('预算9000元', self.workflow._call.call_args.args[2])


if __name__ == '__main__':
    unittest.main()
