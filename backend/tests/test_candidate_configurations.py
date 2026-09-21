"""Synthetic candidates; model transport is forbidden unless explicitly mocked."""
import json
import uuid

import pytest
from sqlalchemy import func, select

from app import worker
from app.models import Analysis, InternalEgressTarget, TestRun as Run, VLLMProfile, VLLMTestRun
from app.services.analysis import AnalysisIngestError
from app.services.candidate_configurations import verify_configuration
from app.services.prompt_snapshots import load_analysis_prompt
from app.services.input_schemas import read_schema_snapshot
from test_agent_configuration import install_calls
from test_input_schemas import payload as schema_payload
from test_model_test_role import create_verified, role
from test_prompt_snapshots import create_and_activate
from test_validation_data import add_item, create_dataset, forbid_model_calls, login

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


@pytest.fixture
def candidate(client):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    primary, verifier, editor = [create_verified(client, f"fixture-{name}") for name in ("primary", "verifier", "editor")]
    active_prompt = client.get("/api/v1/admin/prompt-policies").json()["active_version_id"]
    policy = client.post("/api/v1/admin/prompt-policies", json={"name": "candidate instructions", "policy_text": "CANDIDATE-INSTRUCTIONS-FIXTURE",
        "change_note": "offline fixture", "parent_version_id": active_prompt}).json()
    client.get("/api/v1/admin/input-schemas")
    fields = schema_payload()["fields"] + [{"name": "vendor_score", "type": "integer", "required": True,
        "description": "FIELD-METADATA-MUST-NOT-ENTER-PROMPT", "minimum": 0, "maximum": 10}]
    schema = client.post("/api/v1/admin/input-schemas", json=schema_payload(fields=fields)).json()
    return {"primary_profile_id": primary["id"], "verifier_profile_id": verifier["id"],
        "evidence_editor_enabled": True, "evidence_editor_profile_id": editor["id"],
        "prompt_policy_version_id": policy["id"], "input_schema_version_id": schema["id"]}


def direct(client, event, candidate, *, key=None):
    return client.post("/api/v1/test-runs", json={"name": "candidate fixture", "event": event,
        "idempotency_key": key or str(uuid.uuid4()), "candidate_configuration": candidate,
        "expected_verdict": "inconclusive"})


def current_configuration(client):
    return [client.get(path).json() for path in ("/api/v1/admin/agent-settings", "/api/v1/admin/prompt-policies", "/api/v1/admin/input-schemas")]


@pytest.mark.parametrize("entry", ["direct", "upload", "dataset"])
def test_all_test_entrypoints_freeze_explicit_candidate_without_activation(client, event_payload, candidate, entry):
    before = current_configuration(client)
    event = {**event_payload, "vendor_score": 2}
    if entry == "direct":
        response = direct(client, event, candidate)
    elif entry == "upload":
        response = client.post("/api/v1/test-runs/uploads", data={"name": "candidate fixture", "idempotency_key": str(uuid.uuid4()),
            "candidate_configuration": json.dumps(candidate)}, files={"file": ("fixture.json", json.dumps([event]).encode())})
    else:
        dataset = create_dataset(client)
        add_item(client, dataset, event, "inconclusive")
        response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json={"expected_revision": dataset["revision"],
            "idempotency_key": str(uuid.uuid4()), "candidate_configuration": candidate})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["accepted"] == 1
    assert len(run["configuration_hash"]) == 64
    snapshot = run["configuration_snapshot"]
    for role_name in ("primary", "verifier", "evidence_editor"):
        assert snapshot[role_name]["profile_id"] == candidate[f"{role_name}_profile_id"]
    assert snapshot["prompt"]["policy_version_id"] == candidate["prompt_policy_version_id"]
    assert snapshot["input_schema"]["version_id"] == candidate["input_schema_version_id"]
    assert current_configuration(client) == before
    assert all(secret not in json.dumps(snapshot) for secret in ("profile-secret", "CANDIDATE-INSTRUCTIONS-FIXTURE", "Cookie", "FIELD-METADATA"))
    with client.app.state.session_factory() as db:
        saved = db.get(Run, run["id"])
        assert verify_configuration(saved, client.app.state.crypto) == snapshot
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        assert analysis.prompt_snapshot_ciphertext == saved.prompt_snapshot_ciphertext
        assert analysis.input_schema_version_id == candidate["input_schema_version_id"]
        instructions = load_analysis_prompt(analysis, client.app.state.crypto).primary_instructions
        assert "CANDIDATE-INSTRUCTIONS-FIXTURE" in instructions and "FIELD-METADATA" not in instructions
        assert read_schema_snapshot(client.app.state.crypto, analysis)["field_metadata_usage"] == "history_only"
        assert not {"candidate_configuration", "expected_verdict"}.intersection(analysis.extra_fields)


def test_replay_and_hash_are_based_on_exact_candidate_not_test_name_or_data(client, event_payload, candidate):
    event = {**event_payload, "vendor_score": 2}
    key = str(uuid.uuid4())
    first = direct(client, event, candidate, key=key).json()
    assert direct(client, event, candidate, key=key).json()["id"] == first["id"]
    other = direct(client, {**event, "event_id": "another"}, candidate).json()
    assert other["id"] != first["id"] and other["configuration_hash"] == first["configuration_hash"]
    changed = {**candidate, "verifier_profile_id": None}
    assert direct(client, event, changed, key=key).status_code == 409
    response = direct(client, event, changed).json()
    assert response["configuration_hash"] != first["configuration_hash"]
    assert response["configuration_snapshot"]["verifier"]["profile_id"] == candidate["primary_profile_id"]


@pytest.mark.parametrize("role_name", ["primary", "verifier", "evidence_editor"])
@pytest.mark.parametrize("change", ["disabled", "draft", "fingerprint"])
def test_each_candidate_role_requires_current_full_verification(client, event_payload, candidate, role_name, change):
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, candidate[f"{role_name}_profile_id"])
        if change == "fingerprint":
            profile.max_output_tokens += 1
        else:
            profile.status = change
        db.commit()
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert response.status_code == 422, response.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0


@pytest.mark.parametrize("field", ["prompt_policy_version_id", "input_schema_version_id"])
def test_missing_saved_version_rolls_back_the_whole_submission(client, event_payload, candidate, field):
    response = direct(client, {**event_payload, "vendor_score": 2}, {**candidate, field: str(uuid.uuid4())})
    assert response.status_code == 404, response.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0


def test_candidate_schema_validates_events_and_never_changes_production_admission(client, event_payload, candidate):
    response = direct(client, event_payload, candidate)
    assert response.status_code == 202 and response.json()["rejected"] == 1
    assert client.post("/api/v1/analyses", json=event_payload).status_code == 202
    # Configuration is accepted only outside the event, not as model input.
    response = direct(client, {**event_payload, "vendor_score": 2, "candidate_configuration": candidate}, candidate)
    assert response.status_code == 202 and response.json()["rejected"] == 1


def test_candidate_never_bypasses_egress(client, event_payload, candidate):
    with client.app.state.session_factory() as db:
        target = db.scalar(select(InternalEgressTarget))
        db.delete(target)
        db.commit()
    assert direct(client, {**event_payload, "vendor_score": 2}, candidate).status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0


def test_candidate_never_bypasses_external_approval(client, event_payload, candidate):
    from app.services.vllm_profiles import profile_fingerprint
    external = create_verified(client, "fixture-external", provider="openai")
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, external["id"])
        profile.external_data_approved = False
        # Even a matching synthetic pass must not bypass revoked approval.
        db.scalar(select(VLLMTestRun).where(VLLMTestRun.profile_id == profile.id)).profile_fingerprint = profile_fingerprint(profile)
        db.commit()
    response = direct(client, {**event_payload, "vendor_score": 2}, {**candidate, "verifier_profile_id": external["id"]})
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0


@pytest.mark.parametrize("role_name", ["primary", "verifier", "evidence_editor"])
def test_internal_dataset_checks_explicit_candidate_roles(client, event_payload, candidate, role_name):
    external = create_verified(client, "fixture-external", provider="openai")
    dataset = create_dataset(client)
    item = add_item(client, dataset, {**event_payload, "vendor_score": 2}, "inconclusive")
    from app.models import ValidationDatasetItem
    with client.app.state.session_factory() as db:
        db.get(ValidationDatasetItem, item["id"]).internal_only = True
        db.commit()
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json={
        "expected_revision": dataset["revision"], "idempotency_key": str(uuid.uuid4()),
        "candidate_configuration": {**candidate, f"{role_name}_profile_id": external["id"]}})
    assert response.status_code == 409 and response.json()["detail"] == "dataset_internal_models_required"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0


@pytest.mark.parametrize("corrupt", ["hash", "snapshot", "prompt", "schema", "editor"])
def test_inconsistent_candidate_is_blocked_before_any_model_call(client, event_payload, candidate, corrupt):
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate).json()
    with client.app.state.session_factory() as db:
        run = db.get(Run, response["id"])
        if corrupt == "hash":
            run.configuration_hash = "0" * 64
        elif corrupt == "snapshot":
            run.configuration_snapshot_json = {"schema_version": 1}
        else:
            attr = {"prompt": "prompt_snapshot_ciphertext", "schema": "input_schema_snapshot_ciphertext", "editor": "evidence_editor_snapshot_ciphertext"}[corrupt]
            setattr(run, attr, "SYNTHETIC-INVALID-CIPHERTEXT")
        db.commit()
        with pytest.raises(AnalysisIngestError, match="candidate_configuration_invalid"):
            worker.process_moduagent(db, client.app.state.crypto, db.get(Analysis, response["items"][0]["analysis_id"]), "", .75)


def test_worker_uses_pinned_candidate_after_defaults_change(client, event_payload, candidate, monkeypatch):
    no_editor = {**candidate, "evidence_editor_enabled": False, "evidence_editor_profile_id": None}
    response = direct(client, {**event_payload, "vendor_score": 2}, no_editor).json()
    create_and_activate(client, "OTHER-POLICY-AFTER-ENQUEUE")
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, candidate["evidence_editor_profile_id"])
        from app.services.vllm_profiles import profile_fingerprint
        alternative = {"id": profile.id, "profile_fingerprint": profile_fingerprint(profile)}
    assert role(client, alternative, "assign-test").status_code == 200
    calls = install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, response["items"][0]["analysis_id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        assert analysis.status == "completed"
        assert analysis.result_json["agent"]["role_profiles"]["primary"]["model_profile_id"] == candidate["primary_profile_id"]
        assert analysis.result_json["agent"]["role_profiles"]["verifier"]["model_profile_id"] == candidate["verifier_profile_id"]
    assert len(calls) == 2
    assert all("CANDIDATE-INSTRUCTIONS-FIXTURE" in call["instructions"] for call in calls)
    assert all("OTHER-POLICY-AFTER-ENQUEUE" not in call["instructions"] for call in calls)
    assert all("FIELD-METADATA" not in call["user_input"] for call in calls)


def test_explicit_candidate_is_not_silently_ignored_in_stub_mode(client, event_payload, candidate):
    client.app.state.settings.agent_mode = "stub"
    response = direct(client, event_payload, candidate)
    assert response.status_code == 409 and response.json()["detail"] == "candidate_requires_llm_test"


@pytest.mark.parametrize("changed", ["prompt", "schema"])
def test_candidate_blocks_individually_valid_but_different_item_snapshot(client, event_payload, candidate, changed):
    run = direct(client, {**event_payload, "vendor_score": 2}, candidate).json()
    ordinary = client.post("/api/v1/analyses", json=event_payload).json()
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        other = db.get(Analysis, ordinary["id"])
        fields = ("prompt_policy_version_id", "prompt_version", "prompt_snapshot_ciphertext") if changed == "prompt" else (
            "input_schema_version_id", "input_schema_snapshot_ciphertext")
        for field in fields:
            setattr(analysis, field, getattr(other, field))
        db.commit()
        with pytest.raises(AnalysisIngestError, match="candidate_configuration_invalid"):
            worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)


def test_upload_rejects_malformed_candidate_without_echoing_values(client, candidate):
    for value in ("PRIVATE-INVALID-JSON", json.dumps({**candidate, "api_key": "PRIVATE-SECRET"})):
        response = client.post("/api/v1/test-runs/uploads", data={"name": "fixture", "idempotency_key": str(uuid.uuid4()),
            "candidate_configuration": value}, files={"file": ("fixture.json", b"[]")})
        assert response.status_code == 422
        assert response.json()["detail"] == "invalid_candidate_configuration"
        assert "PRIVATE" not in response.text
