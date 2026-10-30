"""Admin-only parser versions, local diagnostics and internal suggestions."""
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from ..database import get_db
from ..models import ParserJob, ParserVersion
from ..parser_schemas import (ParserComparisonCreate, ParserComparisonReview, ParserOptions,
                              ParserSuggestionCreate, ParserVersionCreate)
from ..security import Principal, require_scope
from ..services.parser_jobs import enqueue_suggestion
from ..services.parser_management import audit, compare, job_detail, job_summary, review_comparison
from ..services.parser_versions import (ParserVersionError, create_version, ensure_parser_state,
                                        get_version, version_detail, version_summary)

router = APIRouter(prefix="/admin/parsers", tags=["parsers"])
DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


@contextmanager
def errors(db):
    try:
        yield
    except ParserVersionError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "parser_configuration_conflict") from None
    except (ValueError, UnicodeError):
        db.rollback()
        raise HTTPException(409, "parser_stored_data_invalid") from None


@router.get("")
def list_versions(request: Request, db: DbSession, principal: Admin):
    with errors(db):
        state = ensure_parser_state(db)
        rows = db.scalars(select(ParserVersion).order_by(ParserVersion.created_at.desc(), ParserVersion.id).limit(200)).all()
        active = get_version(db, state.active_version_id)
        total = db.scalar(select(func.count()).select_from(ParserVersion)) or 0
        result = {"items": [version_summary(row) for row in rows], "active_version_id": state.active_version_id,
            "active_version": version_summary(active), "total": total, "recent_limit": 200, "limited": total > len(rows),
            "revision": state.revision, "engines": ["generic-http-v3", "http-capture-v1"],
            "option_schema": ParserOptions.model_json_schema(),
            "limits": {"comparison_cases": 100, "suggestion_cases": 20, "source_bytes": 65536, "total_source_bytes": 524288},
            "activation_requires_production_review": True}
        audit(db, principal.username or "unknown", "list_parser_versions", "all", "parser_version")
        db.commit()
        return result


@router.post("", status_code=201)
def save_version(payload: ParserVersionCreate, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = create_version(db, request.app.state.crypto, **payload.model_dump(mode="json"), actor=principal.username or "unknown")
        result = version_detail(row, request.app.state.crypto)
        audit(db, principal.username or "unknown", "create_parser_version", row.id, "parser_version")
        db.commit()
        return result


@router.get("/jobs")
def list_jobs(db: DbSession, principal: Admin, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    total = db.scalar(select(func.count()).select_from(ParserJob)) or 0
    rows = db.scalars(select(ParserJob).options(load_only(ParserJob.id, ParserJob.kind, ParserJob.status,
        ParserJob.baseline_version_id, ParserJob.candidate_version_id, ParserJob.profile_id, ParserJob.summary_json,
        ParserJob.error_code, ParserJob.created_by, ParserJob.created_at, ParserJob.started_at, ParserJob.completed_at))
        .order_by(ParserJob.created_at.desc(), ParserJob.id).offset(offset).limit(limit))
    result = {"items": [job_summary(row) for row in rows], "total": total, "limit": limit, "offset": offset}
    audit(db, principal.username or "unknown", "list_parser_jobs", "all")
    db.commit()
    return result


@router.get("/jobs/{identifier}")
def get_job(identifier: str, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        result = job_detail(db, request.app.state.crypto, identifier, principal.username or "unknown")
        db.commit()
        return result


@router.post("/jobs/{identifier}/review")
def review_job(identifier: str, payload: ParserComparisonReview, db: DbSession, principal: Admin):
    with errors(db):
        row = review_comparison(db, identifier, principal.username or "unknown")
        result = job_summary(row)
        db.commit()
        return result


@router.post("/comparisons", status_code=201)
def compare_parsers(payload: ParserComparisonCreate, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = compare(db, request.app.state.crypto, payload, principal.username or "unknown")
        result = job_detail(db, request.app.state.crypto, row.id, principal.username or "unknown")
        db.commit()
        return result


@router.post("/suggestions", status_code=202)
def suggest_parser(payload: ParserSuggestionCreate, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = enqueue_suggestion(db, request.app.state.crypto, payload, principal.username or "unknown")
        result = job_summary(row)
        db.commit()
        return result


@router.get("/{version_id}")
def get_parser(version_id: str, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        result = version_detail(get_version(db, version_id), request.app.state.crypto)
        audit(db, principal.username or "unknown", "view_parser_version", version_id, "parser_version")
        db.commit()
        return result


@router.post("/{version_id}/activate")
def activate_parser(version_id: str, principal: Admin):
    raise HTTPException(409, "production_promotion_required")
