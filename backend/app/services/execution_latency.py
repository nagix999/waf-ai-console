"""Count-only latency diagnostics, outside the public analysis API contract.

These observations are not GPU generation time or HTTP request counts. A model
attempt includes transport, framework and validation work. Retry times overlap
the enclosing step, and output retries can overlap citation-repair time; never
sum their percentiles or add them to the enclosing duration.

Only fixed diagnostic names, bounded numeric values and timestamps are consumed. Missing
old telemetry stays unknown. Neither raw input, encrypted traces, model output
nor arbitrary diagnostic strings are returned.
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from statistics import median


VERSION = "execution-latency-v1"
PHASES = (
    "input", "parser", "decoder", "agent_input", "llm_primary", "policy",
    "llm_verifier", "finalize", "llm_evidence_editor", "agent_stub",
)
ROLES = {"llm_primary": "primary", "llm_verifier": "verifier",
         "llm_evidence_editor": "legacy_evidence_editor"}
ANALYSIS_STATUSES = ("pending", "processing", "completed", "failed", "canceled")
STEP_STATUSES = ("pending", "running", "completed", "failed", "skipped", "canceled")
DISPLAY_STATUSES = ("pending", "running", "completed", "skipped", "failed", "canceled")
RETRY_KINDS = ("schema_correction", "compact_output", "timeout_recovery")
# Closed diagnostic vocabulary. Never echo an upstream error, schema key or
# model-produced value merely because it resembles a diagnostic string.
OUTPUT_ERROR_CODES = ("output_validation_failed", "model_output_incomplete",
                      "vllm_output_incomplete", "openai_output_incomplete")
PROVIDER_FINISH_REASONS = ("stop", "length", "max_tokens", "timeout")
ATTEMPT_FINISH_REASONS = ("completed", "failed", "error", "canceled")
VALIDATION_FIELDS = ("$", "evidence", "evidence_relations", "signature_assessment", "summary_ko",
    "verdict", "confidence_score", "threat_analysis", "decision_issue", "input_truncated",
    "interpretation_conflict", "capture_assessment", "corrections", "analyst_checks",
    "recommended_checks", "tuning_recommendation", "retained_evidence_ids")
VALIDATION_TYPES = (
    "evidence_text_required", "evidence_source_repeated", "evidence_expansion_limit", "evidence_support_conflict",
    "evidence_source_unavailable", "evidence_source_support_forbidden", "decisive_verdict_requires_directional_content",
    "decision_source_unselected", "decision_source_repeated",
    "evidence_id_repeated", "evidence_link_repeated", "evidence_link_unknown",
    "evidence_contribution_support_invalid", "evidence_contribution_target_not_root",
    "evidence_link_self", "evidence_link_cycle", "decision_evidence_unselected", "decision_evidence_repeated",
    "decision_review_context_invalid", "review_evidence_repeated", "review_evidence_not_eligible",
    "review_evidence_omitted", "review_evidence_dependency_missing",
    "evidence_relation_invalid", "evidence_relation_conflicting_supports",
    "decisive_verdict_requires_evidence", "true_positive_requires_threat_severity",
    "false_positive_requires_none_severity", "inconclusive_requires_unknown_severity",
    "recommended_tuning_requires_scope_proposal_risk_and_validation", "duplicate_correction_index",
    "signature_exact_requires_match", "signature_exact_disallows_mismatch",
    "signature_partial_requires_match_and_mismatch", "signature_mismatch_requires_difference",
    "signature_unknown_requires_reason", "signature_v2_explanation_must_be_null",
    "signature_legacy_requires_explanation", "signature_v2_requires_version",
    "signature_detail_requires_explanation", "analyst_check_purpose_required",
    "decisive_verdict_disallows_decision_condition", "recommended_checks_legacy_only",
    "decisive_verdict_disallows_missing_condition", "summary_must_describe_request",
    "json_invalid", "json_duplicate_key", "json_non_finite", "json_size_limit", "json_depth_limit",
    "json_encoding_invalid", "json_top_level_object_required", "decode_error", "validation_error",
    "missing", "extra_forbidden", "literal_error", "enum", "string_type", "string_too_short",
    "string_too_long", "list_type", "too_short", "too_long", "int_type", "int_parsing",
    "float_type", "float_parsing", "bool_type", "dict_type", "model_type", "none_required",
    "greater_than", "greater_than_equal", "less_than", "less_than_equal", "finite_number", "value_error",
)
_TERMINAL = frozenset({"completed", "failed", "canceled", "skipped"})
_MAX_MS = 7 * 24 * 60 * 60 * 1000


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _fixed(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unrecorded"


def _number(value, maximum=_MAX_MS):
    return value if type(value) is int and 0 <= value <= maximum else None


def _time(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _elapsed(start, end):
    start, end = _time(start), _time(end)
    if start is None or end is None or end < start:
        return None
    return _number(int((end - start).total_seconds() * 1000))


@dataclass
class _Metric:
    values: list[int] = field(default_factory=list)
    unrecorded: int = 0

    def add(self, value):
        value = _number(value)
        if value is None:
            self.unrecorded += 1
        else:
            self.values.append(value)

    def present(self):
        values = sorted(self.values)
        return {"recorded": len(values), "unrecorded": self.unrecorded,
                "reported_sum_ms": sum(values),
                "min_ms": values[0] if values else None,
                "p50_ms": median(values) if values else None,
                "p95_ms": values[ceil(.95 * len(values)) - 1] if values else None,
                "p99_ms": values[ceil(.99 * len(values)) - 1] if values else None,
                "max_ms": values[-1] if values else None}


def _grounding_calls(metadata):
    """Recognize a bounded third decision review, never an arbitrary extra call."""
    grounding = _mapping(metadata.get("evidence_grounding_retry"))
    calls, count = grounding.get("attempts"), grounding.get("attempt_count")
    if type(count) is not int or not isinstance(calls, list) or len(calls) != count:
        return None
    if grounding.get("decision_review_attempted") is True:
        if count != 3 or _mapping(calls[2]).get("kind") != "decision_review":
            return None
        counts = [_mapping(_mapping(call).get("output_validation_retry")).get("attempt_count") for call in calls]
        if (any(type(value) is not int for value in counts)
                or not 1 <= counts[0] <= 3 or counts[1:] != [1, 1]):
            return None
        for call, expected in zip(calls, counts, strict=True):
            attempts = _mapping(_mapping(call).get("output_validation_retry")).get("attempts")
            if (not isinstance(attempts, list) or len(attempts) != expected
                    or any(not isinstance(attempt, Mapping) for attempt in attempts)):
                return None
    elif not 1 <= count <= 2:
        return None
    return calls


def _calls(metadata):
    """Prefer nested calls; the top-level retry repeats the LAST nested call."""
    if "evidence_grounding_retry" in metadata:
        calls = _grounding_calls(metadata)
        if calls is None:
            return None
    else:
        calls = [metadata]
    parsed = []
    for call in calls:
        retry = _mapping(_mapping(call).get("output_validation_retry"))
        count, attempts = retry.get("attempt_count"), retry.get("attempts")
        if (type(count) is not int or not 1 <= count <= 4
                or not isinstance(attempts, list) or len(attempts) != count
                or any(not isinstance(attempt, Mapping) for attempt in attempts)):
            return None
        parsed.append(attempts)
    return parsed


def _sum_durations(attempts):
    durations = [_number(attempt.get("duration_ms")) for attempt in attempts]
    return sum(durations) if all(value is not None for value in durations) else None


def _slot_wait(metadata, calls):
    grounding = _mapping(metadata.get("evidence_grounding_retry"))
    nested = grounding.get("attempts")
    if isinstance(nested, list) and len(nested) == len(calls):
        waits = [_number(_mapping(_mapping(call).get("concurrency")).get("wait_ms"))
                 for call in nested]
        if all(value is not None for value in waits):
            return sum(waits)
        if len(calls) > 1 or any("concurrency" in _mapping(call) for call in nested):
            return None
    # Historical single calls keep their top-level wait. On old citation
    # repair only the last call survives there; it is not the role total.
    return (_number(_mapping(metadata.get("concurrency")).get("wait_ms"))
            if len(calls) == 1 else None)


def _role_observation(metadata):
    calls = _calls(metadata)
    unknown = dict.fromkeys(("invocation_ms", "first_attempt_ms", "output_retry_ms",
                            "citation_repair_ms", "decision_review_ms", "slot_wait_ms", "output_attempts",
                            "citation_repairs", "decision_reviews"))
    if calls is None:
        return {**unknown, "retry_kinds": {}}
    attempts = [attempt for call in calls for attempt in call]
    kinds = Counter(_fixed(attempt.get("retry_kind"), RETRY_KINDS)
                    for call in calls for attempt in call[:-1])
    wait = _slot_wait(metadata, calls)
    return {"invocation_ms": _sum_durations(attempts),
            "first_attempt_ms": _number(calls[0][0].get("duration_ms")),
            "output_retry_ms": _sum_durations([attempt for call in calls for attempt in call[1:]]),
            "citation_repair_ms": _sum_durations([attempt for call in calls[1:2] for attempt in call]),
            "decision_review_ms": _sum_durations([attempt for call in calls[2:] for attempt in call]),
            "slot_wait_ms": wait, "output_attempts": len(attempts),
            "citation_repairs": int(len(calls) > 1), "decision_reviews": int(len(calls) == 3), "retry_kinds": kinds}


def _output_outcome(attempt):
    code = _fixed(attempt.get("error_code"), OUTPUT_ERROR_CODES)
    reason = _fixed(attempt.get("provider_finish_reason"), PROVIDER_FINISH_REASONS)
    if code == "output_validation_failed":
        return "output_validation"
    if code in OUTPUT_ERROR_CODES[1:]:
        return ("output_limit" if reason in {"length", "max_tokens"} else
                "timeout" if reason == "timeout" else "incomplete_unspecified")
    # A finish_reason alone is insufficient if a missing/unknown error code
    # could have been discarded by the SQL allowlist.
    if (attempt.get("finish_reason") == "completed" and attempt.get("error_code") is None
            and attempt.get("error_code_present", False) is False):
        return "completed"
    return "unrecorded"


@dataclass
class _OutputFailures:
    states: Counter = field(default_factory=Counter)
    outcomes: Counter = field(default_factory=Counter)
    issues: Counter = field(default_factory=Counter)
    recorded_cases: int = 0
    unrecorded_cases: int = 0
    cases_with_output_failure: int = 0
    completed_cases_with_output_failure: int = 0
    validation_attempts_without_recorded_issues: int = 0

    def add(self, metadata, state):
        self.states[state] += 1
        calls = _calls(metadata)
        if calls is None:
            self.unrecorded_cases += 1
            return
        self.recorded_cases += 1
        observed_failure = False
        for attempt in (attempt for call in calls for attempt in call):
            outcome = _output_outcome(attempt)
            self.outcomes[outcome] += 1
            observed_failure |= outcome not in {"completed", "unrecorded"}
            if outcome != "output_validation":
                continue
            issues = attempt.get("validation_issues")
            if not isinstance(issues, list) or not 1 <= len(issues) <= 12:
                self.validation_attempts_without_recorded_issues += 1
                continue
            # Count each field/type once per attempt; repeated Pydantic paths
            # do not inflate the number of affected attempts.
            seen = set()
            for raw_issue in issues:
                issue = _mapping(raw_issue)
                path = issue.get("field")
                root = path.split(".", 1)[0] if isinstance(path, str) else None
                seen.add((_fixed(root, VALIDATION_FIELDS), _fixed(issue.get("type"), VALIDATION_TYPES)))
            self.issues.update(seen)
        if observed_failure:
            self.cases_with_output_failure += 1
            if state == "completed":
                self.completed_cases_with_output_failure += 1

    def present(self):
        return {"stage_states": dict(sorted(self.states.items())),
                "recorded_cases": self.recorded_cases, "unrecorded_cases": self.unrecorded_cases,
                "attempt_outcomes": dict(sorted(self.outcomes.items())),
                "cases_with_output_failure": self.cases_with_output_failure,
                "completed_cases_with_output_failure": self.completed_cases_with_output_failure,
                "validation_attempts_without_recorded_issues": self.validation_attempts_without_recorded_issues,
                "validation_issues": [{"field": field, "type": kind, "attempts": count}
                    for (field, kind), count in sorted(self.issues.items())]}


def summarize_execution_latency(records):
    """Summarize selected current test attempts, not every retry in their history.

    Records contain terminal analysis timestamps, latest-run steps and an
    optional display job. p50 is the median, p95 is nearest rank. Each metric
    reports its own missing coverage; phase and nested times are not additive.
    The caller must supply a bounded iterable from one consistent DB snapshot.
    """
    statuses, modes, phase_states, display_states = Counter(), Counter(), {}, Counter()
    timings = {name: _Metric() for name in (
        "decision_elapsed_ms", "analysis_queue_ms", "analysis_processing_ms", "result_ready_elapsed_ms")}
    phases = {name: _Metric() for name in PHASES}
    roles = {role: {name: _Metric() for name in (
        "invocation_ms", "first_attempt_ms", "output_retry_ms", "citation_repair_ms", "decision_review_ms", "slot_wait_ms")}
        for role in ROLES.values()}
    role_counts = {role: Counter() for role in ROLES.values()}
    retry_kinds = {role: Counter() for role in ROLES.values()}
    output_failures = {role: _OutputFailures() for role in ROLES.values()}
    display_metrics = {name: _Metric() for name in ("queue_ms", "processing_ms", "post_decision_elapsed_ms")}
    display_call_marks = Counter()
    cases = no_display_job = 0
    for raw in records:
        record = _mapping(raw)
        cases += 1
        status = _fixed(record.get("status"), ANALYSIS_STATUSES)
        statuses[status] += 1
        modes[_fixed(record.get("execution_mode"), ("stub", "moduagent"))] += 1
        end = record.get("completed_at") if status in _TERMINAL else None
        timings["decision_elapsed_ms"].add(_elapsed(record.get("created_at"), end))
        timings["analysis_queue_ms"].add(_elapsed(record.get("created_at"), record.get("started_at")))
        timings["analysis_processing_ms"].add(_elapsed(record.get("started_at"), end))
        job = record.get("display_job")
        ready_end = end if status == "completed" else None
        if job is None:
            no_display_job += 1
        else:
            job = _mapping(job)
            job_status = _fixed(job.get("status"), DISPLAY_STATUSES)
            display_states[job_status] += 1
            job_end = job.get("completed_at") if job_status in _TERMINAL else None
            # A display job can be created when the analysis is admitted, long
            # before its decision exists. Do not count Primary/Verifier time
            # again as display queue time.
            ready_start = (_time(end) if _time(end) is not None and _time(job.get("created_at")) is not None else None)
            if ready_start is not None:
                ready_start = max(ready_start, _time(job.get("created_at")))
            display_metrics["queue_ms"].add(_elapsed(ready_start, job.get("started_at")))
            job_metadata = _mapping(job.get("metadata"))
            processing = _number(job_metadata.get("duration_ms")) if job_end is not None else None
            display_metrics["processing_ms"].add(processing)
            display_metrics["post_decision_elapsed_ms"].add(_elapsed(ready_start, job_end))
            marker = job_metadata.get("model_called")
            display_call_marks[str(marker).lower() if type(marker) is bool else "unrecorded"] += 1
            if _time(job_end) is None or _time(ready_end) is None:
                ready_end = None
            else:
                ready_end = max(_time(ready_end), _time(job_end))
        timings["result_ready_elapsed_ms"].add(_elapsed(record.get("created_at"), ready_end))
        steps = record.get("steps")
        seen = set()
        for raw_step in steps if isinstance(steps, (list, tuple)) else ():
            step = _mapping(raw_step)
            name = step.get("step_type")
            if not isinstance(name, str) or name not in phases or name in seen:
                continue
            seen.add(name)
            state = _fixed(step.get("status"), STEP_STATUSES)
            phase_states.setdefault(name, Counter())[state] += 1
            metadata = _mapping(step.get("metadata"))
            valid_timing = (metadata.get("timing_measured") is True
                            and metadata.get("timing_incomplete", False) is False and state in _TERMINAL)
            phases[name].add(metadata.get("duration_ms") if valid_timing else None)
            if name not in ROLES:
                continue
            role = ROLES[name]
            output_failures[role].add(metadata, state)
            observation = _role_observation(metadata)
            for metric in roles[role]:
                roles[role][metric].add(observation[metric])
            if observation["output_attempts"] is None:
                role_counts[role]["unrecorded_cases"] += 1
            else:
                role_counts[role]["recorded_cases"] += 1
                role_counts[role]["reported_output_attempts"] += observation["output_attempts"]
                role_counts[role]["reported_output_retries"] += sum(observation["retry_kinds"].values())
                role_counts[role]["reported_citation_repairs"] += observation["citation_repairs"]
                role_counts[role]["reported_decision_reviews"] += observation["decision_reviews"]
                retry_kinds[role].update(observation["retry_kinds"])
    return {"version": VERSION, "cases": cases,
            "statuses": dict(sorted(statuses.items())), "execution_modes": dict(sorted(modes.items())),
            "timings": {name: metric.present() for name, metric in timings.items()},
            "phases": {name: {"entered_cases": sum(phase_states.get(name, {}).values()),
                              "not_observed_cases": cases - sum(phase_states.get(name, {}).values()),
                              "states": dict(sorted(phase_states.get(name, {}).items())),
                              "duration": metric.present()} for name, metric in phases.items()},
            "roles": {role: {"not_observed_cases": cases - sum(phase_states.get(phase, {}).values()),
                "accounting": {key: role_counts[role][key] for key in (
                "recorded_cases", "unrecorded_cases", "reported_output_attempts", "reported_output_retries",
                "reported_citation_repairs", "reported_decision_reviews")}, "retry_kinds": dict(sorted(retry_kinds[role].items())),
                "output_failures": output_failures[role].present(),
                **{name: metric.present() for name, metric in roles[role].items()}} for phase, role in ROLES.items()},
            "display": {"no_job_cases": no_display_job, "states": dict(sorted(display_states.items())),
                        "dispatch_markers": dict(sorted(display_call_marks.items())),
                        **{name: metric.present() for name, metric in display_metrics.items()}},
            "notes": ["model_attempt_includes_transport_framework_validation",
                      "nested_timings_overlap_do_not_add", "missing_is_not_zero",
                      "stage_not_observed_does_not_prove_skipped",
                      "slot_wait_unknown_without_all_citation_calls", "dispatch_marker_is_not_http_count",
                      "display_processing_includes_setup_and_slot_wait",
                      "result_ready_elapsed_is_not_client_network_latency",
                      "output_failure_attempts_include_successfully_recovered_cases",
                      "validation_issue_counts_are_per_attempt_and_not_additive"]}
