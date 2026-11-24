"""Only fixed, synthetic third-call telemetry; no model or private event reads."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import event

from app.services.execution_latency import summarize_execution_latency
from app.services.test_comparisons import _step_usage
from app.services.test_performance_summary import summarize_performance
from scripts import compare_security_analysis_v3 as comparison
from test_execution_latency import phase, record
from test_test_performance_summary import CANARY, add_run, attempts, summary
from test_test_statistics_exports import fixture_run


def telemetry():
    initial = {"output_validation_retry": attempts((100, 10, 1), (200, 20, 2)),
               "concurrency": {"wait_ms": 1}}
    source = {"output_validation_retry": attempts((300, 30, 3)),
              "concurrency": {"wait_ms": 2}}
    review = {"kind": "decision_review", "output_validation_retry": attempts((400, 40, 4)),
              "concurrency": {"wait_ms": 3}}
    for call in (initial, source, review):
        for attempt in call["output_validation_retry"]["attempts"]:
            usage = attempt["usage"]
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return {**deepcopy(review), "usage": {"input_tokens": 99999, "output_tokens": 99999, "total_tokens": 99999},
        "private_text": CANARY,
        "evidence_grounding_retry": {"attempt_count": 3, "max_attempts": 3, "recovered": False,
            "decision_review_attempted": True, "attempts": [initial, source, review]},
        "evidence_decision_review": {"version": "grounded-decision-review-v1", "attempted": True,
            "recovered": True, "skipped_reason": None, "excluded_count": 1}}


def test_third_review_is_in_totals_but_not_citation_time_or_schema_retries():
    metadata = telemetry()
    before = deepcopy(metadata)
    report = summarize_performance([record(steps=[phase(metadata)])])
    role = report["roles"]["primary"]
    assert role["invocation_ms"]["mean_ms"] == 1000
    assert role["first_attempt_ms"]["mean_ms"] == 100
    assert role["output_retry_ms"]["mean_ms"] == 200
    assert role["citation_repair_ms"]["mean_ms"] == 300
    assert role["decision_review_ms"]["mean_ms"] == 400
    assert role["slot_wait_ms"]["mean_ms"] == 6
    assert role["accounting"]["reported_citation_repairs"] == 1
    assert role["accounting"]["reported_decision_reviews"] == 1
    assert role["accounting"]["reported_output_attempts"] == 4
    assert role["accounting"]["reported_output_retries"] == 1
    assert role["accounting"]["cases_with_decision_reviews"] == 1
    assert role["tokens"]["input_tokens"]["sum"] == 100
    assert role["tokens"]["output_tokens"]["sum"] == 10
    assert role["tokens"]["first_attempt_output_tokens"]["sum"] == 1
    assert _step_usage(metadata) == {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    accounting = comparison.retry_accounting(metadata)
    assert accounting["schema_attempts"] == 4 and accounting["schema_retries"] == 1
    assert accounting["output_recovery_duration_ms"] == 200
    assert metadata == before and metadata["evidence_grounding_retry"]["recovered"] is False
    assert CANARY not in json.dumps(report)


@pytest.mark.parametrize("mutation", [
    lambda m: m["evidence_grounding_retry"].pop("decision_review_attempted"),
    lambda m: m["evidence_grounding_retry"].update(decision_review_attempted="true"),
    lambda m: m["evidence_grounding_retry"]["attempts"][2].update(kind=CANARY),
    lambda m: m["evidence_grounding_retry"].update(attempt_count=2),
    lambda m: m["evidence_grounding_retry"]["attempts"][2]["output_validation_retry"].update(attempt_count=2),
    lambda m: m["evidence_grounding_retry"]["attempts"][0].update(
        output_validation_retry=attempts((100, 10, 1), (100, 10, 1), (100, 10, 1), (100, 10, 1))),
])
def test_unmarked_or_inconsistent_third_call_stays_unrecorded(mutation):
    metadata = telemetry()
    mutation(metadata)
    role = summarize_execution_latency([record(steps=[phase(metadata)])])["roles"]["primary"]
    assert role["accounting"]["unrecorded_cases"] == 1
    assert role["invocation_ms"]["p50_ms"] is None
    assert _step_usage(metadata) == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    assert comparison.retry_accounting(metadata)["accounting_complete"] is False


@pytest.mark.parametrize("marked", [True, False])
def test_sql_summary_reads_only_fixed_third_call_scalars(client, event_payload, monkeypatch, marked):
    run = fixture_run(client, event_payload)
    metadata = telemetry()
    if not marked:
        metadata["evidence_grounding_retry"].pop("decision_review_attempted")
    with client.app.state.session_factory() as db:
        add_run(db, run["items"][0]["analysis_id"], metadata=metadata)
        db.commit()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("aggregate must not decrypt private text")
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", forbidden)
    engine = client.app.state.session_factory.kw["bind"]
    statements = []
    def inspect(_conn, cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split()[0].upper())
        if cursor.description:
            assert not {"metadata_json", "input_ciphertext", "output_ciphertext", "payload_ciphertext"}.intersection(
                column[0] for column in cursor.description)
    event.listen(engine, "after_cursor_execute", inspect)
    try:
        report = summary(client, run)
    finally:
        event.remove(engine, "after_cursor_execute", inspect)
    role = report["roles"]["primary"]
    if marked:
        assert role["invocation_ms"]["reported_sum_ms"] == 1000
        assert role["citation_repair_ms"]["reported_sum_ms"] == 300
        assert role["decision_review_ms"]["reported_sum_ms"] == 400
        assert role["slot_wait_ms"]["reported_sum_ms"] == 6
        assert role["tokens"]["output_tokens"]["sum"] == 10
        assert role["accounting"]["reported_decision_reviews"] == 1
    else:
        assert role["accounting"]["unrecorded_cases"] == 1
        assert role["tokens"]["output_tokens"]["unrecorded"] == 1
    assert CANARY not in json.dumps(report)
    assert not {"INSERT", "UPDATE", "DELETE", "REPLACE"}.intersection(statements)


def test_review_validation_failure_keeps_fixed_field_and_code():
    metadata = telemetry()
    attempt = metadata["evidence_grounding_retry"]["attempts"][2]["output_validation_retry"]["attempts"][0]
    attempt.update(error_code="output_validation_failed", finish_reason="failed",
                   validation_issues=[{"field": "retained_evidence_ids", "type": "review_evidence_omitted"}])
    failures = summarize_performance([record(steps=[phase(metadata)])])["roles"]["primary"]["output_failures"]
    assert failures["validation_issues"] == [{"field": "retained_evidence_ids", "type": "review_evidence_omitted", "attempts": 1}]
