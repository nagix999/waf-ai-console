"""Synthetic telemetry only: no database, model, payload or output decoding."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
import json

import pytest

from app.services.execution_latency import summarize_execution_latency


NOW = datetime(2026, 11, 12, tzinfo=UTC)


def retry(*durations, kinds=()):
    return {"attempt_count": len(durations), "attempts": [
        {"attempt": index + 1, "duration_ms": duration,
         "retry_kind": kinds[index] if index < len(kinds) else None}
        for index, duration in enumerate(durations)]}


def phase(metadata=None, *, name="llm_primary", state="completed"):
    return {"step_type": name, "status": state,
            "metadata": {"timing_measured": True, "duration_ms": 1500,
                         "output_validation_retry": retry(1000), "concurrency": {"wait_ms": 100},
                         **(metadata or {})}}


def record(**changes):
    return {"status": "completed", "execution_mode": "moduagent", "created_at": NOW,
            "started_at": NOW + timedelta(seconds=1), "completed_at": NOW + timedelta(seconds=3),
            "steps": [phase()], "display_job": None, **changes}


def test_basic_breakdown_separates_steps_attempts_wait_and_wall_time():
    report = summarize_execution_latency([record()])
    assert report["version"] == "execution-latency-v1"
    assert report["cases"] == 1
    assert report["timings"]["decision_elapsed_ms"]["p50_ms"] == 3000
    assert report["timings"]["analysis_queue_ms"]["p50_ms"] == 1000
    assert report["timings"]["analysis_processing_ms"]["p50_ms"] == 2000
    assert report["timings"]["result_ready_elapsed_ms"]["p50_ms"] == 3000
    assert report["phases"]["llm_primary"]["duration"]["p50_ms"] == 1500
    role = report["roles"]["primary"]
    assert role["invocation_ms"]["p50_ms"] == 1000
    assert role["slot_wait_ms"]["p50_ms"] == 100
    assert role["output_retry_ms"]["p50_ms"] == 0
    assert role["citation_repair_ms"]["p50_ms"] == 0
    assert role["accounting"]["reported_output_attempts"] == 1
    assert report["phases"]["llm_verifier"]["entered_cases"] == 0
    assert report["phases"]["llm_verifier"]["not_observed_cases"] == 1
    assert report["roles"]["verifier"]["invocation_ms"]["recorded"] == 0
    assert report["roles"]["verifier"]["not_observed_cases"] == 1


def test_citation_and_output_retries_are_counted_once_not_also_at_top_level():
    first, citation = retry(900, 700, kinds=("schema_correction",)), retry(300)
    metadata = {"output_validation_retry": citation, "evidence_grounding_retry": {
        "attempt_count": 2, "attempts": [
            {"output_validation_retry": first}, {"output_validation_retry": citation}]}}
    report = summarize_execution_latency([record(steps=[phase(metadata)])])
    role = report["roles"]["primary"]
    assert role["invocation_ms"]["p50_ms"] == 1900
    assert role["first_attempt_ms"]["p50_ms"] == 900
    assert role["output_retry_ms"]["p50_ms"] == 700
    assert role["citation_repair_ms"]["p50_ms"] == 300
    assert role["slot_wait_ms"]["p50_ms"] is None  # Only the last wait survives in old metadata.
    assert role["slot_wait_ms"]["unrecorded"] == 1
    assert role["accounting"] == {"recorded_cases": 1, "unrecorded_cases": 0,
        "reported_output_attempts": 3, "reported_output_retries": 1, "reported_citation_repairs": 1}
    assert role["retry_kinds"] == {"schema_correction": 1}


def test_retry_time_is_not_a_pure_schema_error_or_http_count():
    report = summarize_execution_latency([record(steps=[phase({
        "output_validation_retry": retry(500, 400, 200, kinds=("compact_output", "timeout_recovery"))})])])
    role = report["roles"]["primary"]
    assert role["output_retry_ms"]["p50_ms"] == 600
    assert role["retry_kinds"] == {"compact_output": 1, "timeout_recovery": 1}
    assert "model_attempt_includes_transport_framework_validation" in report["notes"]
    assert "nested_timings_overlap_do_not_add" in report["notes"]


def test_display_queue_begins_after_decision_not_when_job_was_admitted():
    job = {"status": "completed", "created_at": NOW, "started_at": NOW + timedelta(seconds=4),
           "completed_at": NOW + timedelta(seconds=6), "metadata": {"duration_ms": 1900, "model_called": True}}
    report = summarize_execution_latency([record(display_job=job)])
    assert report["timings"]["decision_elapsed_ms"]["p50_ms"] == 3000
    assert report["timings"]["result_ready_elapsed_ms"]["p50_ms"] == 6000
    display = report["display"]
    assert display["queue_ms"]["p50_ms"] == 1000
    assert display["processing_ms"]["p50_ms"] == 1900
    assert display["post_decision_elapsed_ms"]["p50_ms"] == 3000
    assert display["dispatch_markers"] == {"true": 1}


@pytest.mark.parametrize("status", ["pending", "running"])
def test_decision_completed_is_not_reported_ready_while_display_is_pending(status):
    report = summarize_execution_latency([record(display_job={"status": status, "created_at": NOW})])
    assert report["timings"]["decision_elapsed_ms"]["p50_ms"] == 3000
    assert report["timings"]["result_ready_elapsed_ms"]["p50_ms"] is None
    assert report["timings"]["result_ready_elapsed_ms"]["unrecorded"] == 1


def test_job_created_after_decision_and_failure_fallback_remain_measurable():
    job = {"status": "failed", "created_at": NOW + timedelta(seconds=4),
           "started_at": NOW + timedelta(seconds=5), "completed_at": NOW + timedelta(seconds=6),
           "metadata": {"duration_ms": 1000}}
    report = summarize_execution_latency([record(display_job=job)])
    assert report["display"]["queue_ms"]["p50_ms"] == 1000
    assert report["display"]["post_decision_elapsed_ms"]["p50_ms"] == 2000
    assert report["timings"]["result_ready_elapsed_ms"]["p50_ms"] == 6000


def test_missing_legacy_records_and_leases_are_not_zero_time():
    report = summarize_execution_latency([record(steps=[{"step_type": "llm_primary", "status": "failed"}]),
        record(status="processing", completed_at=None, steps=[phase({"timing_incomplete": True})]),
        record(status="failed", steps=[phase(state="failed")])])
    role = report["roles"]["primary"]
    assert role["invocation_ms"]["recorded"] == 2
    assert role["invocation_ms"]["unrecorded"] == 1
    assert report["phases"]["llm_primary"]["duration"]["recorded"] == 1
    assert report["phases"]["llm_primary"]["duration"]["unrecorded"] == 2
    assert report["timings"]["decision_elapsed_ms"]["unrecorded"] == 1
    assert report["timings"]["result_ready_elapsed_ms"]["unrecorded"] == 2


@pytest.mark.parametrize("bad", [True, -1, "1000", 1.5, float("nan"), float("inf"), 10**40, None])
def test_invalid_numeric_diagnostics_are_unknown_and_safe_json(bad):
    report = summarize_execution_latency([record(steps=[phase({"duration_ms": bad,
        "output_validation_retry": retry(bad), "concurrency": {"wait_ms": bad}})])])
    assert report["phases"]["llm_primary"]["duration"]["unrecorded"] == 1
    assert report["roles"]["primary"]["invocation_ms"]["unrecorded"] == 1
    assert report["roles"]["primary"]["slot_wait_ms"]["unrecorded"] == 1
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("grounding", [None, {}, {"attempt_count": True, "attempts": [{}]},
    {"attempt_count": 2, "attempts": [{"output_validation_retry": retry(100)}]},
    {"attempt_count": 3, "attempts": [{}, {}, {}]}])
def test_partial_nested_record_does_not_fall_back_to_duplicated_last_call(grounding):
    report = summarize_execution_latency([record(steps=[phase({"evidence_grounding_retry": grounding})])])
    assert report["roles"]["primary"]["accounting"]["unrecorded_cases"] == 1
    assert report["roles"]["primary"]["invocation_ms"]["p50_ms"] is None


def test_partially_missing_attempt_duration_preserves_coverage_and_known_counts():
    report = summarize_execution_latency([record(steps=[phase({"output_validation_retry": retry(500, None)})])])
    role = report["roles"]["primary"]
    assert role["first_attempt_ms"]["p50_ms"] == 500
    assert role["invocation_ms"]["p50_ms"] is None
    assert role["output_retry_ms"]["p50_ms"] is None
    assert role["accounting"]["reported_output_attempts"] == 2


def test_only_fixed_names_counts_and_numbers_leave_the_helper_without_input_mutation():
    secret = "SYNTHETIC_SECRET_DO_NOT_EXPORT"
    row = record(status=secret, execution_mode=secret, source_system=secret, event_id=secret,
        result_json={"summary_ko": secret}, payload_ciphertext=secret,
        steps=[phase({"error_summary": {"code": secret}, "output_validation_retry": retry(500, 200, kinds=(secret,))}),
               {"step_type": secret, "status": secret, "metadata": {secret: secret}}],
        display_job={"status": secret, "metadata": {"model_called": secret, "payload": secret}})
    before = deepcopy(row)
    report = summarize_execution_latency([row])
    assert secret not in json.dumps(report)
    assert row == before
    assert report["statuses"] == {"unrecorded": 1}
    assert report["roles"]["primary"]["retry_kinds"] == {"unrecorded": 1}


def test_percentiles_are_from_individual_observations_not_sum_of_phase_percentiles():
    rows = [record(steps=[phase({"duration_ms": duration})]) for duration in range(100, 2100, 100)]
    report = summarize_execution_latency(iter(rows))
    metric = report["phases"]["llm_primary"]["duration"]
    assert metric == {"recorded": 20, "unrecorded": 0, "reported_sum_ms": 21000,
                      "min_ms": 100, "p50_ms": 1050.0, "p95_ms": 1900, "max_ms": 2000}


def test_naive_sqlite_and_offset_timestamps_are_equivalent_and_negative_gaps_unknown():
    korea = timezone(timedelta(hours=9))
    row = record(created_at=NOW.replace(tzinfo=None), started_at=(NOW + timedelta(seconds=1)).astimezone(korea))
    report = summarize_execution_latency([row, record(completed_at=NOW - timedelta(seconds=1))])
    assert report["timings"]["analysis_queue_ms"]["p50_ms"] == 1000
    assert report["timings"]["decision_elapsed_ms"]["recorded"] == 1
    assert report["timings"]["decision_elapsed_ms"]["unrecorded"] == 1


def test_empty_report_is_valid_and_does_not_claim_zero_latency():
    report = summarize_execution_latency([])
    assert report["cases"] == 0
    assert report["timings"]["decision_elapsed_ms"]["p50_ms"] is None
    assert report["timings"]["decision_elapsed_ms"]["recorded"] == 0


def test_absent_step_is_not_proof_of_zero_time_or_a_skipped_model_call():
    report = summarize_execution_latency([record(steps=[])])
    assert report["phases"]["llm_primary"]["entered_cases"] == 0
    assert report["phases"]["llm_primary"]["not_observed_cases"] == 1
    assert report["phases"]["llm_primary"]["duration"]["p50_ms"] is None
    assert "stage_not_observed_does_not_prove_skipped" in report["notes"]


@pytest.mark.parametrize("flag", [None, 0, "false", [], {}])
def test_malformed_incomplete_flag_does_not_certify_a_step_duration(flag):
    report = summarize_execution_latency([record(steps=[phase({"timing_incomplete": flag})])])
    assert report["phases"]["llm_primary"]["duration"]["unrecorded"] == 1
