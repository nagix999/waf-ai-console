"""Bounded PDF view of the live Markdown contract, with no external resources.

The Markdown is the same document returned by the JSON endpoint, not a second
API definition. Only its fixed heading/table/fence syntax controls layout;
all text, including administrator field descriptions, is escaped before it
reaches ReportLab. Links and code examples remain inert printed text.
"""
from dataclasses import dataclass
from html import unescape
from io import BytesIO
from pathlib import Path
import re
from xml.sax.saxutils import escape

from .analysis_exports import _FONT_LOCK

MAX_DOCUMENT_BYTES = 512_000
MAX_DOCUMENT_LINES = 12_000
MAX_BLOCKS = 5_000
MAX_TABLE_COLUMNS = 12
MAX_PDF_PAGES = 100
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


class ProductionAPIPDFError(ValueError):
    def __init__(self, code="production_api_pdf_unavailable", status_code=503):
        self.code, self.status_code = code, status_code
        super().__init__(code)


def too_large():
    raise ProductionAPIPDFError("production_api_pdf_too_large", 413)


@dataclass(frozen=True)
class DocumentBlock:
    kind: str
    text: str = ""
    level: int = 0
    language: str = ""
    rows: tuple[tuple[str, ...], ...] = ()


def table_row(line):
    """Split table delimiters, not pipes within inline code or escaped pipes."""
    source = line.strip()
    cells, cell, ticks, separators, index = [], [], 0, 0, 0
    while index < len(source):
        char = source[index]
        if char == "\\" and not ticks and index + 1 < len(source):
            cell.extend(source[index:index + 2])
            index += 2
            continue
        if char == "`":
            end = index + 1
            while end < len(source) and source[end] == "`":
                end += 1
            width = end - index
            if ticks == width:
                ticks = 0
            elif not ticks and re.search(rf"(?<!`){re.escape('`' * width)}(?!`)", source[end:]):
                ticks = width
            cell.append(source[index:end])
            index = end
            continue
        if char == "|" and not ticks:
            cells.append("".join(cell).strip())
            cell = []
            separators += 1
        else:
            cell.append(char)
        index += 1
    cells.append("".join(cell).strip())
    if separators and source.startswith("|") and not cells[0]:
        cells.pop(0)
    if separators and source.endswith("|") and not cells[-1]:
        cells.pop()
    return tuple(cells), separators


def document_blocks(markdown):
    if not isinstance(markdown, str) or not markdown.strip():
        raise ProductionAPIPDFError()
    if len(markdown) > MAX_DOCUMENT_BYTES or len(markdown.encode("utf-8", errors="surrogatepass")) > MAX_DOCUMENT_BYTES:
        too_large()
    lines = [line.removesuffix("\r") for line in markdown.split("\n")]
    if len(lines) > MAX_DOCUMENT_LINES:
        too_large()
    blocks, index = [], 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^`]*)", line)
        heading = re.fullmatch(r" {0,3}(#{1,6})[ \t]+(.+?)[ \t]*", line)
        if fence:
            marker, language = fence.groups()
            index += 1
            code = []
            while index < len(lines) and not re.fullmatch(rf" {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*", lines[index]):
                code.append(lines[index])
                index += 1
            if index == len(lines):
                raise ProductionAPIPDFError()
            blocks.append(DocumentBlock("code", "\n".join(code), language=language.strip()))
            index += 1
        elif heading:
            blocks.append(DocumentBlock("heading", heading[2], level=len(heading[1])))
            index += 1
        else:
            headers, separators = table_row(line)
            delimiter = table_row(lines[index + 1])[0] if index + 1 < len(lines) else ()
            if separators and len(headers) == len(delimiter) and all(re.fullmatch(r":?-{3,}:?", item) for item in delimiter):
                if len(headers) > MAX_TABLE_COLUMNS:
                    too_large()
                rows = [headers]
                index += 2
                while index < len(lines) and lines[index].strip():
                    row, count = table_row(lines[index])
                    if not count:
                        break
                    if len(row) != len(headers):
                        raise ProductionAPIPDFError()
                    rows.append(row)
                    index += 1
                blocks.append(DocumentBlock("table", rows=tuple(rows)))
            else:
                # Keep every source line. Paragraph line wrapping is display
                # only; examples and schema constraints are never shortened.
                blocks.append(DocumentBlock("text", line))
                index += 1
        if len(blocks) > MAX_BLOCKS:
            too_large()
    return tuple(blocks)


class BoundedPDFBuffer(BytesIO):
    limit_exceeded = False

    def write(self, content):
        if self.tell() + len(content) > MAX_DOWNLOAD_BYTES:
            self.limit_exceeded = True
            too_large()
        return super().write(content)


def render_production_api_pdf(markdown: str, metadata: dict) -> bytes:
    page_limit_exceeded = False
    buffer = None
    try:
        blocks = document_blocks(markdown)
        if not blocks or blocks[0].kind != "heading" or blocks[0].level != 1:
            raise ProductionAPIPDFError()
        version = metadata["version_number"]
        if type(version) is not int or version < 1:
            raise ProductionAPIPDFError()

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

        # Share the existing lock and the already packaged Nanum font family.
        # No runtime font discovery, download or administrator-supplied path.
        with _FONT_LOCK:
            for name, filename in (("WAFNanum", "NanumGothic.ttf"), ("WAFNanumBold", "NanumGothicBold.ttf")):
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, str(Path(__file__).parents[1] / "assets/fonts" / filename)))
        glyphs = pdfmetrics.getFont("WAFNanum").face.charToGlyph

        def literal(value, *, spaces=False, entities=False):
            if entities:
                value = unescape(value)
            visible = "".join(char if ord(char) in glyphs and ord(char) >= 32 and not (
                0x7F <= ord(char) <= 0x9F or ord(char) in {0x061C, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF}
                or 0x202A <= ord(char) <= 0x202E or 0x2066 <= ord(char) <= 0x2069
            ) else f"[U+{ord(char):04X}]" for char in value)
            safe = escape(visible)
            return safe.replace(" ", "&#160;") if spaces else safe

        def inline(value):
            # HTML entities from markdown_contract are decoded exactly once,
            # then escaped. No string can introduce a ReportLab markup tag.
            parts = []
            for piece in re.split(r"(`[^`]+`|\*\*[^*]+\*\*)", value):
                if piece.startswith("`") and piece.endswith("`"):
                    parts.append('<font color="#284869">' + literal(piece[1:-1], entities=True) + "</font>")
                elif piece.startswith("**") and piece.endswith("**"):
                    parts.append('<font name="WAFNanumBold">' + literal(piece[2:-2], entities=True) + "</font>")
                else:
                    parts.append(literal(piece, entities=True))
            return "".join(parts)

        ink, muted, line_color = [colors.HexColor(value) for value in ("#17334F", "#5F7083", "#D8E1EA")]
        body = ParagraphStyle("api-body", fontName="WAFNanum", fontSize=9, leading=15,
            textColor=ink, wordWrap="CJK", splitLongWords=True, spaceAfter=7)
        heading = ParagraphStyle("api-heading", parent=body, fontName="WAFNanumBold",
            fontSize=15, leading=22, spaceBefore=17, spaceAfter=10, keepWithNext=True)
        subheading = ParagraphStyle("api-subheading", parent=heading, fontSize=11, leading=17, spaceBefore=12)
        title = ParagraphStyle("api-title", parent=heading, fontSize=23, leading=31, spaceBefore=0, spaceAfter=15)
        table_body = ParagraphStyle("api-table-body", parent=body, fontSize=8, leading=12, spaceAfter=0)
        table_head = ParagraphStyle("api-table-head", parent=table_body, fontName="WAFNanumBold")
        code_style = ParagraphStyle("api-code", parent=body, fontSize=8, leading=12,
            leftIndent=8, rightIndent=8, spaceAfter=0, backColor=colors.HexColor("#F1F5F9"))
        code_label = ParagraphStyle("api-code-label", parent=body, fontSize=7, leading=10,
            textColor=muted, spaceAfter=3, keepWithNext=True)
        buffer = BoundedPDFBuffer()
        width = A4[0] - 88
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=44, leftMargin=44,
            topMargin=48, bottomMargin=48, title="Production WAF Analysis API v0.2.0",
            author="WAF AI Console", pageCompression=1)
        story = []
        for block in blocks:
            if block.kind == "heading":
                style = title if block.level == 1 else heading if block.level == 2 else subheading
                story.append(Paragraph(inline(block.text), style))
                if block.level == 1:
                    story.append(Paragraph(f"전체 운영 API 정의서 · 입력 스키마 v{version}", body))
                    story.append(Paragraph("조회한 시점의 정의서입니다. 예시·주소는 실행하지 않으며, 글꼴이 지원하지 않는 문자와 제어 문자는 [U+코드]로 표시합니다.", code_label))
            elif block.kind == "code":
                story.append(Paragraph(literal(block.language or "TEXT"), code_label))
                for line in block.text.split("\n"):
                    story.append(Paragraph(literal(line, spaces=True) or "&#160;", code_style))
                story.append(Spacer(1, 10))
            elif block.kind == "table":
                columns = len(block.rows[0])
                weights = {2: (.32, .68), 3: (.29, .17, .54), 4: (.36, .12, .26, .26), 5: (.23, .13, .09, .10, .45)}.get(columns, (1 / columns,) * columns)
                rows = [[Paragraph(inline(cell), table_head if index == 0 else table_body) for cell in row]
                        for index, row in enumerate(block.rows)]
                table = LongTable(rows, colWidths=[width * value for value in weights], repeatRows=1,
                    splitByRow=1, splitInRow=1, hAlign="LEFT", spaceBefore=4, spaceAfter=10)
                table.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EFF6")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("GRID", (0, 0), (-1, -1), .35, line_color),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]))
                story.append(table)
            else:
                story.append(Paragraph(inline(block.text), body))
            if len(story) > MAX_DOCUMENT_LINES:
                too_large()

        def page(canvas, document):
            nonlocal page_limit_exceeded
            if document.page > MAX_PDF_PAGES:
                page_limit_exceeded = True
                too_large()
            canvas.saveState()
            canvas.setStrokeColor(line_color)
            canvas.line(44, 37, A4[0] - 44, 37)
            canvas.setFont("WAFNanum", 8)
            canvas.setFillColor(muted)
            canvas.drawString(44, 25, f"운영 API 정의서 · 입력 스키마 v{version}")
            canvas.drawRightString(A4[0] - 44, 25, str(document.page))
            canvas.restoreState()

        doc.build(story, onFirstPage=page, onLaterPages=page)
        return buffer.getvalue()
    except Exception as exc:
        # ReportLab can reconstruct callback exceptions with its own message
        # and omit their status. Preserve limits using trusted local state,
        # never by parsing those potentially document-bearing messages.
        if (page_limit_exceeded or (buffer is not None and buffer.limit_exceeded)
                or (isinstance(exc, ProductionAPIPDFError)
                    and exc.code == "production_api_pdf_too_large" and exc.status_code == 413)):
            raise ProductionAPIPDFError("production_api_pdf_too_large", 413) from None
        # Layout/font errors may contain document fragments. Do not chain or
        # return their text, and do not write a partial download.
        raise ProductionAPIPDFError() from None
