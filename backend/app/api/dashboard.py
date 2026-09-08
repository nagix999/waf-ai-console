from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..dashboard_schemas import DashboardCounts, DashboardRuntime, DashboardSeverityCounts, DashboardSummary, DashboardWindow, DashboardServiceKey, DashboardTrendPoint
from ..database import get_db
from ..models import Analysis, ServiceApiKey, VLLMProfile, utcnow
from ..security import Principal, require_scope
from ..services.evaluation import evaluation_relation, summarize_evaluation_rows


router = APIRouter(tags=["dashboard"])
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN")


def count_where(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_scope("admin"))],
    days: int = Query(default=7, ge=1, le=90),
    service_api_key_id: str | None = Query(default=None, min_length=1, max_length=36),
) -> DashboardSummary:
    """Production aggregates for a rolling UTC window; runtime describes API configuration, not worker health."""
    now = utcnow()
    # Browser Date preserves milliseconds; use the same boundary for aggregates and list drill-down.
    created_to = now.replace(microsecond=(now.microsecond // 1000) * 1000)
    created_from = created_to - timedelta(days=days)
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    selected_key = None
    if service_api_key_id is not None:
        selected_key = db.get(ServiceApiKey, service_api_key_id)
        if selected_key is None or selected_key.deleted_at is not None:
            raise HTTPException(404, "service_api_key_not_found")
    scope = [Analysis.analysis_purpose == "production", Analysis.retry_of_analysis_id.is_(None),
             Analysis.created_at >= created_from, Analysis.created_at < created_to]
    if service_api_key_id is not None:
        scope.append(Analysis.service_api_key_id == service_api_key_id)
    completed = Analysis.status == "completed"
    conditions = {
        **{state: Analysis.status == state for state in ("pending", "processing", "completed", "failed")},
        **{
            verdict: and_(completed, Analysis.verdict == verdict)
            for verdict in ("true_positive", "false_positive", "inconclusive")
        },
        "unreviewed": ~Analysis.reviews.any(),
        "critical_high_allowed": and_(
            completed, Analysis.verdict == "true_positive",
            Analysis.severity.in_(("CRITICAL", "HIGH")), Analysis.waf_action == "A",
        ),
        "false_positive_denied": and_(completed, Analysis.verdict == "false_positive", Analysis.waf_action == "D"),
    }
    severity_conditions = {level: and_(completed, Analysis.severity == level) for level in SEVERITIES}
    severity_conditions["unrated"] = and_(
        completed, or_(Analysis.severity.is_(None), Analysis.severity.not_in(SEVERITIES)),
    )
    # A single aggregate statement reads no event/result content and has no page-size limit.
    query = select(
        func.count(Analysis.id).label("total"),
        *(count_where(condition).label(name) for name, condition in conditions.items()),
        *(count_where(condition).label(f"severity_{name}") for name, condition in severity_conditions.items()),
    ).where(*scope)
    aggregated = db.execute(query).mappings().one()
    relation = evaluation_relation()
    # One grouped query for every date, never 90 full-table queries or averages
    # of percentages. The same source groups/denominators drive every card.
    date_column = func.date(Analysis.created_at)
    grouped = list(db.execute(select(
        date_column.label("date"), relation.c.outcome, relation.c.source_kind,
        relation.c.ai_visible, relation.c.reference_verdict, func.count().label("count"),
    ).select_from(Analysis).join(relation, relation.c.analysis_id == Analysis.id).where(*scope)
        .group_by(date_column, relation.c.outcome, relation.c.source_kind,
                  relation.c.ai_visible, relation.c.reference_verdict)).mappings())
    evaluation_summary = summarize_evaluation_rows(grouped)
    by_day = {}
    for row in grouped:
        by_day.setdefault(str(row["date"]), []).append(row)
    trend = []
    day_start = created_from.replace(hour=0, minute=0, second=0, microsecond=0)
    while day_start < created_to:
        day_end = day_start + timedelta(days=1)
        day_evaluation = summarize_evaluation_rows(by_day.get(day_start.date().isoformat(), []))
        trend.append(DashboardTrendPoint(date=day_start.date().isoformat(), total=day_evaluation.total, evaluation_summary=day_evaluation))
        day_start = day_end
    profile = db.execute(
        select(VLLMProfile.id, VLLMProfile.name, VLLMProfile.model_name)
        .where(VLLMProfile.status == "production").limit(1)
    ).mappings().first()
    return DashboardSummary(
        window=DashboardWindow(days=days, created_from=created_from, created_to=created_to),
        counts=DashboardCounts(total=aggregated["total"], **{name: aggregated[name] for name in conditions}),
        severity_counts=DashboardSeverityCounts(**{name: aggregated[f"severity_{name}"] for name in severity_conditions}),
        runtime=DashboardRuntime(
            agent_mode=request.app.state.settings.agent_mode,
            production_profile=dict(profile) if profile is not None else None,
        ),
        service_api_key=DashboardServiceKey(id=selected_key.id, name=selected_key.name, source_system=selected_key.source_system) if selected_key else None,
        attribution_unknown_count=int(db.scalar(select(func.count()).select_from(Analysis).where(*scope, Analysis.service_api_key_id.is_(None))) or 0),
        evaluation_summary=evaluation_summary,
        trend=trend,
    )
