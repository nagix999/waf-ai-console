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
from ..services.analysis import AnalysisIngestError, enqueue_analysis, fetch_analysis, to_detail, to_summary
from ..services.analysis_query import AnalysisFilters, filtered_evaluation_summary, find_analyses
from ..services.timing import run_duration_ms, step_duration_ms
from ..services.payload_decoding import decode_payload
from ..services.uploads import UploadFormatError, extract_test_upload_row, normalize_upload_row, parse_upload
from ..services.upload_expected_labels import enqueue_test_upload_row
from ..services.input_schemas import InputSchemaError, pin_schema

router = APIRouter(tags=["analyses"])
DbSession = Annotated[Session, Depends(get_db)]


def require_source(principal: Principal) -> str:
    if principal.source_system:
        return principal.source_system
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="source_system_required")


def visible_source(principal: Principal) -> str | None:
    return None if "admin" in principal.scopes else require_source(principal)


def begin_analysis_read_snapshot(db: Session) -> None:
    # SQLite legacy transaction mode does not begin a DBAPI transaction for
    # SELECT. Keep count, page, labels and aggregate on one read snapshot in
    # these read-only routes; leave ingest, worker and global DB behavior alone.
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


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
    responses={202: {"model": AnalysisDetail, "description": "Queued or still processing"}},
)
async def create_analysis(
    payload: AnalysisInput,
    request: Request,
    response: Response,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("ingest"))],
    wait_seconds: int = Query(default=0, ge=0, le=60),
) -> AnalysisDetail:
    return await submit_analysis(payload, request, response, db, principal, wait_seconds, "production", "service_api")


@router.post(
    "/test-analyses", response_model=AnalysisDetail,
    responses={202: {"model": AnalysisDetail, "description": "Queued or still processing"}},
)
async def create_test_analysis(
    payload: AnalysisInput,
    request: Request,
    response: Response,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("admin"))],
    name: str = Query(..., min_length=1, max_length=120),
    idempotency_key: str = Query(..., min_length=8, max_length=120),
    wait_seconds: int = Query(default=0, ge=0, le=60),
) -> AnalysisDetail:
    if set(request.query_params) - {"wait_seconds", "name", "idempotency_key"}:
        raise HTTPException(422, "unsupported_query_parameter")
    from .test_runs import submit
    from ..models import TestRunItem
    run, _ = submit(db, request, principal, name=name, idempotency_key=idempotency_key,
        rows=[payload.model_dump(mode="json", exclude_unset=True)], kind="direct")
    item = db.scalar(select(TestRunItem).where(TestRunItem.test_run_id == run.id))
    if item.analysis_id is None:
        raise HTTPException(413 if item.error_code == "payload_too_large" else 422,
                            item.error_code or "invalid_test_event")
    row = fetch_analysis(db, item.analysis_id)
    if wait_seconds:
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while row.status in {"pending", "processing"} and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            db.expire_all()
            row = fetch_analysis(db, item.analysis_id)
    row._test_run_id = run.id
    if row.status in {"pending", "processing"}:
        response.status_code = 202
    return to_detail(row, request.app.state.crypto)


async def submit_analysis(
    payload: AnalysisInput, request: Request, response: Response, db: Session,
    principal: Principal, wait_seconds: int, purpose: str, channel: str,
) -> AnalysisDetail:
    if set(request.query_params) - {"wait_seconds"}:
        raise HTTPException(status_code=422, detail="unsupported_query_parameter")
    try:
        row, _duplicate = enqueue_analysis(
            db, request.app.state.crypto, require_source(principal), payload,
            analysis_purpose=purpose, ingest_channel=channel,
            payload_max_bytes=request.app.state.settings.payload_max_bytes,
        )
    except AnalysisIngestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.issues or exc.code) from None
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
    return to_detail(row, request.app.state.crypto)


@router.get("/analyses", response_model=AnalysisListResponse)
def list_analyses(
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("ingest"))],
    filters: Annotated[AnalysisFilters, Query()],
) -> AnalysisListResponse:
    begin_analysis_read_snapshot(db)
    rows, total = find_analyses(db, filters, visible_source(principal))
    return AnalysisListResponse(
        items=[to_summary(row) for row in rows], total=total, limit=filters.limit, offset=filters.offset,
        evaluation_summary=filtered_evaluation_summary(db, filters, visible_source(principal)),
    )


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetail)
def get_analysis(
    analysis_id: str, db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("ingest"))],
    request: Request = None,
) -> AnalysisDetail:
    begin_analysis_read_snapshot(db)
    row = fetch_analysis(db, analysis_id, visible_source(principal))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="analysis_not_found")
    return to_detail(row, request.app.state.crypto if request is not None else None)


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
        decoding=decode_payload(payload),
    )


@router.post("/analyses/{analysis_id}/reviews", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED)
def create_review(
    analysis_id: str,
    payload: ReviewCreate,
    response: Response,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("review"))],
) -> ReviewResponse:
    analysis = fetch_analysis(db, analysis_id, visible_source(principal))
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
        validate_review_duplicate(existing, analysis_id, payload)
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
        validate_review_duplicate(row, analysis_id, payload)
        response.status_code = status.HTTP_200_OK
    db.refresh(row)
    return ReviewResponse.model_validate(row, from_attributes=True)


def validate_review_duplicate(existing: Review, analysis_id: str, payload: ReviewCreate) -> None:
    if existing.analysis_id != analysis_id or any(
        getattr(existing, field) != value for field, value in payload.model_dump().items()
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="external_review_id_conflict")


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
                    duration_ms=step_duration_ms(step),
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
                duration_ms=run_duration_ms(run),
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
    return await submit_upload(request, db, principal, file, "production", "file_upload")


@router.post("/test-uploads", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_test_events(
    request: Request,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_scope("admin"))],
    file: UploadFile = File(...),
    name: str = Query(..., min_length=1, max_length=120),
    idempotency_key: str = Query(..., min_length=8, max_length=120),
) -> UploadResponse:
    if set(request.query_params) - {"name", "idempotency_key"}:
        raise HTTPException(422, "unsupported_query_parameter")
    from .test_runs import upload_test_run
    from ..models import TestRunItem
    detail = await upload_test_run(request, db, principal, name, idempotency_key, file)
    rows = list(db.scalars(select(TestRunItem).where(TestRunItem.test_run_id == detail.id).order_by(TestRunItem.row_number)))
    return UploadResponse(accepted=detail.accepted, duplicates=detail.duplicates, rejected=detail.rejected,
        analysis_ids=[row.analysis_id for row in rows if row.analysis_id],
        errors=[{"row": row.row_number, "message": row.error_code} for row in rows if row.error_code][:100],
        label_attached=sum(row.label_id is not None and row.ingest_status == "accepted" for row in rows),
        label_unchanged=sum(row.label_id is not None and row.ingest_status == "duplicate" for row in rows),
        test_run_id=detail.id)


async def submit_upload(
    request: Request, db: Session, principal: Principal, file: UploadFile, purpose: str, channel: str,
) -> UploadResponse:
    if request.query_params:
        raise HTTPException(status_code=422, detail="unsupported_query_parameter")
    content = await file.read(request.app.state.settings.upload_max_bytes + 1)
    if len(content) > request.app.state.settings.upload_max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="upload_too_large")
    try:
        rows = parse_upload(file.filename or "", content)
    except UploadFormatError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    accepted = duplicates = rejected = 0
    label_attached = label_unchanged = 0
    analysis_ids: list[str] = []
    errors: list[dict] = []
    source_system = require_source(principal)
    try:
        schema_snapshot = pin_schema(db, request.app.state.crypto, Analysis())
        db.commit()  # Persist first-use default before row-level transactions.
    except InputSchemaError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None
    for index, raw_row in enumerate(rows, start=1):
        try:
            expected_verdict = None
            if purpose == "test":
                raw_row, expected_verdict = extract_test_upload_row(raw_row)
            payload = AnalysisInput.model_validate(normalize_upload_row(raw_row))
            if purpose == "test":
                analysis, duplicate, label_state = enqueue_test_upload_row(
                    db, request.app.state.crypto, source_system, payload,
                    expected_verdict=expected_verdict,
                    actor=principal.username or source_system,
                    payload_max_bytes=request.app.state.settings.payload_max_bytes,
                    ingest_channel=channel,
                    schema_snapshot=schema_snapshot,
                )
                label_attached += label_state == "attached"
                label_unchanged += label_state == "unchanged"
            else:
                analysis, duplicate = enqueue_analysis(
                    db, request.app.state.crypto, source_system, payload,
                    analysis_purpose=purpose, ingest_channel=channel,
                    payload_max_bytes=request.app.state.settings.payload_max_bytes,
                    schema_snapshot=schema_snapshot,
                )
            analysis_ids.append(analysis.id)
            if duplicate:
                duplicates += 1
            else:
                accepted += 1
        except ValidationError as exc:
            rejected += 1
            if len(errors) < 100:
                errors.append({"row": index, "validation": safe_validation_errors(exc)})
        except AnalysisIngestError as exc:
            rejected += 1
            if len(errors) < 100:
                errors.append({"row": index, "message": exc.code, **({"validation": exc.issues} if exc.issues else {})})
        except UploadFormatError as exc:
            rejected += 1
            if len(errors) < 100:
                errors.append({"row": index, "message": str(exc)})
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
        label_attached=label_attached,
        label_unchanged=label_unchanged,
    )
