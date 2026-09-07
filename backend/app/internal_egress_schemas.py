from datetime import UTC, datetime
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InternalEgressCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ip_address: str = Field(min_length=1, max_length=64)
    port: int = Field(ge=1, le=65535)
    description: str = Field(default="", max_length=500)

    @field_validator("description", mode="before")
    @classmethod
    def clean_description(cls, value):
        if not isinstance(value, str):
            return value
        if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
            raise ValueError("internal_egress_description_invalid")
        return value.strip()


class InternalEgressUpdate(InternalEgressCreate):
    expected_revision: int = Field(ge=1, le=2**63 - 1)


class InternalEgressProfile(BaseModel):
    id: str
    name: str
    status: str


class InternalEgressResponse(BaseModel):
    id: str
    ip_address: str
    port: int
    description: str
    revision: int
    created_at: datetime
    updated_at: datetime
    in_use_profiles: list[InternalEgressProfile]

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
