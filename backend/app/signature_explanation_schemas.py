"""Bounded reference descriptions, separated from observed request evidence."""
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ENTRIES = 200
MAX_CATALOG_BYTES = 262144


class SignatureExplanationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    waf_vendor: str = Field(min_length=1, max_length=120)
    signature: str = Field(min_length=1, max_length=1000)
    signature_explanation: str = Field(min_length=1, max_length=2000)
    reference_source: str = Field(default="", max_length=1000)

    @field_validator("waf_vendor", "signature", mode="before")
    @classmethod
    def no_label_controls(cls, value):
        if isinstance(value, str) and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("signature_explanation_label_invalid")
        return value


class SignatureExplanationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    change_reason: str = Field(min_length=1, max_length=1000)
    parent_id: str | None = Field(default=None, min_length=1, max_length=36)
    entries: list[SignatureExplanationEntry] = Field(max_length=MAX_ENTRIES)

    @model_validator(mode="after")
    def unique_match_keys(self):
        keys = [(entry.waf_vendor.casefold(), entry.signature) for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_signature_explanation")
        return self


class SignatureExplanationMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    version_id: str = Field(min_length=1, max_length=36)
    waf_vendor: str = Field(min_length=1, max_length=120)
    signature: str = Field(min_length=1, max_length=1000)
