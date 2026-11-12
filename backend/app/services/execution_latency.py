"""Count-only latency diagnostics, outside the public analysis API contract.

These observations are not GPU generation time or HTTP request counts. A model
attempt includes transport, framework and validation work. Retry times overlap
the enclosing step, and output retries can overlap citation-repair time; never
sum their percentiles or add them to the enclosing duration.

Only fixed names, bounded numeric values and timestamps are consumed. Missing
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
                "max_ms": values[-1] if values else None}


def _calls(metadata):
    """Prefer nested calls; the top-level retry repeats the LAST nested call."""
    if "evidence_grounding_retry" in metadata:
        grounding = _mapping(metadata["evidence_grounding_retry"])
        calls = grounding.get("attempts")
        count = grounding.get("attempt_count")
        if (type(count) is not int or not 1 <= count <= 2
                or not isinstance(calls, list) or len(calls) != count):
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


def _role_observation(metadata):
    calls = _calls(metadata)
    unknown = dict.fromkeys(("invocation_ms", "first_attempt_ms", "output_retry_ms",
                            "citation_repair_ms", "slot_wait_ms", "output_attempts", "citation_repairs"))
    if calls is None:
        return {**unknown, "retry_kinds": {}}
    attempts = [attempt for call in calls for attempt in call]
    kinds = Counter(_fixed(attempt.get("retry_kind"), RETRY_KINDS)
                    for call in calls for attempt in call[:-1])
    # On citation repair the worker retains only the last call's slot wait.
    # It cannot be presented as the whole role's wait or subtracted from it.
    wait = (_number(_mapping(metadata.get("concurrency")).get("wait_ms"))
            if len(calls) == 1 else None)
    return {"invocation_ms": _sum_durations(attempts),
            "first_attempt_ms": _number(calls[0][0].get("duration_ms")),
            "output_retry_ms": _sum_durations([attempt for call in calls for attempt in call[1:]]),
            "citation_repair_ms": _sum_durations([attempt for call in calls[1:] for attempt in call]),
            "slot_wait_ms": wait, "output_attempts": len(attempts),
            "citation_repairs": len(calls) - 1, "retry_kinds": kinds}


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
        "invocation_ms", "first_attempt_ms", "output_retry_ms", "citation_repair_ms", "slot_wait_ms")}
        for role in ROLES.values()}
    role_counts = {role: Counter() for role in ROLES.values()}
    retry_kinds = {role: Counter() for role in ROLES.values()}
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
                "reported_citation_repairs")}, "retry_kinds": dict(sorted(retry_kinds[role].items())),
                **{name: metric.present() for name, metric in roles[role].items()}} for phase, role in ROLES.items()},
            "display": {"no_job_cases": no_display_job, "states": dict(sorted(display_states.items())),
                        "dispatch_markers": dict(sorted(display_call_marks.items())),
                        **{name: metric.present() for name, metric in display_metrics.items()}},
            "notes": ["model_attempt_includes_transport_framework_validation",
                      "nested_timings_overlap_do_not_add", "missing_is_not_zero",
                      "stage_not_observed_does_not_prove_skipped",
                      "slot_wait_unrecorded_across_citation_calls", "dispatch_marker_is_not_http_count",
                      "display_processing_includes_setup_and_slot_wait",
                      "result_ready_elapsed_is_not_client_network_latency"]}
