"""Append-only feedback identified externally by an event, not review UUIDs.

The new contract does not collect AI exposure. The legacy non-null ai_visible
column retains its storage default for compatibility only; it must not be read
as an assertion that the submitting analyst has not seen an AI result.
Historical feedback and independent Ground Truth exposure metadata are unchanged.
"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..event_api_schemas import EventReviewCreate
from ..models import Analysis, Review
from .analysis_access import ServiceAnalysisScope
from .event_lookup import lookup_event_analysis


class EventReviewError(ValueError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code
        super().__init__(code)


def review_content_key(analysis_id: str, payload: EventReviewCreate) -> str:
    # None and an empty string are intentionally different opinions/authors.
    content = ["event-review-v1", analysis_id, payload.analyst_id,
               payload.feedback_categories, payload.comment]
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return "event-review:v1:" + digest


def same_content(row: Review, analysis: Analysis, payload: EventReviewCreate) -> bool:
    return (row.analysis_id == analysis.id and row.source_system == analysis.source_system
            and row.event_id == payload.event_id and row.decision is None and row.rating is None
            and row.analyst_id == payload.analyst_id and row.comment == payload.comment
            and isinstance(row.feedback_categories, list)
            and all(isinstance(category, str) for category in row.feedback_categories)
            and sorted(row.feedback_categories) == sorted(payload.feedback_categories))


def create_event_review(db: Session, payload: EventReviewCreate, *,
                        source: ServiceAnalysisScope,
                        test_run_id: str | None = None) -> tuple[Review, bool]:
    analysis = lookup_event_analysis(db, payload.event_id, source=source, test_run_id=test_run_id)
    identifier = review_content_key(analysis.id, payload)
    # Also reuse equivalent feedback accepted through the compatibility API.
    # Never reinterpret historical decision/rating records or rewrite exposure.
    candidates = db.scalars(select(Review).where(
        Review.analysis_id == analysis.id,
        Review.analyst_id == payload.analyst_id,
        Review.comment == payload.comment,
        Review.decision.is_(None), Review.rating.is_(None),
    ).order_by(Review.created_at, Review.id))
    for existing in candidates:
        if same_content(existing, analysis, payload):
            return existing, True
    row = Review(analysis_id=analysis.id, source_system=analysis.source_system,
        external_review_id=identifier, event_id=payload.event_id,
        feedback_categories=payload.feedback_categories, analyst_id=payload.analyst_id,
        comment=payload.comment)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Re-establish event/purpose/source authorization after ending the failed
        # transaction. A concurrent deletion must not expose a stale review.
        analysis = lookup_event_analysis(db, payload.event_id, source=source, test_run_id=test_run_id)
        existing = db.scalar(select(Review).where(
            Review.source_system == analysis.source_system,
            Review.external_review_id == identifier,
        ))
        if existing is None:
            raise EventReviewError(503, "review_storage_unavailable") from None
        if not same_content(existing, analysis, payload):
            # A legacy caller may have chosen the same reserved identifier;
            # never overwrite or acknowledge a different feedback record.
            raise EventReviewError(409, "review_content_conflict") from None
        return existing, True
    db.refresh(row)
    return row, False
