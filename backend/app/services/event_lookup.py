"""Resolve external event IDs without weakening source/purpose isolation."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Analysis, TestRun
from .analysis import fetch_analysis
from .analysis_access import ServiceAnalysisScope, source_condition


class EventLookupError(ValueError):
    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def lookup_event_analysis(
    db: Session,
    event_id: str,
    *,
    source: ServiceAnalysisScope,
    test_run_id: str | None = None,
) -> Analysis:
    # No administrator-wide fallback: external IDs are only unique within a
    # source. Failed-job retries must not silently change the reviewed result.
    if not source.source_system:
        raise EventLookupError(403, "source_system_required")
    if test_run_id is not None and source.purpose != "test":
        raise EventLookupError(422, "test_run_id_test_only")
    query = select(Analysis.id).where(
        Analysis.event_id == event_id,
        Analysis.retry_of_analysis_id.is_(None),
        source_condition(source),
    )
    if test_run_id is not None:
        query = query.where(Analysis.source_system.in_(select(TestRun.source_system).where(
            TestRun.id == test_run_id,
            TestRun.api_source_system == source.source_system,
        )))
    identifiers = list(db.scalars(query.limit(2)))
    if not identifiers:
        raise EventLookupError(404, "analysis_not_found")
    if len(identifiers) != 1:
        raise EventLookupError(409, "test_event_ambiguous")
    row = fetch_analysis(db, identifiers[0], source)
    if row is None:
        raise EventLookupError(404, "analysis_not_found")
    return row
