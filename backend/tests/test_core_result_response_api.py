"""Synthetic public core evidence, single-deadline waits and immutable storage."""
import asyncio
from copy import deepcopy
import json

import pytest
from fastapi import Response
from sqlalchemy import func, select

from app.api import analyses
from app.models import AccessAudit, Analysis, EvidenceDisplayJob, VLLMProfile, utcnow
from app.result_contracts import validate_new_result
from app.services import evidence_display_jobs as jobs
from app.services.core_result_response import to_core_detail
from app.services.evidence_display_plan import build_display_plan, source_hash
from test_analysis_retries_keys import profile_fixture
from test_event_api import service_key
from test_result_contract_v3 import result_fixture

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def dense_result():
    result = result_fixture()
    result["evidence"] = []
    for index in range(5):
        item = deepcopy(result_fixture()["evidence"][0])
        item.update(evidence_id=f"e{index + 1}", excerpt=f"synthetic-query-{index + 1}",
            interpretation_ko=f"허용된 검색 기능으로 전달된 합성 입력 {index + 1}입니다.",
            origins=[{"role": "primary", "index": index, "source_id": f"c{index + 1}"}])
        result["evidence"].append(item)
    ids = [item["evidence_id"] for item in result["evidence"]]
    result["primary"]["evidence_ids"] = ids
    result["policy"]["decision_evidence_ids"] = ids
    return validate_new_result(result)


def saved_analysis(client, event_payload, headers, *, job=False):
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload, headers=headers)
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    profile_id = profile_fixture(client) if job else None
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.result_json, row.verdict = "completed", dense_result(), "false_positive"
        row.severity, row.confidence_score = "NONE", .9
        row.summary_ko, row.completed_at = dense_result()["summary_ko"], utcnow()
        display = jobs.pin_job(db, client.app.state.crypto, row,
            jobs.capture_display_profile(db.get(VLLMProfile, profile_id))) if job else None
        db.commit()
        return identifier, display.id if display else None


def complete_display(client, identifier):
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        plan = build_display_plan(row.result_json, groups=[{
            "member_ids": [item["evidence_id"] for item in row.result_json["evidence"]], "representative_id": "e1"}],
            status="completed")
        job = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == identifier))
        job.status, job.source_hash = "completed", source_hash(row.result_json)
        job.output_ciphertext = client.app.state.crypto.encrypt_text(json.dumps({"plan": plan}))
        db.commit()


@pytest.mark.parametrize("version", ["v2", "v3"])
def test_api_returns_only_selected_evidence_in_every_branch_without_modifying_storage(
    client, event_payload, service_headers, version,
):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    for response in (
        client.get(f"/api/v1/analyses/{identifier}", params={"result_version": version}, headers=service_headers),
        client.get("/api/v1/analyses/by-event", params={"event_id": event_payload["event_id"], "result_version": version}, headers=service_headers),
        client.post("/api/v1/analyses?wait_seconds=0", params={"result_version": version}, json=event_payload, headers=service_headers),
    ):
        assert response.status_code == 200, response.text
        value = response.json()["result"]
        assert value["schema_version"] == f"waf-analysis-{version}"
        assert response.headers["X-Evidence-View"] == "core-v1"
        assert response.headers["Cache-Control"] == "no-store"
        assert int(response.headers["X-Evidence-Original-Count"]) == 5
        assert 1 <= int(response.headers["X-Evidence-Returned-Count"]) <= 3
        assert "synthetic-query-5" not in response.text
        if version == "v3":
            assert len(validate_new_result(value)["evidence"]) <= 3
        else:
            assert len(value["analyst_assessment"]["evidence"]) <= 3
            assert len(value["primary"]["evidence"]) <= 3
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).result_json == dense_result()
        assert db.scalar(select(func.count()).select_from(EvidenceDisplayJob)) == 0


def test_pending_display_is_processing_with_null_decision_but_never_mutates_completed_db(
    client, event_payload, service_headers,
):
    identifier, job_id = saved_analysis(client, event_payload, service_headers, job=True)
    for method in ("GET", "POST"):
        response = (client.get(f"/api/v1/analyses/{identifier}", headers=service_headers) if method == "GET" else
            client.post("/api/v1/analyses?wait_seconds=0", json=event_payload, headers=service_headers))
        assert response.status_code == (200 if method == "GET" else 202), response.text
        body = response.json()
        assert body["status"] == "processing" and body["result"] is None
        assert all(body[field] is None for field in (
            "verdict", "severity", "confidence_score", "summary_ko", "completed_at", "processing_duration_ms"))
        assert response.headers["X-Evidence-Status"] == "pending"
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).status == "completed"
        assert db.get(Analysis, identifier).result_json == dense_result()
        assert db.get(EvidenceDisplayJob, job_id).status == "pending"
        assert db.scalar(select(func.count()).select_from(EvidenceDisplayJob)) == 1


def test_saved_semantic_plan_is_shared_by_post_get_and_admin_without_new_model_work(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, job_id = saved_analysis(client, event_payload, service_headers, job=True)
    complete_display(client, identifier)
    def forbid(*_args, **_kwargs):
        raise AssertionError("A result read must not create or run a display job")
    monkeypatch.setattr(jobs, "process_job", forbid)
    monkeypatch.setattr(jobs, "pin_job", forbid)
    public = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers)
    repeated = client.post("/api/v1/analyses?wait_seconds=0&result_version=v3", json=event_payload, headers=service_headers)
    assert public.status_code == repeated.status_code == 200
    assert len(public.json()["result"]["evidence"]) == 1
    assert public.json()["result"] == repeated.json()["result"]
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200
    admin = client.get(f"/api/v1/analyses/{identifier}")
    assert admin.json()["result"] == public.json()["result"]
    with client.app.state.session_factory() as db:
        assert db.get(EvidenceDisplayJob, job_id).status == "completed"
        assert db.get(Analysis, identifier).result_json == dense_result()


def test_single_http_wait_observes_analysis_then_display_without_holding_connection(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, _ = saved_analysis(client, event_payload, service_headers, job=True)
    with client.app.state.session_factory() as db:
        db.get(Analysis, identifier).status = "processing"
        db.commit()
        sleeps = []
        async def progresses(_seconds):
            assert not db.in_transaction()
            sleeps.append(_seconds)
            if len(sleeps) == 1:
                with client.app.state.session_factory() as worker:
                    worker.get(Analysis, identifier).status = "completed"
                    worker.commit()
            else:
                complete_display(client, identifier)
        monkeypatch.setattr(analyses.asyncio, "sleep", progresses)
        row = asyncio.run(analyses.wait_for_analysis(db, identifier, "test-parser", 600))
        assert len(sleeps) == 2
        detail, metadata = to_core_detail(db, client.app.state.crypto, row)
        assert detail.status == "completed" and metadata["status"] == "completed"
        assert len(detail.result.evidence) == 1


def test_http_timeout_does_not_fail_the_display_job_or_leak_full_result(client, event_payload, service_headers):
    identifier, job_id = saved_analysis(client, event_payload, service_headers, job=True)
    response = client.post("/api/v1/analyses?wait_seconds=1", json=event_payload, headers=service_headers)
    assert response.status_code == 202 and response.json()["result"] is None
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).status == "completed"
        assert db.get(EvidenceDisplayJob, job_id).status == "pending"
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1


def test_disconnect_preserves_existing_job_and_closes_status_transaction(client, event_payload, service_headers, monkeypatch):
    identifier, job_id = saved_analysis(client, event_payload, service_headers, job=True)
    with client.app.state.session_factory() as db:
        async def disconnect(_seconds):
            assert not db.in_transaction()
            raise asyncio.CancelledError
        monkeypatch.setattr(analyses.asyncio, "sleep", disconnect)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(analyses.wait_for_analysis(db, identifier, "test-parser", 600))
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).status == "completed"
        assert db.get(EvidenceDisplayJob, job_id).status == "pending"


def test_core_read_keeps_source_boundary_and_audits_before_encrypted_access(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    foreign = service_key(client, source="synthetic-foreign-core")
    assert client.get(f"/api/v1/analyses/{identifier}", headers=foreign).status_code == 404
    from app.services import evidence_display_access
    original = evidence_display_access.read_display_view
    def guarded(db, crypto, row):
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "view_core_result",
            AccessAudit.resource_id == identifier))
        return original(db, crypto, row)
    monkeypatch.setattr(evidence_display_access, "read_display_view", guarded)
    assert client.get(f"/api/v1/analyses/{identifier}", headers=service_headers).status_code == 200


def test_corrupt_relation_source_returns_fixed_unavailable_without_result_leak(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    def broken(*_args):
        raise ValueError("SYNTHETIC_PRIVATE_RAW_LOG")
    monkeypatch.setattr("app.services.evidence_display_access.read_display_view", broken)
    response = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers)
    assert response.status_code == 503 and response.json() == {"detail": "core_result_unavailable"}
    assert response.headers["Cache-Control"] == "no-store" and "SYNTHETIC_PRIVATE_RAW_LOG" not in response.text


def test_preloaded_display_view_needs_no_additional_reads(client, event_payload, service_headers, monkeypatch):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        # Summary projection may read lazy reviews; resolve them before the
        # view-injection assertion, as batch export already does.
        _ = row.reviews
        def forbid(*_args):
            raise AssertionError("A preloaded display view must not be fetched again")
        monkeypatch.setattr("app.services.evidence_display_access.read_display_view", forbid)
        view = {"status": "disabled", "relations": None, "plan": build_display_plan(row.result_json)}
        detail, metadata = to_core_detail(db, client.app.state.crypto, row, display_view=view)
        assert len(detail.result.evidence) <= 3 and metadata["returned_count"] <= 3


def test_export_bridge_can_exclude_encrypted_field_history(client, event_payload, service_headers, monkeypatch):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        _ = row.reviews
        assert row.input_schema_snapshot_ciphertext
        view = {"status": "disabled", "relations": None, "plan": build_display_plan(row.result_json)}
        def forbidden(*_args):
            raise AssertionError("Report field history must not be decrypted")
        monkeypatch.setattr(client.app.state.crypto, "decrypt_text", forbidden)
        detail, metadata = to_core_detail(db, client.app.state.crypto, row, display_view=view,
            include_field_metadata=False)
        assert detail.input_schema_metadata is None and metadata["returned_count"] <= 3


@pytest.mark.parametrize("schema", ["waf-analysis-v1", "waf-analysis-v2"])
def test_historical_results_are_not_falsely_labeled_as_core(client, event_payload, service_headers, schema):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    historical = {"schema_version": schema, "historical_payload": {"keep": True}}
    with client.app.state.session_factory() as db:
        db.get(Analysis, identifier).result_json = historical
        db.commit()
    response = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers)
    assert response.status_code == 200 and response.json()["result"] == historical
    assert response.headers["Cache-Control"] == "no-store"
    assert not any(key.startswith("x-evidence-") for key in response.headers)


def test_invalid_omitted_evidence_cannot_hide_behind_core_selection(client, event_payload, service_headers):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    with client.app.state.session_factory() as db:
        invalid = dense_result()
        invalid["evidence"][-1]["undeclared"] = "SYNTHETIC_PRIVATE_OMITTED_LOG"
        db.get(Analysis, identifier).result_json = invalid
        db.commit()
    response = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers)
    assert response.status_code == 503 and response.json() == {"detail": "analysis_result_invalid"}
    assert "SYNTHETIC_PRIVATE_OMITTED_LOG" not in response.text
