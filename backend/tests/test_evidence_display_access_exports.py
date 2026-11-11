"""Synthetic audited reads and matching detail/report/export representations."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import event, select

from app.models import AccessAudit, AgentStep, Analysis, EvidenceDisplayJob
from app.services.analyst_presentation import assessment_view
from app.services.analysis_exports import build_report
from app.services.evidence_display_access import read_many_evidence_relations
from app.services.report_pdf import pdf_document
from app.services.test_run_exports import evidence_columns
from test_analysis_exports import detail_fixture, report_text
from test_evidence_display_jobs import enqueue_job, install_model, run_job
from test_evidence_relations_api import login, relation_fixture, saved_analysis


def test_display_endpoint_requires_admin_and_keeps_public_result_closed(client, event_payload, service_headers):
    identifier, _, original = saved_analysis(client, event_payload, service_headers)
    path = f"/api/v1/analyses/{identifier}/evidence-display"
    assert client.get(path, headers=service_headers).status_code == 403
    assert client.get("/api/v1/analyses/missing/evidence-display", headers=service_headers).status_code == 403
    public = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers).json()
    from app.result_contracts import AnalysisResultV3
    AnalysisResultV3.model_validate(public["result"])
    assert public["result"]["evidence"] == original["evidence"]
    assert public["result"]["verdict"] == original["verdict"]
    assert "evidence_display" not in public
    login(client)
    response = client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    value = response.json()
    assert value["version"] == "evidence-display-view-v1" and value["status"] == "disabled"
    assert value["relations"] == relation_fixture()
    assert value["plan"]["groups"][0]["related_ids"] == ["e2"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit).where(AccessAudit.action == "view_evidence_display")).resource_id == identifier
        assert db.get(Analysis, identifier).result_json == original
        assert db.scalar(select(EvidenceDisplayJob.id)) is None
    assert "/api/v1/analyses/{analysis_id}/evidence-display" not in client.get("/openapi.json").json()["paths"]
    assert client.get("/api/v1/analyses/missing/evidence-display").status_code == 404


def test_corrupt_display_history_has_audited_content_free_failure(client, event_payload, service_headers):
    identifier, run_id, _ = saved_analysis(client, event_payload, service_headers)
    secret = "SYNTHETIC_PRIVATE_SOURCE_DO_NOT_EXPOSE"
    with client.app.state.session_factory() as db:
        db.scalar(select(AgentStep).where(AgentStep.run_id == run_id)).output_ciphertext = secret
        db.commit()
    login(client)
    response = client.get(f"/api/v1/analyses/{identifier}/evidence-display")
    assert response.status_code == 503 and response.json() == {"detail": "evidence_display_unavailable"}
    assert response.headers["cache-control"] == "no-store" and secret not in response.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "view_evidence_display"))


def test_batch_history_reads_only_latest_completed_source_in_one_query(client, event_payload, service_headers):
    identifier, _, _ = saved_analysis(client, event_payload, service_headers)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, identifier)
        statements = []
        def trace(_connection, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(db.get_bind(), "before_cursor_execute", trace)
        try:
            values = read_many_evidence_relations(db, client.app.state.crypto, [analysis])
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", trace)
        assert values == {identifier: relation_fixture()}
        assert len(statements) == 1
        assert read_many_evidence_relations(db, client.app.state.crypto, []) == {}


@pytest.mark.usefixtures("registered_vllm_target")
def test_detail_report_pdf_and_excel_share_representative_and_all_sources(client, event_payload, monkeypatch):
    identifier, job_id, _ = enqueue_job(client, event_payload)
    calls = install_model(monkeypatch)
    run_job(client, job_id)
    view = client.get(f"/api/v1/analyses/{identifier}/evidence-display").json()
    with client.app.state.session_factory() as db:
        original = deepcopy(db.get(Analysis, identifier).result_json)
    detail = {**detail_fixture(), "result": original, "evidence_display": view}
    before = deepcopy(detail)
    assessment = assessment_view(original, display_view=view)
    assert len(assessment["evidence"]) == 1
    group = assessment["evidence"][0]
    assert group["evidence_id"] == "e2" and group["number"] == 2
    assert group["related_sources"][0]["evidence_id"] == "e1"
    text = report_text(build_report(detail))
    columns = evidence_columns(original, display_view=view)
    for content in (text, columns["evidence_normal_request"]):
        assert content.count(original["evidence"][1]["interpretation_ko"]) == 1
        assert original["evidence"][0]["interpretation_ko"] not in content
        assert all(item["excerpt"] in content for item in original["evidence"])
    from app.services.evidence_display_plan import validate_display_plan
    pdf_detail = pdf_document(detail)["detail"]
    assert pdf_detail["evidence_display"]["plan"] == view["plan"]
    assert validate_display_plan(pdf_detail["result"], view["relations"], view["plan"]) == view["plan"]
    assert detail == before and len(calls) == 1


def test_pdf_sidecar_allowlist_drops_unknown_metadata_and_cannot_hide_corrupt_sources():
    from test_evidence_display_jobs import canonical
    from app.services.evidence_display_plan import build_display_plan
    result = canonical()
    relations = {"version": "evidence-relations-v1", "groups": []}
    plan = build_display_plan(result, relations, groups=[{"representative_id": "e2", "member_ids": ["e1", "e2"]}], status="completed")
    detail = {**detail_fixture(), "result": result, "evidence_display": {
        "version": "evidence-display-view-v1", "status": "completed", "plan": plan,
        "relations": relations, "metadata": {"private": "SYNTHETIC_PRIVATE_METADATA"},
        "snapshot_ciphertext": "SYNTHETIC_SECRET_CIPHERTEXT"}}
    serialized = json.dumps(pdf_document(detail), ensure_ascii=False, default=str)
    assert "SYNTHETIC_PRIVATE_METADATA" not in serialized and "SYNTHETIC_SECRET_CIPHERTEXT" not in serialized
    detail["evidence_display"]["plan"]["groups"][0]["member_ids"] = ["e2"]
    clean = pdf_document(detail)["detail"]["evidence_display"]
    assert clean["status"] == "fallback" and len(clean["plan"]["groups"]) == 2
    assert all(group["default_visible"] for group in clean["plan"]["groups"])


@pytest.mark.skipif(os.environ.get("WAF_TEST_REPORT_RENDERER") != "1", reason="Release image renderer check")
def test_real_pdf_formatter_and_renderer_preserve_the_same_bound_group():
    from test_evidence_display_jobs import canonical
    from app.services.evidence_display_plan import build_display_plan
    from app.services.report_pdf import render_report_pdf
    result = canonical()
    relations = {"version": "evidence-relations-v1", "groups": []}
    plan = build_display_plan(result, relations, groups=[{"representative_id": "e2", "member_ids": ["e1", "e2"]}], status="completed")
    document = pdf_document({**detail_fixture(), "result": result, "evidence_display": {
        "version": "evidence-display-view-v1", "status": "completed", "plan": plan, "relations": relations}})
    asset = Path(__file__).parents[1] / "app/data/report-document.cjs"
    formatted = subprocess.run(["node", str(asset)], input=json.dumps(document, default=str).encode(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert formatted.returncode == 0
    text = json.dumps(json.loads(formatted.stdout), ensure_ascii=False)
    assert text.count(result["evidence"][1]["interpretation_ko"]) == 1
    assert result["evidence"][0]["interpretation_ko"] not in text
    assert all(item["excerpt"] in text for item in result["evidence"])
    body = render_report_pdf(json.loads(json.dumps(document, default=str)))
    assert body.startswith(b"%PDF-") and b"/FontFile2" in body
