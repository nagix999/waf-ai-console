from datetime import UTC, datetime
from typing import Literal
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator


ServiceScope = Literal["ingest", "review"]
SOURCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z", re.ASCII)


def valid_service_source(value: str) -> bool:
    return isinstance(value, str) and bool(SOURCE_PATTERN.fullmatch(value)) and value.lower() != "admin-ui" and not value.lower().startswith("waf-internal-")


class ServiceApiKeyRename(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name", mode="before")
    @classmethod
    def clean_name(cls, value):
        if not isinstance(value, str):
            return value
        if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
            raise ValueError("service_api_key_name_invalid")
        return value.strip()


class ServiceApiKeyCreate(ServiceApiKeyRename):
    source_system: str = Field(min_length=1, max_length=120)
    scopes: list[ServiceScope] = Field(min_length=1, max_length=2)

    @field_validator("source_system")
    @classmethod
    def check_source(cls, value: str) -> str:
        if not valid_service_source(value):
            raise ValueError("service_api_key_source_invalid")
        return value

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("service_api_key_scopes_invalid")
        return sorted(value)


class ServiceApiKeyItem(BaseModel):
    id: str
    name: str
    key_prefix: str
    source_system: str
    scopes: list[ServiceScope]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    @field_validator("created_at", "last_used_at", "revoked_at")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ServiceApiKeyList(BaseModel):
    items: list[ServiceApiKeyItem]


class ServiceApiKeyIssued(BaseModel):
    item: ServiceApiKeyItem
    api_key: str
