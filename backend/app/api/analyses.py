import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import AccessAudit, AgentRun, Analysis, Review
from ..schemas import (
    AgentRunResponse,
    AgentStepResponse,
    AccessAuditResponse,
    AnalysisDetail,
    AnalysisInput,
    AnalysisListResponse,
    AnalysisSummary,
    RawEventResponse,
    ReviewCreate,
    ReviewResponse,
    UploadResponse,
)
from ..security import CurrentPrincipal, Principal, require_scope
from ..services.analysis import count_analyses, enqueue_analysis, fetch_analysis, to_detail, to_summary
from ..services.uploads import UploadFormatError, normalize_upload_row, parse_upload

router = APIRouter(tags=["analyses"])
DbSession = Annotated[Session, Depends(get_db)]


def require_source(principal: Principal) -> str:
    if principal.source_system:
        return principal.source_system
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="source_system_required")


def safe_validation_errors(exc: ValidationError) -> list[dict]:
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "type": error["type"],
            "message": error["msg"],
        }
        for error in exc.errors(include_input=False, include_context=False, include_url=False)
    ]


def record_access(db: Session, principal: Principal, action: str, resource_type: str, resource_id: str) -> None:
    db.add(
        AccessAudit(
            actor_kind=principal.kind,
            actor_id=principal.username or principal.source_system or "unknown",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )
    db.commit()


@router.post(
    "/analyses",
    response_model=AnalysisDetail,
    responses={202: {"description": "Queued or still processing"}},
)
async def create_analysis(
    payload: AnalysisInput,
    request: Request,
    response: Response,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("ingest"))],
    wait_seconds: int = Query(default=0, ge=0, le=60),
) -> AnalysisDetail:
    row, _duplicate = enqueue_analysis(db, request.app.state.crypto, require_source(principal), payload)
    if wait_seconds:
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while row.status in {"pending", "processing"} and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            db.expire_all()
            refreshed = fetch_analysis(db, row.id)
            if refreshed is None:
                break
            row = refreshed
    if row.status in {"pending", "processing"}:
        response.status_code = status.HTTP_202_ACCEPTED
    return to_detail(row)


@router.get("/analyses", response_model=AnalysisListResponse)
def list_analyses(
    db: DbSession,
    _principal: CurrentPrincipal,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    analysis_status: str | None = Query(default=None, alias="status"),
    verdict: str | None = Query(default=None),
) -> AnalysisListResponse:
    query = select(Analysis).options(selectinload(Analysis.reviews)).order_by(desc(Analysis.created_at))
    if analysis_status:
        query = query.where(Analysis.status == analysis_status)
    if verdict:
        query = query.where(Analysis.verdict == verdict)
    rows = list(db.scalars(query.offset(offset).limit(limit)).all())
    return AnalysisListResponse(items=[to_summary(row) for row in rows], total=count_analyses(db), limit=limit, offset=offset)


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetail)
def get_analysis(analysis_id: str, db: DbSession, _principal: CurrentPrincipal) -> AnalysisDetail:
    row = fetch_analysis(db, analysis_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis_not_found")
    return to_detail(row)


@router.get("/analyses/{analysis_id}/event", response_model=RawEventResponse)
def get_raw_event(
    analysis_id: str,
    request: Request,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("admin"))],
) -> RawEventResponse:
    row = db.get(Analysis, analysis_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis_not_found")
    try:
        payload = request.app.state.crypto.decrypt_text(row.payload_ciphertext)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    record_access(db, principal, "view_raw_event", "analysis", row.id)
    return RawEventResponse(
        analysis_id=row.id,
        event_id=row.event_id,
        payload=payload,
        extra_fields=row.extra_fields,
        encryption_key_version=row.encryption_key_version,
    )


@router.post("/analyses/{analysis_id}/reviews", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED)
def create_review(
    analysis_id: str,
    payload: ReviewCreate,
    response: Response,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("review"))],
) -> ReviewResponse:
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis_not_found")
    if analysis.event_id != payload.event_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="event_id_mismatch")
    source_system = require_source(principal)
    existing = db.scalar(
        select(Review).where(
            Review.source_system == source_system,
            Review.external_review_id == payload.external_review_id,
        )
    )
    if existing:
        response.status_code = status.HTTP_200_OK
        return ReviewResponse.model_validate(existing, from_attributes=True)
    row = Review(analysis_id=analysis_id, source_system=source_system, **payload.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(
            select(Review).where(
                Review.source_system == source_system,
                Review.external_review_id == payload.external_review_id,
            )
        )
        if row is None:
            raise
        response.status_code = status.HTTP_200_OK
    db.refresh(row)
    return ReviewResponse.model_validate(row, from_attributes=True)


@router.get("/analyses/{analysis_id}/agent-runs", response_model=list[AgentRunResponse])
def get_agent_runs(
    analysis_id: str,
    request: Request,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("admin"))],
) -> list[AgentRunResponse]:
    if db.get(Analysis, analysis_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis_not_found")
    runs = db.scalars(
        select(AgentRun)
        .options(selectinload(AgentRun.steps))
        .where(AgentRun.analysis_id == analysis_id)
        .order_by(desc(AgentRun.created_at))
    ).all()
    record_access(db, principal, "view_agent_runs", "analysis", analysis_id)
    result: list[AgentRunResponse] = []
    for run in runs:
        steps = []
        for step in run.steps:
            try:
                input_text = request.app.state.crypto.decrypt_text(step.input_ciphertext) if step.input_ciphertext else None
                output_text = request.app.state.crypto.decrypt_text(step.output_ciphertext) if step.output_ciphertext else None
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
            steps.append(
                AgentStepResponse(
                    id=step.id,
                    sequence=step.sequence,
                    step_type=step.step_type,
                    name=step.name,
                    status=step.status,
                    input=input_text,
                    output=output_text,
                    metadata=step.metadata_json,
                    tool_calls=step.tool_calls_json,
                    started_at=step.started_at,
                    completed_at=step.completed_at,
                )
            )
        result.append(
            AgentRunResponse(
                id=run.id,
                analysis_id=run.analysis_id,
                framework_run_id=run.framework_run_id,
                fingerprint=run.fingerprint,
                status=run.status,
                failure_id=run.failure_id,
                started_at=run.started_at,
                completed_at=run.completed_at,
                steps=steps,
            )
        )
    return result


@router.get("/audit-logs", response_model=list[AccessAuditResponse])
def get_audit_logs(
    db: DbSession,
    _principal: Annotated[Principal, Depends(require_scope("admin"))],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AccessAuditResponse]:
    rows = db.scalars(select(AccessAudit).order_by(desc(AccessAudit.created_at)).limit(limit)).all()
    return [AccessAuditResponse.model_validate(row, from_attributes=True) for row in rows]


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_events(
    request: Request,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("ingest"))],
    file: UploadFile = File(...),
) -> UploadResponse:
    content = await file.read(request.app.state.settings.upload_max_bytes + 1)
    if len(content) > request.app.state.settings.upload_max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="upload_too_large")
    try:
        rows = parse_upload(file.filename or "", content)
    except UploadFormatError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    accepted = duplicates = rejected = 0
    analysis_ids: list[str] = []
    errors: list[dict] = []
    source_system = require_source(principal)
    for index, raw_row in enumerate(rows, start=1):
        try:
            payload = AnalysisInput.model_validate(normalize_upload_row(raw_row))
            analysis, duplicate = enqueue_analysis(db, request.app.state.crypto, source_system, payload)
            analysis_ids.append(analysis.id)
            if duplicate:
                duplicates += 1
            else:
                accepted += 1
        except ValidationError as exc:
            rejected += 1
            if len(errors) < 100:
                errors.append({"row": index, "validation": safe_validation_errors(exc)})
        except ValueError as exc:
            rejected += 1
            if len(errors) < 100:
                errors.append({"row": index, "message": type(exc).__name__})
    return UploadResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        analysis_ids=analysis_ids,
        errors=errors,
    )
