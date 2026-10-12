"""Administrator whole-Test XLSX export. Raw input stays local and is audited."""
import json
from datetime import UTC, datetime
from io import BytesIO
from math import isfinite
from threading import Lock
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import aliased

from ..models import Analysis, AnalysisLabel, TestRunItem
from .analysis_exports import _chunks, _xml_text
from .analyst_presentation import assessment_view
from .test_runs import MAX_TEST_ITEMS, test_read_context

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_COLUMNS = 512
EXPORT_LOCK = Lock()
INPUT_FIELDS = ("event_id", "company_name", "src_ip", "dest_ip", "src_port", "dest_port", "payload",
                "signature", "event_name", "waf_vendor", "waf_action")
EVIDENCE_COLUMNS = ("주요 정탐 근거", "주요 오탐 근거", "주요 참고 내용")


class TestExportError(ValueError):
    def __init__(self, code="test_export_unavailable", status_code=503):
        self.code, self.status_code = code, status_code
        super().__init__(code)


def evidence_columns(result):
    """Display order/representatives, max five per direction; never reclassify."""
    view = assessment_view(result)
    items = view["evidence"] if view is not None else result.get("evidence", [])
    groups = {name: [] for name in EVIDENCE_COLUMNS}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = {"true_positive": EVIDENCE_COLUMNS[0], "false_positive": EVIDENCE_COLUMNS[1]}.get(item.get("supports"), EVIDENCE_COLUMNS[2])
        if len(groups[name]) == 5:
            continue
        groups[name].append("\n".join(f"{label}: {item[key]}" for label, key in (
            ("위치", "field"), ("로그 발췌", "excerpt"), ("판단 이유", "interpretation_ko"))
            if isinstance(item.get(key), str) and item[key]))
    return {name: "\n---\n".join(values) for name, values in groups.items()}


def collect_rows(db, crypto, run, *, evaluation_id=None, reference_basis="latest"):
    evaluation, _nodes, current, relation = test_read_context(db, run, evaluation_id=evaluation_id, reference_basis=reference_basis)
    admitted_label = aliased(AnalysisLabel)
    query = select(TestRunItem, Analysis, current.c.retry_count, relation.c.reference_verdict, relation.c.outcome, admitted_label.verdict).join(
        current, current.c.item_id == TestRunItem.id).outerjoin(Analysis, Analysis.id == current.c.analysis_id).outerjoin(
        relation, relation.c.analysis_id == Analysis.id).outerjoin(admitted_label, admitted_label.id == TestRunItem.label_id).where(TestRunItem.test_run_id == run.id).order_by(TestRunItem.row_number)
    records, source_bytes = [], 0
    for item, analysis, retries, reference, outcome, expected in db.execute(query.execution_options(yield_per=100)):
        if len(records) >= MAX_TEST_ITEMS:
            raise TestExportError("test_export_too_large", 413)
        record = {"row_number": item.row_number, "case_name": item.case_name, "difficulty": item.difficulty,
            "test_category": item.test_category, "ingest_status": item.ingest_status,
            "status": analysis.status if analysis else "rejected", "error_code": item.error_code or (analysis.error_code if analysis else None),
            "retry_count": retries, "analysis_id": analysis.id if analysis else None,
            "expected_verdict": expected, "source_system": analysis.source_system if analysis else run.source_system}
        record.update({key: getattr(analysis, key) if analysis and key != "payload" else
            crypto.decrypt_text(analysis.payload_ciphertext) if analysis and key == "payload" else
            item.event_id if key == "event_id" else None for key in INPUT_FIELDS})
        result = analysis.result_json if analysis and isinstance(analysis.result_json, dict) else {}
        extra = analysis.extra_fields if analysis and isinstance(analysis.extra_fields, dict) else {}
        record["extra_fields"] = extra
        record.update({f"extra_fields.{key}": value for key, value in extra.items()})
        threat, signature = result.get("threat_analysis"), result.get("signature_assessment")
        threat = threat if isinstance(threat, dict) else {}
        signature = signature if isinstance(signature, dict) else {}
        record.update(verdict=analysis.verdict if analysis else None, summary_ko=analysis.summary_ko if analysis else None,
            severity=analysis.severity if analysis else None, confidence_score=analysis.confidence_score if analysis else None,
            threat_category=analysis.threat_category if analysis else None,
            explanation_ko=result.get("explanation_ko") or signature.get("explanation_ko"),
            technique_ko=threat.get("technique_ko"), potential_impact_ko=threat.get("potential_impact_ko"),
            signature_relation=signature.get("relation"), matched_points=signature.get("matched_points"),
            mismatched_points=signature.get("mismatched_points"), comparison_limit_ko=signature.get("uncertainty_ko"),
            reference_verdict=reference, comparison_outcome=outcome,
            initial_verdict=analysis.initial_verdict if analysis else None,
            initial_probability=analysis.initial_probability if analysis else None,
            initial_model_version=analysis.initial_model_version if analysis else None)
        record.update(evidence_columns(result))
        for key, value in record.items():
            source_bytes += len(key.encode("utf-8")) + len(cell_text(value).encode("utf-8", errors="surrogatepass"))
        if source_bytes > MAX_SOURCE_BYTES:
            raise TestExportError("test_export_too_large", 413)
        records.append(record)
    basis = "saved" if evaluation else "ground_truth" if run.evaluation_mode == "ground_truth" else reference_basis
    return records, basis


def cell_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return str(value)


def column_name(index):
    result = ""
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def render_workbook(records, *, run_name, basis):
    """Inline text cells prohibit formulas/links; continuation columns preserve long input."""
    keys = list(dict.fromkeys(key for record in records for key in record))
    if not keys:
        keys = ["row_number", *INPUT_FIELDS, "extra_fields", "status", "error_code", "verdict", "summary_ko", "severity", "explanation_ko", *EVIDENCE_COLUMNS]
    parts = {key: 1 for key in keys}
    prepared = []
    for record in records:
        row = {}
        for key, value in record.items():
            if type(value) is int and abs(value) < 10 ** 15 or type(value) is float and isfinite(value):
                row[key] = [value]
            else:
                row[key] = list(_chunks(cell_text(value), limit=32700))
            parts[key] = max(parts[key], len(row[key]))
        prepared.append(row)
    columns = [(key, part) for key in keys for part in range(parts[key])]
    if len(columns) > MAX_COLUMNS:
        raise TestExportError("test_export_too_many_columns", 413)
    headers = [key if part == 0 else f"{key}__part_{part + 1}" for key, part in columns]
    rows = [[row.get(key, [""])[part] if part < len(row.get(key, [""])) else "" for key, part in columns] for row in prepared]
    notes = [["테스트", run_name], ["생성 시각 (UTC)", datetime.now(UTC).isoformat()], ["답안 기준", basis],
        ["범위", "테스트 전체 문항. 화면의 검색·필터·페이지와 무관하며 재실행은 선택한 평가 기준의 문항별 실행 한 건입니다."],
        ["원문", "payload·Cookie와 모든 저장 입력을 포함합니다. 접수 거부 문항의 원문은 저장되지 않아 비어 있습니다."],
        ["긴 필드", "32700 UTF-16 단위를 넘는 값은 원본 컬럼 뒤의 __part_2, __part_3 순서로 이어집니다. 이어 붙이면 전체 원문입니다."],
        ["근거", "웹의 표시 순서·대표 근거를 사용하며 정탐·오탐·참고 각각 최대 5개입니다. 여러 개는 줄바꿈 --- 줄바꿈으로 구분합니다. 순위 점수는 없습니다."],
        ["이전 근거", "방향이 기록되지 않은 이전 근거는 참고 내용에 포함합니다. 정탐·오탐 방향을 추측하지 않습니다."],
        ["explanation_ko", "저장된 결과 설명. 이전 형식은 signature_assessment.explanation_ko이며 새 탐지 비교는 matched_points·mismatched_points·comparison_limit_ko입니다."],
        ["셀 형식", "문자열은 텍스트 셀입니다. 수식·매크로·자동 외부 링크를 포함하지 않습니다."],
        ["식별자", "analysis_id는 감사·원문 연결용이며 화면에서 숨기는 기술 식별자도 파일에는 포함합니다."]]
    return _xlsx([( "문항별 결과", headers, rows), ("안내", ["항목", "내용"], notes)])


def _xlsx(tables):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    content = Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    for extension, kind in (("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml")):
        SubElement(content, "Default", Extension=extension, ContentType=kind)
    workbook = Element("workbook", xmlns=ns, **{"xmlns:r": rel})
    sheets = SubElement(workbook, "sheets")
    relationships = Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    root_rels = Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
    SubElement(root_rels, "Relationship", Id="rId1", Type=rel + "/officeDocument", Target="xl/workbook.xml")
    styles = f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="맑은 고딕"/></font><font><b/><sz val="11"/><name val="맑은 고딕"/><color rgb="FFFFFFFF"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF26765E"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    SubElement(relationships, "Relationship", Id="styles", Type=rel + "/styles", Target="styles.xml")
    for name, kind in (("workbook", "sheet.main"), ("styles", "styles")):
        SubElement(content, "Override", PartName=f"/xl/{name}.xml", ContentType=f"application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml")
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for number, (name, headers, rows) in enumerate(tables, 1):
            SubElement(sheets, "sheet", name=name, sheetId=str(number), **{"r:id": f"rId{number}"})
            path = f"worksheets/sheet{number}.xml"
            SubElement(relationships, "Relationship", Id=f"rId{number}", Type=rel + "/worksheet", Target=path)
            SubElement(content, "Override", PartName="/xl/" + path, ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
            sheet = Element("worksheet", xmlns=ns)
            view = SubElement(SubElement(sheet, "sheetViews"), "sheetView", workbookViewId="0")
            SubElement(view, "pane", ySplit="1", topLeftCell="A2", activePane="bottomLeft", state="frozen")
            cols = SubElement(sheet, "cols")
            for index, header in enumerate(headers, 1):
                width = "64" if header in EVIDENCE_COLUMNS or header.startswith("payload") or header.endswith("_ko") else "24"
                SubElement(cols, "col", min=str(index), max=str(index), width=width, customWidth="1")
            # Stream row XML into the ZIP; never build the payload-sized XML tree.
            prefix = tostring(sheet, encoding="utf-8", xml_declaration=True)
            with archive.open("xl/" + path, "w") as part:
                part.write(prefix[:-len(b"</worksheet>")] + b"<sheetData>")
                for row_number, values in enumerate([headers, *rows], 1):
                    row = Element("row", r=str(row_number), ht="32" if row_number == 1 else "64", customHeight="1")
                    for index, value in enumerate(values, 1):
                        numeric = type(value) in (int, float)
                        cell = SubElement(row, "c", r=f"{column_name(index)}{row_number}", t="n" if numeric else "inlineStr", s="1" if row_number == 1 else "0")
                        text = SubElement(cell, "v") if numeric else SubElement(SubElement(cell, "is"), "t", **{"xml:space": "preserve"})
                        text.text = str(value) if numeric else _xml_text(value)
                    part.write(tostring(row, encoding="utf-8"))
                    if output.tell() > MAX_FILE_BYTES:
                        raise TestExportError("test_export_too_large", 413)
                part.write(f'</sheetData><autoFilter ref="A1:{column_name(len(headers))}{len(rows) + 1}"/></worksheet>'.encode())
            if output.tell() > MAX_FILE_BYTES:
                raise TestExportError("test_export_too_large", 413)
        for path, element in (("[Content_Types].xml", content), ("_rels/.rels", root_rels), ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", relationships)):
            archive.writestr(path, tostring(element, encoding="utf-8", xml_declaration=True))
        archive.writestr("xl/styles.xml", styles.encode())
    if output.tell() > MAX_FILE_BYTES:
        raise TestExportError("test_export_too_large", 413)
    return output.getvalue()
