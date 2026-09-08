"""Bounded, local exports of one saved final result; no raw/Agent decryption.

PDF and XLSX use the same allowlisted sections. XLSX is a deliberately small
OOXML writer: text cells only, no formulas, links, macros or external resources.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
import re
from threading import Lock
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from ..agent.analyst_guidance import EVIDENCE_SOURCE_CHECK, EVIDENCE_SOURCE_NOTICE

MAX_REPORT_BYTES = 512_000
MAX_REPORT_ROWS = 1000
MAX_PDF_PAGES = 80
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
CELL_UNITS = 800  # Keep long narrative readable; never silently truncate a cell.
VERDICTS = {"true_positive": "정탐", "false_positive": "오탐", "inconclusive": "판정 보류"}
OUTCOMES = {
    "unlabeled": "참고 답안 없음", "match": "답안 일치", "false_negative": "미탐",
    "false_positive": "과탐", "abstained": "모델 판단 보류",
    "expected_abstention_match": "기대 보류 일치", "expected_abstention_mismatch": "기대 보류 불일치",
    "unknown_provenance": "평가 제외 · 실행 출처 미확인", "input_contaminated": "평가 제외 · 정답 포함 입력",
    "stub": "평가 제외 · 모의 실행", "failed": "평가 제외 · 실행 실패", "pending": "평가 제외 · 미완료",
}
_INTERNAL = re.compile(r"primary|verifier|1차\s*판정|독립\s*(검증|판정)|검증\s*실패|(?:판정|분석\s*결과).{0,30}(?:불일치|일치하지|서로\s*다|다릅)|(?:실패|failure)[\s_]*id|output_validation_failed|framework_run_id", re.I)
_EXCEL_ESCAPE = re.compile(r"_x[0-9a-fA-F]{4}_")
_FONT_LOCK = Lock()


class ReportExportError(ValueError):
    def __init__(self, code="report_export_unavailable", status_code=503):
        self.code, self.status_code = code, status_code
        super().__init__(code)


@dataclass(frozen=True)
class ReportSection:
    title: str
    rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AnalysisReport:
    analysis_id: str
    sections: tuple[ReportSection, ...]
    title: str = "WAF 분석 보고서"


def _record(value):
    return value if isinstance(value, dict) else {}


def _text(value, fallback="미기록"):
    if isinstance(value, str):
        return value if value else fallback
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (int, float)):
        return str(value)
    return fallback


def _analyst(value, fallback="미기록"):
    return value if isinstance(value, str) and value.strip() and not _INTERNAL.search(value) else fallback


def _list(value):
    if not isinstance(value, list):
        return []
    if len(value) > MAX_REPORT_ROWS:
        raise ReportExportError("report_too_large", 413)
    return value


def _date(value):
    if isinstance(value, datetime):
        return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()
    return _text(value)


def _duration(value):
    if type(value) not in (int, float) or not 0 <= value < float("inf"):
        return "미측정"
    seconds = value / 1000
    return f"{seconds / 60:.2f}분 ({value:g} ms)" if seconds >= 60 else f"{seconds:.2f}초 ({value:g} ms)"


def _is_stub(detail, result):
    agent, policy = _record(result.get("agent")), _record(result.get("policy"))
    return (detail.get("model_profile") in ("stub", "stub-no-llm")
        or detail.get("prompt_version") in ("stub", "stub-v0")
        or policy.get("prompt_version") in ("stub", "stub-v0")
        or agent.get("framework") in ("stub", "local-stub")
        or agent.get("execution") == "stub" or agent.get("agent_mode") == "stub"
        or agent.get("llm_called") is False)


def build_report(detail: dict, *, generated_at: datetime | None = None) -> AnalysisReport:
    """Only final stored fields are exported, never intermediate role results."""
    result = _record(detail.get("result"))
    verdict = result.get("verdict")
    if detail.get("status") != "completed" or not isinstance(verdict, str) or verdict not in VERDICTS or _is_stub(detail, result):
        raise ReportExportError("report_not_final", 409)
    sections = []
    total_bytes, total_rows = 0, 0

    def section(title, rows):
        nonlocal total_bytes, total_rows
        safe_rows = []
        for label, value in rows:
            value = _text(value)
            # Bound before renderer escaping/paragraph processing; no huge
            # legacy text is silently shortened into an apparently full report.
            total_bytes += len(value.encode("utf-8", errors="surrogatepass")) + len(label.encode("utf-8"))
            total_rows += 1
            if total_bytes > MAX_REPORT_BYTES or total_rows > MAX_REPORT_ROWS:
                raise ReportExportError("report_too_large", 413)
            safe_rows.append((label, value))
        sections.append(ReportSection(title, tuple(safe_rows)))

    guidance, threat = _record(result.get("analyst_guidance")), _record(result.get("threat_analysis"))
    fallback = "현재 분석에서는 정탐·오탐 판정을 보류했습니다." if verdict == "inconclusive" else "저장된 최종 판정과 원문 근거를 확인해 주세요."
    summary = _analyst(guidance.get("summary_ko"), _analyst(result.get("summary_ko"), fallback))
    severity = threat.get("severity")
    if severity is None:
        severity = "미평가 · 이전 결과 또는 심각도 미기록"
    section("판정 요약", [
        ("최종 판정", VERDICTS[verdict]), ("심각도", severity), ("요약", summary),
        ("안내", "자동 분석 결과입니다. 공격 시도와 실제 피해 발생을 구분하고 최종 판단은 분석가가 검토합니다."),
    ])
    if threat:
        section("세부 분석", [("공격 유형", _analyst(threat.get("category"))),
            ("분석 위치", _text(threat.get("target"))), ("분석 내용", _analyst(threat.get("technique_ko"))),
            ("예상 영향", _analyst(threat.get("potential_impact_ko"))),
            *[("인코딩·난독화", value) for value in _list(threat.get("obfuscations")) if _analyst(value, "")]])
    signature = _record(result.get("signature_assessment"))
    if signature:
        section("탐지 내용 검토", [("요청과의 연관성", {"exact": "일치", "partial": "부분 일치", "mismatch": "불일치", "unknown": "평가 불가"}.get(_text(signature.get("relation")), "미기록")),
            ("설명", _analyst(signature.get("explanation_ko")))])
    rows, seen = [], set()
    for item in _list(result.get("evidence")):
        if not isinstance(item, dict):
            continue
        field, excerpt = _text(item.get("field")), _text(item.get("excerpt"))
        interpretation = _analyst(item.get("interpretation_ko"), "저장된 발췌와 요청 문맥을 함께 확인해 주세요.")
        key = (field, excerpt, interpretation)
        if key in seen:
            continue
        seen.add(key)
        rows += [(f"근거 {len(seen)} · 위치", field), ("원문 발췌", excerpt), ("판정 근거", interpretation)]
    section("판정 근거", rows or [("안내", "저장된 원문 발췌 근거가 없습니다.")])

    limitations = [_analyst(value, "") for value in _list(guidance.get("limitations"))]
    if result.get("input_truncated") is True:
        limitations.append("분석 입력의 일부가 생략되었습니다. 전체 원문을 함께 확인해 주세요.")
    if verdict == "inconclusive":
        limitations.append("위 기법·영향은 검토할 가능성이며 확정된 공격이나 피해를 뜻하지 않습니다.")
    circumstances = [_analyst(value, "") for value in _list(result.get("conflicting_evidence"))]
    limitations += circumstances
    structured = guidance.get("checks")
    explicit_checks = isinstance(structured, list)
    if not explicit_checks:
        structured = result.get("analyst_checks")
    checks = []
    for item in _list(structured):
        if not isinstance(item, dict):
            continue
        if item.get("check_ko") == EVIDENCE_SOURCE_CHECK:
            limitations.append(EVIDENCE_SOURCE_NOTICE)
        elif _analyst(item.get("check_ko"), "") and not any(_INTERNAL.search(_text(item.get(key))) for key in ("source_ko", "check_ko", "why_ko")):
            checks.append((_analyst(item.get("source_ko"), "확인 위치 미기록"), item["check_ko"], _analyst(item.get("why_ko"), "확인 목적 미기록")))
    for item in _list(result.get("recommended_checks")):
        if item == EVIDENCE_SOURCE_CHECK:
            limitations.append(EVIDENCE_SOURCE_NOTICE)
    if not explicit_checks and not checks:
        checks = [("확인 위치 미기록", value, "확인 목적 미기록") for value in _list(result.get("recommended_checks")) if _analyst(value, "") and value != EVIDENCE_SOURCE_CHECK]
    if any(limitations):
        section("해석 시 주의사항", [("주의사항", value) for value in dict.fromkeys(limitations) if value])
    tuning = _record(result.get("tuning_recommendation"))
    if tuning.get("recommended") is True or any(_analyst(tuning.get(key), "") for key in ("scope", "proposal_ko", "risk_ko", "validation_ko")):
        section("WAF 정책 검토", [("안내", "WAF 설정을 자동 변경하지 않습니다."),
            *[(label, _analyst(tuning.get(key))) for label, key in [("제안 범위", "scope"), ("제안", "proposal_ko"), ("주의사항", "risk_ko"), ("적용 전 확인", "validation_ko")] if _analyst(tuning.get(key), "")]])
    evaluation = _record(detail.get("evaluation"))
    reference = _record(evaluation.get("reference_label"))
    evaluation_rows = [("비교 결과", OUTCOMES.get(_text(evaluation.get("outcome")), "평가 정보 미기록"))]
    if reference:
        evaluation_rows += [
        ("참고 답안", VERDICTS.get(_text(reference.get("verdict")), "답안 없음")),
        ("답안 출처", {"synthetic_expected": "기대 답안", "reference": "참고 정답"}.get(_text(reference.get("source_kind")), "해당 없음")),
        ("출처·버전", reference.get("source_ref", "해당 없음")),
        ("답안 연결 시각 (UTC)", _date(reference.get("created_at"))),
        ("답안 연결 버전", reference.get("revision", "해당 없음")),
        ("답안 작성 시 AI 결과 열람", "미확인" if reference.get("ai_visible") is None else _text(reference["ai_visible"])),
        ("비교 기준", "다운로드 시점에 연결된 최신 답안입니다. 테스트 실행의 접수 당시 고정 답안과 다를 수 있습니다."),
        ("해석", "단건 비교이며 Accuracy·F1 등 집계 지표를 계산하지 않습니다. 기대 답안·AI 지원 답안은 검증된 운영 정답이나 독립적인 품질 평가가 아닙니다."),
        ]
    elif evaluation.get("outcome") == "unlabeled":
        evaluation_rows.append(("안내", "참고 답안이 없어 정오답을 평가하지 않았습니다."))
    else:
        evaluation_rows.append(("안내", "답안의 출처·버전 정보가 없습니다. 답안 없음이나 독립적인 품질 평가로 추정하지 않습니다."))
    section("참고 답안 비교", evaluation_rows)
    section("소요 시간", [("접수 시각 (UTC)", _date(detail.get("created_at"))),
        ("처리 시작 (UTC)", _date(detail.get("started_at"))), ("완료 시각 (UTC)", _date(detail.get("completed_at"))),
        ("전체 경과 시간", _duration(detail.get("total_elapsed_ms"))), ("큐 대기 시간", _duration(detail.get("queue_wait_ms"))),
        ("처리 경과 시간", _duration(detail.get("processing_duration_ms"))),
        ("시간 기준", "처리 경과에는 재시도·작업 복구 대기가 포함될 수 있으며 단계별 시간 합계나 CPU 사용시간이 아닙니다.")])
    section("이벤트 정보", [("이벤트명", detail.get("event_name")), ("회사", detail.get("company_name")),
        ("출발지 IP", detail.get("src_ip")), ("출발지 포트", detail.get("src_port")),
        ("목적지 IP", detail.get("dest_ip")), ("목적지 포트", detail.get("dest_port")),
        ("탐지 시그니처", detail.get("signature")), ("이벤트 식별자", detail.get("event_id")),
        ("구분", {"test": "테스트", "production": "프로덕션"}.get(_text(detail.get("analysis_purpose")), "기존 미분류"))])
    section("보고서 정보", [("분석 식별자", detail.get("id")),
        ("작성 시각 (UTC)", _date(generated_at or datetime.now(UTC))),
        ("문서 범위", "분석 상세 1건의 최종 결과·근거·답안 비교·시간입니다. HTTP 전체 원문, 디코딩 원문, Agent 입출력, 프롬프트, 임의 추가 필드는 제외합니다. 새 LLM 호출은 없습니다."),
        ("보관 주의", "판정 근거의 원문 발췌와 내부 정보가 포함될 수 있습니다. 사내 반출·보관 정책에 따라 취급하세요."),
        ("표시 안내", "PDF에서 제어 문자와 글꼴이 지원하지 않는 문자는 [U+코드]로 표시합니다. Excel의 긴 내용은 순서대로 계속 행에 나눕니다."),
        ("출력 한도", f"본문 UTF-8 {MAX_REPORT_BYTES:,}바이트·{MAX_REPORT_ROWS:,}항목·PDF {MAX_PDF_PAGES}페이지·파일 10 MiB. 초과하면 부분 파일을 만들지 않습니다.")])
    # Follow-up stays last; explicit [] on decisive verdicts remains empty.
    if verdict == "inconclusive" or checks:
        check_rows = [("안내", "판정 보류를 해소하려면 아래 자료를 확인해 주세요." if verdict == "inconclusive" else "현재 판정과 별개로 영향 범위·후속 대응에 유용한 선택적 확인입니다.")]
        if not checks:
            check_rows.append(("확인 사항", "구체적인 확인 자료는 기록되지 않았습니다. 저장된 원문과 대상 서비스의 처리 문맥을 함께 검토해 주세요."))
        for index, (source, check, why) in enumerate(checks, 1):
            check_rows += [(f"확인 {index} · 자료", source), ("확인 내용", check), ("확인 목적", why)]
        check_rows.append(("주의", "안내한 자료를 시스템이 이미 조회했다는 뜻이 아니며 확인 결과를 미리 단정하지 않습니다."))
        section("추가 확인 사항", check_rows)
    return AnalysisReport(_text(detail.get("id")), tuple(sections))


def _xml_text(value):
    # Protect literal Excel escapes before representing XML-illegal characters.
    value = _EXCEL_ESCAPE.sub(lambda match: "_x005F_" + match.group()[1:], value)
    return "".join(
        f"_x{ord(char):04X}_" if ord(char) < 32 and char not in "\t\n" or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in (0xFFFE, 0xFFFF) else char
        for char in value
    )


def _chunks(value, limit=CELL_UNITS):
    chunk, units = [], 0
    for char in value:
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > limit:
            yield "".join(chunk)
            chunk, units = [], 0
        chunk.append(char)
        units += size
    yield "".join(chunk)


def render_xlsx(report: AnalysisReport) -> bytes:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    types_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    workbook = Element("workbook", xmlns=ns, **{"xmlns:r": rel})
    sheets = SubElement(workbook, "sheets")
    relationships = Element("Relationships", xmlns=package_rel)
    content = Element("Types", xmlns=types_ns)
    SubElement(content, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    SubElement(content, "Default", Extension="xml", ContentType="application/xml")
    for name, kind in [("workbook", "sheet.main"), ("styles", "styles")]:
        SubElement(content, "Override", PartName=f"/xl/{name}.xml", ContentType=f"application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml")
    root_rels = Element("Relationships", xmlns=package_rel)
    SubElement(root_rels, "Relationship", Id="rId1", Type=f"{rel}/officeDocument", Target="xl/workbook.xml")
    SubElement(relationships, "Relationship", Id="styles", Type=f"{rel}/styles", Target="styles.xml")
    styles = Element("styleSheet", xmlns=ns)
    fonts = SubElement(styles, "fonts", count="2")
    for bold in (False, True):
        font = SubElement(fonts, "font")
        SubElement(font, "sz", val="11")
        SubElement(font, "name", val="맑은 고딕")
        if bold:
            SubElement(font, "b")
    fills = SubElement(styles, "fills", count="2")
    for pattern in ("none", "gray125"):
        SubElement(SubElement(fills, "fill"), "patternFill", patternType=pattern)
    borders = SubElement(styles, "borders", count="1")
    SubElement(borders, "border")
    master = SubElement(styles, "cellStyleXfs", count="1")
    SubElement(master, "xf", numFmtId="0", fontId="0", fillId="0", borderId="0")
    cells = SubElement(styles, "cellXfs", count="2")
    for font_id in ("0", "1"):
        xf = SubElement(cells, "xf", numFmtId="49", fontId=font_id, fillId="0", borderId="0", xfId="0", applyAlignment="1", applyNumberFormat="1")
        SubElement(xf, "alignment", vertical="top", wrapText="1")
    named = SubElement(styles, "cellStyles", count="1")
    SubElement(named, "cellStyle", name="Normal", xfId="0", builtinId="0")
    buffer = BytesIO()
    total_rows = 0
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for index, section in enumerate(report.sections, 1):
            SubElement(sheets, "sheet", name=section.title, sheetId=str(index), **{"r:id": f"rId{index}"})
            SubElement(relationships, "Relationship", Id=f"rId{index}", Type=f"{rel}/worksheet", Target=f"worksheets/sheet{index}.xml")
            SubElement(content, "Override", PartName=f"/xl/worksheets/sheet{index}.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
            sheet = Element("worksheet", xmlns=ns)
            views = SubElement(sheet, "sheetViews")
            view = SubElement(views, "sheetView", workbookViewId="0")
            SubElement(view, "pane", ySplit="1", topLeftCell="A2", activePane="bottomLeft", state="frozen")
            columns = SubElement(sheet, "cols")
            SubElement(columns, "col", min="1", max="1", width="26", customWidth="1")
            SubElement(columns, "col", min="2", max="2", width="90", customWidth="1")
            data = SubElement(sheet, "sheetData")
            rows = [("항목", section.title)]
            for label, value in section.rows:
                rows.extend((label if part == 0 else f"{label} · 계속 {part + 1}", chunk) for part, chunk in enumerate(_chunks(value)))
            total_rows += len(rows)
            if total_rows > MAX_REPORT_ROWS * 3:
                raise ReportExportError("report_too_large", 413)
            for number, values in enumerate(rows, 1):
                height = min(409, max(30, (sum(max(1, (len(line) + 43) // 44) for line in values[1].split("\n")) + 1) * 16))
                row = SubElement(data, "row", r=str(number), ht=str(height), customHeight="1")
                for column, value in zip(("A", "B"), values):
                    cell = SubElement(row, "c", r=f"{column}{number}", t="inlineStr", s="1" if number == 1 or column == "A" else "0")
                    text = SubElement(SubElement(cell, "is"), "t", **{"xml:space": "preserve"})
                    text.text = _xml_text(value)
            SubElement(sheet, "pageMargins", left="0.3", right="0.3", top="0.5", bottom="0.5", header="0.2", footer="0.2")
            archive.writestr(f"xl/worksheets/sheet{index}.xml", tostring(sheet, encoding="utf-8", xml_declaration=True))
        for path, element in [("[Content_Types].xml", content), ("_rels/.rels", root_rels), ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", relationships), ("xl/styles.xml", styles)]:
            archive.writestr(path, tostring(element, encoding="utf-8", xml_declaration=True))
    return _bounded_download(buffer.getvalue())


def _bounded_download(value):
    if len(value) > MAX_DOWNLOAD_BYTES:
        raise ReportExportError("report_too_large", 413)
    return value


def render_pdf(report: AnalysisReport) -> bytes:
    # Never pass model-generated markup, images, URLs or filenames to ReportLab.
    # Import lazily: missing packaging must not break unrelated API endpoints.
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

        with _FONT_LOCK:
            for name, filename in (("WAFNanum", "NanumGothic.ttf"), ("WAFNanumBold", "NanumGothicBold.ttf")):
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, str(Path(__file__).parents[1] / "assets" / "fonts" / filename)))
        glyphs = pdfmetrics.getFont("WAFNanum").face.charToGlyph

        def plain(value):
            visible = "".join(char if char == "\n" or ord(char) in glyphs and ord(char) >= 32 and not (0x7F <= ord(char) <= 0x9F or ord(char) in {0x061C, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF} or 0x202A <= ord(char) <= 0x202E or 0x2066 <= ord(char) <= 0x2069) else f"[U+{ord(char):04X}]" for char in value)
            return escape(visible).replace("\n", "<br/>")

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=44, leftMargin=44, topMargin=46, bottomMargin=46,
            title="WAF 분석 보고서", author="WAF AI Console", pageCompression=1)
        body = ParagraphStyle("body", fontName="WAFNanum", fontSize=9, leading=15, wordWrap="CJK", splitLongWords=True, spaceAfter=7)
        heading = ParagraphStyle("heading", parent=body, fontName="WAFNanumBold", fontSize=14, leading=21, spaceBefore=16, spaceAfter=10, textColor=colors.HexColor("#153d67"), keepWithNext=True)
        label_style = ParagraphStyle("label", parent=body, fontName="WAFNanumBold", fontSize=9, spaceAfter=3, keepWithNext=True)
        story = [Paragraph(plain(report.title), ParagraphStyle("title", parent=heading, fontSize=23, leading=30)),
            Paragraph("분석 상세 · 내부 검토용", body), Spacer(1, 8)]
        for section in report.sections:
            story.append(Paragraph(plain(section.title), heading))
            for label, value in section.rows:
                narrative = label in {"요약", "분석 내용", "예상 영향", "판정 근거", "원문 발췌", "설명", "제안", "확인 내용", "확인 목적", "주의사항"}
                if not narrative and len(value) <= 160 and "\n" not in value:
                    story.append(Paragraph(f'<font name="WAFNanumBold">{plain(label)}</font>　{plain(value)}', body))
                else:
                    story += [Paragraph(plain(label), label_style), Paragraph(plain(value), body)]

        def page(canvas, document):
            if document.page > MAX_PDF_PAGES:
                raise ReportExportError("report_too_large", 413)
            canvas.saveState()
            canvas.setFont("WAFNanum", 8)
            canvas.setFillColor(colors.HexColor("#61718a"))
            canvas.drawString(44, 27, f"WAF 분석 보고서 · {report.analysis_id}")
            canvas.drawRightString(A4[0] - 44, 27, str(document.page))
            canvas.restoreState()

        doc.build(story, onFirstPage=page, onLaterPages=page)
        return _bounded_download(buffer.getvalue())
    except ReportExportError:
        raise
    except Exception:
        # Layout/font/Unicode failures can embed text fragments in exceptions.
        # Neither return them nor chain them into API/server error logs.
        raise ReportExportError() from None
