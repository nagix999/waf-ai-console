"""Synthetic parity checks: the API subset is also the downloadable subset."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import event

from app.models import Analysis, EvidenceDisplayJob
from app.services.analysis_exports import build_report
from app.services.analyst_presentation import assessment_view
from app.services.report_pdf import pdf_document
from app.services.test_run_exports import evidence_columns
from test_analysis_exports import detail_fixture, login, report_text, xlsx_cells
from test_core_result_response_api import dense_result, saved_analysis
from test_filtered_test_exports import download, preview
from test_test_statistics_exports import fixture_run, workbook

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def assert_selected_content(text, original, selected):
    selected_ids = {item["evidence_id"] for item in selected}
    for item in original["evidence"]:
        assert (item["excerpt"] in text) == (item["evidence_id"] in selected_ids)


def test_api_excel_and_pdf_share_selected_objects_without_full_plan_reapplication(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    response = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers)
    assert response.status_code == 200, response.text
    selected = response.json()["result"]["evidence"]
    assert 0 < len(selected) < len(dense_result()["evidence"])
    login(client)
    excel = client.get(f"/api/v1/analyses/{identifier}/report.xlsx")
    assert excel.status_code == 200, excel.text if excel.status_code != 200 else ""
    assert excel.headers["X-Evidence-View"] == "core-v1"
    excel_text = "\n".join("".join(cell.itertext()) for cell in xlsx_cells(excel.content))
    assert_selected_content(excel_text, dense_result(), selected)
    documents = []
    def render(document):
        documents.append(document)
        return b"%PDF-synthetic-core-report"
    monkeypatch.setattr("app.api.analysis_exports.render_report_pdf", render)
    pdf = client.get(f"/api/v1/analyses/{identifier}/report.pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["X-Evidence-View"] == "core-v1"
    document = documents[0]
    assert document["evidenceView"] == "core-v1"
    assert "evidence_display" not in document["detail"]
    assert document["detail"]["result"]["evidence"] == selected
    assert_selected_content(json.dumps(document, ensure_ascii=False), dense_result(), selected)
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).result_json == dense_result()


def test_waiting_organization_is_not_exported_as_full_or_final(client, event_payload, service_headers):
    identifier, job_id = saved_analysis(client, event_payload, service_headers, job=True)
    login(client)
    for extension in ("pdf", "xlsx"):
        response = client.get(f"/api/v1/analyses/{identifier}/report.{extension}")
        assert response.status_code == 409 and response.json() == {"detail": "report_not_final"}
        assert response.headers["cache-control"] == "no-store"
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).status == "completed"
        assert db.get(EvidenceDisplayJob, job_id).status == "pending"


def test_core_formatters_do_not_merge_received_items_or_renumber_original_ids():
    result = dense_result()
    result["evidence"] = [deepcopy(result["evidence"][3]), deepcopy(result["evidence"][0])]
    # Deliberately equal-looking originals still have distinct provenance and IDs.
    result["evidence"][1].update(excerpt=result["evidence"][0]["excerpt"],
        interpretation_ko=result["evidence"][0]["interpretation_ko"])
    detail = {**detail_fixture(), "result": result}
    before = deepcopy(detail)
    assessment = assessment_view(result, evidence_view="core-v1")
    assert [item["evidence_id"] for item in assessment["evidence"]] == ["e4", "e1"]
    assert [item["number"] for item in assessment["evidence"]] == [4, 1]
    text = report_text(build_report(detail, evidence_view="core-v1"))
    explanation = result["evidence"][0]["interpretation_ko"]
    assert text.count(explanation) == 2
    assert "근거 4" in text and "근거 1" in text
    columns = evidence_columns(result, evidence_view="core-v1")
    assert columns["evidence_normal_request"].count(explanation) == 2
    assert "\n---\n" in columns["evidence_normal_request"]
    assert pdf_document(detail, evidence_view="core-v1")["evidenceView"] == "core-v1"
    assert detail == before


def test_core_report_preserves_received_order_across_support_directions_and_all_conditions():
    result = dense_result()
    result["verdict"] = "inconclusive"
    result["diagnostics"]["inconclusive_reasons"] = ["verifier_failed"]
    result["evidence"] = [result["evidence"][3], result["evidence"][0], result["evidence"][2]]
    result["evidence"][0]["supports"] = "context"
    result["evidence"][2]["supports"] = "attack_request"
    result["decision_issues"] = [{"point_ko": "합성 서비스의 입력 사용 조건을 구분해야 합니다.",
        "evidence_ids": ["e4", "e3"], "missing_condition_ko": "이 입력의 실제 처리 위치를 확인합니다.",
        "origins": ["primary"]}]
    report = build_report({**detail_fixture(), "result": result}, evidence_view="core-v1")
    text = report_text(report)
    assert text.index("근거 4 · 위치") < text.index("근거 1 · 위치") < text.index("근거 3 · 위치")
    assert text.count(result["decision_issues"][0]["point_ko"]) == 1
    assert text.count(result["decision_issues"][0]["missing_condition_ko"]) == 1


def test_batch_excel_uses_core_subset_with_bounded_history_queries(client, event_payload):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.result_json = dense_result()
        row.status, row.verdict, row.severity = "completed", "false_positive", "NONE"
        db.commit()
    detail = client.get(f"/api/v1/analyses/{identifier}").json()
    selected = detail["result"]["evidence"]
    metadata = preview(client, run)
    statements = []
    engine = client.app.state.session_factory.kw["bind"]
    def trace(_connection, _cursor, statement, *_args):
        if "agent_steps" in statement.lower() or "evidence_display_jobs" in statement.lower():
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", trace)
    try:
        response = download(client, run, {}, metadata, ["event_id", "evidence_normal_request"])
    finally:
        event.remove(engine, "before_cursor_execute", trace)
    _, _, rows = workbook(response)
    exported = next(row for row in rows if row["event_id"] == detail["event_id"])
    assert_selected_content(exported["evidence_normal_request"], dense_result(), selected)
    assert len(statements) == 2


def test_batch_pending_evidence_is_explicit_but_metadata_export_still_works(client, event_payload):
    from app.services import evidence_display_jobs as jobs
    from app.models import VLLMProfile
    from test_analysis_retries_keys import profile_fixture
    run = fixture_run(client, event_payload)
    profile_id = profile_fixture(client)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.result_json, row.status = dense_result(), "completed"
        jobs.pin_job(db, client.app.state.crypto, row, jobs.capture_display_profile(db.get(VLLMProfile, profile_id)))
        db.commit()
    metadata = preview(client, run)
    response = download(client, run, {}, metadata, ["evidence_normal_request"])
    assert response.status_code == 409 and response.json() == {"detail": "test_export_evidence_pending"}
    assert download(client, run, {}, metadata, ["event_id", "verdict"]).status_code == 200


@pytest.mark.skipif(os.environ.get("WAF_TEST_REPORT_RENDERER") != "1", reason="Release image renderer check")
def test_core_pdf_shared_formatter_and_real_renderer_only_receive_selected_evidence():
    from app.services.core_evidence_v3 import build_core_result
    from app.services.report_pdf import render_report_pdf
    original = dense_result()
    result = build_core_result(original)["result"]
    document = pdf_document({**detail_fixture(), "result": result}, evidence_view="core-v1")
    asset = Path(__file__).parents[1] / "app/data/report-document.cjs"
    formatted = subprocess.run(["node", str(asset)], input=json.dumps(document, default=str).encode(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert formatted.returncode == 0, formatted.stderr.decode(errors="replace")
    text = json.dumps(json.loads(formatted.stdout), ensure_ascii=False)
    assert_selected_content(text, original, result["evidence"])
    body = render_report_pdf(json.loads(json.dumps(document, default=str)))
    assert body.startswith(b"%PDF-") and b"/FontFile2" in body
