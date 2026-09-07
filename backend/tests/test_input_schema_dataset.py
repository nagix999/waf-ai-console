"""Candidate 150-case admission only: no worker execution or provider requests."""
import pytest
from sqlalchemy import func, select

from app.models import (
    Analysis, AnalysisLabel, InputSchemaActivation, InputSchemaState, InputSchemaVersion,
    TestRun as NamedRun, TestRunItem as RunItem, VLLMProfile, VLLMTestRun,
)
from app.services.input_schemas import read_schema_snapshot
from app.services.model_validation import load_dataset
from test_input_schema_integration import activate, login, new_version
from test_model_profiles import profile_payload


@pytest.fixture(autouse=True)
def forbid_model_execution(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Schema/dataset admission tests must never invoke a provider")
    monkeypatch.setattr("app.worker.execute_structured_agent", forbidden)
    monkeypatch.setattr("app.services.model_validation_worker.run_vllm_test", forbidden)


def create_candidate(client):
    response = client.post("/api/v1/model-profiles", json=profile_payload())
    assert response.status_code == 201, response.text
    return response.json()


def request_body(key="synthetic-schema-dataset"):
    return {"mode": "full", "include_dataset": True, "name": "합성 스키마 호환 검증", "idempotency_key": key}


def table_counts(db):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model)) for model in (
        Analysis, AnalysisLabel, VLLMTestRun, NamedRun, RunItem, InputSchemaVersion, InputSchemaActivation,
    )}


def test_compatible_dataset_pins_same_schema_for_candidate_run_named_run_and_all_150(client, registered_vllm_target):
    login(client)
    events, _ = load_dataset()
    first = events[0][0].model_dump(mode="json", exclude_unset=True)
    marker = "SYNTHETIC-FIELD-METADATA-ONLY-150"
    version = new_version(client, {"name": "sensor_zone", "type": "string", "description": marker})
    activate(client, version, first)
    profile = create_candidate(client)
    response = client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json=request_body())
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    with client.app.state.session_factory() as db:
        candidate = db.get(VLLMTestRun, identifier)
        named = db.scalar(select(NamedRun).where(NamedRun.model_test_run_id == identifier))
        analyses = db.scalars(select(Analysis).where(Analysis.model_test_run_id == identifier)).all()
        assert len(analyses) == 150
        assert db.scalar(select(func.count()).select_from(RunItem).where(RunItem.test_run_id == named.id)) == 150
        assert db.scalar(select(func.count()).select_from(AnalysisLabel)) == 150
        assert {target.input_schema_version_id for target in [candidate, named, *analyses]} == {version["id"]}
        snapshot = read_schema_snapshot(client.app.state.crypto, candidate)
        assert snapshot["content_hash"] == version["content_hash"]
        assert snapshot["selection_origin"] == "model_validation"
        assert snapshot["field_metadata_usage"] == "history_only"
        assert any(field["description"] == marker for field in snapshot["fields"])
        assert all(read_schema_snapshot(client.app.state.crypto, target) == snapshot for target in [named, *analyses])
        assert marker not in candidate.input_schema_snapshot_ciphertext
        assert all("sensor_zone" not in row.extra_fields for row in analyses)
        assert {row.status for row in analyses} == {"pending"}
        assert candidate.status == "pending" and candidate.started_at is None
        assert db.get(VLLMProfile, profile["id"]).status == "draft"
        before_ciphertexts = {row.id: row.input_schema_snapshot_ciphertext for row in analyses}
        counts = table_counts(db)

    # A later active contract is incompatible with this dataset, but cannot
    # rewrite queued cases or force revalidation of an idempotent retransmit.
    later = new_version(client, {"name": "mandatory_after_submission", "type": "boolean", "required": True})
    activate(client, later, {**first, "mandatory_after_submission": True})
    replay = client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json=request_body())
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == identifier
    with client.app.state.session_factory() as db:
        candidate = db.get(VLLMTestRun, identifier)
        named = db.scalar(select(NamedRun).where(NamedRun.model_test_run_id == identifier))
        analyses = db.scalars(select(Analysis).where(Analysis.model_test_run_id == identifier)).all()
        assert {row.id: row.input_schema_snapshot_ciphertext for row in analyses} == before_ciphertexts
        assert read_schema_snapshot(client.app.state.crypto, candidate) == snapshot
        assert read_schema_snapshot(client.app.state.crypto, named) == snapshot
        after = table_counts(db)
        for table in ("analyses", "analysis_labels", "vllm_test_runs", "test_runs", "test_run_items"):
            assert after[table] == counts[table]
        assert db.get(InputSchemaState, 1).active_version_id == later["id"]


@pytest.mark.parametrize("incompatibility,expected_attempts", [("new_required", 1), ("third_event_disallowed", 3)])
def test_incompatible_dataset_rolls_back_all_new_members_without_changing_old_data(
    client, registered_vllm_target, event_payload, monkeypatch, incompatibility, expected_attempts,
):
    login(client)
    existing = client.post("/api/v1/analyses", json=event_payload)
    assert existing.status_code == 202, existing.text
    existing_id = existing.json()["id"]
    events, _ = load_dataset()
    sample = events[0][0].model_dump(mode="json", exclude_unset=True)
    if incompatibility == "new_required":
        version = new_version(client, {"name": "new_required_zone", "type": "string", "required": True})
        sample["new_required_zone"] = "synthetic"
    else:
        # The first two rows are actually admitted inside savepoints. The third
        # triggers the contract violation, proving outer rollback is atomic.
        def restrict_event_ids(fields):
            next(field for field in fields if field["name"] == "event_id")["enum"] = [events[0][0].event_id, events[1][0].event_id]
        version = new_version(client, modify=restrict_event_ids)
    activate(client, version, sample)
    profile = create_candidate(client)
    from app.services.test_runs import enqueue_test_upload_row
    attempts = 0
    def record_admission(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return enqueue_test_upload_row(*args, **kwargs)
    monkeypatch.setattr("app.services.test_runs.enqueue_test_upload_row", record_admission)
    with client.app.state.session_factory() as db:
        counts = table_counts(db)
        row = db.get(Analysis, existing_id)
        old_values = {column.name: getattr(row, column.name) for column in Analysis.__table__.columns}
        active_revision = db.get(InputSchemaState, 1).revision
    failed = client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json=request_body())
    assert failed.status_code == 422, failed.text
    assert failed.json()["detail"] == "input_schema_validation_failed"
    assert attempts == expected_attempts
    with client.app.state.session_factory() as db:
        assert table_counts(db) == counts
        row = db.get(Analysis, existing_id)
        assert {column.name: getattr(row, column.name) for column in Analysis.__table__.columns} == old_values
        state = db.get(InputSchemaState, 1)
        assert (state.active_version_id, state.revision) == (version["id"], active_revision)
        assert db.get(VLLMProfile, profile["id"]).status == "draft"
        assert db.scalar(select(func.count()).select_from(VLLMTestRun)) == 0
