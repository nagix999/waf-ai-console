"""Explicit test candidates and content-free fingerprints of frozen settings."""
import hashlib
import hmac
import json

from ..models import VLLMProfile
from .agent_configuration import has_input_budget, validate_role_profile
from .analysis import AnalysisIngestError
from .evidence_editor import EditorSnapshot, capture_editor_profile
from .input_schemas import read_schema_snapshot
from .prompt_policies import get_policy_version, read_policy_text
from .prompt_snapshots import load_analysis_prompt


def resolve_profiles(db, crypto, candidate):
    primary = validate_role_profile(db, db.get(VLLMProfile, candidate.primary_profile_id))
    verifier = validate_role_profile(db, db.get(VLLMProfile, candidate.verifier_profile_id or primary.id))
    editor = validate_role_profile(db, db.get(VLLMProfile, candidate.evidence_editor_profile_id or primary.id)) if candidate.evidence_editor_enabled else None
    policy = read_policy_text(get_policy_version(db, candidate.prompt_policy_version_id), crypto)
    # The editor uses its own small, separately captured instructions.
    if any(not has_input_budget(profile, policy) for profile in (primary, verifier)):
        raise AnalysisIngestError("agent_context_budget_too_small", 422)
    return primary, verifier, capture_editor_profile(editor) if editor else None


def configuration_digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def snapshot_from_run(run, crypto):
    """Use the queued instructions, never the currently deployed prompt code."""
    prompt = load_analysis_prompt(run, crypto)
    schema = read_schema_snapshot(crypto, run)
    editor = EditorSnapshot.model_validate_json(crypto.decrypt_text(run.evidence_editor_snapshot_ciphertext)) if run.evidence_editor_snapshot_ciphertext else None
    verifier = run.profile_metadata["verifier_profile"]
    return {
        "schema_version": 1,
        "primary": {"profile_id": run.profile_id, "profile_fingerprint": run.profile_fingerprint},
        "verifier": {"profile_id": verifier["model_profile_id"], "profile_fingerprint": verifier["profile_fingerprint"]},
        "evidence_editor": {"enabled": editor is not None, "profile_id": editor.profile_id if editor else None,
            "profile_fingerprint": editor.profile_fingerprint if editor else None,
            "version": editor.version if editor else None, "instructions_hash": editor.instructions_hash if editor else None},
        "prompt": {"policy_version_id": prompt.policy_version_id, "policy_hash": prompt.policy_hash,
            "prompt_version": prompt.prompt_version, "fixed_rules_version": prompt.fixed_rules_version,
            "fixed_rules_hash": prompt.fixed_rules_hash, "instructions_hash": prompt.instructions_hash,
            "reserved_tokens": prompt.reserved_tokens},
        "input_schema": {"version_id": schema["version_id"], "content_hash": schema["content_hash"]},
        "verifier_confidence_threshold": run.profile_metadata["verifier_confidence_threshold"],
    }


def pin_configuration(run, crypto):
    if run.execution_mode != "moduagent":
        return  # Stub runs must never look like a tested model configuration.
    run.configuration_snapshot_json = snapshot_from_run(run, crypto)
    run.configuration_hash = configuration_digest(run.configuration_snapshot_json)


def verify_configuration(run, crypto, analysis=None):
    """Used before execution and later by promotion; no incomplete snapshots."""
    try:
        if run.execution_mode != "moduagent" or not run.configuration_hash or not isinstance(run.configuration_snapshot_json, dict):
            raise ValueError()
        actual = snapshot_from_run(run, crypto)
        if actual != run.configuration_snapshot_json or not hmac.compare_digest(configuration_digest(actual), run.configuration_hash):
            raise ValueError()
        if analysis is not None:
            # Check membership's actual queued snapshots too, not only the run
            # header. A valid but different saved policy must not be attributed
            # to this candidate during a later official evaluation.
            queued_prompt = load_analysis_prompt(analysis, crypto)
            run_prompt = load_analysis_prompt(run, crypto)
            if queued_prompt.model_dump(exclude={"selection_origin"}) != run_prompt.model_dump(exclude={"selection_origin"}):
                raise ValueError()
            queued_schema = read_schema_snapshot(crypto, analysis)
            if any(queued_schema[key] != actual["input_schema"][key] for key in ("version_id", "content_hash")):
                raise ValueError()
        return actual
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise AnalysisIngestError("candidate_configuration_invalid", 409) from None
