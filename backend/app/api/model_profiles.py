from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AccessAudit, ModelProfileStatus, ModelTestMode, ModelTestStatus, VLLMProfile, VLLMTestRun
from ..schemas import VLLMProfileCreate, VLLMProfileResponse, VLLMProfileUpdate, VLLMTestCreate, VLLMTestRunResponse
from ..security import Principal, require_scope
from ..services.vllm_profiles import (
    TargetNotAllowedError,
    normalize_and_validate_base_url,
    profile_fingerprint,
    to_profile_response,
    to_test_response,
)

router = APIRouter(prefix="/model-profiles", tags=["model-profiles"])
DbSession = Annotated[Session, Depends(get_db)]
AdminPrincipal = Annotated[Principal, Depends(require_scope("admin"))]


def audit(db: Session, principal: Principal, action: str, profile_id: str) -> None:
    db.add(
        AccessAudit(
            actor_kind=principal.kind,
            actor_id=principal.username or principal.source_system or "unknown",
            action=action,
            resource_type="vllm_profile",
            resource_id=profile_id,
        )
    )


def get_profile_or_404(db: Session, profile_id: str) -> VLLMProfile:
    profile = db.get(VLLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model_profile_not_found")
    return profile


@router.get("", response_model=list[VLLMProfileResponse])
def list_profiles(db: DbSession, _principal: AdminPrincipal) -> list[VLLMProfileResponse]:
    profiles = db.scalars(select(VLLMProfile).order_by(VLLMProfile.name)).all()
    return [to_profile_response(profile) for profile in profiles]


@router.post("", response_model=VLLMProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: VLLMProfileCreate,
    request: Request,
    db: DbSession,
    principal: AdminPrincipal,
) -> VLLMProfileResponse:
    try:
        base_url = normalize_and_validate_base_url(payload.base_url, request.app.state.settings.vllm_allowed_targets)
    except TargetNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    profile = VLLMProfile(
        name=payload.name,
        base_url=base_url,
        model_name=payload.model_name,
        api_key_ciphertext=request.app.state.crypto.encrypt_text(payload.api_key) if payload.api_key else None,
        encryption_key_version=request.app.state.crypto.key_version if payload.api_key else None,
        timeout_seconds=payload.timeout_seconds,
        context_window=payload.context_window,
        max_output_tokens=payload.max_output_tokens,
        test_concurrency=payload.test_concurrency,
        tls_verify=payload.tls_verify,
        status=ModelProfileStatus.draft.value,
    )
    db.add(profile)
    try:
        db.flush()
        audit(db, principal, "create_vllm_profile", profile.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model_profile_name_exists") from exc
    db.refresh(profile)
    return to_profile_response(profile)


@router.put("/{profile_id}", response_model=VLLMProfileResponse)
def update_profile(
    profile_id: str,
    payload: VLLMProfileUpdate,
    request: Request,
    db: DbSession,
    principal: AdminPrincipal,
) -> VLLMProfileResponse:
    profile = get_profile_or_404(db, profile_id)
    if profile.status == ModelProfileStatus.production.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="production_profile_is_immutable")
    changes = payload.model_dump(exclude_unset=True)
    if "base_url" in changes:
        try:
            changes["base_url"] = normalize_and_validate_base_url(
                changes["base_url"], request.app.state.settings.vllm_allowed_targets
            )
        except TargetNotAllowedError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if "api_key" in changes:
        api_key = changes.pop("api_key")
        profile.api_key_ciphertext = request.app.state.crypto.encrypt_text(api_key) if api_key else None
        profile.encryption_key_version = request.app.state.crypto.key_version if api_key else None
    for field, value in changes.items():
        setattr(profile, field, value)
    if profile.max_output_tokens >= profile.context_window:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="max_output_tokens_must_be_less_than_context_window")
    if profile.status != ModelProfileStatus.disabled.value:
        profile.status = ModelProfileStatus.draft.value
    profile.last_verified_at = None
    audit(db, principal, "update_vllm_profile", profile.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model_profile_name_exists") from exc
    db.refresh(profile)
    return to_profile_response(profile)


@router.post("/{profile_id}/disable", response_model=VLLMProfileResponse)
def disable_profile(profile_id: str, db: DbSession, principal: AdminPrincipal) -> VLLMProfileResponse:
    profile = get_profile_or_404(db, profile_id)
    profile.status = ModelProfileStatus.disabled.value
    audit(db, principal, "disable_vllm_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return to_profile_response(profile)


@router.post("/{profile_id}/enable", response_model=VLLMProfileResponse)
def enable_profile(profile_id: str, db: DbSession, principal: AdminPrincipal) -> VLLMProfileResponse:
    profile = get_profile_or_404(db, profile_id)
    if profile.status != ModelProfileStatus.disabled.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model_profile_is_not_disabled")
    profile.status = ModelProfileStatus.draft.value
    profile.last_verified_at = None
    audit(db, principal, "enable_vllm_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return to_profile_response(profile)


@router.post("/{profile_id}/tests", response_model=VLLMTestRunResponse, status_code=status.HTTP_202_ACCEPTED)
def enqueue_test(
    profile_id: str,
    payload: VLLMTestCreate,
    db: DbSession,
    principal: AdminPrincipal,
) -> VLLMTestRunResponse:
    profile = get_profile_or_404(db, profile_id)
    if profile.status == ModelProfileStatus.disabled.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="disabled_profile_cannot_be_tested")
    active = db.scalar(
        select(VLLMTestRun).where(
            VLLMTestRun.profile_id == profile.id,
            VLLMTestRun.status.in_([ModelTestStatus.pending.value, ModelTestStatus.running.value]),
        )
    )
    if active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model_profile_test_already_running")
    test_run = VLLMTestRun(
        profile_id=profile.id,
        mode=payload.mode.value,
        status=ModelTestStatus.pending.value,
        profile_fingerprint=profile_fingerprint(profile),
    )
    db.add(test_run)
    db.flush()
    audit(db, principal, f"enqueue_vllm_{payload.mode.value}_test", profile.id)
    db.commit()
    db.refresh(test_run)
    return to_test_response(test_run)


@router.get("/{profile_id}/tests", response_model=list[VLLMTestRunResponse])
def list_tests(profile_id: str, db: DbSession, _principal: AdminPrincipal) -> list[VLLMTestRunResponse]:
    get_profile_or_404(db, profile_id)
    rows = db.scalars(
        select(VLLMTestRun)
        .where(VLLMTestRun.profile_id == profile_id)
        .order_by(desc(VLLMTestRun.created_at))
        .limit(50)
    ).all()
    return [to_test_response(row) for row in rows]


@router.get("/{profile_id}/tests/{test_id}", response_model=VLLMTestRunResponse)
def get_test(profile_id: str, test_id: str, db: DbSession, _principal: AdminPrincipal) -> VLLMTestRunResponse:
    row = db.get(VLLMTestRun, test_id)
    if row is None or row.profile_id != profile_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model_profile_test_not_found")
    return to_test_response(row)


@router.post("/{profile_id}/promote", response_model=VLLMProfileResponse)
def promote_profile(profile_id: str, db: DbSession, principal: AdminPrincipal) -> VLLMProfileResponse:
    profile = get_profile_or_404(db, profile_id)
    if profile.status != ModelProfileStatus.verified.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="only_verified_profile_can_be_promoted")
    passed_full = db.scalar(
        select(VLLMTestRun)
        .where(
            VLLMTestRun.profile_id == profile.id,
            VLLMTestRun.mode == ModelTestMode.full.value,
            VLLMTestRun.status == ModelTestStatus.passed.value,
            VLLMTestRun.profile_fingerprint == profile_fingerprint(profile),
        )
        .order_by(desc(VLLMTestRun.completed_at))
        .limit(1)
    )
    if passed_full is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="matching_full_test_required")
    current = db.scalar(
        select(VLLMProfile).where(
            VLLMProfile.status == ModelProfileStatus.production.value,
            VLLMProfile.id != profile.id,
        )
    )
    if current:
        current.status = ModelProfileStatus.verified.value
        db.flush()
    profile.status = ModelProfileStatus.production.value
    audit(db, principal, "promote_vllm_profile", profile.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="production_profile_changed_concurrently") from exc
    db.refresh(profile)
    return to_profile_response(profile)
