"""Administrator-only immutable signature reference catalogs."""
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AccessAudit, SignatureExplanationVersion
from ..security import Principal, require_scope
from ..signature_explanation_schemas import (
    MAX_CATALOG_BYTES, MAX_ENTRIES, SignatureExplanationCreate, SignatureExplanationMatch,
)
from ..services.signature_explanations import (
    SignatureExplanationError, create_version, ensure_state, get_version, match_snapshot,
    snapshot_for_version, version_detail, version_summary,
)

router = APIRouter(prefix="/admin/signature-explanations", tags=["signature-explanations"])
DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


@contextmanager
def errors(db):
    try:
        yield
    except SignatureExplanationError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "signature_explanation_configuration_conflict") from None


def _audit(db, principal, action, identifier):
    db.add(AccessAudit(actor_kind="admin_session", actor_id=principal.username or "unknown", action=action,
        resource_type="signature_explanation_version", resource_id=identifier))


@router.get("")
def list_versions(db: DbSession, principal: Admin, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    with errors(db):
        state = ensure_state(db)
        total = db.scalar(select(func.count()).select_from(SignatureExplanationVersion)) or 0
        rows = db.scalars(select(SignatureExplanationVersion).order_by(
            SignatureExplanationVersion.version_no.desc(), SignatureExplanationVersion.id).offset(offset).limit(limit)).all()
        result = {"items": [version_summary(row) for row in rows], "total": total, "limit": limit, "offset": offset,
            "active_version_id": state.active_version_id, "revision": state.revision,
            "activation_requires_production_review": True,
            "limits": {"entries": MAX_ENTRIES, "catalog_bytes": MAX_CATALOG_BYTES,
                       "signature_explanation_chars": 2000, "reference_source_chars": 1000}}
        _audit(db, principal, "list_signature_explanations", "all")
        db.commit()
        return result


@router.post("", status_code=201)
def save_version(payload: SignatureExplanationCreate, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = create_version(db, request.app.state.crypto, **payload.model_dump(), actor=principal.username or "unknown")
        result = version_detail(row, request.app.state.crypto)
        _audit(db, principal, "create_signature_explanation", row.id)
        db.commit()
        return result


@router.post("/match")
def preview_match(payload: SignatureExplanationMatch, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = get_version(db, payload.version_id)
        snapshot = snapshot_for_version(db, request.app.state.crypto, row.id)
        entry = match_snapshot(snapshot, payload.waf_vendor, payload.signature)
        result = {"matched": entry is not None, "entry": entry, "version": version_summary(row)}
        _audit(db, principal, "preview_signature_explanation", row.id)
        db.commit()
        return result


@router.get("/{version_id}")
def detail(version_id: str, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        result = version_detail(get_version(db, version_id), request.app.state.crypto)
        _audit(db, principal, "view_signature_explanation", version_id)
        db.commit()
        return result


@router.post("/{version_id}/activate")
def direct_activation_forbidden(version_id: str, principal: Admin):
    raise HTTPException(409, "production_promotion_required")
