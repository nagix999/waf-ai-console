"""Bounded, offline PDF layout for the shared, normalized report document.

All dynamic values are literal text. No browser, links, images or network IO.
"""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_DIR = Path(__file__).resolve().parents[1] / "assets/fonts"
MAX_INPUT_BYTES = 512_000
MAX_BLOCKS = 1000
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 80
WIDTH = A4[0] - 100  # 44 pt margins + 6 pt frame padding on each side.
FRAME_HEIGHT = A4[1] - 60 - 55 - 12
INK = colors.HexColor("#243640")
MUTED = colors.HexColor("#607078")
ACCENT = colors.HexColor("#147D64")
LINE = colors.HexColor("#DEE7E4")
PAPER = colors.HexColor("#F4F8F6")
GROUPS = {
    "정탐 근거": ("#B94235", "#FCF2EF"),
    "오탐 근거": ("#16775E", "#EEF8F3"),
    "참고 내용": ("#56667B", "#F1F4F8"),
    "구분 미기록": ("#56667B", "#F1F4F8"),
}


class ReportLayoutError(ValueError):
    """Stable error codes only, never include report contents in errors."""


class LimitedBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_PDF_BYTES:
            raise ReportLayoutError("report_pdf_too_large")
        return super().write(data)


def register_fonts():
    for name, file in (("ReportRegular", "NanumGothic.ttf"), ("ReportBold", "NanumGothicBold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))


def literal(value: str, *, preserve_spaces=False) -> str:
    # The report is untrusted text, never ReportLab markup, links or images.
    glyphs = pdfmetrics.getFont("ReportRegular").face.charToGlyph
    text = "".join(
        character if character in "\n\t" or (ord(character) >= 32 and ord(character) in glyphs)
        else f"[U+{ord(character):04X}]"
        for character in value
    )
    text = escape(text).replace("\t", "&#160;" * 4)
    if preserve_spaces:
        text = text.replace(" ", "&#160;")
    return text.replace("\n", "<br/>")


def palette(theme):
    if theme == "dark":
        return {key: colors.HexColor(value) for key, value in {
            "ink": "#E2EBE8", "muted": "#A9BCB6", "accent": "#77CFB6",
            "line": "#3D514A", "paper": "#243B33", "page": "#182923",
        }.items()}
    return {"ink": INK, "muted": MUTED, "accent": ACCENT, "line": LINE,
            "paper": PAPER, "page": colors.white}


def styles(theme="light"):
    tone = palette(theme)
    base = ParagraphStyle("body", fontName="ReportRegular", fontSize=9, leading=14,
                          textColor=tone["ink"], wordWrap="CJK", splitLongWords=True,
                          spaceAfter=7, allowWidows=0, allowOrphans=0, alignment=TA_LEFT)
    return {
        "palette": tone, "theme": theme,
        "body": base,
        "intro": ParagraphStyle("intro", parent=base, fontSize=8, leading=12, textColor=tone["muted"], spaceAfter=3),
        "title": ParagraphStyle("title", parent=base, fontName="ReportBold", fontSize=25, leading=33, spaceAfter=10, keepWithNext=True),
        "section": ParagraphStyle("section", parent=base, fontName="ReportBold", fontSize=13, leading=19, spaceBefore=17, spaceAfter=9, keepWithNext=True),
        "sub": ParagraphStyle("sub", parent=base, fontName="ReportBold", fontSize=10, leading=15, spaceBefore=10, spaceAfter=6, keepWithNext=True),
        "label": ParagraphStyle("label", parent=base, fontSize=8, leading=12, textColor=tone["muted"], spaceAfter=5, keepWithNext=True),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=13, spaceAfter=0),
        "key": ParagraphStyle("key", parent=base, fontName="ReportBold", fontSize=8, leading=13, textColor=tone["muted"], spaceAfter=0),
        "code": ParagraphStyle("code", parent=base, fontSize=8, leading=12, backColor=tone["paper"],
                               borderPadding=9, leftIndent=10, rightIndent=10,
                               spaceBefore=9, spaceAfter=15),
    }


def paragraph(text, style, *, code=False):
    return Paragraph(literal(text, preserve_spaces=code), style)


class EvidenceCard(Table):
    """A group with a repeated heading and splittable evidence rows."""

    def split(self, availWidth, availHeight):
        self._calc(availWidth, availHeight)
        first_item_height = sum(self._rowHeights[:2])
        # Keep a normal-sized evidence item intact. Only very long items may
        # split internally, with enough room for the heading and some content.
        if availHeight < min(first_item_height, 170):
            return []
        if availHeight < first_item_height <= FRAME_HEIGHT:
            return []
        parts = super().split(availWidth, availHeight)
        title = getattr(self, "group_title", None)
        if title and len(parts) > 1:
            for part in parts:
                part.group_title = title
                part.group_heading_style = self.group_heading_style
            # ReportLab shares header rows when repeating them. Copy before
            # changing the continuation label so the first heading stays intact.
            following = parts[1]
            following._cellvalues = list(following._cellvalues)
            following._cellvalues[0] = [paragraph(f"{title} · 계속", self.group_heading_style)]
            # Don't fit a second tiny continuation card into the same page's
            # remaining space. A repeated heading belongs on the next page.
            return [parts[0], PageBreak(), following]
        return parts


def evidence_card(title, rows, sheet):
    tone = sheet["palette"]
    foreground, background = map(colors.HexColor, GROUPS[title])
    if sheet["theme"] == "dark":
        foreground, background = map(colors.HexColor, {
            "정탐 근거": ("#FFA899", "#49332E"),
            "오탐 근거": ("#8EDBC2", "#254339"),
        }.get(title, ("#BFCDE1", "#2D3D4C")))
    border = colors.Color(*[(component + 3 * base) / 4 for component, base in zip(
                            (foreground.red, foreground.green, foreground.blue),
                            (tone["page"].red, tone["page"].green, tone["page"].blue))])
    heading_style = ParagraphStyle("card-heading", parent=sheet["sub"],
                                   textColor=foreground, fontSize=11, leading=16,
                                   spaceBefore=0, spaceAfter=0)
    card = EvidenceCard([[paragraph(title, heading_style)], *[[row] for row in rows]],
                        colWidths=[WIDTH], repeatRows=1, splitByRow=1, splitInRow=1,
                        hAlign="LEFT", spaceBefore=10, spaceAfter=16)
    card.group_title = title
    card.group_heading_style = heading_style
    card.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), tone["page"]),
        ("BACKGROUND", (0, 0), (-1, 0), background),
        ("BOX", (0, 0), (-1, -1), 0.6, border),
        ("LINEBEFORE", (0, 0), (0, -1), 3, foreground),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, border),
        ("LINEABOVE", (0, 2), (-1, -1), 0.4, tone["line"]),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return card


def evidence_groups(blocks):
    """Group only explicit level-3 support headings; don't infer direction."""
    title, children = None, []
    for block in blocks:
        boundary = block["type"] == "heading" and block.get("level", 0) <= 3
        if boundary and title is not None:
            yield title, children
            title, children = None, []
        if boundary and block.get("level") == 3 and block["text"] in GROUPS:
            title = block["text"]
        elif title is not None:
            children.append(block)
        else:
            yield None, [block]
    if title is not None:
        yield title, children


def badge_style(value, parent):
    background = {
        "CRITICAL": "#B4233B", "HIGH": "#A6382B", "MEDIUM": "#885A12", "LOW": "#2864A6",
        "정탐 (true_positive)": "#A6382B", "오탐 (false_positive)": "#16775E",
        "판단 보류 (inconclusive)": "#805713",
    }.get(value)
    if not background:
        return parent, None
    return ParagraphStyle("badge", parent=parent, fontName="ReportBold", textColor=colors.white), colors.HexColor(background)


def report_table(block, sheet, *, compact=False, width=WIDTH):
    tone = sheet["palette"]
    headers, rows = block["headers"], block["rows"]
    columns = len(headers)
    key_value = headers == ["항목", "내용"]
    if compact and key_value and rows:
        # Short metadata only: two label/value pairs per line, no omitted fields.
        pairs = []
        for start in range(0, len(rows), 2):
            row = rows[start] + (rows[start + 1] if start + 1 < len(rows) else ["", ""])
            pairs.append([paragraph(text, sheet["key" if index % 2 == 0 else "cell"])
                          for index, text in enumerate(row)])
        table = Table(pairs, colWidths=[78, width / 2 - 78] * 2, splitByRow=1,
                      splitInRow=1, hAlign="LEFT", spaceAfter=9)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, -1), tone["paper"]),
            ("BACKGROUND", (2, 0), (2, -1), tone["paper"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, tone["line"]),
        ]))
        return table
    data = []
    formatting = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, tone["line"]),
    ]
    if not key_value:
        data.append([paragraph(text, sheet["key"]) for text in headers])
        formatting.append(("BACKGROUND", (0, 0), (-1, 0), tone["paper"]))
    for row in rows:
        cells = []
        row_index = len(data)
        for index, text in enumerate(row):
            style = sheet["key"] if key_value and index == 0 else sheet["cell"]
            if key_value and index == 1 and row[0] in {"판정", "위협 심각도"}:
                style, background = badge_style(text, style)
                if background:
                    formatting.append(("BACKGROUND", (index, row_index), (index, row_index), background))
            cells.append(paragraph(text, style))
        data.append(cells)
    if not data:
        return Spacer(1, 1)
    if key_value:
        formatting.append(("BACKGROUND", (0, 0), (0, -1), tone["paper"]))
    table = Table(data, colWidths=[113, width - 113] if key_value else [width / columns] * columns,
                  repeatRows=0 if key_value else 1, splitByRow=1, splitInRow=1, hAlign="LEFT",
                  spaceAfter=9)
    table.setStyle(TableStyle(formatting))
    return table


def validate_document(document):
    if not isinstance(document, dict) or not isinstance(document.get("title"), str):
        raise ReportLayoutError("report_invalid_document")
    if len(json.dumps(document, ensure_ascii=False).encode()) > MAX_INPUT_BYTES:
        raise ReportLayoutError("report_input_too_large")
    if not isinstance(document.get("intro"), list) or not isinstance(document.get("sections"), list):
        raise ReportLayoutError("report_invalid_document")
    blocks = list(document["intro"])
    for section in document["sections"]:
        if not isinstance(section, dict) or not isinstance(section.get("title"), str) or not isinstance(section.get("blocks"), list):
            raise ReportLayoutError("report_invalid_document")
        blocks.extend(section["blocks"])
    count = len(blocks) + len(document["sections"])
    for block in blocks:
        if not isinstance(block, dict):
            raise ReportLayoutError("report_invalid_block")
        kind = block.get("type")
        if kind in {"paragraph", "heading", "code"}:
            if not isinstance(block.get("text"), str):
                raise ReportLayoutError("report_invalid_block")
            if kind == "heading" and (type(block.get("level")) is not int or not 1 <= block["level"] <= 6):
                raise ReportLayoutError("report_invalid_block")
        elif kind == "list":
            if (not isinstance(block.get("ordered", False), bool)
                    or type(block.get("start", 1)) is not int
                    or not 0 <= block.get("start", 1) <= 1_000_000):
                raise ReportLayoutError("report_invalid_block")
            if not isinstance(block.get("items"), list) or not all(isinstance(item, str) for item in block["items"]):
                raise ReportLayoutError("report_invalid_block")
            count += len(block["items"])
        elif kind == "table":
            headers, rows = block.get("headers"), block.get("rows")
            if (not isinstance(headers, list) or not 1 <= len(headers) <= 8
                    or not all(isinstance(item, str) for item in headers) or not isinstance(rows, list)
                    or not all(isinstance(row, list) and len(row) == len(headers)
                               and all(isinstance(item, str) for item in row) for row in rows)):
                raise ReportLayoutError("report_invalid_table")
            count += len(rows)
        else:
            raise ReportLayoutError("report_unknown_block")
    if count > MAX_BLOCKS:
        raise ReportLayoutError("report_too_many_blocks")


class ReportDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        bookmark = getattr(flowable, "bookmark", None)
        if bookmark:
            key, title = bookmark
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level=0)


def render_pdf(document, *, theme="light"):
    if theme not in {"light", "dark"}:
        raise ReportLayoutError("report_invalid_theme")
    validate_document(document)
    register_fonts()
    sheet = styles(theme)
    tone = sheet["palette"]
    story = [paragraph(document["title"], sheet["title"])]

    card_sheet = dict(sheet)
    card_sheet["sub"] = ParagraphStyle("card-item", parent=sheet["sub"], spaceBefore=0)
    card_sheet["code"] = ParagraphStyle("card-code", parent=sheet["code"], backColor=tone["paper"])

    def add_block(block, intro=False, compact=False, target=None, grouped=False):
        output = story if target is None else target
        block_sheet = card_sheet if grouped else sheet
        kind = block["type"]
        if kind == "table":
            output.append(report_table(block, block_sheet, compact=compact, width=WIDTH - 28 if grouped else WIDTH))
        elif kind == "list":
            for index, item in enumerate(block["items"]):
                prefix = f"{block.get('start', 1) + index}. " if block.get("ordered") else "• "
                output.append(paragraph(prefix + item, block_sheet["body"]))
        elif kind == "heading":
            output.append(paragraph(block["text"], block_sheet["sub"]))
        elif kind == "code":
            output.append(paragraph(block["text"], block_sheet["code"], code=True))
        else:
            text = block["text"]
            label = text in {"로그 발췌:", "판단 이유:", "원본 문자열:", "변환 결과:"} or text.startswith("위치: ")
            output.append(paragraph(text, block_sheet["intro" if intro else "label" if label else "body"]))

    for block in document["intro"]:
        add_block(block, intro=True)
    for number, section in enumerate(document["sections"], 1):
        heading = paragraph(f"{number:02d}   {section['title']}", sheet["section"])
        heading.bookmark = (f"section-{number}", section["title"])
        story.append(heading)
        groups = evidence_groups(section["blocks"]) if section["title"] == "판정 근거" else [(None, section["blocks"])]
        for group_title, blocks in groups:
            if group_title is None:
                for block in blocks:
                    add_block(block, compact=section["title"] in {"분석 개요", "소요 시간"})
                continue
            rows, current = [], []
            for block in blocks:
                if block["type"] == "heading" and block.get("level") == 4 and current:
                    rows.append(current)
                    current = []
                add_block(block, target=current, grouped=True)
            if current:
                rows.append(current)
            story.append(evidence_card(group_title, rows or [[Spacer(1, 1)]], sheet))

    def furniture(canvas, doc):
        if doc.page > MAX_PAGES:
            raise ReportLayoutError("report_too_many_pages")
        canvas.saveState()
        canvas.setFillColor(tone["page"])
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        canvas.setStrokeColor(tone["accent"])
        canvas.setLineWidth(2)
        canvas.line(44, A4[1] - 30, A4[0] - 44, A4[1] - 30)
        canvas.setFont("ReportBold", 7)
        canvas.setFillColor(tone["accent"])
        canvas.drawString(44, A4[1] - 45, "WAF AI  /  ANALYSIS REPORT")
        canvas.setFillColor(tone["muted"])
        canvas.setFont("ReportRegular", 7)
        canvas.drawRightString(A4[0] - 44, A4[1] - 45, "분석 보고서")
        canvas.setStrokeColor(tone["line"])
        canvas.setLineWidth(0.5)
        canvas.line(44, 40, A4[0] - 44, 40)
        canvas.drawString(44, 26, "WAF AI Console · 분석 보고서")
        canvas.drawRightString(A4[0] - 44, 26, f"{doc.page}")
        canvas.restoreState()

    buffer = LimitedBuffer()
    doc = ReportDocTemplate(buffer, pagesize=A4, leftMargin=44, rightMargin=44,
                           topMargin=60, bottomMargin=55, pageCompression=1,
                           title="WAF 분석 보고서", author="WAF AI Console",
                           allowSplitting=1)
    # SimpleDocTemplate frames have 6 pt internal padding; reserve it in width.
    try:
        doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
    except Exception as error:
        # ReportLab annotates exceptions with layout context. Do not propagate
        # that context, which may contain report text in other failure paths.
        for code in ("report_too_many_pages", "report_pdf_too_large"):
            if isinstance(error, ReportLayoutError) and str(error).endswith(code):
                raise ReportLayoutError(code) from None
        raise ReportLayoutError("report_render_failed") from None
    return buffer.getvalue()


