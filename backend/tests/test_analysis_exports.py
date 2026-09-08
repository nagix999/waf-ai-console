"""Synthetic/offline report artifacts, permissions, limits and inert content."""
import copy
from datetime import UTC, datetime, timedelta
from io import BytesIO
import re
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from app.models import AccessAudit, Analysis, AnalysisLabel
from app.services.analysis_exports import (
    AnalysisReport, ReportExportError, ReportSection, _chunks, build_report, render_pdf, render_xlsx,
)

NOW = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def detail_fixture():
    return {
        "id": "11111111-2222-4333-8444-555555555555", "event_id": "synthetic-event",
        "status": "completed", "analysis_purpose": "test", "company_name": "합성 회사",
        "src_ip": "192.0.2.1", "dest_ip": "198.51.100.1", "src_port": 0, "dest_port": 443,
        "event_name": "합성 보안 점검", "signature": "Synthetic rule", "model_profile": "synthetic-model",
        "created_at": NOW, "started_at": NOW + timedelta(seconds=1), "completed_at": NOW + timedelta(seconds=3),
        "total_elapsed_ms": 3000, "queue_wait_ms": 1000, "processing_duration_ms": 2000,
        "verdict": "false_positive", "summary_ko": "STALE-SUMMARY",
        "payload": "RAW-PAYLOAD-MUST-NOT-EXPORT", "extra_fields": {"secret": "EXTRA-MUST-NOT-EXPORT"},
        "evaluation": {"outcome": "unlabeled", "reference_label": None},
        "result": {
            "schema_version": "waf-analysis-v2", "verdict": "true_positive", "summary_ko": "입력에서 공격 구문이 확인됩니다.",
            "threat_analysis": {"severity": "HIGH", "category": "합성 공격", "target": "payload.query", "technique_ko": "요청 값이 조건문을 바꾸려고 합니다.", "potential_impact_ko": "SQL에 직접 사용되는 경우 조회 조건 우회가 가능합니다.", "obfuscations": []},
            "signature_assessment": {"relation": "partial", "explanation_ko": "일부 구문이 탐지 조건과 관련됩니다."},
            "evidence": [{"field": "payload.query", "excerpt": "q=' OR 1=1", "interpretation_ko": "참이 되는 조건을 추가하려는 구문입니다."}],
            "analyst_guidance": {"checks": [], "limitations": []},
            "recommended_checks": ["DO-NOT-INVENT-FOLLOWUP"], "agent": {"framework": "moduagent"},
            "primary": {"summary_ko": "PRIMARY-MUST-NOT-EXPORT"},
            "verifier": {"output": {"summary_ko": "VERIFIER-MUST-NOT-EXPORT"}},
            "unknown": "UNKNOWN-MUST-NOT-EXPORT",
        },
    }


def report_text(report):
    return "\n".join([report.title, *[part for section in report.sections for row in section.rows for part in row]])


def xlsx_cells(content):
    with ZipFile(BytesIO(content)) as archive:
        cells = []
        for name in archive.namelist():
            if name.startswith("xl/worksheets/"):
                tree = ET.fromstring(archive.read(name))
                cells.extend(tree.findall(".//s:c", NS))
        return cells


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def stored_analysis(client, event_payload):
    login(client)
    response = client.post("/api/v1/analyses", json=event_payload)
    assert response.status_code == 202
    id = response.json()["id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, id)
        row.status = "completed"
        row.verdict = "true_positive"
        row.result_json = detail_fixture()["result"]
        row.created_at, row.started_at, row.completed_at = NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=3)
        row.model_profile = "synthetic-model"
        db.commit()
    return id


def test_common_report_only_uses_final_allowlisted_fields_and_preserves_saved_data():
    detail = detail_fixture()
    before = copy.deepcopy(detail)
    report = build_report(detail, generated_at=NOW)
    text = report_text(report)
    assert report.sections[0].title == "판정 요약"
    assert "정탐" in text and "입력에서 공격 구문" in text
    assert "원문 발췌" in text and "q=' OR 1=1" in text
    for marker in ("STALE-SUMMARY", "RAW-PAYLOAD-MUST", "EXTRA-MUST", "PRIMARY-MUST", "VERIFIER-MUST", "UNKNOWN-MUST", "DO-NOT-INVENT"):
        assert marker not in text
    assert "추가 확인 사항" not in [section.title for section in report.sections]
    assert "참고 답안 비교" in [section.title for section in report.sections]
    assert "3.00초 (3000 ms)" in text
    assert detail == before


@pytest.mark.parametrize("status", ["pending", "processing", "failed"])
def test_non_final_analysis_cannot_be_exported(status):
    detail = detail_fixture(); detail["status"] = status
    with pytest.raises(ReportExportError, match="^report_not_final$"):
        build_report(detail)


@pytest.mark.parametrize("patch", [{"result": None}, {"result": []}, {"result": {"verdict": "unknown"}}, {"model_profile": "stub"}])
def test_missing_invalid_or_mock_results_cannot_appear_as_final_reports(patch):
    with pytest.raises(ReportExportError, match="^report_not_final$"):
        build_report({**detail_fixture(), **patch})


def test_inconclusive_followup_last_and_missing_times_are_not_zero():
    detail = detail_fixture()
    detail["result"]["verdict"] = "inconclusive"
    detail["result"]["threat_analysis"]["severity"] = "UNKNOWN"
    detail["processing_duration_ms"] = None
    report = build_report(detail)
    assert report.sections[-1].title == "추가 확인 사항"
    assert "구체적인 확인 자료는 기록되지 않았습니다" in report_text(report)
    assert ("처리 경과 시간", "미측정") in next(s for s in report.sections if s.title == "소요 시간").rows


def test_only_exact_evidence_duplicates_are_removed():
    detail = detail_fixture()
    original = detail["result"]["evidence"][0]
    detail["result"]["evidence"] += [dict(original), {**original, "interpretation_ko": "다른 해석은 보존됩니다."}]
    rows = next(section.rows for section in build_report(detail).sections if section.title == "판정 근거")
    assert sum(label == "원문 발췌" for label, _ in rows) == 2
    assert sum(value == original["interpretation_ko"] for _, value in rows) == 1


def test_size_limits_fail_instead_of_silently_shortening_legacy_text():
    detail = detail_fixture(); detail["result"]["evidence"][0]["excerpt"] = "x" * 512_001
    with pytest.raises(ReportExportError, match="^report_too_large$"):
        build_report(detail)
    detail = detail_fixture(); detail["result"]["evidence"] *= 1001
    with pytest.raises(ReportExportError, match="^report_too_large$"):
        build_report(detail)


@pytest.mark.parametrize("value", ["=1+1", "+SUM(1,2)", "-1+1", "@SUM(A1)", "\t=cmd()", "\r=cmd()", "<img src='https://example.invalid/secret' />", "https://example.invalid/", "한글 😀 _x0041_ \x00\r\n"])
def test_xlsx_untrusted_values_are_strings_without_formulas_links_or_macros(value):
    content = render_xlsx(AnalysisReport("synthetic", (ReportSection("판정 근거", (("원문 발췌", value),)),)))
    with ZipFile(BytesIO(content)) as archive:
        assert archive.testzip() is None
        assert "[Content_Types].xml" in archive.namelist()
        for name in archive.namelist():
            tree = ET.fromstring(archive.read(name))
            assert not tree.findall(".//s:f", NS)
            assert not tree.findall(".//s:hyperlink", NS)
            assert all(element.get("TargetMode") != "External" for element in tree.iter())
            assert "vba" not in name.lower() and "externallink" not in name.lower()
        tree = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        text = tree.find(".//s:c[@r='B2']/s:is/s:t", NS).text
        # Decode Excel's one-pass escape notation, preserving original literal
        # _xNNNN_ sequences and CR as distinct from LF.
        decoded = re.sub(r"_x([0-9A-Fa-f]{4})_", lambda match: chr(int(match[1], 16)), text)
        assert decoded == value
    assert all(cell.get("t") == "inlineStr" for cell in xlsx_cells(content))


def test_xlsx_long_unicode_text_is_complete_across_bounded_continuation_rows():
    value = "한글😀=1+1\n" * 5000
    chunks = list(_chunks(value))
    assert "".join(chunks) == value
    assert all(len(part.encode("utf-16-le")) // 2 <= 800 for part in chunks)
    content = render_xlsx(AnalysisReport("synthetic", (ReportSection("판정 근거", (("발췌", value),)),)))
    cells = xlsx_cells(content)
    assert "".join(cell.find("s:is/s:t", NS).text or "" for cell in cells if cell.get("r").startswith("B") and cell.get("r") != "B1") == value


def test_pdf_embeds_korean_fonts_and_never_interprets_dynamic_markup(monkeypatch):
    pytest.importorskip("reportlab")
    import reportlab.platypus.paraparser
    def forbidden(*args, **kwargs):
        raise AssertionError("dynamic image markup must remain inert")
    monkeypatch.setattr(reportlab.platypus.paraparser.ParaParser, "start_img", forbidden)
    detail = detail_fixture()
    detail["result"]["evidence"][0]["excerpt"] = '<img src="https://example.invalid/secret"/> <a href="file:///private">본문</a> 한글 😀\x00'
    content = render_pdf(build_report(detail, generated_at=NOW))
    assert content.startswith(b"%PDF-") and content.rstrip().endswith(b"%%EOF")
    assert b"/FontFile2" in content and b"/ToUnicode" in content
    assert b"/URI" not in content and b"/JavaScript" not in content and b"/Launch" not in content


def test_pdf_generation_error_does_not_expose_document_text(monkeypatch):
    pytest.importorskip("reportlab")
    import reportlab.platypus
    def fail(*args, **kwargs):
        raise ValueError("SYNTHETIC-PRIVATE-REPORT-FRAGMENT")
    monkeypatch.setattr(reportlab.platypus, "SimpleDocTemplate", fail)
    with pytest.raises(ReportExportError, match="^report_export_unavailable$") as caught:
        render_pdf(build_report(detail_fixture()))
    assert caught.value.__suppress_context__
    assert "SYNTHETIC-PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("format", ["pdf", "xlsx"])
def test_export_endpoints_require_admin(client, service_headers, format):
    path = f"/api/v1/analyses/{uuid4()}/report.{format}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=service_headers).status_code == 403


@pytest.mark.parametrize("format", ["pdf", "xlsx"])
def test_download_is_audited_no_store_and_does_not_decrypt_or_run_models(client, event_payload, monkeypatch, format):
    if format == "pdf":
        pytest.importorskip("reportlab")
    id = stored_analysis(client, event_payload)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, id)
        snapshot = (copy.deepcopy(row.result_json), row.payload_ciphertext, row.prompt_snapshot_ciphertext, row.input_schema_snapshot_ciphertext)
    def forbidden(*args, **kwargs):
        raise AssertionError("export must not decrypt or run analysis")
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", forbidden)
    monkeypatch.setattr("app.worker.execute_structured_agent", forbidden)
    response = client.get(f"/api/v1/analyses/{id}/report.{format}")
    assert response.status_code == 200, response.text[:100] if response.status_code != 200 else ""
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == f'attachment; filename="waf-analysis-{id}.{format}"'
    assert response.content.startswith(b"%PDF-" if format == "pdf" else b"PK")
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, id)
        assert (row.result_json, row.payload_ciphertext, row.prompt_snapshot_ciphertext, row.input_schema_snapshot_ciphertext) == snapshot
        records = db.scalars(select(AccessAudit).where(AccessAudit.action == f"export_analysis_{format}")).all()
        assert len(records) == 1 and records[0].resource_id == id
        assert records[0].actor_id == "admin"
    if format == "xlsx":
        values = "\n".join(cell.find("s:is/s:t", NS).text or "" for cell in xlsx_cells(response.content))
        assert "Cookie: session=fixture" not in values
        assert "PRIMARY-MUST-NOT-EXPORT" not in values


def test_latest_reference_comparison_is_explicit_and_not_a_single_case_accuracy(client, event_payload):
    id = stored_analysis(client, event_payload)
    with client.app.state.session_factory() as db:
        for revision, verdict in ((1, "true_positive"), (2, "false_positive")):
            db.add(AnalysisLabel(analysis_id=id, revision=revision, verdict=verdict, source_kind="synthetic_expected", source_ref=f"합성 답안 {revision}", ai_visible=None, created_by="test", attachment_id=str(uuid4()), token_digest="a" * 64))
        db.commit()
    response = client.get(f"/api/v1/analyses/{id}/report.xlsx")
    assert response.status_code == 200
    values = [cell.find("s:is/s:t", NS).text or "" for cell in xlsx_cells(response.content)]
    assert "과탐" in values and "합성 답안 2" in values and "합성 답안 1" not in values
    assert any("최신 답안" in value for value in values)
    assert any("단건 비교" in value for value in values)


def test_missing_nonfinal_query_override_and_oversize_responses(client, event_payload):
    id = stored_analysis(client, event_payload)
    assert client.get(f"/api/v1/analyses/{uuid4()}/report.xlsx").status_code == 404
    assert client.get(f"/api/v1/analyses/{id}/report.xlsx?include_raw=true").status_code == 422
    assert client.get("/api/v1/analyses/not-a-uuid/report.xlsx").status_code == 422
    with client.app.state.session_factory() as db:
        db.get(Analysis, id).status = "failed"; db.commit()
    response = client.get(f"/api/v1/analyses/{id}/report.xlsx")
    assert response.status_code == 409 and response.json()["detail"] == "report_not_final"
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, id); row.status = "completed"
        result = detail_fixture()["result"]; result["evidence"][0]["excerpt"] = "PRIVATE-OVERSIZE" * 100_000
        row.result_json = result; db.commit()
    response = client.get(f"/api/v1/analyses/{id}/report.xlsx")
    assert response.status_code == 413 and response.json()["detail"] == "report_too_large"
    assert "PRIVATE-OVERSIZE" not in response.text
