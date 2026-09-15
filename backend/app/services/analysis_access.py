from dataclasses import dataclass
from sqlalchemy import and_, select
from ..models import Analysis, TestRun


@dataclass(frozen=True)
class ServiceAnalysisScope:
    source_system: str
    purpose: str


def source_condition(scope):
    if isinstance(scope, str):
        return Analysis.source_system == scope
    if scope.purpose == "test":
        return and_(Analysis.analysis_purpose == "test", Analysis.source_system.in_(
            select(TestRun.source_system).where(TestRun.api_source_system == scope.source_system)))
    return and_(Analysis.source_system == scope.source_system,
                Analysis.analysis_purpose.in_(["production", "legacy_unknown"]))
