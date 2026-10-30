"""Synthetic-only scoped SQL execution observations; no model or live DB access."""
import json
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event, select, text

from app import worker
from app.agent.executor import AgentCallResult
from app.models import AgentRun, AgentStep, Analysis, TestRun as NamedRun, TestRunItem as NamedItem, utcnow
from app.services.test_execution_statistics import execution_summary, fixed_codes, PARSER_WARNING_CODES
from test_test_case_filters import group, rule
from test_test_statistics_exports import fixture_run, stats
from test_test_runs import login, upload


ABSENT = object()
CANARY = "SYNTHETIC_PRIVATE_VALUE_MUST_NOT_LEAVE_SQL"


def result(*reasons, executed=False, framework="moduagent", version="waf-analysis-v3"):
    return {"schema_version": version, "agent": {"framework": framework},
            "verifier": {"executed": executed, "reasons": list(reasons)}}


def record(db, identifier, *, parse="success", warnings=ABSENT, reasons=ABSENT, requested=None,
           entered=False, status="completed", run_status=None, saved=ABSENT, stub=False, at=None):
    row = db.get(Analysis, identifier)
    row.status = status
    if saved is not ABSENT:
        row.result_json = saved
    now = at or utcnow()
    run = AgentRun(analysis_id=identifier, status=run_status or ("running" if status == "processing" else "completed"),
                   created_at=now, started_at=now)
    db.add(run)
    db.flush()
    def step(sequence, name, metadata, **changes):
        item = AgentStep(run_id=run.id, sequence=sequence, step_type=name, name="Synthetic step",
            status="completed", started_at=now, metadata_json=metadata,
            input_ciphertext=CANARY, output_ciphertext=CANARY, **changes)
        db.add(item)
        return item
    if parse is not None:
        metadata = {"parse_status": parse}
        if warnings is not ABSENT:
            metadata["parser_warning_codes"] = warnings
        step(2, "parser", metadata)
    if requested is not None or reasons is not ABSENT:
        metadata = {}
        if requested is not None:
            metadata["execute_verifier"] = requested
        if reasons is not ABSENT:
            metadata["verifier_reason_codes"] = reasons
        step(6, "policy", metadata)
    if entered:
        step(7, "llm_verifier", {})
    if stub:
        step(4, "agent_stub", {"agent_mode": "stub", "llm_called": False})
    return run


def observed_fixture(client, event_payload):
    run = fixture_run(client, event_payload)
    ids = [item["analysis_id"] for item in run["items"]]
    with client.app.state.session_factory() as db:
        db.get(NamedRun, run["id"]).execution_mode = "moduagent"
        record(db, ids[0], warnings=[], reasons=[], requested=False, saved=result())
        record(db, ids[1], parse="partial", warnings=["headers_terminator_missing"],
               reasons=["parser_incomplete", "parser_incomplete"], requested=True, entered=True, saved=result())
        record(db, ids[2], parse="failed", warnings=["headers_terminator_missing", CANARY, CANARY],
               reasons=["parser_incomplete", "low_confidence"], requested=True, entered=True, status="failed", saved=None)
        record(db, ids[3], reasons=["parser_incomplete"], requested=True, status="processing", saved=None)
        record(db, ids[4], parse="partial", warnings={CANARY: CANARY}, reasons=[CANARY, CANARY],
               requested=True, entered=True, status="canceled", saved=None)
        row = db.get(Analysis, ids[5])
        row.status, row.result_json = "failed", None
        row = db.get(Analysis, ids[6])
        row.result_json = result("parser_incomplete", executed=True)
        record(db, ids[7], parse="failed", warnings=[], saved=result(framework="stub"), stub=True)
        db.commit()
    return run


def test_fixed_metadata_codes_never_reflect_arbitrary_values():
    assert fixed_codes([PARSER_WARNING_CODES[0], CANARY, {}, PARSER_WARNING_CODES[0]], PARSER_WARNING_CODES) == [PARSER_WARNING_CODES[0], "other"]
    assert fixed_codes(None, PARSER_WARNING_CODES) == ["other"]
    assert fixed_codes([], PARSER_WARNING_CODES) == []


def test_execution_partition_missing_warnings_and_independent_reason_overlap(client, event_payload, monkeypatch):
    run = observed_fixture(client, event_payload)
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", lambda _: pytest.fail("No execution/raw decryption"))
    response = stats(client, run, limit=1, offset=0, q="not-a-group")
    value = response["execution_summary"]
    assert value["version"] == "test-execution-summary-v1" and value["cases"] == response["summary"]["cases"] == 8
    assert value["parser"] == {"success": 2, "partial": 2, "failed": 2, "unrecorded": 2,
        "warnings_recorded": 4, "warnings_unrecorded": 4,
        "warnings": [{"code": "headers_terminator_missing", "cases": 2}, {"code": "other", "cases": 1}]}
    assert value["verifier"] == {"policy_recorded": 6, "executed": 4, "not_executed": 1,
        "awaiting_execution": 1, "unrecorded": 1, "not_applicable": 1, "reasons_unrecorded": 0,
        "parser_triggered": 3, "parser_only": 2, "parser_with_other": 1,
        "reasons": [{"code": "low_confidence", "cases": 1, "only_cases": 0, "with_other_cases": 1},
                    {"code": "parser_incomplete", "cases": 3, "only_cases": 2, "with_other_cases": 1},
                    {"code": "other", "cases": 1, "only_cases": 1, "with_other_cases": 0}]}
    assert response["total_groups"] == 0 and CANARY not in json.dumps(response)
    assert stats(client, run, group_by="company_name,signature,waf_action", limit=1, offset=2)["execution_summary"] == value


def test_scope_and_nested_reference_filters_apply_before_execution_aggregation(client, event_payload):
    run = observed_fixture(client, event_payload)
    assert stats(client, run, difficulty="hard")["execution_summary"]["cases"] == 4
    expression = group(group(rule("signature", "SQL")), group(rule("verdict", "true_positive"), rule("status", "failed"), op="or"))
    response = client.post(f"/api/v1/test-runs/{run['id']}/statistics/search", json={"case_filter": expression, "q": "no-match", "limit": 1})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"]["cases"] == data["execution_summary"]["cases"] == 3
    assert data["execution_summary"]["verifier"]["executed"] == 2
    assert data["execution_summary"]["parser"]["success"] == data["execution_summary"]["parser"]["partial"] == data["execution_summary"]["parser"]["failed"] == 1
    assert data["total_groups"] == 0
    # Reference/outcome membership is inherited from the existing read context.
    response = client.post(f"/api/v1/test-runs/{run['id']}/statistics/search", json={
        "case_filter": group(rule("reference_verdict", "false_positive"), rule("verdict", "true_positive"))})
    assert response.status_code == 200
    assert response.json()["execution_summary"]["cases"] == response.json()["summary"]["cases"] == 1


def test_latest_run_and_latest_stage_only_and_reclaimed_gap_does_not_mix_old_execution(client, event_payload):
    run = fixture_run(client, event_payload)
    ids = [item["analysis_id"] for item in run["items"]]
    with client.app.state.session_factory() as db:
        db.get(NamedRun, run["id"]).execution_mode = "moduagent"
        old = utcnow() - timedelta(hours=1)
        record(db, ids[0], parse="failed", reasons=["parser_incomplete"], requested=True, entered=True, at=old)
        newest = record(db, ids[0], parse="success", warnings=[], reasons=[], requested=False, saved=result())
        db.add(AgentStep(run_id=newest.id, sequence=3, step_type="parser", name="Latest synthetic parser",
            status="completed", metadata_json={"parse_status": "partial", "parser_warning_codes": []}))
        # Reclaimed analysis has an abandoned old run but no new run yet.
        record(db, ids[1], parse="failed", reasons=["parser_incomplete"], requested=True, entered=True,
               status="processing", run_status="failed", saved=result("parser_incomplete", executed=True))
        db.commit()
        selected = select(Analysis.id.label("analysis_id")).where(Analysis.id.in_(ids[:2]))
        value = execution_summary(db, selected)
    assert value["cases"] == 2 and value["parser"]["partial"] == 1 and value["parser"]["unrecorded"] == 1
    assert value["verifier"]["executed"] == 0 and value["verifier"]["not_executed"] == value["verifier"]["unrecorded"] == 1


@pytest.mark.parametrize("saved", [result("parser_incomplete", executed=True, version="unknown-" + CANARY),
    {"agent": {"framework": "moduagent"}, "verifier": {"executed": "true", "reasons": [CANARY]}}, None])
def test_old_missing_or_unknown_results_do_not_claim_execution_or_reconstruct_reasons(client, event_payload, saved):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.result_json = saved
        value = execution_summary(db, select(Analysis.id.label("analysis_id")).where(Analysis.id == identifier))
    assert value["verifier"]["unrecorded"] == 1 and value["verifier"]["executed"] == 0
    assert value["verifier"]["reasons"] == [] and CANARY not in json.dumps(value)


def test_entered_legacy_failure_without_reasons_has_separate_reason_coverage(client, event_payload):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        record(db, identifier, parse=None, entered=True, status="failed", run_status="failed", saved=None)
        db.commit()
        value = execution_summary(db, select(Analysis.id.label("analysis_id")).where(Analysis.id == identifier))
    verifier = value["verifier"]
    assert verifier["executed"] == verifier["reasons_unrecorded"] == 1
    assert verifier["policy_recorded"] == 0 and verifier["reasons"] == []


@pytest.mark.parametrize("requested,expected", [(False, "not_executed"), (True, "awaiting_execution"), ("true", "unrecorded")])
def test_explicit_policy_decision_and_reason_coverage_are_independent_and_strict(client, event_payload, requested, expected):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        record(db, identifier, parse=None, requested=requested, saved=None)
        db.commit()
        value = execution_summary(db, select(Analysis.id.label("analysis_id")).where(Analysis.id == identifier))
    assert value["verifier"][expected] == 1 and value["verifier"]["policy_recorded"] == 0


@pytest.mark.parametrize("executed", [False, True])
def test_trusted_result_execution_boolean_does_not_require_a_reason_array(client, event_payload, executed):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        saved = result(executed=executed)
        del saved["verifier"]["reasons"]
        db.get(Analysis, identifier).result_json = saved
        db.commit()
        value = execution_summary(db, select(Analysis.id.label("analysis_id")).where(Analysis.id == identifier))
    verifier = value["verifier"]
    assert verifier["executed" if executed else "not_executed"] == 1
    assert verifier["reasons_unrecorded"] == int(executed) and verifier["policy_recorded"] == 0


def test_malformed_stored_json_is_safe_and_cannot_reflect_payload_values(client, event_payload):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        recorded = record(db, identifier, parse=CANARY, entered=True, saved=None)
        db.flush()
        db.execute(text("UPDATE agent_steps SET metadata_json = :value WHERE run_id = :run_id"),
                   {"value": CANARY, "run_id": recorded.id})
        db.execute(text("UPDATE analyses SET result_json = :value WHERE id = :identifier"),
                   {"value": CANARY, "identifier": identifier})
        db.commit()
        value = execution_summary(db, select(Analysis.id.label("analysis_id")).where(Analysis.id == identifier))
    assert value["parser"]["unrecorded"] == value["parser"]["warnings_unrecorded"] == 1
    assert value["verifier"]["executed"] == value["verifier"]["reasons_unrecorded"] == 1
    assert CANARY not in json.dumps(value)


@pytest.mark.parametrize("warnings", [{CANARY: CANARY}, None, CANARY, [PARSER_WARNING_CODES[0]] * 33])
def test_malformed_or_oversized_warning_metadata_is_unrecorded_not_an_observed_warning(client, event_payload, warnings):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        record(db, identifier, warnings=warnings)
        db.commit()
        value = execution_summary(db, select(Analysis.id.label("analysis_id")).where(Analysis.id == identifier))
    assert value["parser"]["warnings_recorded"] == 0 and value["parser"]["warnings_unrecorded"] == 1
    assert value["parser"]["warnings"] == []


def test_current_retry_and_saved_evaluation_keep_the_same_selected_attempt(client, event_payload, registered_vllm_target):
    from test_test_retries import fixture_run as failed_run, finish
    from test_analysis_retries_keys import retry
    run, _ = failed_run(client, event_payload, count=1)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        record(db, identifier, parse="failed", entered=True, reasons=["parser_incomplete"], requested=True,
               status="failed", run_status="failed", saved=None)
        db.commit()
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    response = retry(client, identifier)
    assert response.status_code == 202, response.text
    child = response.json()["analysis_id"]
    finish(client, child)
    with client.app.state.session_factory() as db:
        record(db, child, warnings=[], reasons=[], requested=False, saved=result())
        db.commit()
    current = stats(client, run)["execution_summary"]
    historical = stats(client, run, evaluation_id=saved["id"])["execution_summary"]
    assert current["cases"] == historical["cases"] == 1
    assert current["parser"]["success"] == current["verifier"]["not_executed"] == 1
    assert historical["parser"]["failed"] == historical["verifier"]["parser_only"] == 1


@pytest.mark.parametrize("suffix", ["statistics", "statistics/search"])
def test_execution_statistics_keep_existing_admin_authorization(client, event_payload, service_headers, suffix):
    run = fixture_run(client, event_payload)
    client.cookies.clear()
    request = (lambda **kwargs: client.post(f"/api/v1/test-runs/{run['id']}/{suffix}", json={}, **kwargs)) if suffix.endswith("search") else (
        lambda **kwargs: client.get(f"/api/v1/test-runs/{run['id']}/{suffix}", **kwargs))
    assert request().status_code == 401
    assert request(headers=service_headers).status_code == 403


def test_large_cohort_fetches_only_small_sql_aggregates_and_never_loads_or_decrypts_raw(client, event_payload, monkeypatch):
    login(client)
    run = upload(client, [event_payload])
    count = 600
    large = CANARY + "x" * 8192
    with client.app.state.session_factory() as db:
        seed = db.get(Analysis, run["items"][0]["analysis_id"])
        seed.status, seed.result_json, seed.payload_ciphertext = "completed", result(), large
        record(db, seed.id, warnings=[], reasons=[], requested=False)
        values, membership, runs, steps = [], [], [], []
        for index in range(1, count):
            identifier, run_id = str(uuid.uuid4()), str(uuid.uuid4())
            values.append({"id": identifier, "source_system": run["source_system"], "event_id": f"synthetic-large-{index}",
                "company_name": "Synthetic Corp", "src_ip": "192.0.2.1", "dest_ip": "198.51.100.1", "waf_vendor": "fixture",
                "waf_action": "D", "analysis_purpose": "test", "payload_ciphertext": large, "encryption_key_version": "test",
                "status": "completed", "verdict": "true_positive", "signature": "SQL", "extra_fields": {},
                "result_json": {**result(), "private_canary": large}})
            membership.append({"test_run_id": run["id"], "row_number": index + 1, "analysis_id": identifier, "ingest_status": "accepted"})
            runs.append({"id": run_id, "analysis_id": identifier, "status": "completed"})
            steps.append({"run_id": run_id, "sequence": 2, "step_type": "parser", "name": "Synthetic parser", "status": "completed",
                "metadata_json": {"parse_status": "success", "parser_warning_codes": [], "private_canary": large},
                "input_ciphertext": large, "output_ciphertext": large})
        db.execute(Analysis.__table__.insert(), values)
        db.execute(NamedItem.__table__.insert(), membership)
        db.execute(AgentRun.__table__.insert(), runs)
        db.execute(AgentStep.__table__.insert(), steps)
        db.commit()
    forbidden = {"payload_ciphertext", "result_json", "metadata_json", "input_ciphertext", "output_ciphertext", "tool_calls_json"}
    columns, statements = [], []
    def inspect_columns(_connection, cursor, _statement, _parameters, _context, _many):
        statements.append(_statement)
        if cursor.description:
            selected = {item[0] for item in cursor.description}
            assert not forbidden & selected
            columns.extend(selected)
    def reject_full_load(_target, _context):
        pytest.fail("Execution statistics must not instantiate full event/run/stage rows")
    engine = client.app.state.session_factory.kw["bind"]
    event.listen(engine, "after_cursor_execute", inspect_columns)
    for model in (Analysis, AgentRun, AgentStep):
        event.listen(model, "load", reject_full_load)
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", lambda _: pytest.fail("No payload/trace decryption"))
    durations = {"with_execution_summary_ms": [], "without_execution_summary_ms": [],
                 "with_execution_summary_queries": [], "without_execution_summary_queries": []}
    try:
        for _ in range(2):
            started = time.perf_counter()
            before = len(statements)
            data = stats(client, run, limit=1)
            durations["with_execution_summary_ms"].append(round((time.perf_counter() - started) * 1000, 3))
            durations["with_execution_summary_queries"].append(len(statements) - before)
        from app.services import test_execution_statistics as service
        with monkeypatch.context() as controls:
            controls.setattr(service, "execution_summary", lambda *_args, **_kwargs: {})
            for _ in range(2):
                started = time.perf_counter()
                before = len(statements)
                stats(client, run, limit=1)
                durations["without_execution_summary_ms"].append(round((time.perf_counter() - started) * 1000, 3))
                durations["without_execution_summary_queries"].append(len(statements) - before)
    finally:
        event.remove(engine, "after_cursor_execute", inspect_columns)
        for model in (Analysis, AgentRun, AgentStep):
            event.remove(model, "load", reject_full_load)
    assert data["execution_summary"]["cases"] == data["summary"]["cases"] == count
    assert data["execution_summary"]["parser"]["success"] == count and CANARY not in json.dumps(data)
    assert columns and len(json.dumps(data)) < 12_000
    assert durations["with_execution_summary_queries"] == [value + 1 for value in durations["without_execution_summary_queries"]]
    print("synthetic_execution_statistics_benchmark " + json.dumps({"cases": count, **durations}))


def test_new_worker_metadata_records_only_fixed_parser_warnings(client, event_payload, monkeypatch):
    login(client)
    run = upload(client, [event_payload])
    original = worker.apply_parser_snapshot
    def fake_parser(payload, snapshot, **kwargs):
        value = original(payload, snapshot, **kwargs)
        value["warnings"] = [PARSER_WARNING_CODES[0], CANARY, {CANARY: CANARY}]
        return value
    monkeypatch.setattr(worker, "apply_parser_snapshot", fake_parser)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        worker.process_stub(db, client.app.state.crypto, row)
        metadata = db.scalar(select(AgentStep.metadata_json).where(AgentStep.step_type == "parser"))
    assert metadata["parser_warning_codes"] == [PARSER_WARNING_CODES[0], "other"]
    assert CANARY not in json.dumps(metadata)


@pytest.mark.parametrize("partial", [False, True])
def test_new_model_worker_records_parser_diagnostics_without_parser_only_verifier(client, event_payload, monkeypatch, registered_vllm_target, partial):
    from test_test_retries import fixture_run as model_run
    from test_security_analysis_v3 import output
    if partial:
        event_payload["payload"] = "GET /?q=%3Cscript%3E\r\nHost: example.invalid\r\n\r\n"
    run, _ = model_run(client, event_payload, count=1)
    calls = []
    async def fake(**kwargs):
        calls.append(kwargs)
        value = kwargs["output_model"].model_validate(output().model_dump(mode="json"))
        return AgentCallResult(value, "synthetic", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.status = "pending"
        db.commit()
        worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        policy = db.scalar(select(AgentStep.metadata_json).where(AgentStep.step_type == "policy"))
        diagnostics = row.result_json["diagnostics"]
        db.commit()
        value = execution_summary(db, select(Analysis.id.label("analysis_id")).where(Analysis.id == row.id))
    assert len(calls) == 1
    assert policy["execute_verifier"] is False
    assert policy["verifier_reason_codes"] == []
    assert ("parser_incomplete" in diagnostics["input_signals"]) is partial
    assert value["parser"]["partial" if partial else "success"] == 1
    assert value["verifier"]["not_executed"] == 1
    assert value["verifier"]["parser_triggered"] == value["verifier"]["parser_only"] == 0
