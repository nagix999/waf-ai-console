from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AccessAudit, PromptPolicyVersion
from ..prompt_schemas import (
    PromptPolicyActivate, PromptPolicyActivationResponse, PromptPolicyCreate,
    PromptPolicyDetail, PromptPolicyListResponse,
)
from ..security import Principal, require_scope
from ..services.prompt_policies import (
    PromptPolicyError, activate_policy_version, create_policy_version, get_policy_state,
    get_policy_version, to_policy_detail, to_policy_summary,
)


router = APIRouter(prefix="/admin/prompt-policies", tags=["prompt-policies"])
DbSession = Annotated[Session, Depends(get_db)]
AdminPrincipal = Annotated[Principal, Depends(require_scope("admin"))]


def audit(db: Session, principal: Principal, action: str, resource_id: str) -> None:
    db.add(AccessAudit(
        actor_kind=principal.kind,
        actor_id=principal.username or "unknown",
        action=action,
        resource_type="prompt_policy_version",
        resource_id=resource_id,
    ))


def api_error(db: Session, exc: PromptPolicyError) -> HTTPException:
    db.rollback()
    return HTTPException(status_code=exc.status_code, detail=exc.code)


@router.get("", response_model=PromptPolicyListResponse)
def list_policies(request: Request, db: DbSession, principal: AdminPrincipal) -> PromptPolicyListResponse:
    from ..agent.prompts import FIXED_INSTRUCTIONS, FIXED_RULES_VERSION

    try:
        state = get_policy_state(db, request.app.state.crypto)
        versions = db.scalars(select(PromptPolicyVersion).order_by(PromptPolicyVersion.version_number.desc())).all()
        result = PromptPolicyListResponse(
            items=[to_policy_summary(version) for version in versions],
            active_version_id=state.active_version_id,
            revision=state.revision,
            fixed_instructions=FIXED_INSTRUCTIONS,
            fixed_rules_version=FIXED_RULES_VERSION,
        )
        audit(db, principal, "list_prompt_policies", "all")
        db.commit()
        return result
    except PromptPolicyError as exc:
        raise api_error(db, exc) from None


@router.get("/{version_id}", response_model=PromptPolicyDetail)
def get_policy(version_id: str, request: Request, db: DbSession, principal: AdminPrincipal) -> PromptPolicyDetail:
    try:
        result = to_policy_detail(get_policy_version(db, version_id), request.app.state.crypto)
        audit(db, principal, "view_prompt_policy", version_id)
        db.commit()
        return result
    except PromptPolicyError as exc:
        raise api_error(db, exc) from None


@router.post("", response_model=PromptPolicyDetail, status_code=status.HTTP_201_CREATED)
def create_policy(payload: PromptPolicyCreate, request: Request, db: DbSession, principal: AdminPrincipal) -> PromptPolicyDetail:
    try:
        version = create_policy_version(db, request.app.state.crypto, payload, principal.username or "unknown")
        result = to_policy_detail(version, request.app.state.crypto)
        audit(db, principal, "create_prompt_policy", version.id)
        db.commit()
        return result
    except PromptPolicyError as exc:
        raise api_error(db, exc) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="prompt_version_created_concurrently") from None


@router.post("/{version_id}/activate", response_model=PromptPolicyActivationResponse)
def activate_policy(
    version_id: str, payload: PromptPolicyActivate, request: Request, db: DbSession, principal: AdminPrincipal,
) -> PromptPolicyActivationResponse:
    try:
        state = activate_policy_version(db, request.app.state.crypto, version_id, payload.expected_revision)
        result = PromptPolicyActivationResponse(active_version_id=state.active_version_id, revision=state.revision)
        audit(db, principal, "activate_prompt_policy", version_id)
        db.commit()
        return result
    except PromptPolicyError as exc:
        raise api_error(db, exc) from None
