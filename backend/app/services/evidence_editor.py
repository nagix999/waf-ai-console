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
    return capture_editor_profile(profile)


def capture_editor_profile(profile):
    meta = profile_metadata(profile)
    return EditorSnapshot(version=VERSION, instructions=INSTRUCTIONS, instructions_hash=instructions_hash(INSTRUCTIONS),
                          profile_id=profile.id, profile_fingerprint=meta["profile_fingerprint"])


def editor_profile(db, snapshot):
    return validate_role_profile(db, db.get(VLLMProfile, snapshot.profile_id, populate_existing=True), snapshot.profile_fingerprint)
