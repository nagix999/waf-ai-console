"""Administrator-only feedback inspection, with no feedback mutation path."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..feedback_schemas import FeedbackFilters, FeedbackItem, FeedbackList, FeedbackSummary, FeedbackVersions
from ..models import AccessAudit
from ..schemas import FeedbackCategory
from ..security import Principal, require_scope
from ..services import feedback_queries as service
from ..services.test_runs import read_snapshot

router = APIRouter(prefix="/admin/feedback", tags=["admin-feedback"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]
FILTER_KEYS = set(FeedbackFilters.model_fields)


def get_filters(
    date_from: date | None = None,
    date_to: date | None = None,
    analysis_purpose: Literal["production", "test", "all"] = "production",
    production_configuration_id: str | None = Query(default=None, min_length=1, max_length=36),
    category: FeedbackCategory | None = None,
    q: str | None = Query(default=None, max_length=200),
    analysis_id: str | None = Query(default=None, min_length=1, max_length=36),
) -> FeedbackFilters:
    try:
        return FeedbackFilters(date_from=date_from, date_to=date_to, analysis_purpose=analysis_purpose,
            production_configuration_id=production_configuration_id, category=category, q=q, analysis_id=analysis_id)
    except ValidationError:
        raise HTTPException(422, "invalid_feedback_filters") from None


Filters = Annotated[FeedbackFilters, Depends(get_filters)]


def prepare(request, response, db, *, pagination=False, detail=False):
    response.headers["Cache-Control"] = "no-store"
    allowed = set() if detail else FILTER_KEYS | ({"page", "page_size"} if pagination else set())
    if set(request.query_params) - allowed:
        raise HTTPException(422, "unsupported_feedback_query")
    read_snapshot(db)


def audit(db, principal, action, resource_id="filtered"):
    # All response fields have already been materialized. End the read-only
    # snapshot before a separate audit write: concurrent list/summary/facet
    # reads must not upgrade stale WAL snapshots into writers.
    db.rollback()
    db.add(AccessAudit(actor_kind=principal.kind, actor_id=principal.username or "unknown",
        action=action, resource_type="feedback", resource_id=resource_id))
    db.commit()


def unavailable(db):
    db.rollback()
    return HTTPException(503, "feedback_storage_unavailable", headers={"Cache-Control": "no-store"})


@router.get("", response_model=FeedbackList,
    description="관리자 피드백 등록 이력. 기간은 피드백 접수 시각의 한국 날짜 기준입니다. 자유 검색은 이벤트/분석 ID, 회사, 시그니처, 작성자와 연동 식별자에 적용하며 의견 본문은 검색하지 않습니다.")
def list_feedback(request: Request, response: Response, db: Db, admin: Admin, filters: Filters,
                  page: int = Query(default=1, ge=1, le=1000000),
                  page_size: int = Query(default=20, ge=1, le=100)):
    try:
        prepare(request, response, db, pagination=True)
        result = service.feedback_list(db, filters, page=page, page_size=page_size)
        audit(db, admin, "view_feedback_list")
        return result
    except SQLAlchemyError:
        raise unavailable(db) from None


@router.get("/summary", response_model=FeedbackSummary)
def summary(request: Request, response: Response, db: Db, admin: Admin, filters: Filters):
    try:
        prepare(request, response, db)
        result = service.feedback_summary(db, filters)
        audit(db, admin, "view_feedback_summary")
        return result
    except SQLAlchemyError:
        raise unavailable(db) from None


@router.get("/versions", response_model=FeedbackVersions,
    description="동일 필터의 Production 등록만 집계합니다. 버전 선택 자체는 제외하며 분석 접수 당시 승인되어 있던 운영 구성을 표시합니다. 전체 실제 실행 구성의 일치를 보증하는 값이 아닙니다. 과거 미기록 버전이나 Test를 현재 운영 버전으로 추정하지 않습니다.")
def versions(request: Request, response: Response, db: Db, admin: Admin, filters: Filters):
    try:
        prepare(request, response, db)
        result = service.feedback_versions(db, filters)
        audit(db, admin, "view_feedback_versions")
        return result
    except SQLAlchemyError:
        raise unavailable(db) from None


@router.get("/{review_id}", response_model=FeedbackItem)
def detail(review_id: str, request: Request, response: Response, db: Db, admin: Admin):
    try:
        prepare(request, response, db, detail=True)
        result = service.feedback_detail(db, review_id)
        if result is None:
            raise HTTPException(404, "feedback_not_found")
        audit(db, admin, "view_feedback_detail", review_id)
        return result
    except SQLAlchemyError:
        raise unavailable(db) from None
