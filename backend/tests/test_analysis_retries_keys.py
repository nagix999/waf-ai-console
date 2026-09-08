"""Synthetic in-memory admissions; all model calls are mocked."""
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import worker
from app.agent.executor import AgentCallResult
from app.api_key_schemas import ServiceApiKeyCreate
from app.models import AccessAudit, AgentRun, AgentStep, Analysis, AnalysisLabel, InternalEgressTarget, ServiceApiKey, TestRun as NamedRun, TestRunItem as NamedItem, VLLMProfile, VLLMTestRun, utcnow
from app.services.analysis_retries import RetryError, execution_request_check, load_execution_snapshot, make_execution_snapshot
from app.services.prompt_snapshots import load_analysis_prompt
from app.services.service_api_keys import issue_key
from app.services.vllm_profiles import profile_fingerprint
from test_common_prompt_admission import activate, save_policy
from test_prompt_snapshots import fake_output


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def profile_fixture(client, *, is_test=False):
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(name="synthetic-original", model_name="synthetic-model", base_url="http://10.0.0.10:8000/v1",
                              status="production", is_test=is_test)
        db.add(profile)
        db.flush()
        db.add(VLLMTestRun(profile_id=profile.id, mode="full", status="passed", profile_fingerprint=profile_fingerprint(profile)))
        db.commit()
        return profile.id


def failed_fixture(client, event, profile_id):
    response = client.post("/api/v1/analyses", json=event)
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        prompt = load_analysis_prompt(row, client.app.state.crypto)
        snapshot = make_execution_snapshot(row, db.get(VLLMProfile, profile_id), prompt, 0.73)
        row.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(snapshot.model_dump_json())
        row.status = "failed"
        row.error_code = "synthetic-original-failure"
        row.completed_at = utcnow()
        db.commit()
    return identifier


def retry(client, identifier, key="synthetic-retry-key"):
    return client.post(f"/api/v1/analyses/{identifier}/retry", json={"idempotency_key": key, "cost_acknowledged": True})


def key_fixture(client, name):
    with client.app.state.session_factory() as db:
        key, raw = issue_key(db, ServiceApiKeyCreate(name=name, source_system="synthetic-shared", scopes=["ingest"]), "synthetic")
        db.commit()
        return key.id, {"x-api-key": raw}


def test_key_attribution_first_admission_only_including_upload_and_deletion(client, event_payload):
    first_id, first_headers = key_fixture(client, "first")
    second_id, second_headers = key_fixture(client, "second")
    original = client.post("/api/v1/analyses", headers=first_headers, json=event_payload).json()
    replay = client.post("/api/v1/analyses", headers=second_headers, json=event_payload).json()
    assert replay["id"] == original["id"] and replay["service_api_key_id"] == first_id
    uploaded = client.post("/api/v1/uploads", headers=second_headers, files={
        "file": ("synthetic.json", json.dumps([{**event_payload, "event_id": "second-event"}]).encode()),
    })
    assert uploaded.status_code == 202 and uploaded.json()["accepted"] == 1
    login(client)
    for identifier in (first_id, second_id):
        body = client.get("/api/v1/dashboard/summary", params={"service_api_key_id": identifier}).json()
        assert body["counts"]["total"] == body["evaluation_summary"]["total"] == 1
        assert sum(day["total"] for day in body["trend"]) == 1
        assert client.get("/api/v1/analyses", params={"service_api_key_id": identifier}).json()["total"] == 1
    assert client.delete(f"/api/v1/admin/service-api-keys/{first_id}").status_code == 204
    assert client.delete(f"/api/v1/admin/service-api-keys/{first_id}").status_code == 204
    assert first_id not in {item["id"] for item in client.get("/api/v1/admin/service-api-keys").json()["items"]}
    assert client.get("/api/v1/dashboard/summary", params={"service_api_key_id": first_id}).status_code == 404
    assert client.get(f"/api/v1/analyses/{original['id']}").status_code == 200
    assert client.get("/api/v1/dashboard/summary").json()["counts"]["total"] == 2
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, original["id"]).service_api_key_id == first_id
        assert db.get(ServiceApiKey, first_id).deleted_at is not None
        assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action == "delete_service_api_key")) == 1
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/analyses", headers=first_headers).status_code == 401
    assert client.get("/api/v1/analyses", headers=second_headers).status_code == 200


def test_retry_and_key_delete_are_admin_only_and_cost_ack_is_required(client, event_payload, service_headers):
    identifier = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()["id"]
    for suffix, method in (("retry-eligibility", "get"), ("retry", "post")):
        kwargs = {"json": {"idempotency_key": "synthetic-key", "cost_acknowledged": True}} if method == "post" else {}
        assert getattr(client, method)(f"/api/v1/analyses/{identifier}/{suffix}", **kwargs).status_code == 401
        assert getattr(client, method)(f"/api/v1/analyses/{identifier}/{suffix}", headers=service_headers, **kwargs).status_code == 403
    assert client.delete("/api/v1/admin/service-api-keys/unknown", headers=service_headers).status_code == 403
    login(client)
    for body in ({"idempotency_key": "synthetic-key"}, {"idempotency_key": "synthetic-key", "cost_acknowledged": False}):
        assert client.post(f"/api/v1/analyses/{identifier}/retry", json=body).status_code == 422


def test_retry_keeps_failure_and_pinned_context_idempotency_and_original_ingest(client, registered_vllm_target, event_payload, monkeypatch):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    identifier = failed_fixture(client, event_payload, profile_id)
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, identifier)
        before = {column.name: getattr(original, column.name) for column in Analysis.__table__.columns}
    saved, _ = save_policy(client, text="SYNTHETIC_DIFFERENT_CURRENT_POLICY")
    activate(client, saved["id"])
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).status = "verified"
        db.add(VLLMProfile(name="synthetic-new-role", model_name="different-model", base_url="http://10.0.0.10:8000/v1", status="production"))
        db.commit()
    client.app.state.settings.verifier_confidence_threshold = 0.21
    eligible = client.get(f"/api/v1/analyses/{identifier}/retry-eligibility").json()
    assert eligible["allowed"] is True and eligible["model_profile_id"] == profile_id
    created = retry(client, identifier)
    assert created.status_code == 202, created.text
    child_id = created.json()["analysis_id"]
    assert retry(client, identifier).json() == {**created.json(), "duplicate": True}
    assert retry(client, identifier, "another-key").status_code == 409
    assert client.get(f"/api/v1/analyses/{identifier}").json()["retry_analysis_id"] == child_id
    assert client.get(f"/api/v1/analyses/{child_id}").json()["retry_of_analysis_id"] == identifier
    assert client.get("/api/v1/analyses").json()["total"] == 1
    assert client.get("/api/v1/analyses?include_retries=true").json()["total"] == 2
    assert client.get("/api/v1/dashboard/summary").json()["counts"]["total"] == 1
    assert client.post("/api/v1/analyses", json=event_payload).json()["id"] == identifier
    assert client.post("/api/v1/analyses", json={**event_payload, "payload": "changed synthetic"}).status_code == 409
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        return AgentCallResult(fake_output(), "synthetic-run", "synthetic-fingerprint", "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        child = db.get(Analysis, child_id)
        assert child.prompt_snapshot_ciphertext == before["prompt_snapshot_ciphertext"]
        assert child.input_schema_snapshot_ciphertext == before["input_schema_snapshot_ciphertext"]
        worker.process_moduagent(db, client.app.state.crypto, child, "", 0.21)
        assert child.result_json["policy"]["verifier_confidence_threshold"] == 0.73
        assert child.result_json["agent"]["model_profile_id"] == profile_id
        assert child.prompt_policy_version_id == before["prompt_policy_version_id"]
        assert not db.scalar(select(NamedItem.id).where(NamedItem.analysis_id == child_id))
        assert not db.scalar(select(AnalysisLabel.id).where(AnalysisLabel.analysis_id == child_id))
        original = db.get(Analysis, identifier)
        assert {column.name: getattr(original, column.name) for column in Analysis.__table__.columns} == before
    assert calls and all("SYNTHETIC_DIFFERENT_CURRENT_POLICY" not in item["instructions"] for item in calls)
    assert all("execution_snapshot" not in item["user_input"] and "retry_of_analysis_id" not in item["user_input"] for item in calls)


@pytest.mark.parametrize("change,reason", [
    ("model", "retry_original_profile_changed"), ("disabled", "retry_original_profile_disabled"),
    ("unverified", "retry_original_profile_not_verified"), ("deleted", "retry_original_profile_missing"),
    ("egress", "retry_original_profile_target_not_allowed"), ("prompt", "retry_input_or_prompt_snapshot_unavailable"),
    ("schema", "retry_input_or_prompt_snapshot_unavailable"), ("snapshot", "retry_execution_snapshot_unavailable"),
])
def test_retry_blocks_changed_missing_or_unapproved_original_settings(client, registered_vllm_target, event_payload, change, reason):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    identifier = failed_fixture(client, event_payload, profile_id)
    with client.app.state.session_factory() as db:
        profile, row = db.get(VLLMProfile, profile_id), db.get(Analysis, identifier)
        if change == "model": profile.model_name = "synthetic-changed"
        if change == "disabled": profile.status = "disabled"
        if change == "unverified": profile.status = "draft"
        if change == "deleted": db.delete(profile)
        if change == "egress": db.delete(db.get(InternalEgressTarget, registered_vllm_target))
        if change == "prompt": row.prompt_snapshot_ciphertext = "synthetic-broken"
        if change == "schema": row.input_schema_snapshot_ciphertext = None
        if change == "snapshot": row.execution_snapshot_ciphertext = "synthetic-broken"
        db.commit()
    body = client.get(f"/api/v1/analyses/{identifier}/retry-eligibility").json()
    assert body["allowed"] is False and body["blocked_reason"] == reason
    assert retry(client, identifier).json()["detail"] == reason


def test_worker_pins_context_before_primary_failure_and_rechecks_after_enqueue(client, registered_vllm_target, event_payload, monkeypatch):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    identifier = client.post("/api/v1/analyses", json=event_payload).json()["id"]
    async def fail(**kwargs):
        return AgentCallResult(None, None, None, None, "synthetic-failure", "synthetic-error", {})
    monkeypatch.setattr(worker, "execute_structured_agent", fail)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        with pytest.raises(worker.WorkerExecutionError) as caught:
            worker.process_moduagent(db, client.app.state.crypto, row, "", 0.73)
        worker.mark_failed(db, row, caught.value)
        snapshot = load_execution_snapshot(row, client.app.state.crypto)
        assert snapshot.profile_id == profile_id and snapshot.verifier_confidence_threshold == 0.73
    child_id = retry(client, identifier).json()["analysis_id"]
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).model_name = "synthetic-changed-after-admission"
        db.commit()
    called = []
    async def never(**kwargs):
        called.append(kwargs)
        raise AssertionError("Must not call a changed model")
    monkeypatch.setattr(worker, "execute_structured_agent", never)
    with client.app.state.session_factory() as db:
        with pytest.raises(RetryError, match="retry_original_profile_changed"):
            worker.process_moduagent(db, client.app.state.crypto, db.get(Analysis, child_id), "", 0.2)
        with pytest.raises(RetryError, match="retry_original_profile_changed"):
            execution_request_check(db.get_bind(), snapshot)()
    assert called == []


def test_historical_failure_without_complete_execution_data_is_not_guessed(client, registered_vllm_target, event_payload):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    identifier = failed_fixture(client, event_payload, profile_fixture(client))
    with client.app.state.session_factory() as db:
        db.get(Analysis, identifier).execution_snapshot_ciphertext = None
        db.commit()
    body = client.get(f"/api/v1/analyses/{identifier}/retry-eligibility").json()
    assert not body["allowed"] and body["blocked_reason"] == "retry_execution_snapshot_unavailable"


@pytest.mark.parametrize("change,reason", [("label", "retry_event_reference_contamination"), ("payload", "retry_event_fingerprint_mismatch")])
def test_retry_never_resubmits_reference_contamination_or_mutated_event(client, registered_vllm_target, event_payload, change, reason):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    identifier = failed_fixture(client, event_payload, profile_fixture(client))
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        if change == "label":
            row.extra_fields = {**row.extra_fields, "expected_verdict": "SYNTHETIC_REFERENCE_NEVER_RESUBMITTED"}
        else:
            row.payload_ciphertext = client.app.state.crypto.encrypt_text("SYNTHETIC_CHANGED_EVENT")
        db.commit()
    response = retry(client, identifier)
    assert response.status_code == 409 and response.json()["detail"] == reason
    assert "SYNTHETIC_REFERENCE" not in response.text and "SYNTHETIC_CHANGED" not in response.text


@pytest.mark.parametrize("missing", ["prompt", "schema", "execution"])
def test_worker_never_recreates_missing_retry_snapshots(client, registered_vllm_target, event_payload, missing, monkeypatch):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    identifier = failed_fixture(client, event_payload, profile_fixture(client))
    child_id = retry(client, identifier).json()["analysis_id"]
    called = []
    async def never(**kwargs):
        called.append(kwargs)
        raise AssertionError("No fallback snapshot or LLM is permitted")
    monkeypatch.setattr(worker, "execute_structured_agent", never)
    with client.app.state.session_factory() as db:
        child = db.get(Analysis, child_id)
        if missing == "prompt":
            child.prompt_version = child.prompt_policy_version_id = child.prompt_snapshot_ciphertext = None
        if missing == "schema":
            child.input_schema_version_id = child.input_schema_snapshot_ciphertext = None
        if missing == "execution":
            child.execution_snapshot_ciphertext = None
        db.commit()
        with pytest.raises(RetryError):
            worker.process_moduagent(db, client.app.state.crypto, child, "", 0.3)
        if missing == "prompt": assert child.prompt_snapshot_ciphertext is None
        if missing == "schema": assert child.input_schema_snapshot_ciphertext is None
        with pytest.raises(worker.WorkerExecutionError, match="retry_requires_moduagent_mode"):
            worker.process_stub(db, client.app.state.crypto, child)
    assert called == []


def test_test_retry_does_not_change_fixed_cohort_or_labels(client, registered_vllm_target, event_payload):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client, is_test=True)
    run = client.post("/api/v1/test-runs", json={"name": "synthetic fixed cohort", "idempotency_key": "synthetic-cohort",
        "event": {**event_payload, "expected_verdict": "true_positive"}}).json()
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status = "failed"
        row.completed_at = utcnow()
        snapshot = make_execution_snapshot(row, db.get(VLLMProfile, profile_id), load_analysis_prompt(row, client.app.state.crypto), 0.75)
        row.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(snapshot.model_dump_json())
        db.commit()
    before = client.get(f"/api/v1/test-runs/{run['id']}").json()
    response = retry(client, identifier)
    assert response.status_code == 202, response.text
    after = client.get(f"/api/v1/test-runs/{run['id']}").json()
    assert before == after
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(NamedItem)) == 1
        assert db.scalar(select(func.count()).select_from(AnalysisLabel)) == 1
        assert db.get(Analysis, response.json()["analysis_id"]).extra_fields == {"future_vendor_field": event_payload["future_vendor_field"]}


def test_historical_named_failure_can_recover_recorded_profile_and_threshold(client, registered_vllm_target, event_payload, monkeypatch):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_fixture(client, is_test=True)
    named = client.post("/api/v1/test-runs", json={"name": "synthetic old named run", "idempotency_key": "synthetic-old-named",
        "event": event_payload}).json()
    identifier = named["items"][0]["analysis_id"]
    async def fail(**kwargs):
        return AgentCallResult(None, None, None, None, "synthetic-failed", "synthetic-error", {})
    monkeypatch.setattr(worker, "execute_structured_agent", fail)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        with pytest.raises(worker.WorkerExecutionError) as caught:
            worker.process_moduagent(db, client.app.state.crypto, row, "", 0.73)
        worker.mark_failed(db, row, caught.value)
        row.execution_snapshot_ciphertext = None  # Synthetic pre-0013 format.
        db.commit()
    assert client.get(f"/api/v1/analyses/{identifier}/retry-eligibility").json()["allowed"] is True
    response = retry(client, identifier)
    assert response.status_code == 202
    with client.app.state.session_factory() as db:
        snapshot = load_execution_snapshot(db.get(Analysis, response.json()["analysis_id"]), client.app.state.crypto)
        assert snapshot.verifier_confidence_threshold == named["profile_metadata"]["verifier_confidence_threshold"]
        assert db.get(Analysis, identifier).execution_snapshot_ciphertext is None


def test_key_dashboard_metrics_and_daily_trend_share_scope_and_null_denominators(client, event_payload, monkeypatch):
    from app.api import dashboard
    fixed = utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(dashboard, "utcnow", lambda: fixed)
    key_id, headers = key_fixture(client, "daily synthetic")
    other_id, other_headers = key_fixture(client, "other synthetic")
    identifiers = []
    for number, credential in enumerate((headers, headers, headers, other_headers)):
        row = client.post("/api/v1/analyses", headers=credential, json={**event_payload, "event_id": f"daily-{number}"}).json()
        identifiers.append(row["id"])
    with client.app.state.session_factory() as db:
        for number, identifier in enumerate(identifiers):
            row = db.get(Analysis, identifier)
            row.created_at = fixed - timedelta(days=1 if number < 2 else 2)
            row.completed_at = row.created_at + timedelta(seconds=1)
            row.status = "completed"
            row.verdict = "true_positive" if number != 1 else "false_positive"
            row.result_json = {"verdict": row.verdict, "agent": {"framework": "moduagent"}}
            if number != 2:
                db.add(AnalysisLabel(analysis_id=identifier, revision=1, verdict="true_positive", source_kind="reference",
                    source_ref="synthetic-source", ai_visible=False, created_by="synthetic", attachment_id=str(uuid.uuid4()), token_digest="synthetic"))
        db.commit()
    login(client)
    body = client.get("/api/v1/dashboard/summary", params={"days": 3, "service_api_key_id": key_id}).json()
    metrics = body["evaluation_summary"]
    assert body["counts"]["total"] == metrics["total"] == 3
    assert metrics["labeled"] == metrics["binary_evaluable"] == 2
    assert metrics["confusion_matrix"]["tp"] == metrics["confusion_matrix"]["fn"] == 1
    assert metrics["metrics"]["accuracy"] == metrics["metrics"]["recall"] == 0.5
    assert sum(day["total"] for day in body["trend"]) == metrics["total"]
    empty_day = next(day for day in body["trend"] if day["total"] == 0)
    assert empty_day["evaluation_summary"]["metrics"]["accuracy"] is None
    unlabeled_day = next(day for day in body["trend"] if day["total"] == 1)
    assert unlabeled_day["evaluation_summary"]["metrics"]["accuracy"] is None
    listing = client.get("/api/v1/analyses", params={"service_api_key_id": key_id, "analysis_purpose": "production",
        "created_from": body["window"]["created_from"], "created_to": body["window"]["created_to"]}).json()
    assert listing["evaluation_summary"] == metrics
    assert client.get("/api/v1/dashboard/summary").json()["counts"]["total"] == 4
