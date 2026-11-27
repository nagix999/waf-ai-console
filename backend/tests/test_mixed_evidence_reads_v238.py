"""Synthetic encrypted final records: API, Editor readiness and export parity."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from app.models import AccessAudit, AgentRun, AgentStep, Analysis, EvidenceDisplayJob, VLLMProfile
from app.services import evidence_display_jobs_v3 as old_jobs
from app.services import evidence_display_jobs_v4 as jobs
from app.services.evidence_display_access import read_many_evidence_relations
from app.services.evidence_display_plan_v3 import source_hash
from app.services.evidence_relation_dispatch import validate_saved_relations
from test_analysis_exports import login, xlsx_cells
from test_analysis_retries_keys import profile_fixture
from test_evidence_relations_api import saved_analysis
from test_filtered_test_exports import download, preview
from test_mixed_evidence_relations_v238 import result, relations, view
from test_test_statistics_exports import fixture_run, workbook


pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def store(client, identifier, *, value=None, refs=None):
    value, refs = value or result(), refs or relations()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.result_json = "completed", deepcopy(value)
        row.verdict, row.severity = value["verdict"], value["threat_analysis"]["severity"]
        row.summary_ko, row.confidence_score = value["summary_ko"], value["confidence_score"]
        run = AgentRun(analysis_id=identifier, status="completed")
        db.add(run)
        db.flush()
        db.add(AgentStep(run_id=run.id, sequence=7, step_type="finalize", name="Synthetic essential comparison",
            status="completed", output_ciphertext=client.app.state.crypto.encrypt_text(
                json.dumps({**value, "evidence_relations": refs}))))
        db.commit()
        return run.id


def saved(client, event_payload, headers):
    identifier, _, _ = saved_analysis(client, event_payload, headers)
    store(client, identifier)
    return identifier


def test_public_v3_and_admin_relations_share_preserved_opposite_evidence(client, event_payload, service_headers):
    identifier = saved(client, event_payload, service_headers)
    path = f"/api/v1/analyses/{identifier}"
    assert client.get(path + "/evidence-relations", headers=service_headers).status_code == 403
    public = client.get(path, headers=service_headers)
    assert public.status_code == 200, public.text
    received = public.json()["result"]
    assert received["schema_version"] == "waf-analysis-v3"
    assert received["verdict"] == "true_positive" and received["interpretation_conflict"] is False
    assert received["verifier"]["executed"] is False
    assert {item["supports"] for item in received["evidence"]} >= {"attack_request", "normal_request"}
    assert "evidence_relations" not in received
    assert public.headers["X-Evidence-View"] == "core-v2"
    login(client)
    linked = client.get(path + "/evidence-relations")
    assert linked.status_code == 200 and linked.json() == relations()
    assert linked.headers["Cache-Control"] == "no-store"
    display = client.get(path + "/evidence-display")
    assert display.status_code == 200 and display.json()["version"] == jobs.VIEW_VERSION
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).result_json == result()
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "view_evidence_relations"))


@pytest.mark.parametrize("damage", ["old_policy", "wrong_relation_version", "bad_origin"])
def test_new_tag_does_not_reinterpret_old_or_unproven_records(client, event_payload, service_headers, damage):
    identifier = saved(client, event_payload, service_headers)
    value, refs = result(), relations()
    if damage == "old_policy": value["policy"]["fixed_rules_version"] = "waf-system-v2.36"
    if damage == "wrong_relation_version": refs["version"] = "evidence-relations-v1"
    if damage == "bad_origin": refs["groups"][0]["role"] = "verifier"
    store(client, identifier, value=value, refs=refs)
    with pytest.raises(ValueError):
        validate_saved_relations(refs, value)
    login(client)
    response = client.get(f"/api/v1/analyses/{identifier}/evidence-relations")
    assert response.status_code == 500 and response.json()["detail"] == "evidence_relations_unavailable"


def test_api_excel_and_pdf_use_identical_core_objects_and_no_hidden_counterloss(
        client, event_payload, service_headers, monkeypatch):
    identifier = saved(client, event_payload, service_headers)
    selected = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers).json()["result"]["evidence"]
    login(client)
    excel = client.get(f"/api/v1/analyses/{identifier}/report.xlsx")
    assert excel.status_code == 200, excel.text if excel.status_code != 200 else ""
    text = "\n".join("".join(cell.itertext()) for cell in xlsx_cells(excel.content))
    selected_ids = {item["evidence_id"] for item in selected}
    assert {"e1", "e2"} <= selected_ids
    for item in result()["evidence"]:
        assert (item["excerpt"] in text) == (item["evidence_id"] in selected_ids)
    captured = []
    monkeypatch.setattr("app.api.analysis_exports.render_report_pdf",
        lambda document: captured.append(document) or b"%PDF-synthetic-mixed-refs")
    response = client.get(f"/api/v1/analyses/{identifier}/report.pdf")
    assert response.status_code == 200, response.text
    assert captured[0]["detail"]["result"]["evidence"] == selected
    assert captured[0]["evidenceView"] == "core-v2"


def test_batch_export_uses_bounded_mixed_reference_reader(client, event_payload):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    store(client, identifier)
    with client.app.state.session_factory() as db:
        mapping = read_many_evidence_relations(db, client.app.state.crypto, [db.get(Analysis, identifier)])
        assert mapping[identifier] == relations()
    selected = client.get(f"/api/v1/analyses/{identifier}").json()["result"]["evidence"]
    response = download(client, run, {}, preview(client, run),
                        ["event_id", "evidence_attack_request", "evidence_normal_request"])
    assert response.status_code == 200, response.text if response.status_code != 200 else ""
    _, _, rows = workbook(response)
    row = next(row for row in rows if row["event_id"] == "synthetic-0")
    for item in selected:
        if item["supports"] in {"attack_request", "normal_request"}:
            assert item["interpretation_ko"] in row["evidence_" + item["supports"]]


def test_new_job_readiness_is_pending_then_source_bound_without_editing_judgment(
        client, event_payload, service_headers):
    identifier = saved(client, event_payload, service_headers)
    login(client)
    profile_id = profile_fixture(client)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        selected = jobs.capture_display_profile(db.get(VLLMProfile, profile_id))
        job = jobs.pin_job(db, client.app.state.crypto, row, selected)
        db.commit()
        job_id = job.id
    pending = client.get(f"/api/v1/analyses/{identifier}").json()
    assert pending["status"] == "processing" and pending["result"] is None
    with client.app.state.session_factory() as db:
        job = db.get(EvidenceDisplayJob, job_id)
        display = view(result(), relations())
        job.status, job.source_hash = "skipped", source_hash(result(), relations())
        job.output_ciphertext = client.app.state.crypto.encrypt_text(json.dumps({"plan": display["plan"]}))
        db.commit()
    response = client.get(f"/api/v1/analyses/{identifier}")
    assert response.status_code == 200 and response.json()["status"] == "completed", response.text
    assert response.json()["result"]["verdict"] == "true_positive"


def test_invalid_new_editor_snapshot_uses_safe_core_fallback_not_old_mixed_rejection(
        client, event_payload, service_headers):
    identifier = saved(client, event_payload, service_headers)
    with client.app.state.session_factory() as db:
        db.add(EvidenceDisplayJob(analysis_id=identifier, snapshot_ciphertext="synthetic-invalid",
            encryption_key_version=client.app.state.crypto.key_version, status="failed"))
        db.commit()
    response = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers)
    assert response.status_code == 200, response.text
    assert {item["supports"] for item in response.json()["result"]["evidence"]} >= {"attack_request", "normal_request"}


def test_new_processor_skips_unneeded_model_call_and_records_verified_projection(
        client, event_payload, service_headers, monkeypatch):
    identifier = saved(client, event_payload, service_headers)
    login(client)
    profile_id = profile_fixture(client)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        selected = jobs.capture_display_profile(db.get(VLLMProfile, profile_id))
        job = jobs.pin_job(db, client.app.state.crypto, row, selected)
        db.commit()
        job_id = job.id
        assert jobs.claim_next(db, "synthetic-v4-owner") == job_id
    async def forbidden(**_kwargs):
        pytest.fail("An already distinct set must not trigger an Editor model call")
    monkeypatch.setattr("app.agent.executor_v221.execute_structured_agent", forbidden)
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, job_id, "synthetic-v4-owner")
    with client.app.state.session_factory() as db:
        row, job = db.get(Analysis, identifier), db.get(EvidenceDisplayJob, job_id)
        assert row.result_json == result() and row.status == "completed"
        assert job.status == "skipped" and job.metadata_json["model_called"] is False
        assert job.metadata_json["projection_fallback"] is False
        assert result()["evidence"][0]["excerpt"] not in job.input_ciphertext
        assert result()["evidence"][0]["excerpt"] not in json.dumps(job.metadata_json)
        display = jobs.get_display_view(db, client.app.state.crypto, row)
        assert display["status"] == "skipped" and display["relations"] == relations()


def test_frozen_v3_qualification_is_delegated_without_weakening_projection_gate(monkeypatch):
    from types import SimpleNamespace
    run = SimpleNamespace(display_editor_snapshot_ciphertext="synthetic", configuration_snapshot_json={
        "evidence_editor": {"version": old_jobs.VERSION}})
    calls = []
    monkeypatch.setattr(old_jobs, "qualification", lambda *args: calls.append(args) or {"ready": False})
    assert jobs.qualification(object(), run) == {"ready": False}
    assert len(calls) == 1
