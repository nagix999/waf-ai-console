"""Read-only Test performance aggregates from allowlisted SQL telemetry.

The scope matches Test statistics before grouping or paging. Source events,
encrypted traces, model text and full metadata documents never leave SQL.
"""
import json
from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil
from statistics import median

from sqlalchemy import and_, case, func, literal_column, or_, select, text

from ..models import AgentRun, AgentStep, Analysis, EvidenceDisplayJob, TestRunItem
from . import execution_latency as latency
from .test_case_filters import case_condition
from .test_runs import run_cohort, test_read_context

VERSION = "test-performance-summary-v1"
_TOKEN_LIMIT = 2**31 - 1


def _enum(column, values):
    allowed = ",".join("'" + value + "'" for value in values)
    return f"CASE WHEN {column} IN ({allowed}) THEN {column} END"


def _document(column):
    return f"CASE WHEN json_valid({column}) THEN {column} ELSE '{{}}' END"


def _number(document, path, maximum=latency._MAX_MS):
    return (f"CASE WHEN json_type({document},'{path}')='integer' "
            f"AND json_extract({document},'{path}') BETWEEN 0 AND {maximum} "
            f"THEN json_extract({document},'{path}') END")


def _boolean(document, path):
    return f"CASE json_type({document},'{path}') WHEN 'true' THEN 1 WHEN 'false' THEN 0 END"


def _bounded_count(document, path, maximum):
    return (f"CASE WHEN json_type({document},'{path}.attempts')='array' "
            f"AND json_array_length({document},'{path}.attempts')<={maximum} "
            f"THEN {_number(document, path + '.attempt_count', maximum)} END")


def _array(document, path, maximum=4):
    return (f"CASE WHEN json_type({document},'{path}')='array' "
            f"AND json_array_length({document},'{path}')<={maximum} "
            f"THEN json_extract({document},'{path}') ELSE '[]' END")


def _grounding_limit(document):
    return (f"CASE WHEN json_type({document},'$.evidence_grounding_retry.decision_review_attempted')='true' "
            f"AND json_extract({document},'$.evidence_grounding_retry.attempts[2].kind')='decision_review' "
            "THEN 3 ELSE 2 END")


def _validation_issues(document):
    item = "CASE WHEN diagnostic_issue.type='object' THEN diagnostic_issue.value ELSE '{}' END"
    path = f"CASE WHEN json_type({item},'$.field')='text' THEN json_extract({item},'$.field') END"
    root = f"CASE WHEN instr({path},'.')>0 THEN substr({path},1,instr({path},'.')-1) ELSE {path} END"
    field_name = _enum(root, latency.VALIDATION_FIELDS)
    issue_type = _enum(f"json_extract({item},'$.type')", latency.VALIDATION_TYPES)
    return (f"CASE WHEN json_type({document},'$.validation_issues')='array' "
            f"AND json_array_length({document},'$.validation_issues')<=12 THEN "
            f"(SELECT json_group_array(json_object('field',{field_name},'type',{issue_type})) "
            f"FROM json_each(json_extract({document},'$.validation_issues')) diagnostic_issue) END")


def _attempt_values(document, *, valid_object):
    return ",".join([
        f"{valid_object} AS valid_object",
        _number(document, "$.duration_ms") + " AS duration_ms",
        _enum(f"json_extract({document},'$.retry_kind')", latency.RETRY_KINDS) + " AS retry_kind",
        _enum(f"json_extract({document},'$.error_code')", latency.OUTPUT_ERROR_CODES) + " AS error_code",
        f"CASE WHEN json_type({document},'$.error_code') IS NOT NULL "
        f"AND json_type({document},'$.error_code')!='null' THEN 1 ELSE 0 END AS error_code_present",
        _enum(f"json_extract({document},'$.provider_finish_reason')", latency.PROVIDER_FINISH_REASONS) + " AS provider_finish_reason",
        _enum(f"json_extract({document},'$.finish_reason')", latency.ATTEMPT_FINISH_REASONS) + " AS finish_reason",
        _validation_issues(document) + " AS validation_issues",
        _number(document, "$.usage.input_tokens", _TOKEN_LIMIT) + " AS input_tokens",
        _number(document, "$.usage.output_tokens", _TOKEN_LIMIT) + " AS output_tokens",
    ])


def _attempt_record(row):
    if not row["valid_object"]:
        return None
    return {key: row[key] for key in (
        "duration_ms", "retry_kind", "error_code", "error_code_present", "provider_finish_reason", "finish_reason")} | {
        "error_code_present": bool(row["error_code_present"]),
        "validation_issues": json.loads(row["validation_issues"]) if row["validation_issues"] else None,
        "usage": {"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"]}}


def _load_steps(db, cases):
    ranked_runs = select(AgentRun.id, AgentRun.analysis_id, AgentRun.status,
        func.row_number().over(partition_by=AgentRun.analysis_id,
            order_by=(AgentRun.created_at.desc(), AgentRun.id.desc())).label("position")).join(
                cases, cases.c.analysis_id == AgentRun.analysis_id).subquery()
    # Pending work has no current execution. During processing an older,
    # abandoned run must not appear to be this attempt's completed work.
    latest_runs = select(ranked_runs.c.id, ranked_runs.c.analysis_id).join(
        cases, cases.c.analysis_id == ranked_runs.c.analysis_id).where(ranked_runs.c.position == 1,
        or_(cases.c.status.not_in(("pending", "processing")),
            and_(cases.c.status == "processing", ranked_runs.c.status == "running"))).cte(
                "performance_latest_runs").prefix_with("MATERIALIZED", dialect="sqlite")
    ranked_steps = select(AgentStep.id, AgentStep.run_id, AgentStep.step_type,
        func.row_number().over(partition_by=(AgentStep.run_id, AgentStep.step_type),
            order_by=(AgentStep.sequence.desc(), AgentStep.started_at.desc(), AgentStep.id.desc())).label("position")).join(
                latest_runs, latest_runs.c.id == AgentStep.run_id).where(AgentStep.step_type.in_(latency.PHASES)).subquery()
    chosen = select(AgentStep.id, latest_runs.c.analysis_id, AgentStep.step_type, AgentStep.status,
        AgentStep.metadata_json).join(ranked_steps, ranked_steps.c.id == AgentStep.id).join(
            latest_runs, latest_runs.c.id == AgentStep.run_id).where(ranked_steps.c.position == 1).cte(
                "performance_steps").prefix_with("MATERIALIZED", dialect="sqlite")
    # The CTE metadata column is only used inside SQL JSON projections. The
    # SELECT crossing the application boundary contains small safe scalars.
    meta = _document("s.metadata_json")
    grounding_limit = _grounding_limit(meta)
    columns = ["s.id", "s.analysis_id", _enum("s.step_type", latency.PHASES) + " AS step_type",
        _enum("s.status", latency.STEP_STATUSES) + " AS status",
        f"CASE WHEN json_type({meta},'$.evidence_grounding_retry') IS NOT NULL THEN 1 ELSE 0 END AS grounding_present",
        _number(meta, "$.duration_ms") + " AS duration_ms", _number(meta, "$.concurrency.wait_ms") + " AS wait_ms",
        _bounded_count(meta, "$.output_validation_retry", 4) + " AS validation_count",
        _bounded_count(meta, "$.evidence_grounding_retry", grounding_limit) + " AS grounding_count",
        _boolean(meta, "$.evidence_grounding_retry.decision_review_attempted") + " AS decision_review_attempted",
        _boolean(meta, "$.timing_measured") + " AS timing_measured",
        f"CASE WHEN json_type({meta},'$.timing_incomplete') IS NULL "
        f"OR json_type({meta},'$.timing_incomplete')='false' THEN 0 ELSE 1 END AS timing_incomplete"]
    # SQL fragments contain only server constants. SQLAlchemy binds every
    # value from the case/filter CTE, including wildcard characters.
    execute = lambda sql: db.execute(text(sql).columns().add_cte(chosen)).mappings()
    steps, by_analysis = {}, defaultdict(list)
    for row in execute("SELECT " + ",".join(columns) + " FROM performance_steps s"):
        metadata = {"timing_measured": row["timing_measured"] == 1,
            "timing_incomplete": row["timing_incomplete"] == 1, "duration_ms": row["duration_ms"],
            "concurrency": {"wait_ms": row["wait_ms"]},
            "output_validation_retry": {"attempt_count": row["validation_count"], "attempts": []}}
        if row["grounding_present"]:
            metadata["evidence_grounding_retry"] = {"attempt_count": row["grounding_count"], "attempts": [],
                "decision_review_attempted": row["decision_review_attempted"] == 1}
        value = {"step_type": row["step_type"], "status": row["status"], "metadata": metadata}
        steps[row["id"]] = value
        by_analysis[row["analysis_id"]].append(value)
    item = "CASE WHEN a.type='object' THEN a.value ELSE '{}' END"
    attempt_columns = _attempt_values(item, valid_object="a.type='object'")
    top = _array(meta, "$.output_validation_retry.attempts")
    for row in execute(f"SELECT s.id,a.key AS idx,{attempt_columns} "
                       f"FROM performance_steps s,json_each({top}) a WHERE CAST(a.key AS INTEGER)<4 "
                       "ORDER BY s.id,CAST(a.key AS INTEGER)"):
        steps[row["id"]]["metadata"]["output_validation_retry"]["attempts"].append(_attempt_record(row))
    ground_array = _array(meta, "$.evidence_grounding_retry.attempts", grounding_limit)
    ground = "CASE WHEN g.type='object' THEN g.value ELSE '{}' END"
    columns = ["s.id", "g.key AS idx", _bounded_count(ground, "$.output_validation_retry", 4) + " AS validation_count",
        _enum(f"json_extract({ground},'$.kind')", ("decision_review",)) + " AS kind",
        _number(ground, "$.concurrency.wait_ms") + " AS wait_ms",
        f"CASE WHEN json_type({ground},'$.concurrency') IS NOT NULL THEN 1 ELSE 0 END AS concurrency_present"]
    grounding = {}
    for row in execute("SELECT " + ",".join(columns) + f" FROM performance_steps s,json_each({ground_array}) g "
                       "WHERE CAST(g.key AS INTEGER)<3 ORDER BY s.id,CAST(g.key AS INTEGER)"):
        value = {"output_validation_retry": {"attempt_count": row["validation_count"], "attempts": []}, "kind": row["kind"]}
        if row["concurrency_present"]:
            value["concurrency"] = {"wait_ms": row["wait_ms"]}
        steps[row["id"]]["metadata"]["evidence_grounding_retry"]["attempts"].append(value)
        grounding[row["id"], row["idx"]] = value
    nested = _array(ground, "$.output_validation_retry.attempts")
    for row in execute(f"SELECT s.id,g.key AS ground_idx,a.key AS idx,{attempt_columns} "
                       f"FROM performance_steps s,json_each({ground_array}) g,json_each({nested}) a "
                       "WHERE CAST(g.key AS INTEGER)<3 AND CAST(a.key AS INTEGER)<4 "
                       "ORDER BY s.id,CAST(g.key AS INTEGER),CAST(a.key AS INTEGER)"):
        grounding[row["id"], row["ground_idx"]]["output_validation_retry"]["attempts"].append(_attempt_record(row))
    return by_analysis


def _load_displays(db, cases):
    meta = _document("j.metadata_json")
    ranked = select(EvidenceDisplayJob.id, EvidenceDisplayJob.analysis_id,
        func.row_number().over(partition_by=EvidenceDisplayJob.analysis_id,
            order_by=(EvidenceDisplayJob.created_at.desc(), EvidenceDisplayJob.id.desc())).label("position")).join(
                cases, cases.c.analysis_id == EvidenceDisplayJob.analysis_id).subquery()
    # Keep typed timestamps while selecting only the two allowed metadata bits.
    metadata = select(EvidenceDisplayJob.id, EvidenceDisplayJob.analysis_id, EvidenceDisplayJob.status,
        EvidenceDisplayJob.created_at, EvidenceDisplayJob.started_at, EvidenceDisplayJob.completed_at,
        EvidenceDisplayJob.metadata_json).join(ranked, ranked.c.id == EvidenceDisplayJob.id).where(ranked.c.position == 1).subquery("j")
    query = select(metadata.c.analysis_id, literal_column(_enum("j.status", latency.DISPLAY_STATUSES)).label("status"),
        metadata.c.created_at, metadata.c.started_at,
        metadata.c.completed_at, literal_column(_number(meta, "$.duration_ms")).label("duration_ms"),
        literal_column(_boolean(meta, "$.model_called")).label("model_called"))
    return {row["analysis_id"]: {key: row[key] for key in ("status", "created_at", "started_at", "completed_at")} | {
        "metadata": {"duration_ms": row["duration_ms"],
            "model_called": None if row["model_called"] is None else bool(row["model_called"])}}
        for row in db.execute(query).mappings()}


@dataclass
class _TokenMetric:
    values: list[int] = field(default_factory=list)
    unrecorded: int = 0

    def add(self, value):
        if type(value) is not int or not 0 <= value <= _TOKEN_LIMIT:
            self.unrecorded += 1
        else:
            self.values.append(value)

    def present(self):
        values = sorted(self.values)
        return {"recorded": len(values), "unrecorded": self.unrecorded, "sum": sum(values),
            "mean": sum(values) / len(values) if values else None,
            "p50": median(values) if values else None,
            "p95": values[ceil(.95 * len(values)) - 1] if values else None,
            "max": values[-1] if values else None}


def summarize_performance(records):
    completed_records = []
    tokens = {role: {name: _TokenMetric() for name in (
        "input_tokens", "output_tokens", "first_attempt_output_tokens")} for role in latency.ROLES.values()}
    retries = {role: {"cases_with_output_retries": 0, "cases_with_citation_repairs": 0,
        "cases_with_decision_reviews": 0} for role in latency.ROLES.values()}
    def observe():
        for record in records:
            if record.get("status") == "completed":
                completed_records.append(record)
            seen = set()
            for step in record.get("steps", ()):
                phase = step.get("step_type")
                if phase not in latency.ROLES or phase in seen:
                    continue
                seen.add(phase)
                role = latency.ROLES[phase]
                calls = latency._calls(step.get("metadata", {}))
                values = dict.fromkeys(tokens[role])
                if calls is not None:
                    retries[role]["cases_with_output_retries"] += any(len(call) > 1 for call in calls)
                    retries[role]["cases_with_citation_repairs"] += len(calls) > 1
                    retries[role]["cases_with_decision_reviews"] += len(calls) == 3
                    for name in ("input_tokens", "output_tokens"):
                        observed = [latency._mapping(attempt.get("usage")).get(name) for call in calls for attempt in call]
                        if all(type(value) is int and 0 <= value <= _TOKEN_LIMIT for value in observed):
                            values[name] = sum(observed)
                    values["first_attempt_output_tokens"] = latency._mapping(calls[0][0].get("usage")).get("output_tokens")
                for name, metric in tokens[role].items():
                    metric.add(values[name])
            yield record
    report = latency.summarize_execution_latency(observe())
    completed_report = latency.summarize_execution_latency(completed_records)
    report["completed_cases"] = len(completed_records)
    report["completed_timings"] = completed_report["timings"]
    for role, metrics in tokens.items():
        report["roles"][role]["tokens"] = {name: metric.present() for name, metric in metrics.items()}
        report["roles"][role]["accounting"].update(retries[role])
    def mean(value):
        if isinstance(value, dict):
            if "reported_sum_ms" in value and "recorded" in value:
                value["mean_ms"] = value["reported_sum_ms"] / value["recorded"] if value["recorded"] else None
            for child in value.values():
                mean(child)
    mean(report)
    report["version"] = VERSION
    report["notes"].extend(("token_totals_include_output_retries_and_citation_repairs",
        "token_totals_include_decision_review", "decision_review_is_not_citation_repair",
        "first_attempt_tokens_exclude_retries", "token_observation_is_not_generation_time"))
    return report


def performance_summary(db, run, *, evaluation_id=None, reference_basis="latest", difficulty=None,
                        test_category=None, difficulty_missing=False, test_category_missing=False, case_filter=None):
    cohort = run_cohort(run.id, difficulty, test_category, difficulty_missing, test_category_missing)
    evaluation, _nodes, current, relation = test_read_context(db, run, evaluation_id=evaluation_id,
        reference_basis=reference_basis, cohort=cohort)
    query = select(Analysis.id.label("analysis_id"),
        case((Analysis.status.in_(latency.ANALYSIS_STATUSES), Analysis.status), else_=None).label("status"),
        Analysis.created_at, Analysis.started_at,
        Analysis.completed_at).select_from(TestRunItem).join(current, current.c.item_id == TestRunItem.id).join(
            Analysis, Analysis.id == current.c.analysis_id).where(
                *cohort, TestRunItem.ingest_status == "accepted")
    condition = case_condition(case_filter, relation)
    if condition is not None:
        query = query.outerjoin(relation, relation.c.analysis_id == Analysis.id).where(condition)
    cases = query.cte("performance_cases").prefix_with("MATERIALIZED", dialect="sqlite")
    rows = db.execute(select(cases)).mappings().all()
    steps = _load_steps(db, cases)
    displays = _load_displays(db, cases)
    report = summarize_performance(({key: row[key] for key in ("status", "created_at", "started_at", "completed_at")} | {
        "execution_mode": run.execution_mode, "steps": steps.get(row["analysis_id"], []),
        "display_job": displays.get(row["analysis_id"])} for row in rows))
    return {**report, "test_run_id": run.id, "case_filter_applied": condition is not None,
        "reference_basis": "saved" if evaluation else "ground_truth" if run.evaluation_mode == "ground_truth" else reference_basis,
        "evaluation_id": evaluation.id if evaluation else None}
