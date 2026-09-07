from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..database import get_db
from ..internal_egress_schemas import InternalEgressCreate, InternalEgressResponse, InternalEgressUpdate
from ..models import AccessAudit, InternalEgressTarget
from ..security import Principal, require_scope
from ..services.internal_egress import InternalEgressError, create_target, delete_target, to_egress_response, update_target


router = APIRouter(prefix="/admin/internal-egress", tags=["internal-egress"])
DbSession = Annotated[Session, Depends(get_db)]
AdminPrincipal = Annotated[Principal, Depends(require_scope("admin"))]


def audit(db: Session, principal: Principal, action: str, target_id: str) -> None:
    db.add(AccessAudit(actor_kind=principal.kind, actor_id=principal.username or "unknown",
                       action=action, resource_type="internal_egress_target", resource_id=target_id))


def api_error(db: Session, exc: Exception) -> HTTPException:
    db.rollback()
    if isinstance(exc, InternalEgressError):
        return HTTPException(status_code=exc.status_code, detail=exc.code)
    if isinstance(exc, IntegrityError):
        return HTTPException(status_code=409, detail="internal_egress_target_exists")
    return HTTPException(status_code=409, detail="internal_egress_changed")


@router.get("", response_model=list[InternalEgressResponse])
def list_targets(db: DbSession, _principal: AdminPrincipal) -> list[InternalEgressResponse]:
    rows = db.scalars(select(InternalEgressTarget).order_by(InternalEgressTarget.ip_address, InternalEgressTarget.port)).all()
    return [to_egress_response(db, row) for row in rows]


@router.post("", response_model=InternalEgressResponse, status_code=201)
def create_egress(payload: InternalEgressCreate, db: DbSession, principal: AdminPrincipal) -> InternalEgressResponse:
    try:
        target = create_target(db, payload)
        result = to_egress_response(db, target)
        audit(db, principal, "create_internal_egress", target.id)
        db.commit()
        return result
    except (InternalEgressError, IntegrityError, OperationalError) as exc:
        raise api_error(db, exc) from None


@router.put("/{target_id}", response_model=InternalEgressResponse)
def update_egress(target_id: str, payload: InternalEgressUpdate, db: DbSession, principal: AdminPrincipal) -> InternalEgressResponse:
    try:
        target = update_target(db, target_id, payload)
        result = to_egress_response(db, target)
        audit(db, principal, "update_internal_egress", target.id)
        db.commit()
        return result
    except (InternalEgressError, IntegrityError, OperationalError) as exc:
        raise api_error(db, exc) from None


@router.delete("/{target_id}", status_code=204)
def delete_egress(
    target_id: str, db: DbSession, principal: AdminPrincipal,
    expected_revision: Annotated[int, Query(ge=1, le=2**63 - 1)],
) -> Response:
    try:
        delete_target(db, target_id, expected_revision)
        audit(db, principal, "delete_internal_egress", target_id)
        db.commit()
        return Response(status_code=204)
    except (InternalEgressError, IntegrityError, OperationalError) as exc:
        raise api_error(db, exc) from None
