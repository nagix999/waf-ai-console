from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..dashboard_schemas import DashboardCounts, DashboardRuntime, DashboardSeverityCounts, DashboardSummary, DashboardWindow
from ..database import get_db
from ..models import Analysis, VLLMProfile, utcnow
from ..security import Principal, require_scope


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
) -> DashboardSummary:
    """Production aggregates for a rolling UTC window; runtime describes API configuration, not worker health."""
    now = utcnow()
    # Browser Date preserves milliseconds; use the same boundary for aggregates and list drill-down.
    created_to = now.replace(microsecond=(now.microsecond // 1000) * 1000)
    created_from = created_to - timedelta(days=days)
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
    ).where(
        Analysis.analysis_purpose == "production",
        Analysis.created_at >= created_from,
        Analysis.created_at < created_to,
    )
    aggregated = db.execute(query).mappings().one()
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
    )
