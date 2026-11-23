"""Synthetic scoped performance observations; no model or live DB access."""
from copy import deepcopy
from datetime import timedelta
import json
import time
import uuid

import pytest
from sqlalchemy import event, text

from app.models import AgentRun, AgentStep, Analysis, AnalysisLabel, EvidenceDisplayJob, TestRun as NamedRun, TestRunItem as NamedItem, utcnow
from app.services.test_performance_summary import summarize_performance
from test_execution_latency import NOW, phase, record, retry
from test_test_case_filters import group, rule
from test_candidate_configurations import candidate
from test_test_runs import login
from test_test_statistics_exports import fixture_run

CANARY = "SYNTHETIC_PRIVATE_TEXT_MUST_NOT_LEAVE_SQL"


def attempts(*values):
    result = retry(*(value[0] for value in values), kinds=("schema_correction",) * (len(values) - 1))
    for entry, (_duration, input_tokens, output_tokens) in zip(result["attempts"], values):
        entry["usage"] = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        entry["finish_reason"] = "completed"
    return result


def summary(client, run, **body):
    response = client.post(f"/api/v1/test-runs/{run['id']}/performance-summary", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def add_run(db, analysis_id, *, at=None, status="completed", metadata=None, step_status="completed"):
    now = at or utcnow()
    run = AgentRun(analysis_id=analysis_id, status=status, created_at=now, started_at=now)
    db.add(run)
    db.flush()
    db.add(AgentStep(run_id=run.id, sequence=5, step_type="llm_primary", name="Synthetic stage",
        status=step_status, input_ciphertext=CANARY, output_ciphertext=CANARY,
        metadata_json={"timing_measured": True, "duration_ms": 500,
            "concurrency": {"wait_ms": 50}, "output_validation_retry": attempts((400, 100, 30)),
            "private_text": CANARY, **(metadata or {})}))
    return run


def test_tokens_and_output_retries_do_not_double_count_nested_citation_calls():
    first, citation = attempts((900, 100, 30), (700, 120, 40)), attempts((300, 80, 20))
    metadata = {"output_validation_retry": citation, "evidence_grounding_retry": {
        "attempt_count": 2, "attempts": [
            {"output_validation_retry": first}, {"output_validation_retry": citation}]}}
    original = record(steps=[phase(metadata)])
    before = deepcopy(original)
    report = summarize_performance([original])
    assert original == before
    role = report["roles"]["primary"]
    assert role["tokens"]["input_tokens"] == {"recorded": 1, "unrecorded": 0,
        "sum": 300, "mean": 300, "p50": 300, "p95": 300, "max": 300}
    assert role["tokens"]["output_tokens"]["sum"] == 90
    assert role["tokens"]["first_attempt_output_tokens"]["sum"] == 30
    assert role["accounting"]["cases_with_output_retries"] == 1
    assert role["accounting"]["cases_with_citation_repairs"] == 1
    assert role["accounting"]["reported_output_attempts"] == 3
    assert role["invocation_ms"]["mean_ms"] == 1900
    assert role["output_retry_ms"]["mean_ms"] == 700
    assert role["citation_repair_ms"]["mean_ms"] == 300
    assert report["timings"]["decision_elapsed_ms"]["mean_ms"] == 3000
    assert report["roles"]["verifier"]["tokens"]["output_tokens"]["recorded"] == 0


@pytest.mark.parametrize("missing", [None, True, -1, 1.5, "30", 2**40])
def test_missing_or_invalid_tokens_are_unknown_and_never_partial_totals(missing):
    value = attempts((300, 100, 20), (200, 120, missing))
    report = summarize_performance([record(steps=[phase({"output_validation_retry": value})])])
    metrics = report["roles"]["primary"]["tokens"]
    assert metrics["output_tokens"]["recorded"] == 0
    assert metrics["output_tokens"]["unrecorded"] == 1
    assert metrics["output_tokens"]["mean"] is None
    assert metrics["input_tokens"]["sum"] == 220
    assert metrics["first_attempt_output_tokens"]["sum"] == 20


def test_endpoint_selects_latest_run_step_and_preserves_display_and_missing_data(client, event_payload, monkeypatch):
    run = fixture_run(client, event_payload)
    ids = [item["analysis_id"] for item in run["items"]]
    at = utcnow()
    with client.app.state.session_factory() as db:
        db.get(NamedRun, run["id"]).execution_mode = "moduagent"
        row = db.get(Analysis, ids[0])
        row.created_at, row.started_at, row.completed_at = at, at + timedelta(seconds=1), at + timedelta(seconds=3)
        add_run(db, ids[0], at=at - timedelta(seconds=5), metadata={"duration_ms": 999999})
        newest = add_run(db, ids[0], at=at, metadata={"duration_ms": 9999})
        db.flush()
        initial = attempts((100, 20, 10), (100, 30, 15))
        initial["attempts"][0].update(error_code="output_validation_failed", provider_finish_reason="stop",
            finish_reason="failed", validation_issues=[{"field": "evidence.interpretation_ko", "type": "evidence_text_required"}])
        citation = attempts((200, 40, 25))
        db.add(AgentStep(run_id=newest.id, sequence=6, step_type="llm_primary", name="Latest synthetic stage", status="completed",
            metadata_json={"duration_ms": 600, "timing_measured": True,
                "output_validation_retry": citation, "evidence_grounding_retry": {
                    "attempt_count": 2, "attempts": [
                        {"output_validation_retry": initial}, {"output_validation_retry": citation}]}}))
        # Older abandoned execution is not the current processing attempt.
        db.get(Analysis, ids[1]).status = "processing"
        add_run(db, ids[1], status="failed")
        # Skipped stages without telemetry stay unknown, not zero-token calls.
        add_run(db, ids[2], metadata={"output_validation_retry": {}}, step_status="skipped")
        db.add(EvidenceDisplayJob(analysis_id=ids[0], status="completed", snapshot_ciphertext=CANARY,
            encryption_key_version="test", created_at=at, started_at=at + timedelta(seconds=4),
            completed_at=at + timedelta(seconds=5), metadata_json={"duration_ms": 900, "model_called": True, "private": CANARY}))
        db.commit()
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", lambda _: pytest.fail("Never decrypt events/traces"))
    result = summary(client, run)
    assert result["version"] == "test-performance-summary-v1" and result["cases"] == 8
    primary = result["roles"]["primary"]
    assert primary["not_observed_cases"] == 6
    assert primary["tokens"]["output_tokens"]["sum"] == 50
    assert primary["tokens"]["first_attempt_output_tokens"]["sum"] == 10
    assert primary["tokens"]["output_tokens"]["unrecorded"] == 1
    assert primary["accounting"]["cases_with_output_retries"] == primary["accounting"]["cases_with_citation_repairs"] == 1
    assert primary["output_failures"]["validation_issues"] == [
        {"field": "evidence", "type": "evidence_text_required", "attempts": 1}]
    assert result["phases"]["llm_primary"]["duration"]["max_ms"] == 600
    assert result["display"]["queue_ms"]["mean_ms"] == 1000
    assert result["display"]["processing_ms"]["mean_ms"] == 900
    assert result["timings"]["result_ready_elapsed_ms"]["max_ms"] == 5000
    assert CANARY not in json.dumps(result)
    assert not {"analysis_id", "run_id", "metadata_json", "input_ciphertext", "output_ciphertext"}.intersection(result)


def test_scope_reference_filters_and_saved_evaluation_match_statistics(client, event_payload):
    run = fixture_run(client, event_payload)
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][1]["analysis_id"])
        db.add(AnalysisLabel(analysis_id=row.id, revision=2, verdict="true_positive", source_kind="reference", source_ref="synthetic",
            ai_visible=False, created_by="fixture", attachment_id=str(uuid.uuid4()), token_digest="c" * 64))
        db.commit()
    predicate = group(rule("evaluation_outcome", "false_positive"))
    for scope, expected in [({}, 0), ({"reference_basis": "initial"}, 1), ({"evaluation_id": saved["id"]}, 1)]:
        result = summary(client, run, case_filter=predicate, **scope)
        stats = client.post(f"/api/v1/test-runs/{run['id']}/statistics/search", json={"case_filter": predicate,
            "q": "no matching group", "limit": 1, **scope}).json()
        assert result["cases"] == stats["summary"]["cases"] == expected
        assert result["case_filter_applied"] is True
    assert summary(client, run, difficulty="hard")["cases"] == 4
    assert summary(client, run, difficulty_missing=True)["cases"] == 0
    assert summary(client, run, test_category_missing=True)["cases"] == 8


def test_latest_successful_retry_is_counted_once_and_saved_evaluation_keeps_original(client, event_payload):
    run = fixture_run(client, event_payload)
    original_id = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, original_id)
        original.status, original.verdict = "failed", None
        add_run(db, original_id, status="failed")
        db.commit()
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, original_id)
        child = Analysis(**{key: getattr(original, key) for key in (
            "source_system", "event_id", "company_name", "src_ip", "dest_ip", "waf_vendor", "waf_action",
            "payload_ciphertext", "encryption_key_version")}, retry_of_analysis_id=original_id,
            analysis_purpose="test", status="completed", verdict="true_positive", completed_at=utcnow())
        db.add(child)
        db.flush()
        add_run(db, child.id, metadata={"output_validation_retry": attempts((800, 300, 80))})
        db.commit()
    selected = {"case_filter": group(rule("event_id", "synthetic-0"))}
    current = summary(client, run, **selected)
    historical = summary(client, run, evaluation_id=saved["id"], **selected)
    assert current["cases"] == historical["cases"] == 1
    assert current["statuses"] == {"completed": 1} and historical["statuses"] == {"failed": 1}
    assert current["roles"]["primary"]["tokens"]["output_tokens"]["sum"] == 80
    assert historical["roles"]["primary"]["tokens"]["output_tokens"]["sum"] == 30


@pytest.mark.usefixtures("registered_vllm_target")
def test_official_performance_scope_uses_frozen_ground_truth(client, event_payload, candidate):
    from test_official_evaluations import approved_item, execute
    from test_validation_data import create_dataset
    from test_test_retries import finish
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2}, "false_positive")
    run = execute(client, dataset, candidate)
    identifier = run["items"][0]["analysis_id"]
    finish(client, identifier, "true_positive")
    with client.app.state.session_factory() as db:
        db.add(AnalysisLabel(analysis_id=identifier, revision=2, verdict="true_positive", source_kind="reference", source_ref="synthetic",
            ai_visible=False, created_by="fixture", attachment_id=str(uuid.uuid4()), token_digest="d" * 64))
        db.commit()
    result = summary(client, run, case_filter=group(rule("evaluation_outcome", "false_positive")), reference_basis="latest")
    assert result["reference_basis"] == "ground_truth" and result["cases"] == 1


def test_read_only_admin_endpoint_validates_scope_and_rejects_query_options(client, event_payload, service_headers):
    run = fixture_run(client, event_payload)
    path = f"/api/v1/test-runs/{run['id']}/performance-summary"
    for body in [{"group_by": "signature"}, {"difficulty": "hard", "difficulty_missing": True},
                 {"case_filter": group(rule("payload", CANARY))}]:
        assert client.post(path, json=body).status_code == 422
    assert client.post(path + "?limit=1", json={}).status_code == 422
    assert client.post(path, json={"evaluation_id": str(uuid.uuid4())}).status_code == 404
    assert client.post(f"/api/v1/test-runs/{uuid.uuid4()}/performance-summary", json={}).status_code == 404
    client.cookies.clear()
    assert client.post(path, json={}).status_code == 401
    assert client.post(path, json={}, headers=service_headers).status_code == 403
    login(client)
    engine = client.app.state.session_factory.kw["bind"]
    statements, selected_columns = [], []
    forbidden = {"payload_ciphertext", "result_json", "metadata_json", "input_ciphertext", "output_ciphertext", "tool_calls_json"}
    def inspect(_connection, cursor, statement, _parameters, _context, _many):
        statements.append(statement)
        if cursor.description:
            names = {column[0] for column in cursor.description}
            assert not names & forbidden
            selected_columns.extend(names)
        assert not statement.lstrip().upper().startswith(("UPDATE", "DELETE", "INSERT", "REPLACE"))
    def no_hydration(_target, _context):
        pytest.fail("Performance aggregate must not hydrate an entire event/trace/job")
    event.listen(engine, "after_cursor_execute", inspect)
    for model in (Analysis, AgentRun, AgentStep, EvidenceDisplayJob):
        event.listen(model, "load", no_hydration)
    try:
        assert summary(client, run)["cases"] == 8
    finally:
        event.remove(engine, "after_cursor_execute", inspect)
        for model in (Analysis, AgentRun, AgentStep, EvidenceDisplayJob):
            event.remove(model, "load", no_hydration)
    assert len(statements) <= 12  # Fixed query count, no per-case event/trace fetches.
    assert "analysis_id" in selected_columns


def test_malformed_metadata_and_unknown_diagnostics_remain_payload_free(client, event_payload):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        newest = add_run(db, identifier, metadata={"output_validation_retry": {
            "attempt_count": 1, "attempts": [{"duration_ms": 100, "error_code": CANARY,
                "provider_finish_reason": CANARY, "finish_reason": "completed", "usage": {"output_tokens": CANARY},
                "validation_issues": [{"field": CANARY, "type": CANARY}]}]}})
        db.commit()
        newest_id = newest.id
    result = summary(client, run)
    assert CANARY not in json.dumps(result)
    assert result["roles"]["primary"]["output_failures"]["attempt_outcomes"] == {"unrecorded": 1}
    assert result["roles"]["primary"]["tokens"]["output_tokens"]["unrecorded"] == 1
    with client.app.state.session_factory() as db:
        db.execute(text("UPDATE agent_steps SET metadata_json=:value WHERE run_id=:run"), {"value": CANARY, "run": newest_id})
        db.commit()
    result = summary(client, run)
    assert result["roles"]["primary"]["accounting"]["unrecorded_cases"] == 1
    assert CANARY not in json.dumps(result)


def test_large_synthetic_cohort_uses_fixed_scalar_queries(client, event_payload):
    run = fixture_run(client, event_payload)
    count = 500
    large = CANARY * 256
    analyses, members, runs, steps = [], [], [], []
    with client.app.state.session_factory() as db:
        for index in range(count):
            identifier, run_id = str(uuid.uuid4()), str(uuid.uuid4())
            analyses.append({"id": identifier, "source_system": "synthetic-performance", "event_id": f"performance-{index}",
                "company_name": "Synthetic Corp", "src_ip": "192.0.2.1", "dest_ip": "198.51.100.1", "waf_vendor": "fixture",
                "waf_action": "D", "analysis_purpose": "test", "payload_ciphertext": large, "encryption_key_version": "test",
                "status": "completed", "verdict": "true_positive", "signature": "SQL", "extra_fields": {},
                "result_json": {"private_canary": large}})
            members.append({"test_run_id": run["id"], "row_number": index + 11, "analysis_id": identifier, "ingest_status": "accepted"})
            runs.append({"id": run_id, "analysis_id": identifier, "status": "completed"})
            steps.append({"run_id": run_id, "sequence": 5, "step_type": "llm_primary", "name": "Synthetic primary", "status": "completed",
                "metadata_json": {"timing_measured": True, "duration_ms": 600, "output_validation_retry": attempts((500, 200, 50)),
                    "private_canary": large}, "input_ciphertext": large, "output_ciphertext": large})
        db.execute(Analysis.__table__.insert(), analyses)
        db.execute(NamedItem.__table__.insert(), members)
        db.execute(AgentRun.__table__.insert(), runs)
        db.execute(AgentStep.__table__.insert(), steps)
        db.commit()
    engine = client.app.state.session_factory.kw["bind"]
    statements, query_ms = [], []
    def begin(_connection, _cursor, _statement, _parameters, context, _many):
        context.performance_started = time.perf_counter()
    def inspect(_connection, cursor, statement, _parameters, _context, _many):
        statements.append(statement)
        query_ms.append(round((time.perf_counter() - _context.performance_started) * 1000, 3))
        if cursor.description:
            assert not {"payload_ciphertext", "result_json", "metadata_json", "input_ciphertext", "output_ciphertext"}.intersection(
                column[0] for column in cursor.description)
    event.listen(engine, "before_cursor_execute", begin)
    event.listen(engine, "after_cursor_execute", inspect)
    try:
        started = time.perf_counter()
        result = summary(client, run)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    finally:
        event.remove(engine, "before_cursor_execute", begin)
        event.remove(engine, "after_cursor_execute", inspect)
    assert result["cases"] == count + 8
    assert result["roles"]["primary"]["tokens"]["output_tokens"]["sum"] == count * 50
    assert len(statements) <= 12
    assert CANARY not in json.dumps(result)
    print("synthetic_performance_summary_benchmark " + json.dumps({
        "cases": count + 8, "elapsed_ms": elapsed_ms, "queries": len(statements), "query_ms": query_ms}))
