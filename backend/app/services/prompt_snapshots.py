"""Pin complete role instructions before queueing, independent of later edits."""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent.input_builder import PROMPT_SCHEMA_RESERVED_TOKENS
from ..agent.prompts import policy_reserved_tokens
from ..models import Analysis, ProductionPromotion, TestRun
from .crypto import CryptoService
from .prompt_policies import get_active_policy, get_policy_version, read_policy_text


class PromptSnapshotError(ValueError):
    def __init__(self) -> None:
        super().__init__("prompt_snapshot_invalid")
        self.code = "prompt_snapshot_invalid"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _instructions_hash(primary: str, verifier: str) -> str:
    return _hash(json.dumps([primary, verifier], ensure_ascii=False, separators=(",", ":")))


class PromptSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: int = Field(default=1, ge=1, le=2)
    policy_version_id: str = Field(min_length=1, max_length=36)
    policy_version_number: int = Field(ge=1)
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(min_length=1, max_length=120)
    fixed_rules_version: str = Field(min_length=1, max_length=120)
    fixed_rules_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    instructions_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_instructions: str = Field(min_length=1, max_length=50000)
    verifier_instructions: str = Field(min_length=1, max_length=50000)
    reserved_tokens: int = Field(ge=PROMPT_SCHEMA_RESERVED_TOKENS, le=100000)
    selection_origin: str = Field(pattern=r"^(enqueue|legacy_execution)$")
    evidence_pipeline: dict | None = None

    @model_validator(mode="after")
    def pipeline_binding(self):
        from ..agent.evidence_pipeline_v222 import RULES_VERSIONS, validate_pipeline_snapshot
        if (self.schema_version == 2) != (self.fixed_rules_version in RULES_VERSIONS):
            raise ValueError("prompt_snapshot_invalid")
        validate_pipeline_snapshot(self.evidence_pipeline, self.fixed_rules_version)
        return self

    def metadata(self) -> dict:
        return self.model_dump(exclude={"primary_instructions", "verifier_instructions", "schema_version", "evidence_pipeline"})


def load_analysis_prompt(analysis: Analysis, crypto: CryptoService) -> PromptSnapshot:
    try:
        if not isinstance(analysis.prompt_snapshot_ciphertext, str) or not analysis.prompt_snapshot_ciphertext:
            raise PromptSnapshotError()
        snapshot = PromptSnapshot.model_validate_json(crypto.decrypt_text(analysis.prompt_snapshot_ciphertext))
        if (
            snapshot.policy_version_id != analysis.prompt_policy_version_id
            or snapshot.prompt_version != analysis.prompt_version
            or snapshot.instructions_hash != _instructions_hash(snapshot.primary_instructions, snapshot.verifier_instructions)
        ):
            raise PromptSnapshotError()
        return snapshot
    except (ValueError, TypeError, ValidationError, UnicodeError):
        # Never attach decrypted instructions or Pydantic input values to errors.
        raise PromptSnapshotError() from None


def pin_analysis_prompt(
    db: Session, crypto: CryptoService, analysis: Analysis, *, origin: str = "enqueue", version_id: str | None = None,
    production: bool | None = None,
) -> PromptSnapshot:
    if analysis.prompt_snapshot_ciphertext:
        if version_id is not None and version_id != analysis.prompt_policy_version_id:
            raise PromptSnapshotError()
        return load_analysis_prompt(analysis, crypto)
    if analysis.prompt_policy_version_id is not None or (
        isinstance(analysis.prompt_version, str) and "/policy-" in analysis.prompt_version
    ):
        raise PromptSnapshotError()
    use_production = getattr(analysis, "analysis_purpose", None) == "production" if production is None else production
    approved = approved_production_prompt(db, crypto) if use_production and version_id is None else None
    if approved is not None:
        snapshot = approved.model_copy(update={"selection_origin": origin})
    else:
        # New candidates do not change Production's pinned system rules.
        version = get_policy_version(db, version_id) if version_id is not None else get_active_policy(db, crypto)
        if origin == "legacy_execution":
            # Rows admitted before snapshots retain the last legacy builder;
            # New versioned candidates always pin at enqueue instead.
            from ..agent import legacy_prompts_v220
            snapshot = _build_snapshot(version, crypto, origin=origin, rules=legacy_prompts_v220)
        else:
            snapshot = _build_snapshot(version, crypto, origin=origin)
    analysis.prompt_policy_version_id = snapshot.policy_version_id
    analysis.prompt_version = snapshot.prompt_version
    analysis.prompt_snapshot_ciphertext = crypto.encrypt_text(snapshot.model_dump_json())
    return snapshot


def _build_snapshot(version, crypto, *, origin="enqueue", rules=None):
    from ..agent import prompts
    from ..agent.evidence_pipeline_v222 import pipeline_for_rules
    rules = rules or prompts
    try:
        policy_text = read_policy_text(version, crypto)
    except (AttributeError, TypeError):
        raise PromptSnapshotError() from None
    primary, verifier = rules.build_role_instructions(policy_text)
    pipeline = pipeline_for_rules(rules.FIXED_RULES_VERSION)
    snapshot = PromptSnapshot(
        schema_version=2 if pipeline is not None else 1,
        evidence_pipeline=pipeline,
        policy_version_id=version.id,
        policy_version_number=version.version_number,
        policy_hash=version.content_hash,
        prompt_version=f"{rules.PROMPT_VERSION}/policy-{version.version_number}",
        fixed_rules_version=rules.FIXED_RULES_VERSION,
        fixed_rules_hash=_hash(rules.FIXED_INSTRUCTIONS),
        instructions_hash=_instructions_hash(primary, verifier),
        primary_instructions=primary,
        verifier_instructions=verifier,
        # Conservative allowance for the editable text, on top of the existing
        # fixed-rules/schema reserve. Not a model-specific tokenizer guarantee.
        reserved_tokens=policy_reserved_tokens(policy_text),
        selection_origin=origin,
    )
    return snapshot


def approved_production_record(db):
    """A code deployment must not silently upgrade the approved instructions."""
    promotion = db.scalar(select(ProductionPromotion).where(ProductionPromotion.kind == "promotion")
        .order_by(ProductionPromotion.created_at.desc(), ProductionPromotion.id.desc()).limit(1))
    return promotion or db.scalar(select(ProductionPromotion).where(ProductionPromotion.kind == "baseline")
        .order_by(ProductionPromotion.created_at.asc(), ProductionPromotion.id.asc()).limit(1))


def _baseline_rules(expected):
    """Restore the exact approved text, not the latest text with its label.

    v2.21 text revisions share the unchanged execution contract, not approval.
    Pre-compaction and compact text remain available by immutable content hash;
    no approval or evaluation transfers to the latest candidate instructions.
    """
    from ..agent import (
        legacy_prompts_v212, legacy_prompts_v213, legacy_prompts_v214,
        legacy_prompts_v215, legacy_prompts_v216, legacy_prompts_v217,
        legacy_prompts_v218, legacy_prompts_v219, legacy_prompts_v220,
        legacy_prompts_v221, legacy_prompts_v221_compact, legacy_prompts_v221_reasoning,
        legacy_prompts_v222, prompts,
    )
    modules = dict.fromkeys((
        legacy_prompts_v212, legacy_prompts_v213, legacy_prompts_v214,
        legacy_prompts_v215, legacy_prompts_v216, legacy_prompts_v217,
        legacy_prompts_v218, legacy_prompts_v219, legacy_prompts_v220,
        legacy_prompts_v221, legacy_prompts_v221_compact, legacy_prompts_v221_reasoning,
        legacy_prompts_v222, prompts,
    ))
    matches = [module for module in modules
        if module.FIXED_RULES_VERSION == expected["fixed_rules_version"]
        and _hash(module.FIXED_INSTRUCTIONS) == expected["fixed_rules_hash"]]
    if len(matches) != 1:
        raise PromptSnapshotError()
    return matches[0]


def approved_production_prompt(db, crypto):
    record = approved_production_record(db)
    if record is None:
        return None  # A fresh installation has no older approved configuration.
    try:
        from .candidate_configurations import configuration_digest
        if configuration_digest(record.snapshot_json) != record.configuration_hash:
            raise ValueError()
        expected = record.snapshot_json["prompt"]
        from ..agent.evidence_pipeline_v222 import validate_pipeline_snapshot
        approved_pipeline = record.snapshot_json.get("evidence_pipeline")
        validate_pipeline_snapshot(approved_pipeline, expected["fixed_rules_version"])
        if record.kind == "promotion":
            run = db.get(TestRun, record.source_test_run_id)
            snapshot = load_analysis_prompt(run, crypto)
        else:
            rules = _baseline_rules(expected)
            snapshot = _build_snapshot(get_policy_version(db, expected["policy_version_id"]), crypto, rules=rules)
        actual = {key: getattr(snapshot, key) for key in expected}
        if (actual != expected or snapshot.evidence_pipeline != approved_pipeline
                or get_active_policy(db, crypto).id != snapshot.policy_version_id):
            raise ValueError()
        return snapshot
    except (ValueError, TypeError, KeyError, AttributeError):
        # Missing/corrupt approval is not permission to use the latest rules.
        raise PromptSnapshotError() from None
