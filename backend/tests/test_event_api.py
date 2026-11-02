"""Synthetic event-key API and bounded HTTP wait regressions; no model calls."""
import asyncio
import uuid

import pytest
from fastapi import Response
from sqlalchemy import func, select
from starlette.requests import Request

from app.api import analyses
from app.api_key_schemas import ServiceApiKeyCreate
from app.models import Analysis, TestRun as Run
from app.result_contracts import AnalysisResultV3
from app.schemas import AnalysisRequest
from app.security import Principal
from app.services.service_api_keys import issue_key
from test_result_contract_v3 import result_fixture


def service_key(client, *, purpose="production", source="event-api-fixture", scopes=None):
    with client.app.state.session_factory() as db:
        _, raw = issue_key(db, ServiceApiKeyCreate(
            name=f"Synthetic {purpose} {uuid.uuid4()}", source_system=source,
            purpose=purpose, scopes=scopes or ["ingest", "review"],
        ), "synthetic-fixture")
        db.commit()
    return {"x-api-key": raw}


def submit(client, event, headers, **query):
    return client.post("/api/v1/analyses", params={"wait_seconds": 0, **query},
                       headers=headers, json=event)


def lookup(client, event_id, headers, **query):
    return client.get("/api/v1/analyses/by-event", params={"event_id": event_id, **query},
                      headers=headers)


def session(client, headers):
    response = client.post("/api/v1/test-sessions", headers=headers,
        json={"name": "Synthetic event lookup", "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def complete(client, identifier):
    canonical = result_fixture()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.verdict = "completed", canonical["verdict"]
        row.summary_ko, row.severity = canonical["summary_ko"], "NONE"
        row.result_json = canonical
        db.commit()
    return canonical


def test_openapi_service_wait_defaults_and_admin_wait_are_separate(client):
    paths = client.get("/openapi.json").json()["paths"]
    parameters = {item["name"]: item for item in paths["/api/v1/analyses"]["post"]["parameters"]}
    wait = parameters["wait_seconds"]
    assert wait["required"] is False
    assert wait["schema"]["default"] == 300
    assert wait["schema"]["minimum"] == 0
    assert wait["schema"]["maximum"] == 600
    admin = {item["name"]: item for item in paths["/api/v1/test-analyses"]["post"]["parameters"]}
    assert admin["wait_seconds"]["schema"]["default"] == 0
    assert admin["wait_seconds"]["schema"]["maximum"] == 60
    lookup_parameters = {item["name"]: item for item in paths["/api/v1/analyses/by-event"]["get"]["parameters"]}
    assert lookup_parameters["event_id"]["required"] is True
    assert lookup_parameters["result_version"]["schema"]["default"] == "v3"


@pytest.mark.parametrize("purpose", ["production", "test"])
@pytest.mark.parametrize("value", [None, 0, 600])
def test_service_wait_accepts_default_zero_and_max_without_real_sleep(client, event_payload, monkeypatch, purpose, value):
    headers = service_key(client, purpose=purpose)
    real_wait, waits = analyses.wait_for_analysis, []

    async def immediate(db, identifier, source, wait_seconds):
        waits.append(wait_seconds)
        return await real_wait(db, identifier, source, 0)

    monkeypatch.setattr(analyses, "wait_for_analysis", immediate)
    response = client.post("/api/v1/analyses", params={} if value is None else {"wait_seconds": value},
                           headers=headers, json=event_payload)
    assert response.status_code == 202, response.text
    assert waits == [300 if value is None else value]
    assert response.json()["analysis_purpose"] == purpose


@pytest.mark.parametrize("value", [-1, 601])
def test_service_wait_rejects_out_of_range_before_admission(client, event_payload, service_headers, value):
    response = submit(client, event_payload, service_headers, wait_seconds=value)
    assert response.status_code == 422
    assert any(item["field"] == "query.wait_seconds" for item in response.json()["detail"])
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0


def test_default_wait_returns_completed_result_and_repost_preserves_it(client, event_payload, service_headers, monkeypatch):
    canonical, pauses = result_fixture(), []

    async def finish_during_wait(_seconds):
        pauses.append(_seconds)
        with client.app.state.session_factory() as db:
            row = db.scalar(select(Analysis).where(Analysis.event_id == event_payload["event_id"]))
            row.status, row.verdict, row.result_json = "completed", canonical["verdict"], canonical
            db.commit()

    monkeypatch.setattr(analyses.asyncio, "sleep", finish_during_wait)
    first = client.post("/api/v1/analyses?result_version=v3", headers=service_headers, json=event_payload)
    assert first.status_code == 200, first.text
    assert pauses
    assert first.json()["status"] == "completed"
    assert AnalysisResultV3.model_validate(first.json()["result"]) == AnalysisResultV3.model_validate(canonical)
    second = submit(client, event_payload, service_headers, result_version="v3")
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["result"] == first.json()["result"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1


def test_failed_duplicate_is_not_automatically_rerun_and_changed_input_conflicts(client, event_payload, service_headers):
    first = submit(client, event_payload, service_headers).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, first["id"])
        row.status, row.error_code, row.error_message = "failed", "primary_agent_failed", "Synthetic failure"
        db.commit()
    repeated = client.post("/api/v1/analyses", json=event_payload, headers=service_headers)
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "failed"
    assert repeated.json()["id"] == first["id"]
    assert repeated.json()["error_code"] == "primary_agent_failed"
    changed = submit(client, {**event_payload, "signature": "changed fixture"}, service_headers)
    assert changed.status_code == 409
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1


def test_event_lookup_defaults_v3_and_explicit_v2_never_rewrites_storage(client, event_payload, service_headers):
    identifier = submit(client, event_payload, service_headers).json()["id"]
    canonical = complete(client, identifier)
    response = lookup(client, event_payload["event_id"], service_headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == identifier
    assert AnalysisResultV3.model_validate(response.json()["result"]) == AnalysisResultV3.model_validate(canonical)
    legacy = lookup(client, event_payload["event_id"], service_headers, result_version="v2")
    assert legacy.status_code == 200
    assert legacy.json()["result"]["schema_version"] == "waf-analysis-v2"
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).result_json == canonical


@pytest.mark.parametrize("version", ["waf-analysis-v1", "waf-analysis-v2"])
def test_event_lookup_keeps_historical_result_versions(client, event_payload, service_headers, version):
    identifier = submit(client, event_payload, service_headers).json()["id"]
    historical = {"schema_version": version, "historical_value": "synthetic"}
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.result_json = "completed", historical
        db.commit()
    response = lookup(client, event_payload["event_id"], service_headers)
    assert response.status_code == 200
    assert response.json()["result"] == historical


def test_event_lookup_requires_ingest_scope_and_key_source(client, event_payload, service_headers):
    submit(client, event_payload, service_headers)
    assert lookup(client, event_payload["event_id"], {}).status_code == 401
    foreign = service_key(client, source="other-event-source")
    assert lookup(client, event_payload["event_id"], foreign).status_code == 404
    review_only = service_key(client, source="test-parser", scopes=["review"])
    assert lookup(client, event_payload["event_id"], review_only).status_code == 403
    assert client.get("/api/v1/analyses/by-event", headers=service_headers).status_code == 422
    assert lookup(client, "missing-fixture", service_headers).status_code == 404


def test_event_lookup_same_event_is_resolved_within_source_and_purpose(client, event_payload):
    production = service_key(client, source="shared-event-source")
    testing = service_key(client, source="shared-event-source", purpose="test")
    other = service_key(client, source="other-event-source")
    identifiers = [submit(client, event_payload, headers).json()["id"] for headers in (production, testing, other)]
    assert len(set(identifiers)) == 3
    for headers, identifier in zip((production, testing, other), identifiers):
        response = lookup(client, event_payload["event_id"], headers)
        assert response.status_code == 200
        assert response.json()["id"] == identifier


def test_test_event_lookup_rejects_ambiguity_and_accepts_owned_session(client, event_payload):
    headers = service_key(client, purpose="test")
    run_ids = [session(client, headers) for _ in range(2)]
    identifiers = [submit(client, event_payload, headers, test_run_id=run_id).json()["id"] for run_id in run_ids]
    ambiguous = lookup(client, event_payload["event_id"], headers)
    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"] == "test_event_ambiguous"
    for run_id, identifier in zip(run_ids, identifiers):
        response = lookup(client, event_payload["event_id"], headers, test_run_id=run_id)
        assert response.status_code == 200
        assert response.json()["id"] == identifier
        assert response.json()["test_run_id"] == run_id
    foreign = service_key(client, purpose="test", source="other-test-source")
    assert lookup(client, event_payload["event_id"], foreign, test_run_id=run_ids[0]).status_code == 404
    assert lookup(client, event_payload["event_id"], headers, test_run_id=str(uuid.uuid4())).status_code == 404


def test_production_event_lookup_rejects_test_session_parameter(client, event_payload, service_headers):
    submit(client, event_payload, service_headers)
    response = lookup(client, event_payload["event_id"], service_headers, test_run_id=str(uuid.uuid4()))
    assert response.status_code == 422


def test_lookup_and_duplicate_keep_original_analysis_when_later_retry_exists(client, event_payload, service_headers):
    original = submit(client, event_payload, service_headers).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, original["id"])
        row.status, row.error_code = "failed", "synthetic_failure"
        copied = {name: getattr(row, name) for name in (
            "source_system", "event_id", "company_name", "src_ip", "dest_ip", "waf_vendor", "waf_action",
            "payload_ciphertext", "encryption_key_version", "analysis_purpose", "ingest_channel", "extra_fields",
        )}
        db.add(Analysis(**copied, retry_of_analysis_id=row.id, status="completed"))
        db.commit()
    for response in (lookup(client, event_payload["event_id"], service_headers),
                     submit(client, event_payload, service_headers)):
        assert response.status_code == 200
        assert response.json()["id"] == original["id"]
        assert response.json()["status"] == "failed"


def test_cancelled_http_wait_does_not_rollback_committed_job(client, event_payload, service_headers, monkeypatch):
    """ASGI cancellation after admission is not cancellation of the DB queue job."""
    async def disconnected(*_args, **_kwargs):
        raise asyncio.CancelledError

    real_wait = analyses.wait_for_analysis
    monkeypatch.setattr(analyses, "wait_for_analysis", disconnected)
    request = Request({"type": "http", "app": client.app, "query_string": b"", "headers": []})
    principal = Principal(kind="service_api_key", scopes=frozenset({"ingest"}), source_system="test-parser")
    with client.app.state.session_factory() as db:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(analyses.submit_analysis(AnalysisRequest.model_validate(event_payload), request,
                Response(), db, principal, 300, "production", "service_api"))
    with client.app.state.session_factory() as db:
        row = db.scalar(select(Analysis))
        assert row is not None and row.status == "pending"
        identifier = row.id
    monkeypatch.setattr(analyses, "wait_for_analysis", real_wait)
    repeated = submit(client, event_payload, service_headers)
    assert repeated.status_code == 202
    assert repeated.json()["id"] == identifier
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1
        assert db.scalar(select(func.count()).select_from(Run)) == 0


def test_wait_releases_db_transaction_before_sleep_and_observes_worker_completion(client, event_payload, service_headers, monkeypatch):
    identifier = submit(client, event_payload, service_headers).json()["id"]
    with client.app.state.session_factory() as db:
        sleeps = []

        async def worker_completes(_seconds):
            assert not db.in_transaction(), "HTTP waiting must not pin a DB connection or read snapshot"
            sleeps.append(_seconds)
            with client.app.state.session_factory() as worker_db:
                worker_db.get(Analysis, identifier).status = "completed"
                worker_db.commit()

        monkeypatch.setattr(analyses.asyncio, "sleep", worker_completes)
        row = asyncio.run(analyses.wait_for_analysis(db, identifier, "test-parser", 600))
        assert sleeps and row.status == "completed"
