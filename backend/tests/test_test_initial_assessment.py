"""Single Test metadata isolation; synthetic data, no model or network I/O."""
import json
import uuid

import pytest
from sqlalchemy import select

from app.api_key_schemas import ServiceApiKeyCreate
from app.models import Analysis, AnalysisLabel, ValidationDatasetWorkingItem
from app.schemas import AnalysisInput
from app.worker import _event_document
from app.services.analysis import event_fingerprint
from app.services.service_api_keys import issue_key
from test_validation_data import login, create_dataset
from test_ground_truth_working import save

INITIAL = {"initial_verdict": "true_positive", "initial_probability": 0.0, "initial_model_version": "offline-model-v1"}


@pytest.fixture
def test_key(client):
    with client.app.state.session_factory() as db:
        _, key = issue_key(db, ServiceApiKeyCreate(name="Synthetic Test", source_system="initial-test", scopes=["ingest"], purpose="test"), "fixture")
        db.commit()
    return {"x-api-key": key}


def web_request(event, **extra):
    return {"name": "Initial assessment fixture", "idempotency_key": str(uuid.uuid4()), "event": event, **extra}


@pytest.mark.parametrize("surface", ["web", "inline", "legacy", "service"])
def test_single_initial_is_stored_without_evidence_or_answer_contamination(client, event_payload, test_key, surface):
    if surface == "service":
        response = client.post("/api/v1/analyses", json={**event_payload, **INITIAL, "expected_verdict": "false_positive"}, headers=test_key)
        identifier = response.json().get("id")
    else:
        login(client)
        if surface == "legacy":
            response = client.post("/api/v1/test-analyses", params={"name": "fixture", "idempotency_key": str(uuid.uuid4())}, json={**event_payload, **INITIAL, "expected_verdict": "false_positive"})
            identifier = response.json().get("id")
        else:
            document = web_request({**event_payload, **(INITIAL if surface == "inline" else {})}, expected_verdict="false_positive", **(INITIAL if surface == "web" else {}))
            response = client.post("/api/v1/test-runs", json=document)
            identifier = response.json().get("items", [{}])[0].get("analysis_id")
            assert client.post("/api/v1/test-runs", json=document).json()["id"] == response.json()["id"]
    assert response.status_code == 202, response.text
    assert identifier
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        assert row.analysis_purpose == "test"
        assert {key: getattr(row, key) for key in INITIAL} == INITIAL
        assert not set(INITIAL).intersection(row.extra_fields)
        assert not set(INITIAL).intersection(_event_document(row))
        assert row.event_fingerprint == event_fingerprint(AnalysisInput.model_validate(event_payload).model_dump(mode="json"))
        assert client.app.state.crypto.decrypt_text(row.payload_ciphertext) == event_payload["payload"]
        assert "initial_verdict" not in client.app.state.crypto.decrypt_text(row.prompt_snapshot_ciphertext)
        assert db.scalar(select(AnalysisLabel.verdict).where(AnalysisLabel.analysis_id == identifier)) == "false_positive"
        row.status, row.verdict = "completed", "false_positive"
        db.commit()
    login(client)
    result = client.get(f"/api/v1/analyses/{identifier}").json()
    assert result["initial_assessment"] == {"verdict": "true_positive", "probability": 0.0, "model_version": INITIAL["initial_model_version"], "comparison": "different"}


def test_single_retry_preserves_initial_without_using_it_as_reference(client, event_payload, registered_vllm_target):
    from app.models import VLLMProfile, utcnow
    from app.services.analysis_retries import make_execution_snapshot
    from app.services.prompt_snapshots import load_analysis_prompt
    from test_analysis_retries_keys import profile_fixture, retry

    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client, is_test=True)
    response = client.post("/api/v1/test-runs", json=web_request(event_payload, **INITIAL))
    assert response.status_code == 202, response.text
    identifier = response.json()["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.error_code, row.completed_at = "failed", "primary_agent_failed", utcnow()
        snapshot = make_execution_snapshot(row, db.get(VLLMProfile, profile_id), load_analysis_prompt(row, client.app.state.crypto), .75)
        row.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(snapshot.model_dump_json())
        db.commit()
    response = retry(client, identifier)
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        child = db.get(Analysis, response.json()["analysis_id"])
        assert child.retry_of_analysis_id == identifier
        assert {key: getattr(child, key) for key in INITIAL} == INITIAL
        assert not set(INITIAL).intersection(_event_document(child))
        assert db.get(Analysis, identifier).status == "failed"
        assert db.scalar(select(AnalysisLabel).where(AnalysisLabel.analysis_id.in_([identifier, child.id]))) is None


@pytest.mark.parametrize("session", [False, True])
@pytest.mark.parametrize("first,second", [(INITIAL, INITIAL), ({}, INITIAL), (INITIAL, {}), (INITIAL, {**INITIAL, "initial_probability": .9}), (INITIAL, {**INITIAL, "initial_model_version": "changed"})])
def test_service_replay_checks_initial_metadata_including_omission(client, event_payload, test_key, session, first, second):
    query = {}
    if session:
        run = client.post("/api/v1/test-sessions", json={"name": "fixture", "idempotency_key": str(uuid.uuid4())}, headers=test_key).json()
        query["test_run_id"] = run["id"]
    response = client.post("/api/v1/analyses", params=query, json={**event_payload, **first}, headers=test_key)
    assert response.status_code == 202, response.text
    repeated = client.post("/api/v1/analyses", params=query, json={**event_payload, **second}, headers=test_key)
    if first == second:
        assert repeated.status_code == 202 and repeated.json()["id"] == response.json()["id"]
    else:
        assert repeated.status_code == 409 and repeated.json()["detail"] == "initial_assessment_conflict"
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, response.json()["id"])
        assert row.initial_probability == first.get("initial_probability")


@pytest.mark.parametrize("metadata,code", [
    ({"initial_verdict": "true_positive"}, "initial_assessment_pair_required"),
    ({"initial_model_version": "alone"}, "initial_assessment_pair_required"),
    ({**INITIAL, "initial_verdict": "inconclusive"}, "initial_verdict_invalid"),
    ({**INITIAL, "initial_probability": 90}, "initial_probability_invalid"),
    ({**INITIAL, "initial_probability": True}, "initial_probability_invalid"),
    ({**INITIAL, "initial_probability": "0.9"}, "initial_probability_invalid"),
    ({**INITIAL, "initial_model_version": "x" * 256}, "initial_model_version_invalid"),
])
def test_inline_invalid_initial_has_specific_safe_error(client, event_payload, metadata, code):
    login(client)
    response = client.post("/api/v1/test-runs", json=web_request({**event_payload, **metadata}))
    assert response.status_code == 202
    item = response.json()["items"][0]
    assert item["ingest_status"] == "rejected" and item["error_code"] == code
    assert event_payload["payload"] not in response.text


def test_duplicate_location_and_nested_fields_are_rejected(client, event_payload):
    login(client)
    response = client.post("/api/v1/test-runs", json=web_request({**event_payload, **INITIAL}, **INITIAL))
    assert response.status_code == 422 and response.json()["detail"] == "duplicate_test_metadata_location"
    response = client.post("/api/v1/test-runs", json=web_request({**event_payload, "extra_fields": INITIAL}))
    assert response.json()["items"][0]["error_code"] == "initial_assessment_location_invalid"


@pytest.mark.parametrize("service", [False, True])
def test_files_still_reject_initial_without_dropping_valid_rows(client, event_payload, test_key, service):
    rows = [{**event_payload, **INITIAL}, {**event_payload, "event_id": "valid-second"}]
    if service:
        response = client.post("/api/v1/uploads", files={"file": ("fixture.json", json.dumps(rows), "application/json")}, headers=test_key)
        assert response.status_code in (200, 202), response.text
        assert response.json()["accepted"] == 1 and response.json()["rejected"] == 1
    else:
        login(client)
        response = client.post("/api/v1/test-runs/uploads", data={"name": "fixture", "idempotency_key": str(uuid.uuid4())}, files={"file": ("fixture.json", json.dumps(rows), "application/json")})
        assert response.status_code == 202, response.text
        assert response.json()["accepted"] == 1 and response.json()["rejected"] == 1
        assert response.json()["items"][0]["error_code"] == "initial_assessment_single_request_only"


@pytest.mark.parametrize("expected", [None, "false_positive"])
def test_dataset_copy_never_uses_initial_as_expected(client, event_payload, expected):
    login(client)
    response = client.post("/api/v1/test-runs", json=web_request(event_payload, **INITIAL, expected_verdict=expected))
    run = response.json()
    base = f"/api/v1/test-runs/{run['id']}/ground-truth-import"
    token = client.post(base + "/preview", json={"target": "create_new_dataset"}).json()["preview_token"]
    copied = client.post(base + "/confirm", json={"preview_token": token, "idempotency_key": str(uuid.uuid4())})
    assert copied.status_code == 200, copied.text
    with client.app.state.session_factory() as db:
        row = db.scalar(select(ValidationDatasetWorkingItem).where(ValidationDatasetWorkingItem.dataset_id == copied.json()["dataset_id"]))
        assert row.reference_verdict == expected
        assert not set(INITIAL).intersection(json.loads(client.app.state.crypto.decrypt_text(row.event_ciphertext)))
    dataset = create_dataset(client)
    assert save(client, dataset, {**event_payload, **INITIAL}).status_code == 422
