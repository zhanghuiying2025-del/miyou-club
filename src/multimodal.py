from __future__ import annotations

from io import BytesIO
from pathlib import PurePath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader


SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 12
MAX_PDF_CHARACTERS = 24_000
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_DOCX_BYTES = 12 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 48 * 1024 * 1024
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
MAX_DOCX_CHARACTERS = 24_000


def attachment_media_type(file_name: str, media_type: str) -> str:
    """Browsers sometimes send DOCX as ZIP or application/octet-stream."""
    if PurePath(file_name).suffix.lower() == ".docx":
        return DOCX_MEDIA_TYPE
    return media_type


def validate_attachment(file_name: str, media_type: str, data: bytes) -> None:
    media_type = attachment_media_type(file_name, media_type)
    if media_type in SUPPORTED_IMAGE_TYPES:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"图片 {file_name} 超过 8 MB")
        return

    if media_type == "application/pdf":
        if len(data) > MAX_PDF_BYTES:
            raise ValueError(f"PDF {file_name} 超过 12 MB")
        return

    if media_type == DOCX_MEDIA_TYPE:
        if len(data) > MAX_DOCX_BYTES:
            raise ValueError(f"Word文档 {file_name} 超过 12 MB")
        return

    if PurePath(file_name).suffix.lower() == ".doc":
        raise ValueError("暂不支持旧版 .doc，请在Word中另存为 .docx 后上传")

    raise ValueError(f"不支持的附件格式：{file_name}")


def extract_pdf_text(data: bytes) -> tuple[str, list[str]]:
    reader = PdfReader(BytesIO(data))
    page_count = len(reader.pages)
    warnings: list[str] = []
    text_parts: list[str] = []

    for page_number, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        page_text = (page.extract_text() or "").strip()
        if page_text:
            text_parts.append(f"[第{page_number}页]\n{page_text}")

    text = "\n\n".join(text_parts)
    if page_count > MAX_PDF_PAGES:
        warnings.append(f"PDF共{page_count}页，目前只读取前{MAX_PDF_PAGES}页")
    if len(text) > MAX_PDF_CHARACTERS:
        text = text[:MAX_PDF_CHARACTERS]
        warnings.append("PDF文字较长，已截取前24000个字符")
    if not text:
        warnings.append("PDF没有可提取文字，可能是扫描件")

    return text, warnings


def extract_docx_text(data: bytes) -> tuple[str, list[str]]:
    """Read visible body paragraphs and table cells without executing Word content."""
    if len(data) > MAX_DOCX_BYTES:
        raise ValueError("Word文档超过 12 MB")
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(x.file_size for x in entries) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise ValueError("Word文档解压后内容过大，请精简文档后重试")
            if any(x.flag_bits & 1 for x in entries):
                raise ValueError("无法读取加密Word文档，请取消密码保护后上传")
            try:
                document = archive.getinfo("word/document.xml")
            except KeyError as exc:
                raise ValueError("不是有效的 .docx 文档，请在Word中重新另存后上传") from exc
            if document.file_size > MAX_DOCX_XML_BYTES:
                raise ValueError("Word正文过大，请拆分文档后上传")
            xml = archive.read(document)
        # Uploaded files are data; DTD/entity definitions are not needed for DOCX.
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise ValueError("Word文档包含不支持的XML定义，请重新另存后上传")
        root = ElementTree.fromstring(xml)
    except (BadZipFile, ElementTree.ParseError, RuntimeError, NotImplementedError, OSError) as exc:
        raise ValueError("Word文档损坏、已加密或不是 .docx 格式，请重新另存后上传") from exc

    namespace = root.tag.removeprefix("{").split("}")[0]
    if namespace not in {
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    } or root.tag != f"{{{namespace}}}document":
        raise ValueError("无法识别Word正文，请重新另存为 .docx 后上传")
    tag = lambda name: f"{{{namespace}}}{name}"
    body = root.find(tag("body"))
    if body is None:
        raise ValueError("Word文档缺少正文，请重新另存后上传")

    def paragraph_text(paragraph):
        return "".join(
            (node.text or "") if node.tag == tag("t") else
            "\t" if node.tag == tag("tab") else "\n"
            for node in paragraph.iter()
            if node.tag in {tag("t"), tag("tab"), tag("br"), tag("cr")}
        ).strip()

    def blocks(parent):
        for block in parent:
            if block.tag == tag("p"):
                yield paragraph_text(block)
            elif block.tag == tag("tbl"):
                has_rows = False
                for row in block.findall(tag("tr")):
                    cells = [" / ".join(filter(None, (
                        paragraph_text(p) for p in cell.iter(tag("p"))
                    ))) for cell in row.findall(tag("tc"))]
                    if any(cells):
                        if not has_rows:
                            yield "[表格]"
                            has_rows = True
                        yield " | ".join(cells)
            else:
                # Includes paragraphs inside Word content controls.
                yield from blocks(block)

    warnings: list[str] = []
    parts: list[str] = []
    length = 0
    for content in blocks(body):
        if not content:
            continue
        length += len(content) + (2 if parts else 0)
        parts.append(content)
        if length > MAX_DOCX_CHARACTERS:
            warnings.append("Word文字较长，已截取前24000个字符，请核对末尾未读取的要求")
            break
    text = "\n\n".join(parts)[:MAX_DOCX_CHARACTERS]
    if any(node.tag in {tag("drawing"), tag("pict")} for node in body.iter()):
        warnings.append("Word内嵌图片暂未识别；图片中的要求请补充文字或单独上传图片")
    if not text:
        warnings.append("Word没有可读取的正文文字，可能为空文档或仅包含图片")
    return text, warnings

