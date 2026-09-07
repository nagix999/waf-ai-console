from datetime import UTC, datetime
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_POLICY_CHARS = 4000


def clean_prompt_text(value: str, *, multiline: bool = False) -> str:
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("prompt_text_invalid_unicode") from None
    allowed = {"\n", "\t"} if multiline else set()
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} and character not in allowed for character in value):
        raise ValueError("prompt_text_control_character")
    return value.strip()


class PromptPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=120)
    policy_text: str = Field(min_length=1, max_length=MAX_POLICY_CHARS)
    change_note: str = Field(min_length=1, max_length=1000)
    parent_version_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("name", "change_note", mode="before")
    @classmethod
    def clean_metadata(cls, value):
        return clean_prompt_text(value) if isinstance(value, str) else value

    @field_validator("policy_text", mode="before")
    @classmethod
    def clean_policy(cls, value):
        return clean_prompt_text(value, multiline=True) if isinstance(value, str) else value


class PromptPolicyActivate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=1)
    acknowledge_unverified: bool

    @field_validator("acknowledge_unverified")
    @classmethod
    def explicit_acknowledgement(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("prompt_quality_acknowledgement_required")
        return value


class PromptPolicySummary(BaseModel):
    id: str
    version_number: int
    name: str
    change_note: str
    parent_version_id: str | None
    content_hash: str
    created_by: str
    created_at: datetime
    quality_status: Literal["not_evaluated"] = "not_evaluated"

    @field_validator("created_at")
    @classmethod
    def utc_created_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class PromptPolicyDetail(PromptPolicySummary):
    policy_text: str


class PromptPolicyActivationResponse(BaseModel):
    active_version_id: str
    revision: int


class PromptPolicyListResponse(PromptPolicyActivationResponse):
    items: list[PromptPolicySummary]
    fixed_instructions: str
    fixed_rules_version: str
    max_policy_chars: int = MAX_POLICY_CHARS
