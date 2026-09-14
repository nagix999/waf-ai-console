"""Purpose-specific role selection; no automatic provider failover."""
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AgentConfiguration, PromptPolicyState, VLLMProfile
from .internal_egress import allowed_targets_from_db
from .vllm_profiles import (TargetNotAllowedError, assignment_block_reason, normalize_and_validate_profile_url,
                           profile_fingerprint, to_profile_response, validate_profile_provider_settings)


def profile_metadata(profile):
    return {"model_profile_id": profile.id, "model_profile": profile.name, "model_name": profile.model_name,
            "llm_provider": profile.provider, "profile_fingerprint": profile_fingerprint(profile),
            "external_data_approved": profile.external_data_approved}


def verifier_roles(db, profile_id):
    config = db.get(AgentConfiguration, 1)
    return [purpose + "." + role for purpose in ("production", "test") for role in ("verifier", "evidence_editor")
            if config and getattr(config, purpose + "_" + role + "_profile_id") == profile_id]


def configuration_document(db):
    config = db.get(AgentConfiguration, 1)
    profiles = list(db.scalars(select(VLLMProfile).order_by(VLLMProfile.name)))
    assignments = {}
    for purpose in ("production", "test"):
        primary = next((p for p in profiles if (p.status == "production" if purpose == "production" else p.is_test)), None)
        assignments[purpose] = {"primary_profile_id": primary.id if primary else None,
                                "verifier_profile_id": getattr(config, purpose + "_verifier_profile_id") if config else None,
                                "evidence_editor_enabled": bool(getattr(config, purpose + "_evidence_editor_enabled", False)),
                                "evidence_editor_profile_id": getattr(config, purpose + "_evidence_editor_profile_id", None)}
    revision = config.revision if config else 0
    policy = db.get(PromptPolicyState, 1)
    # Includes legacy assignment API changes and profile edits, not just this row.
    state = {"revision": revision, "assignments": assignments,
             "common_policy": [policy.active_version_id, policy.revision] if policy else None,
             "profiles": sorted((p.id, profile_fingerprint(p), p.status, p.is_test) for p in profiles)}
    return {"revision": revision, "state_token": hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(),
            "assignments": assignments, "profiles": [to_profile_response(p, db).model_dump(mode="json") for p in profiles]}


def validate_role_profile(db, profile, fingerprint=None, *, require_verified=True):
    if profile is None or profile.status == "disabled":
        raise TargetNotAllowedError("agent_profile_unavailable")
    if fingerprint is not None and profile_fingerprint(profile) != fingerprint:
        raise TargetNotAllowedError("agent_profile_changed")
    if require_verified and assignment_block_reason(db, profile):
        raise TargetNotAllowedError("agent_profile_not_verified")
    normalized = normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
    validate_profile_provider_settings(profile, has_api_key=bool(profile.api_key_ciphertext))
    if normalized != profile.base_url:
        raise TargetNotAllowedError("agent_profile_changed")
    return profile


def has_input_budget(profile, policy_text):
    from ..agent.input_builder import ESTIMATED_CHARS_PER_TOKEN, MIN_INPUT_CHARS
    from ..agent.prompts import policy_reserved_tokens
    from ..agent.grounding_repair import REPAIR_RESERVED_TOKENS
    available = profile.context_window - profile.max_output_tokens - policy_reserved_tokens(policy_text) - REPAIR_RESERVED_TOKENS
    return profile.max_output_tokens >= 0 and available * ESTIMATED_CHARS_PER_TOKEN >= MIN_INPUT_CHARS


def select_verifier(db, purpose, primary):
    config = db.get(AgentConfiguration, 1)
    identifier = getattr(config, purpose + "_verifier_profile_id", None) if config else None
    return validate_role_profile(db, db.get(VLLMProfile, identifier)) if identifier else primary


def role_request_check(engine, profile, prior_check=None, *, require_verified=True):
    identifier, fingerprint = profile.id, profile_fingerprint(profile)
    def check():
        if prior_check:
            prior_check()
        with Session(engine) as latest:
            validate_role_profile(latest, latest.get(VLLMProfile, identifier), fingerprint,
                                  require_verified=require_verified)
    return check
