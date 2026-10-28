"""Administrator filtered, field-selectable XLSX. Raw input stays local/audited."""
import hashlib
import json
from io import BytesIO
from math import isfinite
from threading import Lock
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import case, func, select
from sqlalchemy.orm import aliased, load_only

from ..models import Analysis, AnalysisLabel, TestRunItem
from .analysis_exports import _chunks, _xml_text
from .analyst_presentation import assessment_view
from .test_runs import MAX_TEST_ITEMS, run_cohort, test_case_query, test_read_context
from .test_export_columns import EVIDENCE_COLUMNS, LEGACY_COLUMN_ALIASES, column_catalog

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_COLUMNS = 512
EXPORT_LOCK = Lock()
INPUT_FIELDS = ("event_id", "company_name", "src_ip", "dest_ip", "src_port", "dest_port", "payload",
                "signature", "event_name", "waf_vendor", "waf_action")


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
        name = {"true_positive": EVIDENCE_COLUMNS[0], "attack_request": EVIDENCE_COLUMNS[0],
                "false_positive": EVIDENCE_COLUMNS[1], "normal_request": EVIDENCE_COLUMNS[1]}.get(item.get("supports"), EVIDENCE_COLUMNS[2])
        if len(groups[name]) == 5:
            continue
        groups[name].append("\n".join(f"{label}: {item[key]}" for label, key in (
            ("위치", "field"), ("로그 발췌", "excerpt"), ("판단 이유", "interpretation_ko"))
            if isinstance(item.get(key), str) and item[key]))
    return {name: "\n---\n".join(values) for name, values in groups.items()}


def prepare_export(db, crypto, run, filters=None):
    """Metadata-only membership/catalog; never load/decrypt HTTP or result blobs."""
    filters = filters or {}
    cohort = run_cohort(run.id, **{name: filters.get(name) for name in (
        "difficulty", "test_category", "difficulty_missing", "test_category_missing")})
    evaluation, _, current, relation = test_read_context(db, run, cohort=cohort,
        evaluation_id=filters.get("evaluation_id"), reference_basis=filters.get("reference_basis", "latest"))
    query = test_case_query(current, relation, cohort, **{name: filters.get(name) for name in (
        "status", "evaluation_outcome", "reference_verdict", "verdict", "group_field", "group_value",
        "group_missing", "group_filters", "case_filter")})
    sort = {"row_number": TestRunItem.row_number, "case_name": TestRunItem.case_name, "status": Analysis.status}.get(filters.get("sort_by"), TestRunItem.row_number)
    matches = query.with_only_columns(TestRunItem.id.label("item_id"), current.c.analysis_id,
        current.c.retry_count, relation.c.reference_verdict, relation.c.outcome,
        TestRunItem.row_number, sort.label("sort_value"), maintain_column_froms=True).cte(
        "test_export_matches").prefix_with("MATERIALIZED", dialect="sqlite")
    valid = case((func.json_valid(Analysis.extra_fields) == 1, Analysis.extra_fields), else_="{}")
    extra = case((func.json_type(valid) == "object", valid), else_="{}")
    keys = func.json_each(extra).table_valued("key", "type")
    key_list = select(func.json_group_array(func.json_object("key", keys.c.key, "type", keys.c.type))).select_from(keys).correlate(Analysis).scalar_subquery()
    rows = db.execute(select(matches, key_list.label("extra_keys")).outerjoin(
        Analysis, Analysis.id == matches.c.analysis_id).order_by(matches.c.row_number, matches.c.item_id).limit(MAX_TEST_ITEMS + 1)).mappings().all()
    if len(rows) > MAX_TEST_ITEMS:
        raise TestExportError("test_export_too_large", 413)
    extras = {}
    for row in rows:
        for field in json.loads(row["extra_keys"] or "[]"):
            extras.setdefault(field["key"], field["type"])
            if len(extras) > MAX_COLUMNS:
                raise TestExportError("test_export_too_many_columns", 413)
    definitions = []
    if run.input_schema_snapshot_ciphertext:
        from .input_schemas import read_schema_snapshot
        definitions = read_schema_snapshot(crypto, run)["fields"]
    columns = column_catalog(extras, definitions)
    if len(columns) > MAX_COLUMNS:
        raise TestExportError("test_export_too_many_columns", 413)
    # Protect membership/attempts and field mapping, not worker heartbeats.
    # Unfiltered in-flight cases may finish while fields are being selected;
    # export their consistent current values rather than making export impossible.
    scope = hashlib.sha256(json.dumps({"run": run.id, "filters": filters,
        "rows": [[row["item_id"], row["analysis_id"]] for row in rows],
        "columns": columns}, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False).encode()).hexdigest()
    basis = "saved" if evaluation else "ground_truth" if run.evaluation_mode == "ground_truth" else filters.get("reference_basis", "latest")
    return {"count": len(rows), "columns": columns, "scope": scope, "reference_basis": basis}, matches


def selected_columns(catalog, names):
    by_name = {column["name"]: column for column in catalog}
    names = [LEGACY_COLUMN_ALIASES.get(name, name) for name in names] if names else names
    if not names or len(names) != len(set(names)) or any(name not in by_name for name in names):
        raise TestExportError("invalid_test_export_columns", 422)
    return [by_name[name] for name in names]


def collect_rows(db, crypto, run, *, evaluation_id=None, reference_basis="latest", prepared=None, columns=None, filters=None):
    if prepared is None:
        prepared = prepare_export(db, crypto, run, filters or {"evaluation_id": evaluation_id, "reference_basis": reference_basis})
    metadata, matches = prepared
    columns = metadata["columns"] if columns is None else columns
    names = {column["name"] for column in columns}
    result_fields = {*EVIDENCE_COLUMNS, "explanation_ko", "technique_ko", "potential_impact_ko", "signature_relation",
                     "matched_points", "mismatched_points", "comparison_limit_ko"}
    attrs = {"id"} | (names & {column.name for column in Analysis.__table__.columns})
    if "explanation_ko" in names:
        attrs.add("status")
    if "payload" in names:
        attrs.add("payload_ciphertext")
    if any("source_key" in column for column in columns):
        attrs.add("extra_fields")
    if names & result_fields:
        attrs.add("result_json")
    admitted_label = aliased(AnalysisLabel)
    order = matches.c.sort_value.desc().nulls_last() if (filters or {}).get("sort_order") == "desc" else matches.c.sort_value.asc().nulls_last()
    query = select(TestRunItem, Analysis, matches.c.retry_count, matches.c.reference_verdict, matches.c.outcome, admitted_label.verdict).select_from(
        matches).join(TestRunItem, TestRunItem.id == matches.c.item_id).outerjoin(Analysis, Analysis.id == matches.c.analysis_id).outerjoin(
        admitted_label, admitted_label.id == TestRunItem.label_id).options(load_only(*(getattr(Analysis, name) for name in attrs), raiseload=True)).order_by(order, TestRunItem.row_number, TestRunItem.id)
    records, source_bytes = [], 0
    for item, analysis, retries, reference, outcome, expected in db.execute(query.execution_options(yield_per=100)):
        if len(records) >= MAX_TEST_ITEMS:
            raise TestExportError("test_export_too_large", 413)
        result = analysis.result_json if analysis and names & result_fields and isinstance(analysis.result_json, dict) else {}
        extra = analysis.extra_fields if analysis and "extra_fields" in attrs and isinstance(analysis.extra_fields, dict) else {}
        threat, signature = result.get("threat_analysis"), result.get("signature_assessment")
        threat = threat if isinstance(threat, dict) else {}
        signature = signature if isinstance(signature, dict) else {}
        special = {"retry_count": retries, "analysis_id": analysis.id if analysis else None, "expected_verdict": expected,
            "reference_verdict": reference, "comparison_outcome": outcome, "extra_fields": extra,
            "explanation_ko": result.get("explanation_ko") or signature.get("explanation_ko"),
            "signature_relation": signature.get("relation"), "comparison_limit_ko": signature.get("uncertainty_ko"),
            **{key: threat.get(key) for key in ("technique_ko", "potential_impact_ko")},
            **{key: signature.get(key) for key in ("matched_points", "mismatched_points")}}
        if "explanation_ko" in names and result.get("schema_version") == "waf-analysis-v3":
            from .decision_explanation import decision_explanation
            reason = decision_explanation({"status": analysis.status, "result": result}) if analysis else None
            special["explanation_ko"] = reason["reason_ko"] if reason else None
        if names & set(EVIDENCE_COLUMNS):
            special.update(evidence_columns(result))
        record = {}
        for column in columns:
            name = column["name"]
            if "source_key" in column:
                value = extra.get(column["source_key"])
            elif name == "payload":
                value = crypto.decrypt_text(analysis.payload_ciphertext) if analysis else None
            elif name in {"row_number", "case_name", "difficulty", "test_category", "ingest_status"}:
                value = getattr(item, name)
            elif name == "event_id":
                value = analysis.event_id if analysis else item.event_id
            elif name == "error_code":
                value = item.error_code or (analysis.error_code if analysis else None)
            elif name in special:
                value = special[name]
            else:
                value = getattr(analysis, name) if analysis else "rejected" if name == "status" else run.source_system if name == "source_system" else None
            record[name] = value
        for key, value in record.items():
            source_bytes += len(key.encode("utf-8")) + len(cell_text(value).encode("utf-8", errors="surrogatepass"))
        if source_bytes > MAX_SOURCE_BYTES:
            raise TestExportError("test_export_too_large", 413)
        records.append(record)
    return records, metadata["reference_basis"]


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


def render_workbook(records, *, run_name, basis, columns=None):
    """English headers only; safe inline cells and lossless continuation fields."""
    keys = [column["name"] for column in columns] if columns is not None else list(dict.fromkeys(key for record in records for key in record))
    if not keys:
        keys = [column["name"] for column in column_catalog({})]
    parts = {key: 1 for key in keys}
    prepared = []
    for record in records:
        row = {}
        for key in keys:
            value = record.get(key)
            if type(value) is int and abs(value) < 10 ** 15 or type(value) is float and isfinite(value):
                row[key] = [value]
            else:
                row[key] = list(_chunks(cell_text(value), limit=32700))
            parts[key] = max(parts[key], len(row[key]))
        prepared.append(row)
    physical = [(key, part) for key in keys for part in range(parts[key])]
    if len(physical) > MAX_COLUMNS:
        raise TestExportError("test_export_too_many_columns", 413)
    headers = [key if part == 0 else f"{key}__part_{part + 1}" for key, part in physical]
    if len(headers) != len(set(headers)):
        raise TestExportError("test_export_column_collision", 422)
    rows = [[row.get(key, [""])[part] if part < len(row.get(key, [""])) else "" for key, part in physical] for row in prepared]
    by_name = {column["name"]: column for column in (columns or column_catalog({}))}
    notes = []
    for (key, part), header in zip(physical, headers):
        metadata = by_name.get(key, {"label_ko": "추가 입력", "description": "저장된 추가 입력값입니다.", "example": "예시 입력값"})
        notes.append([header, metadata["label_ko"] + (f" (이어짐 {part + 1})" if part else ""),
            metadata["description"] + (" 앞 컬럼 뒤에 이어 붙이면 전체 값입니다." if part else ""), metadata["example"]])
    return _xlsx([("분석결과", headers, rows), ("컬럼안내", ["column_name", "column_name_ko", "description", "example"], notes)])


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
                width = "64" if header in (*EVIDENCE_COLUMNS, "description", "example") or header.startswith("payload") or header.endswith("_ko") else "24"
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
