"""Read source-bound presentation links without changing saved analysis data.

Administrative callers audit before invoking this helper. Internal workers use
the same validation without manufacturing a user access event. Source excerpts,
ciphertext and arbitrary model errors never leave this module in exceptions.
"""
import json

from sqlalchemy import desc, func, select

from ..agent.evidence_relations import validate_evidence_relations
from ..models import AgentRun, AgentStep


class EvidenceDisplayReadError(ValueError):
    def __init__(self):
        super().__init__("evidence_display_unavailable")


def read_evidence_relations(db, crypto, analysis):
    empty = {"version": "evidence-relations-v1", "groups": []}
    result = analysis.result_json
    if analysis.status != "completed" or not isinstance(result, dict) or result.get("schema_version") != "waf-analysis-v3":
        return empty
    latest = db.execute(select(AgentRun.id, AgentRun.status).where(AgentRun.analysis_id == analysis.id)
        .order_by(desc(AgentRun.created_at), desc(AgentRun.id)).limit(1)).first()
    if latest is None or latest.status != "completed":
        return empty
    ciphertext = db.scalar(select(AgentStep.output_ciphertext).where(AgentStep.run_id == latest.id,
        AgentStep.step_type == "finalize", AgentStep.status == "completed")
        .order_by(desc(AgentStep.sequence)).limit(1))
    return _decode_relations(crypto, result, ciphertext)


def _decode_relations(crypto, result, ciphertext):
    empty = {"version": "evidence-relations-v1", "groups": []}
    if not ciphertext:
        return empty
    try:
        saved = json.loads(crypto.decrypt_text(ciphertext))
        if not isinstance(saved, dict):
            raise ValueError()
        if "evidence_relations" not in saved:
            return empty
        from ..result_contracts import validate_new_result
        canonical = validate_new_result(result)
        if saved.get("schema_version") != "waf-analysis-v3" or saved.get("evidence") != canonical["evidence"]:
            raise ValueError()
        return validate_evidence_relations(saved["evidence_relations"], canonical["evidence"])
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise EvidenceDisplayReadError() from None


def read_many_evidence_relations(db, crypto, analyses):
    """One bounded metadata query per export chunk, not a query per case."""
    modern = {row.id: row for row in analyses if row is not None and row.status == "completed"
        and isinstance(row.result_json, dict) and row.result_json.get("schema_version") == "waf-analysis-v3"}
    if not modern:
        return {}
    if len(modern) > 100:
        raise EvidenceDisplayReadError()
    runs = select(AgentRun.id, AgentRun.analysis_id, AgentRun.status,
        func.row_number().over(partition_by=AgentRun.analysis_id,
            order_by=(desc(AgentRun.created_at), desc(AgentRun.id))).label("rank"))\
        .where(AgentRun.analysis_id.in_(modern)).subquery()
    steps = select(AgentStep.run_id, AgentStep.output_ciphertext,
        func.row_number().over(partition_by=AgentStep.run_id, order_by=desc(AgentStep.sequence)).label("rank"))\
        .join(runs, (runs.c.id == AgentStep.run_id) & (runs.c.rank == 1) & (runs.c.status == "completed"))\
        .where(AgentStep.step_type == "finalize", AgentStep.status == "completed").subquery()
    values = db.execute(select(runs.c.analysis_id, steps.c.output_ciphertext).outerjoin(steps,
        (steps.c.run_id == runs.c.id) & (steps.c.rank == 1)).where(runs.c.rank == 1))
    ciphertexts = dict(values.all())
    return {identifier: _decode_relations(crypto, row.result_json, ciphertexts.get(identifier))
        for identifier, row in modern.items()}


def read_display_view(db, crypto, analysis):
    """An audited HTTP/export read must never queue work or call a model."""
    from .evidence_display_jobs import get_display_view
    relations = read_evidence_relations(db, crypto, analysis)
    return get_display_view(db, crypto, analysis, relations=relations)
