"""Scoped execution observations from small SQL scalars, never raw events/traces.

An entered Verifier stage is not proof of an HTTP request. Old missing metadata
is not reconstructed by decrypting stage output or by interpreting the payload.
"""
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import aliased

from ..models import AgentRun, AgentStep, Analysis


VERSION = "test-execution-summary-v1"
PARSER_WARNING_CODES = (
    "request_protocol_not_recognized", "request_target_not_recognized", "headers_terminator_missing",
    "request_line_not_recognized", "escaped_line_endings_not_decoded", "header_line_not_recognized",
    "capture_inspection_limit", "capture_line_endings_ambiguous",
    "capture_serialization_unverified",
)
VERIFIER_REASON_CODES = (
    "primary_inconclusive", "low_confidence", "signature_not_exact", "true_positive_but_allowed",
    "false_positive_but_denied", "parser_incomplete", "input_truncated", "tuning_recommended",
    "conflicting_evidence", "interpretation_conflict", "evidence_grounding_failed", "input_integrity_requires_review",
)
_RESULT_VERSIONS = ("waf-analysis-v1", "waf-analysis-v2", "waf-analysis-v3")
_ARRAY_LIMIT = 32


def fixed_codes(values, allowed):
    """Only fixed diagnostics may cross into plaintext execution metadata."""
    if not isinstance(values, (list, tuple)):
        return ["other"]
    observed = {value if isinstance(value, str) and value in allowed else "other" for value in values}
    return [code for code in (*allowed, "other") if code in observed]


def _document(column):
    return case((func.json_valid(column) == 1, column), else_="{}")


def _boolean(document, path):
    kind = func.json_type(document, path)
    return case((kind == "true", 1), (kind == "false", 0), else_=None)


def _array_observation(document, path, codes):
    """Read a bounded array once into fixed-code bits, not once per reason.

    SUM(DISTINCT bit) deduplicates each code, including all unknown values into
    one `other` bit. The intermediate and returned values are small integers.
    """
    kind = func.json_type(document, path)
    valid = and_(kind == "array", func.json_array_length(document, path) <= _ARRAY_LIMIT)
    bounded = case((valid, func.json_extract(document, path)), else_="[]")
    entries = func.json_each(bounded).table_valued("value", "type")
    bit = case(*[(and_(entries.c.type == "text", entries.c.value == code), 1 << index)
                 for index, code in enumerate(codes)], else_=1 << len(codes))
    mask = select(func.coalesce(func.sum(func.distinct(bit)), 0)).select_from(entries).correlate_except(entries).scalar_subquery()
    return func.coalesce(valid, False), mask


def _has_code(mask, code, codes):
    return mask.op("&")(1 << (*codes, "other").index(code)) != 0


def _materialized(query, name):
    return query.cte(name).prefix_with("MATERIALIZED", dialect="sqlite")


def execution_summary(db, analysis_ids, *, execution_mode=None):
    """Use the caller's selected attempts/reference/filter, before group paging."""
    ids = _materialized(analysis_ids.distinct(), "execution_case_ids")
    ranked_runs = select(AgentRun.id, AgentRun.analysis_id, AgentRun.status,
        func.row_number().over(partition_by=AgentRun.analysis_id,
            order_by=(AgentRun.created_at.desc(), AgentRun.id.desc())).label("position")).join(
                ids, ids.c.analysis_id == AgentRun.analysis_id).subquery()
    runs = _materialized(select(ranked_runs.c.id, ranked_runs.c.analysis_id, ranked_runs.c.status).where(
        ranked_runs.c.position == 1), "execution_latest_runs")
    ranked_steps = select(AgentStep.id, AgentStep.run_id, AgentStep.step_type,
        func.row_number().over(partition_by=(AgentStep.run_id, AgentStep.step_type),
            order_by=(AgentStep.sequence.desc(), AgentStep.started_at.desc(), AgentStep.id.desc())).label("position")).join(
                runs, runs.c.id == AgentStep.run_id).where(
                    AgentStep.step_type.in_(("parser", "policy", "llm_verifier", "agent_stub"))).subquery()
    steps = _materialized(select(ranked_steps.c.run_id, *[
        func.max(case((ranked_steps.c.step_type == name, ranked_steps.c.id))).label(name)
        for name in ("parser", "policy", "llm_verifier", "agent_stub")]).where(
            ranked_steps.c.position == 1).group_by(ranked_steps.c.run_id), "execution_latest_steps")
    parser, policy = aliased(AgentStep), aliased(AgentStep)
    parser_document, policy_document, result = (_document(parser.metadata_json), _document(policy.metadata_json),
                                               _document(Analysis.result_json))
    warning_recorded, warnings = _array_observation(parser_document, "$.parser_warning_codes", PARSER_WARNING_CODES)
    policy_recorded, policy_reasons = _array_observation(policy_document, "$.verifier_reason_codes", VERIFIER_REASON_CODES)
    result_recorded, result_reasons = _array_observation(result, "$.verifier.reasons", VERIFIER_REASON_CODES)
    policy_execute = _boolean(policy_document, "$.execute_verifier")
    result_execute = _boolean(result, "$.verifier.executed")
    framework = func.json_extract(result, "$.agent.framework")
    completed_result = and_(Analysis.status == "completed",
        func.json_extract(result, "$.schema_version").in_(_RESULT_VERSIONS),
        or_(runs.c.id.is_(None), runs.c.status == "completed"))
    trusted_result = and_(completed_result, framework == "moduagent")
    policy_decision_known = policy_execute.is_not(None)
    result_decision_known = and_(trusted_result, result_execute.is_not(None))
    policy_known = and_(policy_recorded, policy_decision_known)
    result_known = and_(result_recorded, result_decision_known)
    raw_status = func.json_extract(parser_document, "$.parse_status")
    # Stub executes the real parser. Only its Verifier observations are N/A.
    stub = or_(steps.c.agent_stub.is_not(None), and_(completed_result, framework == "stub"),
        and_(literal(execution_mode == "stub"), ~func.coalesce(trusted_result, False),
            policy.id.is_(None), steps.c.llm_verifier.is_(None)))
    observations = _materialized(select(Analysis.id.label("analysis_id"),
        case((raw_status.in_(("success", "partial", "failed")), raw_status), else_="unrecorded").label("parser_status"),
        warning_recorded.label("warning_recorded"), warnings.label("warning_mask"),
        func.coalesce(stub, False).label("stub"),
        steps.c.llm_verifier.is_not(None).label("stage_entered"),
        func.coalesce(or_(policy_known, result_known), False).label("policy_recorded"),
        case((policy_decision_known, policy_execute), (result_decision_known, result_execute), else_=None).label("requested"),
        func.coalesce(and_(trusted_result, result_execute == 1), False).label("result_executed"),
        case((policy_known, policy_reasons), (result_known, result_reasons), else_=0).label("reason_mask")
        ).select_from(ids).join(Analysis, Analysis.id == ids.c.analysis_id).outerjoin(
            runs, and_(runs.c.analysis_id == Analysis.id, or_(Analysis.status.not_in(("pending", "processing")),
                and_(Analysis.status == "processing", runs.c.status == "running")))).outerjoin(steps, steps.c.run_id == runs.c.id).outerjoin(
            parser, parser.id == steps.c.parser).outerjoin(policy, policy.id == steps.c.policy), "execution_observations")
    c = observations.c
    executed = and_(~c.stub, or_(c.stage_entered, c.result_executed))
    state = case((c.stub, "not_applicable"), (executed, "executed"),
        (c.requested == 0, "not_executed"), (c.requested == 1, "awaiting_execution"), else_="unrecorded")
    reason = lambda code: _has_code(c.reason_mask, code, VERIFIER_REASON_CODES)
    reason_count = sum(case((reason(code), 1), else_=0) for code in (*VERIFIER_REASON_CODES, "other"))
    count = lambda predicate: func.coalesce(func.sum(case((predicate, 1), else_=0)), 0)
    aggregate = db.execute(select(func.count().label("cases"),
        *[count(c.parser_status == value).label("parser_" + value) for value in ("success", "partial", "failed", "unrecorded")],
        count(c.warning_recorded).label("warnings_recorded"),
        *[count(_has_code(c.warning_mask, code, PARSER_WARNING_CODES)).label("warning_" + code)
          for code in (*PARSER_WARNING_CODES, "other")],
        count(and_(~c.stub, c.policy_recorded)).label("policy_recorded"),
        count(and_(executed, reason_count == 0)).label("reasons_unrecorded"),
        *[count(state == value).label(value) for value in ("executed", "not_executed", "awaiting_execution", "unrecorded", "not_applicable")],
        *[count(and_(executed, reason(code))).label("reason_" + code) for code in (*VERIFIER_REASON_CODES, "other")],
        *[count(and_(executed, reason(code), reason_count == 1)).label("only_" + code)
          for code in (*VERIFIER_REASON_CODES, "other")])).mappings().one()
    cases = aggregate["cases"]
    reason_rows = [{"code": code, "cases": aggregate["reason_" + code], "only_cases": aggregate["only_" + code],
        "with_other_cases": aggregate["reason_" + code] - aggregate["only_" + code]}
        for code in (*VERIFIER_REASON_CODES, "other") if aggregate["reason_" + code]]
    parser_cases = aggregate["reason_parser_incomplete"]
    parser_only = aggregate["only_parser_incomplete"]
    return {"version": VERSION, "cases": cases,
        "parser": {**{value: aggregate["parser_" + value] for value in ("success", "partial", "failed", "unrecorded")},
            "warnings_recorded": aggregate["warnings_recorded"], "warnings_unrecorded": cases - aggregate["warnings_recorded"],
            "warnings": [{"code": code, "cases": aggregate["warning_" + code]}
                         for code in (*PARSER_WARNING_CODES, "other") if aggregate["warning_" + code]]},
        "verifier": {**{value: aggregate[value] for value in ("policy_recorded", "executed", "not_executed", "awaiting_execution", "unrecorded", "not_applicable", "reasons_unrecorded")},
            "parser_triggered": parser_cases, "parser_only": parser_only,
            "parser_with_other": parser_cases - parser_only, "reasons": reason_rows}}
