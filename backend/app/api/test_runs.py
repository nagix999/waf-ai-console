"""Administrator-only named test submission and fixed-reference reporting."""
import hashlib
from typing import Annotated
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import TestRun
from ..security import Principal, require_scope
from ..test_run_schemas import TestRunCreate, TestRunDetail, TestRunList
from ..services.analysis import AnalysisIngestError
from ..services.prompt_policies import PromptPolicyError
from ..services.prompt_snapshots import PromptSnapshotError
from ..services.analysis_query import contains_text
from ..services.test_runs import describe_run, enqueue_named_run, read_snapshot
from ..services.uploads import UploadFormatError, parse_upload
from ..services.vllm_profiles import TargetNotAllowedError

router = APIRouter(prefix="/test-runs", tags=["test-runs"])
Admin = Annotated[Principal, Depends(require_scope("admin"))]
DbSession = Annotated[Session, Depends(get_db)]


def submit(db, request, principal, **kwargs):
    try:
        return enqueue_named_run(db, request.app.state.crypto, request.app.state.settings,
            actor=principal.username or "admin", **kwargs)
    except (AnalysisIngestError, PromptPolicyError) as exc:
        raise HTTPException(exc.status_code, exc.code) from None
    except PromptSnapshotError as exc:
        raise HTTPException(503, exc.code) from None
    except ValidationError:
        raise HTTPException(422, "invalid_test_run_metadata") from None
    except (TargetNotAllowedError, UploadFormatError) as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("", response_model=TestRunDetail, status_code=202)
def create_test_run(payload: TestRunCreate, request: Request, db: DbSession, principal: Admin):
    row = dict(payload.event)
    for field in ("expected_verdict", "difficulty", "test_category", "case_name"):
        value = getattr(payload, field)
        if value is not None:
            if field in row:
                raise HTTPException(422, "duplicate_test_metadata_location")
            row[field] = value
    run, _ = submit(db, request, principal, name=payload.name,
        idempotency_key=payload.idempotency_key, rows=[row], kind="direct")
    return describe_run(db, run)


@router.post("/uploads", response_model=TestRunDetail, status_code=202)
async def upload_test_run(request: Request, db: DbSession, principal: Admin,
                          name: str = Form(...), idempotency_key: str = Form(...), file: UploadFile = File(...)):
    # Multipart ignores unknown controls by default. Explicitly reject prompt
    # overrides rather than silently accepting a request we will not honor.
    if {"prompt_policy_version_id", "fixed_rules_version", "prompt_template"}.intersection(await request.form()):
        raise HTTPException(422, "test_prompt_selection_not_supported")
    content = await file.read(request.app.state.settings.upload_max_bytes + 1)
    if len(content) > request.app.state.settings.upload_max_bytes:
        raise HTTPException(413, "upload_too_large")
    try:
        rows = parse_upload(file.filename or "", content)
    except UploadFormatError as exc:
        raise HTTPException(422, str(exc)) from None
    except (RecursionError, ValueError, UnicodeError):
        raise HTTPException(422, "invalid_test_document") from None
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1][:255]
    run, _ = submit(db, request, principal, name=name, idempotency_key=idempotency_key,
        rows=rows, kind="upload", filename=filename, content_hash=hashlib.sha256(content).hexdigest())
    return describe_run(db, run)


@router.get("", response_model=TestRunList)
def list_test_runs(db: DbSession, _principal: Admin, limit: int = Query(20, ge=1, le=100),
                   offset: int = Query(0, ge=0), q: str | None = Query(None, max_length=120)):
    read_snapshot(db)
    query = select(TestRun)
    if q:
        query = query.where(contains_text(TestRun.name, q))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(TestRun.created_at.desc(), TestRun.id.desc()).offset(offset).limit(limit))
    return TestRunList(items=[describe_run(db, row, detail=False) for row in rows], total=total, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=TestRunDetail)
def get_test_run(run_id: str, db: DbSession, _principal: Admin,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 difficulty: str | None = Query(None, max_length=80),
                 test_category: str | None = Query(None, max_length=120),
                 difficulty_missing: bool = False, test_category_missing: bool = False,
                 status: str | None = Query(None, pattern=r"^(pending|processing|completed|failed)$"),
                 evaluation_outcome: str | None = Query(None, max_length=80),
                 reference_verdict: str | None = Query(None, pattern=r"^(true_positive|false_positive|inconclusive)$"),
                 verdict: str | None = Query(None, pattern=r"^(true_positive|false_positive|inconclusive)$")):
    read_snapshot(db)
    if (difficulty_missing and difficulty is not None) or (test_category_missing and test_category is not None):
        raise HTTPException(422, "conflicting_test_group_filter")
    run = db.get(TestRun, run_id)
    if run is None:
        raise HTTPException(404, "test_run_not_found")
    return describe_run(db, run, limit=limit, offset=offset, difficulty=difficulty,
        test_category=test_category, status=status, evaluation_outcome=evaluation_outcome,
        reference_verdict=reference_verdict, verdict=verdict,
        difficulty_missing=difficulty_missing, test_category_missing=test_category_missing)
