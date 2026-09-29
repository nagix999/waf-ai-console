"""PR C: frozen observations are copyable independently of model progress."""
import uuid

import pytest
from sqlalchemy import select

from app.models import (Analysis, TestRun as Run, TestRunItem as RunItem,
    ValidationDataset, ValidationDatasetVersion, ValidationDatasetWorkingItem as Item)
from test_validation_data import login, create_dataset
from test_ground_truth_working import save, publish
from test_candidate_configurations import candidate


def create_run(client, event, expected=None):
    login(client)
    response = client.post("/api/v1/test-runs", json={"name": "PR C offline fixture",
        "idempotency_key": str(uuid.uuid4()), "event": event,
        **({"expected_verdict": expected} if expected else {})})
    assert response.status_code == 202, response.text
    return response.json()


def preview(client, run, **target):
    return client.post(f"/api/v1/test-runs/{run['id']}/ground-truth-import/preview",
        json={"target": "create_new_dataset", **target})


def confirm(client, run, token, key=None):
    return client.post(f"/api/v1/test-runs/{run['id']}/ground-truth-import/confirm",
        json={"preview_token": token, "idempotency_key": key or str(uuid.uuid4())})


@pytest.mark.parametrize("status", ["pending", "processing", "completed", "failed", "stopped"])
@pytest.mark.parametrize("expected", [None, "false_positive"])
def test_closed_membership_can_copy_at_any_execution_state(client, event_payload, status, expected):
    run = create_run(client, event_payload, expected)
    analysis_id = run["items"][0]["analysis_id"]
    if status == "stopped":
        assert client.post(f"/api/v1/test-runs/{run['id']}/stop").status_code == 200
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, analysis_id)
        if status != "stopped":
            analysis.status = status
        # Deliberately conflicting model decision, never an expected verdict.
        analysis.verdict = "true_positive"
        analysis.internal_only = True
        db.commit()
        before = (analysis.status, analysis.verdict, analysis.payload_ciphertext, analysis.error_code)
        run_before = (db.get(Run, run["id"]).stopped_at, db.get(Run, run["id"]).accepting_items)
        versions_before = list(db.scalars(select(ValidationDatasetVersion.id).where(ValidationDatasetVersion.is_published)))
    response = preview(client, run)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_count"] == 1 and body["missing_reference_count"] == (expected is None)
    assert event_payload["payload"] not in response.text
    key = str(uuid.uuid4())
    copied = confirm(client, run, body["preview_token"], key)
    assert copied.status_code == 200, copied.text
    assert confirm(client, run, body["preview_token"], key).json() == copied.json()
    with client.app.state.session_factory() as db:
        item = db.scalar(select(Item).where(Item.dataset_id == copied.json()["dataset_id"]))
        assert item.reference_verdict == expected
        assert item.reference_origin == ("reference_label" if expected else "none")
        assert item.validation_state == ("ready" if expected else "needs_attention")
        assert item.internal_only and item.original_analysis_id == analysis_id
        assert event_payload["payload"] not in item.event_ciphertext
        assert db.get(ValidationDataset, item.dataset_id).name == run["name"]
        original = db.get(Analysis, analysis_id)
        assert (original.status, original.verdict, original.payload_ciphertext, original.error_code) == before
        saved_run = db.get(Run, run["id"])
        assert (saved_run.stopped_at, saved_run.accepting_items) == run_before
        assert list(db.scalars(select(ValidationDatasetVersion.id).where(ValidationDatasetVersion.is_published))) == versions_before


def test_open_ingestion_rejected_at_preview_and_confirm_without_partial_writes(client, event_payload):
    run = create_run(client, event_payload)
    token = preview(client, run).json()["preview_token"]
    with client.app.state.session_factory() as db:
        db.get(Run, run["id"]).accepting_items = True
        db.commit()
        before = list(db.scalars(select(ValidationDataset.id)))
    for response in (preview(client, run), confirm(client, run, token)):
        assert response.status_code == 409 and response.json()["detail"] == "test_ingestion_open"
    with client.app.state.session_factory() as db:
        assert list(db.scalars(select(ValidationDataset.id))) == before
    with client.app.state.session_factory() as db:
        db.get(Run, run["id"]).accepting_items = False
        db.commit()
    assert confirm(client, run, token).status_code == 200


@pytest.mark.parametrize("next_state", ["completed", "stopped"])
def test_model_progress_or_stop_does_not_invalidate_input_preview(client, event_payload, next_state):
    run = create_run(client, event_payload, "false_positive")
    token = preview(client, run).json()["preview_token"]
    if next_state == "stopped":
        assert client.post(f"/api/v1/test-runs/{run['id']}/stop").status_code == 200
    else:
        with client.app.state.session_factory() as db:
            analysis = db.get(Analysis, run["items"][0]["analysis_id"])
            analysis.status, analysis.verdict = "completed", "true_positive"
            db.commit()
    assert confirm(client, run, token).status_code == 200


def test_membership_change_invalidates_preview_even_if_ingestion_closed_again(client, event_payload):
    run = create_run(client, event_payload)
    token = preview(client, run).json()["preview_token"]
    with client.app.state.session_factory() as db:
        db.add(RunItem(test_run_id=run["id"], row_number=2, ingest_status="rejected", error_code="invalid_input"))
        db.commit()
    response = confirm(client, run, token)
    assert response.status_code == 409 and response.json()["detail"] == "ground_truth_import_source_changed"


@pytest.mark.usefixtures("registered_vllm_target")
def test_published_verdict_wins_and_revision_is_never_rewritten(client, event_payload, candidate):
    login(client)
    dataset = create_dataset(client)
    assert save(client, dataset, {**event_payload, "vendor_score": 2}, verdict="false_positive").status_code == 201
    version = publish(client, dataset).json()
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json={
        "expected_revision": version["revision"], "dataset_revision_id": version["id"],
        "evaluation_mode": "ground_truth", "candidate_configuration": candidate, "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 202, response.text
    run = response.json()
    token = preview(client, run).json()["preview_token"]
    with client.app.state.session_factory() as db:
        fixed_before = db.get(ValidationDatasetVersion, version["id"]).item_version_ids[:]
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        analysis.status, analysis.verdict = "completed", "true_positive"
        db.commit()
    copied = confirm(client, run, token)
    assert copied.status_code == 200, copied.text
    with client.app.state.session_factory() as db:
        item = db.scalar(select(Item).where(Item.dataset_id == copied.json()["dataset_id"]))
        assert item.reference_verdict == "false_positive" and item.reference_origin == "published_ground_truth"
        assert item.reference_origin_ref_id == fixed_before[0]
        assert db.get(ValidationDatasetVersion, version["id"]).item_version_ids == fixed_before
