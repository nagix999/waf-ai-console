from typing import Annotated
import hashlib
import json

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AccessAudit, ModelProfileStatus, ModelTestMode, ModelTestStatus, VLLMProfile, VLLMTestRun
from ..schemas import ModelProfileAssignment, VLLMProfileCreate, VLLMProfileResponse, VLLMProfileUpdate, VLLMTestCreate, VLLMTestRunResponse
from ..security import Principal, require_scope
from ..services.analysis import AnalysisIngestError
from ..services.internal_egress import InternalEgressError, allowed_targets_from_db, lock_egress_mutation
from ..services.vllm_profiles import (
    TargetNotAllowedError,
    assignment_block_reason,
    normalize_and_validate_profile_url,
    profile_fingerprint,
    to_profile_response,
    to_test_response,
    validate_profile_provider_settings,
)

router = APIRouter(prefix="/model-profiles", tags=["model-profiles"])
DbSession = Annotated[Session, Depends(get_db)]
AdminPrincipal = Annotated[Principal, Depends(require_scope("admin"))]


def begin_test_read_snapshot(db: Session) -> None:
    # SQLite's legacy SELECT behavior otherwise allows status counts and
    # reference comparisons from different worker commits in one response.
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


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


def validate_profile_for_request(profile: VLLMProfile, request: Request, db: Session) -> str:
    try:
        normalized_url = normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
        has_api_key = bool(profile.api_key_ciphertext)
        if profile.provider == "openai" and has_api_key:
            try:
                api_key = request.app.state.crypto.decrypt_text(profile.api_key_ciphertext)
            except ValueError:
                raise TargetNotAllowedError("model_profile_api_key_unavailable") from None
            has_api_key = bool(api_key) and not any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in api_key)
        validate_profile_provider_settings(profile, has_api_key=has_api_key)
        return normalized_url
    except InternalEgressError as exc:
        raise HTTPException(status_code=503, detail="internal_egress_configuration_invalid") from None
    except TargetNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None


@router.get("", response_model=list[VLLMProfileResponse])
def list_profiles(db: DbSession, _principal: AdminPrincipal) -> list[VLLMProfileResponse]:
    begin_test_read_snapshot(db)
    profiles = db.scalars(select(VLLMProfile).order_by(VLLMProfile.name)).all()
    return [to_profile_response(profile, db) for profile in profiles]


@router.post("", response_model=VLLMProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: VLLMProfileCreate,
    request: Request,
    db: DbSession,
    principal: AdminPrincipal,
) -> VLLMProfileResponse:
    lock_egress_mutation(db)
    if payload.provider == "openai" and "model_name" not in payload.model_fields_set:
        raise HTTPException(status_code=422, detail="openai_model_name_required")
    profile = VLLMProfile(
        name=payload.name,
        provider=payload.provider.value,
        external_data_approved=payload.external_data_approved,
        base_url=payload.base_url,
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
    profile.base_url = validate_profile_for_request(profile, request, db)
    db.add(profile)
    try:
        db.flush()
        audit(db, principal, "create_vllm_profile", profile.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model_profile_name_exists") from exc
    db.refresh(profile)
    return to_profile_response(profile, db)


@router.put("/{profile_id}", response_model=VLLMProfileResponse)
def update_profile(
    profile_id: str,
    payload: VLLMProfileUpdate,
    request: Request,
    db: DbSession,
    principal: AdminPrincipal,
) -> VLLMProfileResponse:
    lock_egress_mutation(db)
    profile = get_profile_or_404(db, profile_id)
    if profile.status == ModelProfileStatus.production.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="production_profile_is_immutable")
    if profile.is_test:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="test_profile_is_immutable")
    changes = payload.model_dump(exclude_unset=True, mode="json")
    provider = changes.get("provider", profile.provider)
    provider_changed = provider != profile.provider
    if provider_changed:
        if "model_name" not in changes:
            raise HTTPException(status_code=422, detail="provider_change_requires_model_name")
        if provider == "openai":
            if "api_key" not in changes or not changes["api_key"]:
                raise HTTPException(status_code=422, detail="openai_api_key_required")
            if changes.get("external_data_approved") is not True:
                raise HTTPException(status_code=422, detail="openai_external_data_approval_required")
        else:
            # A provider switch must never silently send the previous provider's
            # credential to a different service. vLLM permits an explicit new key.
            changes.setdefault("api_key", None)
            changes.setdefault("external_data_approved", False)
    if "api_key" in changes:
        api_key = changes.pop("api_key")
        changes["api_key_ciphertext"] = request.app.state.crypto.encrypt_text(api_key) if api_key else None
        changes["encryption_key_version"] = request.app.state.crypto.key_version if api_key else None
    # Validate the merged configuration before touching an existing verified row.
    candidate = VLLMProfile(**{
        column.name: changes.get(column.name, getattr(profile, column.name))
        for column in VLLMProfile.__table__.columns
    })
    if candidate.max_output_tokens >= candidate.context_window:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="max_output_tokens_must_be_less_than_context_window")
    changes["base_url"] = validate_profile_for_request(candidate, request, db)
    for field, value in changes.items():
        setattr(profile, field, value)
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
    return to_profile_response(profile, db)


@router.post("/{profile_id}/disable", response_model=VLLMProfileResponse)
def disable_profile(profile_id: str, db: DbSession, principal: AdminPrincipal) -> VLLMProfileResponse:
    lock_egress_mutation(db)
    profile = get_profile_or_404(db, profile_id)
    profile.status = ModelProfileStatus.disabled.value
    profile.is_test = False
    audit(db, principal, "disable_vllm_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return to_profile_response(profile, db)


@router.post("/{profile_id}/enable", response_model=VLLMProfileResponse)
def enable_profile(profile_id: str, request: Request, db: DbSession, principal: AdminPrincipal) -> VLLMProfileResponse:
    lock_egress_mutation(db)
    profile = get_profile_or_404(db, profile_id)
    if profile.status != ModelProfileStatus.disabled.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model_profile_is_not_disabled")
    validate_profile_for_request(profile, request, db)
    profile.status = ModelProfileStatus.draft.value
    profile.is_test = False
    profile.last_verified_at = None
    audit(db, principal, "enable_vllm_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return to_profile_response(profile, db)


@router.post("/{profile_id}/tests", response_model=VLLMTestRunResponse, status_code=status.HTTP_202_ACCEPTED)
def enqueue_test(
    profile_id: str,
    payload: VLLMTestCreate,
    request: Request,
    db: DbSession,
    principal: AdminPrincipal,
) -> VLLMTestRunResponse:
    lock_egress_mutation(db)
    request_hash = hashlib.sha256(json.dumps({"profile_id": profile_id,
        **payload.model_dump(mode="json", exclude={"idempotency_key"})}, sort_keys=True).encode()).hexdigest()
    if payload.idempotency_key:
        previous = db.scalar(select(VLLMTestRun).where(VLLMTestRun.idempotency_key == payload.idempotency_key))
        if previous:
            if previous.request_hash != request_hash:
                raise HTTPException(409, "test_run_idempotency_conflict")
            return to_test_response(previous, db)
    profile = get_profile_or_404(db, profile_id)
    if profile.status == ModelProfileStatus.disabled.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="disabled_profile_cannot_be_tested")
    if payload.expected_profile_fingerprint is not None and payload.expected_profile_fingerprint != profile_fingerprint(profile):
        raise HTTPException(status_code=409, detail="model_profile_changed_reconfirm")
    validate_profile_for_request(profile, request, db)
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
        include_dataset=payload.include_dataset,
        name=payload.name, idempotency_key=payload.idempotency_key, request_hash=request_hash,
    )
    db.add(test_run)
    from ..services.input_schemas import InputSchemaError, pin_schema
    try:
        pin_schema(db, request.app.state.crypto, test_run, selection_origin="model_validation")
    except InputSchemaError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None
    db.flush()
    if payload.include_dataset:
        from ..services.model_validation import enqueue_dataset, DatasetError
        try:
            enqueue_dataset(db, request.app.state.crypto, test_run, principal.username or "admin", request.app.state.settings)
        except DatasetError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=exc.code) from None
        except AnalysisIngestError as exc:
            db.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
    audit(db, principal, f"enqueue_vllm_{payload.mode.value}_test", profile.id)
    db.commit()
    begin_test_read_snapshot(db)
    db.refresh(test_run)
    return to_test_response(test_run, db)


@router.get("/{profile_id}/tests", response_model=list[VLLMTestRunResponse])
def list_tests(profile_id: str, db: DbSession, _principal: AdminPrincipal) -> list[VLLMTestRunResponse]:
    begin_test_read_snapshot(db)
    get_profile_or_404(db, profile_id)
    rows = db.scalars(
        select(VLLMTestRun)
        .where(VLLMTestRun.profile_id == profile_id)
        .order_by(desc(VLLMTestRun.created_at))
        .limit(50)
    ).all()
    return [to_test_response(row, db) for row in rows]


@router.get("/{profile_id}/tests/{test_id}", response_model=VLLMTestRunResponse)
def get_test(profile_id: str, test_id: str, db: DbSession, _principal: AdminPrincipal) -> VLLMTestRunResponse:
    begin_test_read_snapshot(db)
    row = db.get(VLLMTestRun, test_id)
    if row is None or row.profile_id != profile_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="model_profile_test_not_found")
    return to_test_response(row, db)


@router.post("/{profile_id}/promote", response_model=VLLMProfileResponse)
def promote_profile(profile_id: str, request: Request, db: DbSession, principal: AdminPrincipal,
                    payload: ModelProfileAssignment | None = Body(default=None)) -> VLLMProfileResponse:
    lock_egress_mutation(db)
    profile = get_profile_or_404(db, profile_id)
    if profile.status not in {ModelProfileStatus.verified.value, ModelProfileStatus.production.value}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="only_verified_profile_can_be_promoted")
    if payload is not None and payload.expected_profile_fingerprint != profile_fingerprint(profile):
        raise HTTPException(409, "model_profile_changed_reconfirm")
    validate_profile_for_request(profile, request, db)
    reason = assignment_block_reason(db, profile)
    if reason:
        raise HTTPException(409, reason)
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
    return to_profile_response(profile, db)


@router.post("/{profile_id}/assign-test", response_model=VLLMProfileResponse)
def assign_test_profile(profile_id: str, payload: ModelProfileAssignment, request: Request,
                        db: DbSession, principal: AdminPrincipal) -> VLLMProfileResponse:
    lock_egress_mutation(db)
    profile = get_profile_or_404(db, profile_id)
    if payload.expected_profile_fingerprint != profile_fingerprint(profile):
        raise HTTPException(409, "model_profile_changed_reconfirm")
    validate_profile_for_request(profile, request, db)
    reason = assignment_block_reason(db, profile)
    if reason:
        raise HTTPException(409, reason)
    current = db.scalar(select(VLLMProfile).where(VLLMProfile.is_test.is_(True), VLLMProfile.id != profile.id))
    if current:
        current.is_test = False
        db.flush()
    profile.is_test = True
    audit(db, principal, "assign_test_llm_profile", profile.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "test_profile_changed_concurrently") from None
    db.refresh(profile)
    return to_profile_response(profile, db)


@router.post("/{profile_id}/unassign-test", response_model=VLLMProfileResponse)
def unassign_test_profile(profile_id: str, payload: ModelProfileAssignment,
                          db: DbSession, principal: AdminPrincipal) -> VLLMProfileResponse:
    lock_egress_mutation(db)
    profile = get_profile_or_404(db, profile_id)
    if payload.expected_profile_fingerprint != profile_fingerprint(profile):
        raise HTTPException(409, "model_profile_changed_reconfirm")
    # Removal remains possible even after egress/credentials are revoked.
    # A stale removal of another profile never clears the current Test role.
    profile.is_test = False
    audit(db, principal, "unassign_test_llm_profile", profile.id)
    db.commit()
    db.refresh(profile)
    return to_profile_response(profile, db)
