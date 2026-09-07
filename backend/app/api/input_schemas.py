"""Administrator-only schema editing, local sample checks, explicit activation."""
import hashlib
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..input_schema_schemas import (
    InputSchemaActivate, InputSchemaActivationResponse, InputSchemaActivationSummary, InputSchemaCreate,
    InputSchemaDetail, InputSchemaListResponse, InputSchemaValidate, InputSchemaValidationResponse,
    VALIDATION_TOKEN_SECONDS,
)
from ..models import AccessAudit, InputSchemaActivation, InputSchemaVersion
from ..security import Principal, require_scope
from ..services.input_schemas import (
    InputSchemaError, activate_schema_version, create_schema_version, default_definition, get_schema_state,
    get_schema_version, load_definition, schema_bounds, to_schema_detail, to_schema_summary, validate_event,
)

router = APIRouter(prefix="/admin/input-schemas", tags=["input-schemas"])
DbSession = Annotated[Session, Depends(get_db)]
AdminPrincipal = Annotated[Principal, Depends(require_scope("admin"))]


def audit(db: Session, principal: Principal, action: str, resource_id: str) -> None:
    db.add(AccessAudit(actor_kind=principal.kind, actor_id=principal.username or "unknown", action=action,
                       resource_type="input_schema_version", resource_id=resource_id))


def api_error(db: Session, exc: InputSchemaError) -> HTTPException:
    db.rollback()
    return HTTPException(status_code=exc.status_code, detail=exc.code)


def serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.settings.session_secret, salt="input-schema-sample-validation-v1")


def token_subject(request: Request, principal: Principal, version: InputSchemaVersion, revision: int) -> dict:
    # SessionMiddleware refreshes cookie timestamps: bind a stable signed nonce,
    # not the complete cookie string. Login/logout clears this session state.
    nonce = request.session.get("input_schema_validation_session")
    if not isinstance(nonce, str):
        nonce = secrets.token_urlsafe(32)
        request.session["input_schema_validation_session"] = nonce
    return {"version_id": version.id, "content_hash": version.content_hash, "revision": revision,
            "actor": principal.username, "session": hashlib.sha256(nonce.encode("utf-8")).hexdigest()}


@router.get("", response_model=InputSchemaListResponse)
def list_schemas(request: Request, db: DbSession, principal: AdminPrincipal):
    try:
        state = get_schema_state(db, request.app.state.crypto)
        versions = db.scalars(select(InputSchemaVersion).order_by(InputSchemaVersion.version_number.desc())).all()
        result = InputSchemaListResponse(items=[to_schema_summary(version) for version in versions],
            active_version_id=state.active_version_id, revision=state.revision, default_fields=default_definition(), bounds=schema_bounds())
        audit(db, principal, "list_input_schemas", "all")
        db.commit()
        return result
    except InputSchemaError as exc:
        raise api_error(db, exc) from None


@router.get("/activation-history")
def activation_history(request: Request, db: DbSession, principal: AdminPrincipal):
    try:
        get_schema_state(db, request.app.state.crypto)
        rows = db.scalars(select(InputSchemaActivation).order_by(InputSchemaActivation.revision.desc()).limit(100)).all()
        result = {"items": [InputSchemaActivationSummary(**{name: getattr(row, name) for name in
                  ("id", "previous_version_id", "active_version_id", "revision", "actor_id", "created_at")}) for row in rows]}
        audit(db, principal, "list_input_schema_activations", "all")
        db.commit()
        return result
    except InputSchemaError as exc:
        raise api_error(db, exc) from None


@router.get("/{version_id}", response_model=InputSchemaDetail)
def get_schema(version_id: str, request: Request, db: DbSession, principal: AdminPrincipal):
    try:
        result = to_schema_detail(get_schema_version(db, version_id), request.app.state.crypto)
        audit(db, principal, "view_input_schema", version_id)
        db.commit()
        return result
    except InputSchemaError as exc:
        raise api_error(db, exc) from None


@router.post("", response_model=InputSchemaDetail, status_code=status.HTTP_201_CREATED)
def create_schema(payload: InputSchemaCreate, request: Request, db: DbSession, principal: AdminPrincipal):
    try:
        version = create_schema_version(db, request.app.state.crypto, payload, principal.username or "unknown")
        result = to_schema_detail(version, request.app.state.crypto)
        audit(db, principal, "create_input_schema", version.id)
        db.commit()
        return result
    except InputSchemaError as exc:
        raise api_error(db, exc) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="input_schema_created_concurrently") from None


@router.post("/{version_id}/validate", response_model=InputSchemaValidationResponse)
def validate_schema(version_id: str, payload: InputSchemaValidate, request: Request, db: DbSession, principal: AdminPrincipal):
    try:
        version = get_schema_version(db, version_id)
        state = get_schema_state(db, request.app.state.crypto)
        try:
            size = len(json.dumps(payload.event, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise InputSchemaError("input_schema_sample_invalid_json", 422) from None
        if size > request.app.state.settings.upload_max_bytes:
            raise InputSchemaError("input_schema_sample_too_large", 413)
        issues = validate_event(load_definition(version, request.app.state.crypto), payload.event)
        if not issues and len(payload.event.get("payload", "").encode("utf-8")) > request.app.state.settings.payload_max_bytes:
            issues = [{"field": "payload", "type": "payload_too_large", "message": "시스템의 원문 크기 제한을 초과했습니다."}]
        token = serializer(request).dumps(token_subject(request, principal, version, state.revision)) if not issues else None
        audit(db, principal, "validate_input_schema_sample", version.id)
        db.commit()
        return InputSchemaValidationResponse(valid=not issues, issues=issues, validation_token=token,
                                              expires_in_seconds=VALIDATION_TOKEN_SECONDS if token else None)
    except InputSchemaError as exc:
        raise api_error(db, exc) from None


@router.post("/{version_id}/activate", response_model=InputSchemaActivationResponse)
def activate_schema(version_id: str, payload: InputSchemaActivate, request: Request, db: DbSession, principal: AdminPrincipal):
    try:
        version = get_schema_version(db, version_id)
        try:
            subject = serializer(request).loads(payload.validation_token, max_age=VALIDATION_TOKEN_SECONDS)
        except BadData:
            raise InputSchemaError("input_schema_sample_validation_required", 422) from None
        if subject != token_subject(request, principal, version, payload.expected_revision):
            raise InputSchemaError("input_schema_sample_validation_required", 422)
        state = activate_schema_version(db, request.app.state.crypto, version_id, payload.expected_revision, principal.username or "unknown")
        result = InputSchemaActivationResponse(active_version_id=state.active_version_id, revision=state.revision)
        audit(db, principal, "activate_input_schema", version_id)
        db.commit()
        return result
    except InputSchemaError as exc:
        raise api_error(db, exc) from None
