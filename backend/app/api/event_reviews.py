from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..event_api_schemas import EventReviewCreate, EventReviewResponse
from ..security import Principal, require_scope
from ..services.analysis_access import ServiceAnalysisScope
from ..services.event_lookup import EventLookupError
from ..services.event_reviews import EventReviewError, create_event_review

router = APIRouter(tags=["reviews"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/reviews", response_model=EventReviewResponse, status_code=201,
    responses={200: {"model": EventReviewResponse, "description": "동일한 피드백의 재전송"},
               503: {"description": "리뷰 저장소 일시 사용 불가"}},
    description="event_id로 분석을 찾아 피드백을 추가합니다. 내부 ID와 AI 열람 여부는 입력받지 않습니다. "
                "같은 분석·작성자·항목·의견의 재전송은 기존 기록을 반환하며 피드백을 정답으로 사용하지 않습니다.")
def create_review(
    payload: EventReviewCreate,
    request: Request,
    response: Response,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("review"))],
    test_run_id: str | None = Query(default=None, min_length=1, max_length=36,
        description="Test 키 전용 테스트 식별자. 같은 이벤트가 여러 테스트에 있으면 지정해야 합니다."),
) -> EventReviewResponse:
    response.headers["Cache-Control"] = "no-store"
    if set(request.query_params) - {"test_run_id"}:
        raise HTTPException(422, "unsupported_review_query")
    if not principal.source_system:
        raise HTTPException(403, "source_system_required")
    try:
        row, duplicate = create_event_review(db, payload,
            source=ServiceAnalysisScope(principal.source_system, principal.purpose),
            test_run_id=test_run_id)
        if duplicate:
            response.status_code = 200
        return EventReviewResponse.model_validate(row, from_attributes=True)
    except (EventLookupError, EventReviewError) as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "review_storage_unavailable") from None
