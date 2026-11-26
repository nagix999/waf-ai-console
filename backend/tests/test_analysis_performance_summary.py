"""Synthetic read-only timing populations; no live DB, provider or plaintext reads."""
from datetime import timedelta
import json

import pytest
from sqlalchemy import event

from app.models import Analysis, EvidenceDisplayJob, utcnow
from test_test_runs import login


PATH = "/api/v1/analyses/performance-summary"
CANARY = "SYNTHETIC_PRIVATE_EVENT_MUST_NOT_BE_READ"


def seed(db, *, at, duration=10, queue=2, **changes):
    row = Analysis(source_system="performance-fixture", event_id="synthetic-event",
        company_name="Fixture", analysis_purpose="production", src_ip="192.0.2.1", dest_ip="198.51.100.1",
        waf_vendor="generic", waf_action="D", payload_ciphertext=CANARY, encryption_key_version="fixture-v1",
        status="completed", verdict="true_positive", created_at=at,
        started_at=at + timedelta(seconds=queue), completed_at=at + timedelta(seconds=duration),
        result_json={"private": CANARY})
    for key, value in changes.items():
        setattr(row, key, value)
    db.add(row)
    db.flush()
    return row


def summary(client, **filters):
    response = client.post(PATH, json=filters)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_completed_percentiles_ignore_failures_inflight_and_missing_as_zero(client):
    login(client)
    at = utcnow() - timedelta(minutes=10)
    with client.app.state.session_factory() as db:
        for index in range(1, 101):
            seed(db, at=at, duration=index, queue=1, event_id=f"synthetic-{index}")
        seed(db, at=at, event_id="missing", started_at=None, completed_at=None)
        seed(db, at=at, event_id="failed", status="failed", duration=500)
        seed(db, at=at, event_id="pending", status="pending", started_at=None, completed_at=None)
        seed(db, at=at, event_id="test-excluded", analysis_purpose="test", duration=600)
        db.commit()
    body = summary(client, analysis_purpose="production", limit=1, offset=100,
        sort_by="company_name", sort_order="asc")
    assert body["version"] == "analysis-performance-summary-v1"
    assert body["cases"] == 103 and body["completed_cases"] == 101
    assert body["statuses"] == {"completed": 101, "failed": 1, "pending": 1}
    metric = body["completed_timings"]["decision_elapsed_ms"]
    assert metric["recorded"] == 100 and metric["unrecorded"] == 1
    assert metric["mean_ms"] == metric["p50_ms"] == 50500
    assert metric["p95_ms"] == 95000 and metric["p99_ms"] == 99000
    assert body["completed_timings"]["analysis_processing_ms"]["p50_ms"] == 49500
    assert body["completed_timings"]["analysis_queue_ms"]["p99_ms"] == 1000
    assert summary(client, analysis_purpose="production", status="pending")["completed_cases"] == 0


def test_latest_display_job_controls_result_ready_without_counting_pending_as_ready(client, monkeypatch):
    login(client)
    at = utcnow() - timedelta(minutes=10)
    with client.app.state.session_factory() as db:
        complete = seed(db, at=at, event_id="display-complete")
        pending = seed(db, at=at, event_id="display-pending")
        for row, state in ((complete, "completed"), (pending, "running")):
            db.add(EvidenceDisplayJob(analysis_id=row.id, status=state, snapshot_ciphertext=CANARY,
                encryption_key_version="fixture", created_at=at, started_at=at + timedelta(seconds=11),
                completed_at=at + timedelta(seconds=15) if state == "completed" else None,
                metadata_json={"private": CANARY}))
        db.commit()
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", lambda _: pytest.fail("Must not decrypt"))
    body = summary(client, analysis_purpose="production")
    assert body["completed_timings"]["decision_elapsed_ms"]["recorded"] == 2
    ready = body["completed_timings"]["result_ready_elapsed_ms"]
    assert ready["recorded"] == ready["unrecorded"] == 1
    assert ready["p50_ms"] == ready["p95_ms"] == ready["p99_ms"] == 15000
    assert CANARY not in json.dumps(body)


def test_scope_matches_analysis_list_including_labels_filters_and_retry_exclusion(client):
    login(client)
    at = utcnow() - timedelta(minutes=10)
    with client.app.state.session_factory() as db:
        original = seed(db, at=at, event_id="original", company_name="One")
        seed(db, at=at, event_id="retry", company_name="One", retry_of_analysis_id=original.id)
        seed(db, at=at, event_id="other-company", company_name="Two")
        seed(db, at=at, event_id="other-purpose", company_name="One", analysis_purpose="test")
        db.commit()
    for filters in ({"analysis_purpose": "production"},
                    {"analysis_purpose": "production", "include_retries": True},
                    {"analysis_purpose": "production", "company_name": "One"},
                    {"analysis_purpose": "production", "label_presence": "unlabeled"},
                    {"analysis_purpose": "production", "label_presence": "labeled"},
                    {"analysis_purpose": "production", "q": "other", "search_field": "event_id"}):
        listed = client.get("/api/v1/analyses", params=filters)
        assert listed.status_code == 200, listed.text
        assert summary(client, **filters)["cases"] == listed.json()["total"]


def test_admin_only_no_store_and_small_read_only_sql_projections(client, service_headers):
    assert client.post(PATH, json={}).status_code == 401
    assert client.post(PATH, json={}, headers=service_headers).status_code == 403
    login(client)
    assert client.post(PATH + "?limit=1", json={}).status_code == 422
    assert client.post(PATH, json={"payload": CANARY}).status_code == 422
    at = utcnow() - timedelta(minutes=10)
    with client.app.state.session_factory() as db:
        seed(db, at=at)
        db.commit()
    engine = client.app.state.session_factory.kw["bind"]
    statements = []
    forbidden = {"payload_ciphertext", "result_json", "metadata_json", "input_ciphertext", "output_ciphertext", "tool_calls_json"}
    def inspect(_connection, cursor, statement, _parameters, _context, _many):
        statements.append(statement)
        if cursor.description:
            assert not {column[0] for column in cursor.description} & forbidden
        assert not statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "REPLACE"))
    def no_hydration(_target, _context):
        pytest.fail("Timing aggregates must not hydrate events/jobs")
    event.listen(engine, "after_cursor_execute", inspect)
    event.listen(Analysis, "load", no_hydration)
    event.listen(EvidenceDisplayJob, "load", no_hydration)
    try:
        assert summary(client, analysis_purpose="production")["cases"] == 1
    finally:
        event.remove(engine, "after_cursor_execute", inspect)
        event.remove(Analysis, "load", no_hydration)
        event.remove(EvidenceDisplayJob, "load", no_hydration)
    assert len([sql for sql in statements if sql.lstrip().upper().startswith(("SELECT", "WITH"))]) <= 4


def test_runtime_uses_same_completed_timing_population_and_preserves_legacy_terminal_latency(client):
    login(client)
    at = utcnow() - timedelta(minutes=10)
    with client.app.state.session_factory() as db:
        seed(db, at=at, event_id="completed", duration=10, queue=3)
        seed(db, at=at, event_id="failed", status="failed", duration=100)
        seed(db, at=at, event_id="test-excluded", analysis_purpose="test", duration=500)
        seed(db, at=at - timedelta(days=2), event_id="old-excluded", duration=600)
        db.commit()
    response = client.get("/api/v1/admin/runtime/status?window=1h&purpose=production")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["completed_cases"] == 1
    assert body["completed_timings"]["decision_elapsed_ms"]["p99_ms"] == 10000
    assert body["completed_timings"]["analysis_processing_ms"]["p50_ms"] == 7000
    assert body["latency_summary"]["sample_count"] == 2
    assert body["latency_summary"]["p99"] == 100000
