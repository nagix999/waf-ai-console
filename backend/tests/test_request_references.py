"""Admission/reference regressions: artificial events, isolated DB, no LLM calls."""
import json
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Analysis, AnalysisLabel, AgentRun, TestRunItem as RunItem, TestRun as Run
from app.schemas import AnalysisInput
from app.services.analysis import event_fingerprint
from app.agent.input_builder import build_agent_input
from app.services.http_parser import parse_http_payload
from app.worker import _event_document
from test_validation_data import key, login, save_reference, forbid_model_calls
from app.services.validation_datasets import digest


@pytest.mark.parametrize("purpose", ["test", "production"])
@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
def test_request_answer_is_attached_before_execution_and_never_in_model_input(client, event_payload, purpose, verdict):
    headers = key(client, purpose)
    body = {**event_payload, "expected_verdict": verdict}
    first = client.post("/api/v1/analyses", headers=headers, json=body)
    assert first.status_code == 202, first.text
    result = first.json()
    assert result["analysis_purpose"] == purpose
    assert result["evaluation"]["reference_label"]["verdict"] == verdict
    assert result["evaluation"]["outcome"] == "pending"
    repeated = client.post("/api/v1/analyses", headers=headers, json=body)
    assert repeated.status_code == 202 and repeated.json()["id"] == result["id"]
    assert repeated.json()["evaluation"]["reference_label"]["verdict"] == verdict
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, result["id"])
        label = db.scalar(select(AnalysisLabel))
        assert db.query(AnalysisLabel).count() == 1 and label.revision == 1
        assert label.source_kind == "reference" and label.ai_visible is None
        assert label.source_ref == "analysis-request:expected_verdict"
        assert analysis.event_fingerprint == event_fingerprint(AnalysisInput.model_validate(event_payload).model_dump(mode="json"))
        assert analysis.extra_fields == {"future_vendor_field": "preserved"}
        raw = client.app.state.crypto.decrypt_text(analysis.payload_ciphertext)
        model_input = build_agent_input(_event_document(analysis), raw, parse_http_payload(raw), 32768, 3072).text
        assert "expected_verdict" not in model_input and "analysis-request:" not in model_input
        assert db.query(AgentRun).count() == 0
        schema = client.app.state.crypto.decrypt_text(analysis.input_schema_snapshot_ciphertext)
        assert '"name": "expected_verdict"' not in schema
    # The pure domain input still rejects evaluation fields.
    with pytest.raises(ValidationError):
        AnalysisInput.model_validate(body)


@pytest.mark.parametrize("purpose", ["test", "production"])
@pytest.mark.parametrize("value", ["bad-answer", "TRUE_POSITIVE", True, 1, {}, []])
def test_bad_answer_rejects_without_enqueue(client, event_payload, purpose, value):
    response = client.post("/api/v1/analyses", headers=key(client, purpose), json={**event_payload, "expected_verdict": value})
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 0


def test_production_replay_conflict_and_omission_preserve_reference(client, event_payload):
    headers = key(client, "production")
    plain = client.post("/api/v1/analyses", headers=headers, json=event_payload).json()
    attached = client.post("/api/v1/analyses", headers=headers, json={**event_payload, "expected_verdict": "inconclusive"})
    assert attached.status_code == 202 and attached.json()["id"] == plain["id"]
    for extra in ({}, {"expected_verdict": None}):
        assert client.post("/api/v1/analyses", headers=headers, json={**event_payload, **extra}).json()["evaluation"]["reference_label"]["verdict"] == "inconclusive"
    conflict = client.post("/api/v1/analyses", headers=headers, json={**event_payload, "expected_verdict": "false_positive"})
    assert conflict.status_code == 409 and conflict.json()["detail"] == "expected_verdict_conflict"
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 1


@pytest.mark.parametrize("purpose", ["test", "production"])
def test_upload_answers_for_both_keys(client, event_payload, purpose):
    rows = [{**event_payload, "expected_verdict": "inconclusive"},
            {**event_payload, "event_id": "invalid", "expected_verdict": "no"}]
    response = client.post("/api/v1/uploads", headers=key(client, purpose),
        files={"file": ("fixture.json", json.dumps(rows), "application/json")})
    assert response.status_code == 202, response.text
    result = response.json()
    assert (result["accepted"], result["rejected"], result["label_attached"]) == (1, 1, 1)
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 1


@pytest.mark.parametrize("initial", [None, "false_positive"])
def test_latest_test_scores_refresh_while_initial_and_saved_answers_remain(client, event_payload, initial):
    login(client)
    request = {"name": "오프라인 답안 검증", "idempotency_key": str(uuid.uuid4()), "event": event_payload}
    if initial:
        request["expected_verdict"] = initial
    run = client.post("/api/v1/test-runs", json=request).json()
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        # Fabricated stored result for API/UI behavior, not a measured model score.
        row.status, row.verdict, row.completed_at = "completed", "true_positive", datetime.now(UTC)
        row.model_profile, row.prompt_version = "offline-fixture", "offline-fixture"
        row.result_json = {"verdict": "true_positive", "agent": {"framework": "moduagent"}}
        initial_label = db.scalar(select(RunItem)).label_id
        db.commit()
    save_reference(client, [identifier], "true_positive")
    path = f"/api/v1/test-runs/{run['id']}"
    latest = client.get(path, params={"reference_basis": "latest"}).json()
    assert latest["reference_basis"] == "latest"
    assert latest["evaluation_summary"]["metrics"]["accuracy"] == 1
    assert latest["items"][0]["evaluation"]["outcome"] == "match"
    listing = client.get("/api/v1/test-runs", params={"reference_basis": "latest"}).json()
    assert listing["items"][0]["evaluation_summary"]["metrics"]["accuracy"] == 1
    original = client.get(path, params={"reference_basis": "initial"}).json()
    assert original["evaluation_summary"]["metrics"]["accuracy"] == (0 if initial else None)
    saved = client.post(path + "/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    save_reference(client, [identifier], "false_positive")
    assert client.get(path, params={"reference_basis": "latest"}).json()["evaluation_summary"]["metrics"]["accuracy"] == 0
    frozen = client.get(path, params={"reference_basis": "latest", "evaluation_id": saved["id"]}).json()
    assert frozen["reference_basis"] == "saved" and frozen["evaluation_summary"]["metrics"]["accuracy"] == 1
    with client.app.state.session_factory() as db:
        assert db.scalar(select(RunItem)).label_id == initial_label
        assert db.get(Analysis, identifier).verdict == "true_positive"
        assert db.query(AgentRun).count() == 0


def test_request_openapi_includes_reference_without_editable_schema_field(client):
    login(client)
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    event, request = schemas["AnalysisInput"], schemas["AnalysisRequest"]
    assert "expected_verdict" not in event["properties"]
    assert "expected_verdict" in event["propertyNames"]["not"]["enum"]
    assert "expected_verdict" in request["properties"]
    assert "expected_verdict" not in request["propertyNames"]["not"]["enum"]
    assert "expected_verdict" not in request["required"]


def test_server_sort_orders_all_matching_analyses_before_pagination(client, event_payload):
    headers = key(client, "production")
    for name in ["C", "A", "B", "A"]:
        body = {**event_payload, "event_id": str(uuid.uuid4()), "company_name": name}
        assert client.post("/api/v1/analyses", headers=headers, json=body).status_code == 202
    pages = [client.get("/api/v1/analyses", headers=headers, params={"sort_by": "company_name", "sort_order": "asc", "limit": 2, "offset": offset}).json() for offset in (0, 2)]
    assert [row["company_name"] for page in pages for row in page["items"]] == ["A", "A", "B", "C"]
    assert len({row["id"] for page in pages for row in page["items"]}) == 4
    assert all(page["total"] == page["evaluation_summary"]["total"] == 4 for page in pages)
    assert client.get("/api/v1/analyses", headers=headers, params={"sort_by": "payload"}).status_code == 422


def test_server_sort_orders_test_lists_and_items_before_pagination(client, event_payload):
    login(client)
    for name in ["C", "A", "B"]:
        response = client.post("/api/v1/test-runs", json={"name": name, "idempotency_key": str(uuid.uuid4()), "event": event_payload})
        assert response.status_code == 202
    listing = client.get("/api/v1/test-runs", params={"sort_by": "name", "sort_order": "asc", "limit": 1, "offset": 1}).json()
    assert listing["total"] == 3 and listing["items"][0]["name"] == "B"
    rows = [{**event_payload, "event_id": name, "case_name": name} for name in ["C", "A", "B"]]
    uploaded = client.post("/api/v1/test-runs/uploads", data={"name": "문항 정렬", "idempotency_key": str(uuid.uuid4())},
        files={"file": ("fixture.json", json.dumps(rows), "application/json")}).json()
    result = client.get(f"/api/v1/test-runs/{uploaded['id']}", params={"sort_by": "case_name", "sort_order": "asc", "offset": 1, "limit": 1, "reference_basis": "latest"}).json()
    assert result["total_items"] == result["evaluation_summary"]["total"] == 3
    assert result["items"][0]["case_name"] == "B"


def test_latest_reference_available_before_run_finishes_but_snapshot_waits(client, event_payload):
    login(client)
    run = client.post("/api/v1/test-runs", json={"name": "진행 중 답안", "idempotency_key": str(uuid.uuid4()), "event": event_payload}).json()
    save_reference(client, [run["items"][0]["analysis_id"]], "inconclusive")
    path = f"/api/v1/test-runs/{run['id']}"
    current = client.get(path, params={"reference_basis": "latest"}).json()
    assert current["evaluation_summary"]["labeled"] == 1
    assert current["items"][0]["evaluation"]["outcome"] == "pending"
    assert current["evaluation_summary"]["metrics"]["accuracy"] is None
    assert client.post(path + "/evaluations", json={"idempotency_key": str(uuid.uuid4())}).status_code == 409


def test_test_request_replay_preserves_legacy_normalized_identity(client, event_payload):
    headers = key(client, "test")
    minimal = {key: value for key, value in event_payload.items() if key not in {"signature", "event_name", "src_port", "dest_port"}}
    first = client.post("/api/v1/analyses", headers=headers, json=minimal).json()
    legacy = AnalysisInput.model_validate(minimal).model_dump(mode="json")
    with client.app.state.session_factory() as db:
        assert db.get(Run, first["test_run_id"]).request_hash == digest([legacy])
    for body in (legacy, {**minimal, "expected_verdict": None}):
        replay = client.post("/api/v1/analyses", headers=headers, json=body)
        assert replay.status_code == 202 and replay.json()["id"] == first["id"]


def test_production_label_write_failure_rolls_back_analysis(client, event_payload):
    headers = key(client, "production")
    def fail_label(db, context, instances):
        if any(isinstance(row, AnalysisLabel) for row in db.new):
            raise IntegrityError(None, None, Exception("fixture-label-write-failure"))
    event.listen(Session, "before_flush", fail_label)
    try:
        response = client.post("/api/v1/analyses", headers=headers, json={**event_payload, "expected_verdict": "true_positive"})
    finally:
        event.remove(Session, "before_flush", fail_label)
    assert response.status_code == 409
    assert "fixture-label-write-failure" not in response.text
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 0
