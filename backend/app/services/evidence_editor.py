"""Pin optional editor independently from the mandatory decision roles."""
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..agent.evidence_editor import instructions_hash
from ..agent.result_editor import INSTRUCTIONS, VERSION
from ..models import AgentConfiguration, VLLMProfile
from .agent_configuration import profile_metadata, validate_role_profile


class EditorSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=4000)
    instructions_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str = Field(min_length=1, max_length=36)
    profile_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify(self):
        if instructions_hash(self.instructions) != self.instructions_hash:
            raise ValueError("editor_instructions_invalid")
        return self


def capture_editor(db, purpose, primary):
    config = db.get(AgentConfiguration, 1)
    if not config or not getattr(config, purpose + "_evidence_editor_enabled", False):
        return None
    identifier = getattr(config, purpose + "_evidence_editor_profile_id") or primary.id
    profile = db.get(VLLMProfile, identifier)
    # Assignment API prevents missing profiles. Do not silently select a new one.
    if profile is None:
        return None
    return capture_editor_profile(profile, db=db if purpose == "production" else None)


def capture_editor_profile(profile, *, db=None):
    version, instructions = VERSION, INSTRUCTIONS
    if db is not None:
        from .prompt_snapshots import approved_production_record, PromptSnapshotError
        from ..agent import evidence_editor, result_editor
        approved = approved_production_record(db)
        if approved is not None:
            try:
                expected = approved.snapshot_json["evidence_editor"]
                if not expected["enabled"]:
                    raise ValueError()
                version = expected["version"]
                instructions = {evidence_editor.VERSION: evidence_editor.INSTRUCTIONS,
                    result_editor.LEGACY_VERSION: result_editor.LEGACY_INSTRUCTIONS,
                    VERSION: INSTRUCTIONS}[version]
                if instructions_hash(instructions) != expected["instructions_hash"]:
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise PromptSnapshotError() from None
    meta = profile_metadata(profile)
    return EditorSnapshot(version=version, instructions=instructions, instructions_hash=instructions_hash(instructions),
                          profile_id=profile.id, profile_fingerprint=meta["profile_fingerprint"])


def editor_profile(db, snapshot):
    return validate_role_profile(db, db.get(VLLMProfile, snapshot.profile_id, populate_existing=True), snapshot.profile_fingerprint)
