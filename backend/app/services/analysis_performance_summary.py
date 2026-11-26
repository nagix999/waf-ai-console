"""Administrator timing aggregates over the exact analysis-list filter scope."""
from sqlalchemy import select

from ..models import Analysis
from .analysis_query import analysis_conditions
from .evaluation import evaluation_relation
from .performance_timings import completed_timing_summary


def filtered_performance_summary(db, filters, source_system=None):
    evaluation = evaluation_relation()
    conditions = analysis_conditions(filters, source_system, evaluation)
    uses_evaluation = any(getattr(filters, name) is not None for name in (
        "label_presence", "evaluation_outcome", "reference_label", "label_source_kind", "label_source_ref", "label_ai_visible"))
    query = select(Analysis.id.label("analysis_id"), Analysis.status,
        Analysis.created_at, Analysis.started_at, Analysis.completed_at)
    if uses_evaluation:
        query = query.join(evaluation, evaluation.c.analysis_id == Analysis.id)
    cases = query.where(*conditions).cte("analysis_performance_cases").prefix_with("MATERIALIZED", dialect="sqlite")
    return {"version": "analysis-performance-summary-v1", **completed_timing_summary(db, cases)}
