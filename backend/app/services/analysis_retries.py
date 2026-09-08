"""Explicit, cost-acknowledged retries without changing the failed observation.

Execution provenance is encrypted, server-owned and never part of LLM input.
Historical traces are used only when every required setting is recorded; no
fallback to today's role assignment, policy, threshold or missing schema.
"""
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccessAudit, AgentRun, AgentStep, Analysis, VLLMProfile
from ..retry_schemas import RetryEligibility, RetryResponse
from .crypto import CryptoService
from .input_schemas import InputSchemaError, read_schema_snapshot
from .internal_egress import allowed_targets_from_db
from .prompt_snapshots import PromptSnapshotError, load_analysis_prompt
from .label_fields import LABEL_FIELDS
from .vllm_profiles import (TargetNotAllowedError, assignment_block_reason, normalize_and_validate_profile_url,
                           profile_fingerprint, validate_profile_provider_settings)


class RetryError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


class ExecutionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: int = 1
    execution_mode: str = Field(pattern=r"^moduagent$")
    profile_id: str = Field(min_length=1, max_length=36)
    profile_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_confidence_threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    instructions_hash: str
    input_schema_version_id: str
    source_system: str
    event_id: str
    event_fingerprint: str | None


def make_execution_snapshot(analysis, profile, prompt, threshold) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        execution_mode="moduagent", profile_id=profile.id, profile_fingerprint=profile_fingerprint(profile),
        verifier_confidence_threshold=threshold, instructions_hash=prompt.instructions_hash,
        input_schema_version_id=analysis.input_schema_version_id,
        source_system=analysis.source_system, event_id=analysis.event_id,
        event_fingerprint=analysis.event_fingerprint,
    )


def validate_snapshot_binding(analysis, crypto, snapshot):
    try:
        prompt = load_analysis_prompt(analysis, crypto)
        read_schema_snapshot(crypto, analysis)
    except (PromptSnapshotError, InputSchemaError, ValueError):
        raise RetryError("retry_input_or_prompt_snapshot_unavailable") from None
    if (snapshot.schema_version != 1 or snapshot.instructions_hash != prompt.instructions_hash
            or snapshot.input_schema_version_id != analysis.input_schema_version_id
            or snapshot.source_system != analysis.source_system or snapshot.event_id != analysis.event_id
            or snapshot.event_fingerprint != analysis.event_fingerprint):
        raise RetryError("retry_execution_snapshot_invalid")
    validate_event_integrity(analysis, crypto)
    return snapshot


def validate_event_integrity(analysis, crypto):
    if not isinstance(analysis.extra_fields, dict) or LABEL_FIELDS.intersection(analysis.extra_fields):
        raise RetryError("retry_event_reference_contamination")
    from ..schemas import AnalysisInput
    from .analysis import AnalysisIngestError, event_fingerprint
    try:
        document = {**analysis.extra_fields,
                    **{name: getattr(analysis, name) for name in AnalysisInput.model_fields if name != "payload"},
                    "payload": crypto.decrypt_text(analysis.payload_ciphertext)}
        actual = event_fingerprint(document)
    except (ValueError, TypeError, AttributeError, AnalysisIngestError):
        raise RetryError("retry_event_unavailable") from None
    if analysis.event_fingerprint is not None and actual != analysis.event_fingerprint:
        raise RetryError("retry_event_fingerprint_mismatch")


def load_execution_snapshot(analysis, crypto) -> ExecutionSnapshot:
    try:
        if not analysis.execution_snapshot_ciphertext:
            raise ValueError()
        snapshot = ExecutionSnapshot.model_validate_json(crypto.decrypt_text(analysis.execution_snapshot_ciphertext))
    except (ValueError, TypeError, ValidationError):
        raise RetryError("retry_execution_snapshot_unavailable") from None
    return validate_snapshot_binding(analysis, crypto, snapshot)


def check_profile(db, snapshot) -> VLLMProfile:
    profile = db.get(VLLMProfile, snapshot.profile_id, populate_existing=True)
    if profile is None:
        raise RetryError("retry_original_profile_missing")
    if profile.status == "disabled":
        raise RetryError("retry_original_profile_disabled")
    if profile_fingerprint(profile) != snapshot.profile_fingerprint:
        raise RetryError("retry_original_profile_changed")
    if assignment_block_reason(db, profile):
        raise RetryError("retry_original_profile_not_verified")
    try:
        normalized = normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
        validate_profile_provider_settings(profile, has_api_key=bool(profile.api_key_ciphertext))
        if normalized != profile.base_url:
            raise RetryError("retry_original_profile_changed")
    except (TargetNotAllowedError, ValueError):
        raise RetryError("retry_original_profile_target_not_allowed") from None
    return profile


def execution_request_check(engine, snapshot):
    def check():
        # Fresh short transaction on EVERY transport/repair/Verifier call.
        with Session(engine) as db:
            check_profile(db, snapshot)
    return check


def original_execution_snapshot(db, crypto, analysis):
    if analysis.execution_snapshot_ciphertext:
        return load_execution_snapshot(analysis, crypto)
    try:
        prompt = load_analysis_prompt(analysis, crypto)
        read_schema_snapshot(crypto, analysis)
    except (PromptSnapshotError, InputSchemaError, ValueError):
        raise RetryError("retry_input_or_prompt_snapshot_unavailable") from None
    # No inference from a name or current role. Older successful input steps
    # recorded the exact selected profile, even when Primary later failed.
    run = db.scalar(select(AgentRun).where(AgentRun.analysis_id == analysis.id)
                    .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1))
    if run is None or run.status != "failed":
        raise RetryError("retry_execution_snapshot_unavailable")
    steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)))
    selected = next((step.metadata_json for step in steps if step.step_type == "input" and step.status == "completed"
                     and isinstance(step.metadata_json, dict) and step.metadata_json.get("model_profile_id")), None)
    if selected is None:
        raise RetryError("retry_execution_snapshot_unavailable")
    from .test_runs import analysis_test_run
    named = analysis_test_run(db, analysis)
    threshold = None
    if named is not None and named.execution_mode == "moduagent":
        if named.profile_id != selected.get("model_profile_id") or named.profile_fingerprint != selected.get("profile_fingerprint"):
            raise RetryError("retry_execution_snapshot_invalid")
        threshold = named.profile_metadata.get("verifier_confidence_threshold")
    if threshold is None:
        threshold = next((step.metadata_json.get("confidence_threshold") for step in steps
                          if step.step_type == "policy" and step.status == "completed"
                          and isinstance(step.metadata_json, dict)), None)
    try:
        snapshot = ExecutionSnapshot(
            execution_mode="moduagent", profile_id=selected.get("model_profile_id"),
            profile_fingerprint=selected.get("profile_fingerprint"), verifier_confidence_threshold=threshold,
            instructions_hash=prompt.instructions_hash, input_schema_version_id=analysis.input_schema_version_id,
            source_system=analysis.source_system, event_id=analysis.event_id, event_fingerprint=analysis.event_fingerprint,
        )
    except ValidationError:
        raise RetryError("retry_execution_snapshot_unavailable") from None
    return validate_snapshot_binding(analysis, crypto, snapshot)


def retry_context(db, crypto, analysis, agent_mode):
    if analysis.status != "failed":
        raise RetryError("retry_requires_failed_analysis")
    if agent_mode != "moduagent":
        raise RetryError("retry_requires_moduagent_mode")
    if analysis.model_test_run_id:
        # A single retry must never restart or alter the candidate validation
        # worker's immutable benchmark/qualification boundary.
        raise RetryError("retry_model_validation_requires_new_validation")
    snapshot = original_execution_snapshot(db, crypto, analysis)
    profile = check_profile(db, snapshot)
    return snapshot, profile


def eligibility(db, crypto, analysis_id, agent_mode):
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise RetryError("analysis_not_found", 404)
    existing = db.scalar(select(Analysis.id).where(Analysis.retry_of_analysis_id == analysis.id))
    response = RetryEligibility(analysis_id=analysis.id, allowed=False, existing_retry_id=existing,
                                prompt_version=analysis.prompt_version, model_profile=analysis.model_profile)
    if existing:
        response.blocked_reason = "retry_already_created"
        return response
    try:
        _snapshot, profile = retry_context(db, crypto, analysis, agent_mode)
    except RetryError as exc:
        response.blocked_reason = exc.code
        return response
    return response.model_copy(update={"allowed": True, "provider": profile.provider, "model_profile_id": profile.id,
                                       "model_profile": profile.name, "model_name": profile.model_name})


def enqueue_retry(db, crypto, settings, analysis_id, request, actor):
    from .test_runs import write_lock
    write_lock(db)
    original = db.get(Analysis, analysis_id)
    if original is None:
        raise RetryError("analysis_not_found", 404)
    replay = db.scalar(select(Analysis).where(Analysis.retry_idempotency_key == request.idempotency_key))
    if replay:
        if replay.retry_of_analysis_id != original.id:
            raise RetryError("retry_idempotency_conflict")
        return RetryResponse(analysis_id=replay.id, retry_of_analysis_id=original.id, status=replay.status, duplicate=True)
    if db.scalar(select(Analysis.id).where(Analysis.retry_of_analysis_id == original.id)):
        raise RetryError("retry_already_created")
    snapshot, _profile = retry_context(db, crypto, original, settings.agent_mode)
    # Copy only the immutable observation. Do not reset the original failure,
    # attach a new TestRunItem, copy its fixed Label, or overwrite any review.
    fields = (
        "source_system", "analysis_purpose", "ingest_channel", "event_fingerprint", "event_id", "company_name",
        "src_ip", "dest_ip", "src_port", "dest_port", "signature", "event_name", "waf_vendor", "waf_action",
        "payload_ciphertext", "encryption_key_version", "extra_fields", "service_api_key_id",
        "prompt_version", "prompt_policy_version_id", "prompt_snapshot_ciphertext",
        "input_schema_version_id", "input_schema_snapshot_ciphertext",
    )
    row = Analysis(id=str(uuid.uuid4()), **{name: getattr(original, name) for name in fields},
                   retry_of_analysis_id=original.id, retry_idempotency_key=request.idempotency_key,
                   execution_snapshot_ciphertext=crypto.encrypt_text(snapshot.model_dump_json()),
                   model_profile=_profile.name, status="pending")
    db.add(row)
    db.add(AccessAudit(actor_kind="admin_session", actor_id=actor, action="retry_failed_analysis",
                       resource_type="analysis", resource_id=row.id))
    db.commit()
    return RetryResponse(analysis_id=row.id, retry_of_analysis_id=original.id, status=row.status, duplicate=False)
