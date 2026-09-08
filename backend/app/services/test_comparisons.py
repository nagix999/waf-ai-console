"""Paired final-result comparison without reading/decrypting event or step text.

Event fingerprints identify normalized admission data, not byte-identical LLM
inputs: per-run source identifiers and prompt-dependent input budgets can differ.
"""
from collections import Counter
from math import ceil

from sqlalchemy import case, func, literal, select

from ..models import Analysis, AgentRun, AgentStep, InputSchemaVersion, TestRunItem
from ..test_comparison_schemas import (
    ComparisonCounts, ComparisonItem, ComparisonPerformance, ComparisonPerformanceSide,
    ComparisonTiming, ComparisonTokenCounter, ComparisonTokens, TestComparisonResponse,
)
from .evaluation import COMPARABLE, MATCHES, summarize_evaluations
from .test_runs import describe_run, fixed_reference_relation
from .timing import elapsed_ms

VERDICTS = {"true_positive", "false_positive", "inconclusive"}
TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens")
PROFILE_KEYS = {"llm_provider", "model_profile", "model_profile_id", "model_name",
                "profile_fingerprint", "verifier_confidence_threshold", "llm_called"}


def _object(value):
    return value if isinstance(value, dict) else {}


def _cohort(db, run, warnings):
    relation = fixed_reference_relation(run.id)
    result = case((func.json_valid(Analysis.result_json) == 1, Analysis.result_json), else_=literal("{}"))
    value = lambda path: func.json_extract(result, path)
    query = select(
        TestRunItem.event_id, TestRunItem.row_number, TestRunItem.case_name,
        TestRunItem.difficulty, TestRunItem.test_category, TestRunItem.analysis_id,
        TestRunItem.label_id.label("fixed_label_id"),
        Analysis.id.label("present_analysis_id"), Analysis.event_id.label("analysis_event_id"),
        Analysis.event_fingerprint, Analysis.verdict, Analysis.started_at, Analysis.completed_at,
        Analysis.input_schema_version_id, Analysis.input_truncated,
        value("$.agent.model_profile_id").label("actual_profile_id"),
        value("$.agent.profile_fingerprint").label("actual_profile_fingerprint"),
        value("$.policy.fixed_rules_hash").label("fixed_rules_hash"),
        value("$.policy.verifier_confidence_threshold").label("actual_threshold"),
        value("$.verifier.executed").label("verifier_executed"),
        relation.c.outcome, relation.c.reference_verdict, relation.c.source_kind,
        relation.c.source_ref, relation.c.ai_visible, relation.c.label_id,
    ).select_from(TestRunItem).outerjoin(Analysis, Analysis.id == TestRunItem.analysis_id).outerjoin(
        relation, relation.c.analysis_id == Analysis.id).where(
        TestRunItem.test_run_id == run.id, TestRunItem.ingest_status == "accepted",
    ).order_by(TestRunItem.row_number, TestRunItem.id)
    indexed = {}
    for found in db.execute(query).mappings():
        row = dict(found)
        identifier = row["event_id"] or f"missing-event-id:{row['row_number']}"
        if identifier in indexed:
            warnings.add("duplicate_event_rows_ignored")
            continue
        indexed[identifier] = row
    return indexed, relation


def _pair_status(left, right):
    if left is None:
        return "baseline_missing"
    if right is None:
        return "candidate_missing"
    if not left["present_analysis_id"] or not right["present_analysis_id"]:
        return "analysis_missing"
    if any(row["event_id"] != row["analysis_event_id"] for row in (left, right)):
        return "item_identity_mismatch"
    if not left["event_fingerprint"] or not right["event_fingerprint"]:
        return "fingerprint_missing"
    if left["event_fingerprint"] != right["event_fingerprint"]:
        return "input_mismatch"
    if any(not row["label_id"] or row["label_id"] != row["fixed_label_id"]
           or row["reference_verdict"] not in VERDICTS for row in (left, right)):
        return "reference_missing"
    if any(left[field] != right[field] for field in ("reference_verdict", "source_kind", "ai_visible")):
        return "reference_mismatch"
    if left["outcome"] not in COMPARABLE and right["outcome"] not in COMPARABLE:
        return "both_not_evaluable"
    if left["outcome"] not in COMPARABLE:
        return "baseline_not_evaluable"
    if right["outcome"] not in COMPARABLE:
        return "candidate_not_evaluable"
    return "comparable"


def _change(left, right):
    if left["verdict"] == right["verdict"]:
        return "unchanged"
    before, after = left["outcome"] in MATCHES, right["outcome"] in MATCHES
    if not before and after:
        return "improved"
    if before and not after:
        return "regressed"
    # A binary-reference wrong decision -> hold is not a corrected answer.
    return "changed"


def _timing(values):
    measured = sorted(value for value in values if type(value) is int and value >= 0)
    count = len(measured)
    return ComparisonTiming(count=count, missing_count=len(values) - count,
        sum_ms=sum(measured), mean_ms=sum(measured) / count if count else None,
        p50_ms=measured[ceil(count * .5) - 1] if count else None,
        p95_ms=measured[ceil(count * .95) - 1] if count else None)


def _step_usage(metadata):
    retry = _object(metadata.get("output_validation_retry"))
    attempts = retry.get("attempts")
    if isinstance(attempts, list) and attempts:
        counters = [_object(attempt).get("usage") for attempt in attempts]
        expected = retry.get("attempt_count")
        if type(expected) is int and expected > len(attempts):
            counters.append(None)
    elif retry.get("attempted") is True or (type(retry.get("attempt_count")) is int and retry["attempt_count"] > 1):
        # Top-level usage is only the last repair attempt, not the whole step.
        counters = [None]
    else:
        counters = [metadata.get("usage")]
    result = {}
    for key in TOKEN_KEYS:
        values = [_object(counter).get(key) for counter in counters]
        # Historical all-zero provider counters represent unmeasured usage.
        valid = all(type(value) is int and value >= 0 for value in values)
        all_zero = any(isinstance(counter, dict) and all(counter.get(name) == 0 for name in TOKEN_KEYS)
                       for counter in counters)
        result[key] = sum(values) if valid and not all_zero else None
    return result


def _performance(db, rows, warnings):
    identifiers = {row["analysis_id"] for row in rows}
    processing = [elapsed_ms(row["started_at"], row["completed_at"]) for row in rows]
    seen = set()
    durations, usages = [], []
    repair_count = 0
    if identifiers:
        query = select(AgentRun.analysis_id, AgentStep.id, AgentStep.step_type, AgentStep.status, AgentStep.metadata_json).join(
            AgentStep, AgentStep.run_id == AgentRun.id).where(
            AgentRun.analysis_id.in_(identifiers), AgentStep.step_type.in_(("llm_primary", "llm_verifier")))
        for row in db.execute(query).mappings():
            seen.add((row["analysis_id"], row["step_type"]))
            metadata = _object(row["metadata_json"])
            duration = metadata.get("duration_ms")
            measured = metadata.get("timing_measured") is True and not metadata.get("timing_incomplete")
            durations.append(duration if measured and row["status"] in {"completed", "failed"} else None)
            usages.append(_step_usage(metadata))
            retry = _object(metadata.get("output_validation_retry"))
            repair_count += retry.get("attempted") is True
    counters = {}
    for key in TOKEN_KEYS:
        measured = [usage[key] for usage in usages if usage[key] is not None]
        counters[key] = ComparisonTokenCounter(known_sum=sum(measured), measured_steps=len(measured),
                                               missing_steps=len(usages) - len(measured))
    missing_histories = sum(
        (row["analysis_id"], "llm_primary") not in seen
        or (row["verifier_executed"] == 1 and (row["analysis_id"], "llm_verifier") not in seen)
        for row in rows
    )
    if missing_histories or any(counter.missing_steps for counter in counters.values()):
        warnings.add("token_usage_incomplete")
    result = ComparisonPerformanceSide(processing_ms=_timing(processing), llm_step_ms=_timing(durations),
        tokens=ComparisonTokens(**counters), llm_steps=len(usages), missing_agent_histories=missing_histories,
        output_repair_steps=repair_count)
    if result.processing_ms.missing_count or result.llm_step_ms.missing_count:
        warnings.add("timing_incomplete")
    return result


def _warn_difference(warnings, name, left, right):
    if left is None or right is None:
        warnings.add(name + "_unknown")
    elif left != right:
        warnings.add(name + "_different")


def _configuration_warnings(db, baseline, candidate, paired, warnings):
    _warn_difference(warnings, "model_configuration", baseline.profile_fingerprint, candidate.profile_fingerprint)
    _warn_difference(warnings, "execution_mode", baseline.execution_mode, candidate.execution_mode)
    _warn_difference(warnings, "verifier_threshold",
        _object(baseline.profile_metadata).get("verifier_confidence_threshold"),
        _object(candidate.profile_metadata).get("verifier_confidence_threshold"))
    schema_ids = {run.input_schema_version_id for run in (baseline, candidate) if run.input_schema_version_id}
    schema_hashes = dict(db.execute(select(InputSchemaVersion.id, InputSchemaVersion.content_hash).where(
        InputSchemaVersion.id.in_(schema_ids))).all()) if schema_ids else {}
    _warn_difference(warnings, "input_schema", schema_hashes.get(baseline.input_schema_version_id),
                     schema_hashes.get(candidate.input_schema_version_id))
    if not paired:
        warnings.add("fixed_rules_unknown")
    for left, right in paired:
        _warn_difference(warnings, "fixed_rules", left["fixed_rules_hash"], right["fixed_rules_hash"])
        if left["input_truncated"] or right["input_truncated"]:
            warnings.add("input_truncation_present")
        if left["input_truncated"] != right["input_truncated"]:
            warnings.add("input_truncation_different")
        for row, run in ((left, baseline), (right, candidate)):
            if row["actual_profile_id"] is None or row["actual_profile_fingerprint"] is None:
                warnings.add("execution_identity_unknown")
            elif row["actual_profile_id"] != run.profile_id or row["actual_profile_fingerprint"] != run.profile_fingerprint:
                warnings.add("execution_identity_mismatch")
            if row["actual_threshold"] is None:
                warnings.add("executed_verifier_threshold_unknown")
            elif row["actual_threshold"] != _object(run.profile_metadata).get("verifier_confidence_threshold"):
                warnings.add("executed_verifier_threshold_mismatch")


def _run_summary(db, run):
    summary = describe_run(db, run, detail=False)
    # Do not expand the safe comparison response with arbitrary stored metadata.
    return summary.model_copy(update={"profile_metadata": {
        key: value for key, value in _object(summary.profile_metadata).items()
        if key in PROFILE_KEYS and (value is None or type(value) in (str, int, float, bool))
    }})


def compare_test_runs(db, baseline, candidate, *, limit=25, offset=0, changes_only=False):
    warnings = {"run_source_system_differs", "prompt_budget_may_change_submitted_input",
                "comparison_is_not_causal_proof", "performance_excludes_noncomparable_analyses",
                "recorded_tokens_are_not_billing_total"}
    left_rows, left_relation = _cohort(db, baseline, warnings)
    right_rows, right_relation = _cohort(db, candidate, warnings)
    items, paired = [], []
    exclusions = Counter()
    accepted_pairs = 0
    for identifier in dict.fromkeys([*left_rows, *right_rows]):
        left, right = left_rows.get(identifier), right_rows.get(identifier)
        accepted_pairs += left is not None and right is not None
        status = _pair_status(left, right)
        change = "not_comparable"
        if status == "comparable":
            paired.append((left, right))
            change = _change(left, right)
            if left["source_ref"] != right["source_ref"]:
                warnings.add("reference_source_ref_different")
            if any(left[field] != right[field] for field in ("difficulty", "test_category", "case_name")):
                warnings.add("case_metadata_different")
        else:
            exclusions[status] += 1
        context = left or right
        left, right = left or {}, right or {}
        reference = left.get("reference_verdict") if left.get("reference_verdict") == right.get("reference_verdict") else None
        items.append(ComparisonItem(event_id=identifier, case_name=context["case_name"],
            difficulty=context["difficulty"], test_category=context["test_category"],
            baseline_analysis_id=left.get("analysis_id"), candidate_analysis_id=right.get("analysis_id"),
            baseline_verdict=left.get("verdict") if left.get("verdict") in VERDICTS else None,
            candidate_verdict=right.get("verdict") if right.get("verdict") in VERDICTS else None,
            baseline_outcome=left.get("outcome"), candidate_outcome=right.get("outcome"),
            reference_verdict=reference if reference in VERDICTS else None,
            comparison_status=status, change=change))
    _configuration_warnings(db, baseline, candidate, paired, warnings)
    left_ids = {left["analysis_id"] for left, _ in paired}
    right_ids = {right["analysis_id"] for _, right in paired}
    changes = Counter(item.change for item in items if item.comparison_status == "comparable")
    performance = ComparisonPerformance(
        baseline=_performance(db, [left for left, _ in paired], warnings),
        candidate=_performance(db, [right for _, right in paired], warnings))
    filtered = [item for item in items if item.change in {"improved", "regressed", "changed"}] if changes_only else items
    return TestComparisonResponse(baseline=_run_summary(db, baseline), candidate=_run_summary(db, candidate),
        baseline_evaluation=summarize_evaluations(db, left_relation, [Analysis.id.in_(left_ids)]),
        candidate_evaluation=summarize_evaluations(db, right_relation, [Analysis.id.in_(right_ids)]),
        counts=ComparisonCounts(accepted_pairs=accepted_pairs, comparable_pairs=len(paired),
            changed=changes["changed"] + changes["improved"] + changes["regressed"],
            improved=changes["improved"], regressed=changes["regressed"], exclusions=dict(exclusions)),
        warnings=sorted(warnings), items=filtered[offset:offset + limit], total_items=len(filtered),
        limit=limit, offset=offset, changes_only=changes_only, performance=performance)
