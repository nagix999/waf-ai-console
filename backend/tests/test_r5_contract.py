"""R5 synthetic, offline boundary and lifecycle tests."""
import uuid
import pytest
from sqlalchemy import select
from app.models import Analysis, TestRun as Run, ValidationDatasetWorkingItem, ValidationDatasetVersion
from app.services.analysis import event_fingerprint
from app.schemas import AnalysisInput
from test_validation_data import login, create_dataset
from test_ground_truth_working import draft, save, publish
from test_candidate_configurations import candidate, direct, current_configuration


def finished_run(client, event_payload, expected=None):
    login(client)
    response = client.post("/api/v1/test-runs", json={"name": "offline fixture", "idempotency_key": str(uuid.uuid4()),
        "event": event_payload, **({"expected_verdict": expected} if expected else {})})
    assert response.status_code == 202, response.text
    run = response.json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.status, row.verdict = "completed", "true_positive"
        db.commit()
    return run


def test_test_key_and_nested_metadata_rejected(client, event_payload):
    from app.api_key_schemas import ServiceApiKeyCreate
    from app.services.service_api_keys import issue_key
    with client.app.state.session_factory() as db:
        _, key = issue_key(db, ServiceApiKeyCreate(name="Test only", source_system="fixture-test", scopes=["ingest"], purpose="test"), "fixture")
        db.commit()
    initial = {"initial_verdict": "false_positive", "initial_probability": .5}
    result = client.post("/api/v1/analyses", json={**event_payload, **initial}, headers={"x-api-key": key})
    assert result.status_code == 422
    assert client.post("/api/v1/analyses", json={**event_payload, "extra_fields": initial}, headers={"x-api-key": key}).status_code == 422


def test_import_uses_fixed_reference_not_model_and_preserves_source(client, event_payload):
    run = finished_run(client, event_payload, "false_positive")
    base = f"/api/v1/test-runs/{run['id']}/ground-truth-import"
    preview = client.post(base + "/preview", json={"target": "create_new_dataset", "test_run_item_ids": [run["items"][0]["id"]]}).json()
    result = client.post(base + "/confirm", json={"preview_token": preview["preview_token"], "idempotency_key": str(uuid.uuid4())})
    assert result.status_code == 200, result.text
    with client.app.state.session_factory() as db:
        row = db.scalar(select(ValidationDatasetWorkingItem).where(ValidationDatasetWorkingItem.dataset_id == result.json()["dataset_id"]))
        assert row.reference_verdict == "false_positive" and row.reference_origin == "reference_label"
        assert row.reference_origin_ref_id == row.source_label_id
        assert row.provenance_json["source_reference_label_id"] == row.source_label_id
        assert row.source_kind == "synthetic_expected"
    assert client.post(base + "/preview", json={"target": "create_new_dataset", "test_run_item_ids": [str(uuid.uuid4())]}).status_code == 422


def test_import_expiry_and_revision_conflict_are_atomic(client, event_payload):
    from app.models import GroundTruthImportPreview, ValidationDataset, utcnow
    from datetime import timedelta
    run = finished_run(client, event_payload)
    base = f"/api/v1/test-runs/{run['id']}/ground-truth-import"
    preview = client.post(base + "/preview", json={"target": "create_new_dataset"}).json()
    with client.app.state.session_factory() as db:
        row = db.get(GroundTruthImportPreview, preview["preview_token"])
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        before = list(db.scalars(select(ValidationDataset.id)))
    assert client.post(base + "/confirm", json={"preview_token": preview["preview_token"], "idempotency_key": str(uuid.uuid4())}).status_code == 409
    with client.app.state.session_factory() as db:
        assert list(db.scalars(select(ValidationDataset.id))) == before
    dataset = create_dataset(client)
    preview = client.post(base + "/preview", json={"target": "append_to_existing_dataset", "dataset_id": dataset["id"], "expected_working_revision": 0}).json()
    assert save(client, dataset, event_payload).status_code == 201
    assert client.post(base + "/confirm", json={"preview_token": preview["preview_token"], "idempotency_key": str(uuid.uuid4())}).status_code == 409


def test_initial_metadata_is_separate_and_duplicate_is_exact(client, event_payload, service_headers):
    initial = {"initial_verdict": "false_positive", "initial_probability": .93, "initial_model_version": "fixture-v1"}
    result = client.post("/api/v1/analyses", json={**event_payload, **initial}, headers=service_headers)
    assert result.status_code == 202, result.text
    identifier = result.json()["id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        assert not set(initial).intersection(row.extra_fields)
        assert row.event_fingerprint == event_fingerprint(AnalysisInput.model_validate(event_payload).model_dump(mode="json"))
        assert "initial_verdict" not in client.app.state.crypto.decrypt_text(row.prompt_snapshot_ciphertext)
    assert result.json()["initial_assessment"]["comparison"] == "pending"
    assert client.post("/api/v1/analyses", json={**event_payload, **initial}, headers=service_headers).json()["id"] == identifier
    assert client.post("/api/v1/analyses", json=event_payload, headers=service_headers).status_code == 409
    assert client.post("/api/v1/analyses", json={**event_payload, **initial, "initial_probability": .8}, headers=service_headers).status_code == 409
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.verdict = "completed", "true_positive"
        db.commit()
    login(client)
    assert client.get("/api/v1/analyses", params={"initial_comparison": "different"}).json()["total"] == 1
    assert client.get("/api/v1/analyses", params={"initial_comparison": "match"}).json()["total"] == 0


@pytest.mark.parametrize("initial", [
    {"initial_verdict": "true_positive"}, {"initial_probability": .5},
    {"initial_model_version": "only-version"}, {"initial_verdict": "inconclusive", "initial_probability": .5},
    {"initial_verdict": "true_positive", "initial_probability": 1.01},
    {"initial_verdict": "true_positive", "initial_probability": True},
])
def test_invalid_initial_pair_rejected(client, event_payload, service_headers, initial):
    assert client.post("/api/v1/analyses", json={**event_payload, **initial}, headers=service_headers).status_code == 422


def test_initial_rejected_in_admin_and_ground_truth(client, event_payload):
    login(client)
    initial = {"initial_verdict": "true_positive", "initial_probability": .8}
    assert client.post("/api/v1/analyses", json={**event_payload, **initial}).status_code == 422
    dataset = create_dataset(client)
    assert save(client, dataset, {**event_payload, **initial}).status_code == 422
    assert save(client, dataset, {**event_payload, "extra_fields": initial}).status_code == 422


@pytest.mark.usefixtures("registered_vllm_target")
def test_defaults_are_full_guarded_and_not_production(client, candidate, event_payload):
    before = current_configuration(client)
    endpoint = "/api/v1/admin/test-configuration-defaults"
    assert client.get(endpoint).json()["candidate_configuration"] is None
    result = client.patch(endpoint, json={"expected_revision": 0, "candidate_configuration": candidate})
    assert result.status_code == 200, result.text
    assert result.json()["valid"] is True
    assert current_configuration(client) == before
    assert client.patch(endpoint, json={"expected_revision": 0, "candidate_configuration": candidate}).status_code == 409
    run = direct(client, {**event_payload, "vendor_score": 1}, candidate).json()
    assert run["test_purpose"] == "development"
    template = client.get(f"/api/v1/test-runs/{run['id']}/clone-template").json()
    assert template["candidate_configuration"] == candidate
    assert template["resource_validity"]["valid"]


def test_import_preview_never_uses_deep_verdict_and_is_idempotent(client, event_payload):
    login(client)
    run = client.post("/api/v1/test-runs", json={"name": "fixture", "idempotency_key": str(uuid.uuid4()), "event": event_payload}).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.status, row.verdict = "completed", "true_positive"
        db.commit()
    base = f"/api/v1/test-runs/{run['id']}/ground-truth-import"
    response = client.post(base + "/preview", json={"target": "create_new_dataset", "new_dataset_name": "No guessed answers"})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["missing_reference_count"] == 1
    assert preview["items"][0]["category"] == "missing_reference"
    payload = {"preview_token": preview["preview_token"], "idempotency_key": str(uuid.uuid4())}
    result = client.post(base + "/confirm", json=payload)
    assert result.status_code == 200, result.text
    assert client.post(base + "/confirm", json=payload).json() == result.json()
    with client.app.state.session_factory() as db:
        item = db.scalar(select(ValidationDatasetWorkingItem).where(ValidationDatasetWorkingItem.dataset_id == result.json()["dataset_id"]))
        assert item.reference_verdict is None and item.reference_origin == "none"
        assert item.validation_state == "needs_attention"
        assert item.provenance_json["source_test_run_id"] == run["id"]
        assert not db.scalar(select(ValidationDatasetVersion.id).where(ValidationDatasetVersion.is_published.is_(True)))


def test_manual_publish_origin_and_setup_not_baseline_presence(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    save(client, dataset, event_payload)
    result = publish(client, dataset)
    assert result.json()["metadata"]["included_reference_origin_counts"] == {"manual": 1}
    client.get("/api/v1/admin/production-configurations")
    state = client.get("/api/v1/admin/setup-status")
    assert state.status_code == 200, state.text
    assert state.json()["production_state"] == "unconfigured"
    assert len(state.json()["steps"]) == 5
    assert state.json()["steps"]["ground_truth_published"]["state"] == "ready"


@pytest.mark.usefixtures("registered_vllm_target")
def test_setup_completed_evaluation_with_failures_is_not_promotion_ready(client, event_payload, candidate):
    from app.services.official_evaluations import finalize_official_evaluation
    dataset = create_dataset(client)
    save(client, dataset, {**event_payload, "vendor_score": 2})
    version = publish(client, dataset).json()
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json={
        "expected_revision": version["revision"], "dataset_revision_id": version["id"],
        "evaluation_mode": "ground_truth", "candidate_configuration": candidate, "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 202, response.text
    run = response.json()
    with client.app.state.session_factory() as db:
        db.get(Analysis, run["items"][0]["analysis_id"]).status = "failed"
        db.commit()
        assert finalize_official_evaluation(db, run["id"])
    step = client.get("/api/v1/admin/setup-status").json()["steps"]["official_candidate_test"]
    assert step["state"] == "ready" and step["warning_count"] == 1
    assert not client.get(f"/api/v1/admin/production-configurations/preflight/{run['id']}").json()["eligible"]
    with client.app.state.session_factory() as db:
        db.get(Run, run["id"]).test_purpose = "legacy_unknown"
        db.commit()
    assert client.get("/api/v1/admin/setup-status").json()["steps"]["official_candidate_test"]["state"] == "not_started"


def test_home_attention_covers_all_datasets_without_guessing_a_baseline(client, event_payload):
    login(client)
    datasets = [create_dataset(client), create_dataset(client)]
    for dataset in datasets:
        save(client, dataset, event_payload, verdict=None)
    data = client.get("/api/v1/admin/production-configurations/overview").json()
    assert data["comparison_key"] is None
    actions = [row for row in data["actions"] if row["kind"] == "ground_truth"]
    assert len(actions) == 1 and actions[0]["count"] == 2
    assert {row["dataset_id"] for row in actions[0]["datasets"]} == {row["id"] for row in datasets}
    assert all(row["count"] == 1 for row in actions[0]["datasets"])
