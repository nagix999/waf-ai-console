from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..api_key_schemas import ServiceApiKeyCreate, ServiceApiKeyIssued, ServiceApiKeyItem, ServiceApiKeyList, ServiceApiKeyRename, ServiceApiKeyDelete, ServiceApiKeyDeletionPreview
from ..database import get_db
from ..models import AccessAudit, ServiceApiKey
from ..security import Principal, require_scope
from ..services.service_api_keys import ServiceApiKeyError, delete_key, issue_key, rename_key, revoke_key, to_key_item
from ..services.key_analysis_deletion import preview, purge
from ..services.test_runs import read_snapshot, write_lock
from ..services.change_events import record_change, key_metadata


router = APIRouter(prefix="/admin/service-api-keys", tags=["service-api-keys"])
DbSession = Annotated[Session, Depends(get_db)]
AdminPrincipal = Annotated[Principal, Depends(require_scope("admin"))]
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def no_store(response: Response) -> None:
    response.headers.update(NO_STORE_HEADERS)


def audit(db: Session, principal: Principal, action: str, key_id: str, before=None) -> None:
    record_change(db, category="integration", actor=principal.username or "unknown", action=action,
        resource_type="service_api_key", resource_id=key_id, before=before,
        after=key_metadata(db.get(ServiceApiKey, key_id)))
    db.add(AccessAudit(actor_kind=principal.kind, actor_id=principal.username or "unknown",
                       action=action, resource_type="service_api_key", resource_id=key_id))


def api_error(db: Session, exc: Exception) -> HTTPException:
    db.rollback()
    if isinstance(exc, ServiceApiKeyError):
        return HTTPException(status_code=exc.status_code, detail=exc.code, headers=NO_STORE_HEADERS)
    if isinstance(exc, IntegrityError):
        return HTTPException(status_code=409, detail="service_api_key_name_exists", headers=NO_STORE_HEADERS)
    return HTTPException(status_code=503, detail="service_api_key_storage_unavailable", headers=NO_STORE_HEADERS)


@router.get("", response_model=ServiceApiKeyList)
def list_keys(response: Response, db: DbSession, _principal: AdminPrincipal) -> ServiceApiKeyList:
    no_store(response)
    try:
        rows = db.scalars(select(ServiceApiKey).where(ServiceApiKey.deleted_at.is_(None)).order_by(ServiceApiKey.name, ServiceApiKey.id)).all()
    except SQLAlchemyError as exc:
        raise api_error(db, exc) from None
    return ServiceApiKeyList(items=[to_key_item(row) for row in rows])


@router.post("", response_model=ServiceApiKeyIssued, status_code=201)
def create_key(payload: ServiceApiKeyCreate, response: Response, db: DbSession, principal: AdminPrincipal) -> ServiceApiKeyIssued:
    no_store(response)
    try:
        key, raw = issue_key(db, payload, principal.username or "unknown")
        result = ServiceApiKeyIssued(item=to_key_item(key), api_key=raw)
        audit(db, principal, "issue_service_api_key", key.id)
        db.commit()
        return result
    except (ServiceApiKeyError, SQLAlchemyError) as exc:
        raise api_error(db, exc) from None


@router.patch("/{key_id}", response_model=ServiceApiKeyItem)
def update_key_name(key_id: str, payload: ServiceApiKeyRename, response: Response, db: DbSession, principal: AdminPrincipal) -> ServiceApiKeyItem:
    no_store(response)
    try:
        write_lock(db)
        before = key_metadata(db.get(ServiceApiKey, key_id))
        key = rename_key(db, key_id, payload.name)
        result = to_key_item(key)
        audit(db, principal, "rename_service_api_key", key.id, before)
        db.commit()
        return result
    except (ServiceApiKeyError, SQLAlchemyError) as exc:
        raise api_error(db, exc) from None


@router.post("/{key_id}/revoke", response_model=ServiceApiKeyItem)
def revoke_service_key(key_id: str, response: Response, db: DbSession, principal: AdminPrincipal) -> ServiceApiKeyItem:
    no_store(response)
    try:
        write_lock(db)
        before = key_metadata(db.get(ServiceApiKey, key_id))
        key, changed = revoke_key(db, key_id, principal.username or "unknown")
        result = to_key_item(key)
        if changed:
            audit(db, principal, "revoke_service_api_key", key.id, before)
        db.commit()
        return result
    except (ServiceApiKeyError, SQLAlchemyError) as exc:
        raise api_error(db, exc) from None


@router.delete("/{key_id}", status_code=204)
def delete_service_key(key_id: str, db: DbSession, principal: AdminPrincipal, payload: ServiceApiKeyDelete | None = None) -> Response:
    try:
        write_lock(db)
        key = db.get(ServiceApiKey, key_id, populate_existing=True)
        before = key_metadata(key)
        if key is None:
            raise ServiceApiKeyError("service_api_key_not_found", 404)
        if key.purpose == "production" and (payload is None or payload.confirm_name != key.name):
            raise ServiceApiKeyError("service_api_key_name_confirmation_required", 422)
        if payload and payload.confirm_name != key.name:
            raise ServiceApiKeyError("service_api_key_name_confirmation_required", 422)
        # A repeated delete never gains authority to purge data after the key
        # has already been deleted with the preserve option.
        if payload and payload.delete_analyses and key.deleted_at is not None:
            raise ServiceApiKeyError("service_api_key_not_found", 404)
        if payload and payload.delete_analyses:
            purge(db, key_id, payload, principal.username or "unknown")
        if delete_key(db, key_id, principal.username or "unknown"):
            audit(db, principal, "delete_service_api_key", key_id, before)
        db.commit()
        return Response(status_code=204, headers=NO_STORE_HEADERS)
    except (ServiceApiKeyError, SQLAlchemyError) as exc:
        raise api_error(db, exc) from None


@router.get("/{key_id}/deletion-preview", response_model=ServiceApiKeyDeletionPreview)
def deletion_preview(key_id: str, response: Response, db: DbSession, _principal: AdminPrincipal):
    no_store(response)
    try:
        read_snapshot(db)
        return preview(db, key_id)
    except (ServiceApiKeyError, SQLAlchemyError) as exc:
        raise api_error(db, exc) from None
