"""Bounded administrator-only parser diagnostics contracts.

Only bundled engines and fixed format options are executable. No code, regular
expression, template, URL, or arbitrary field path is accepted from a model.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_serializer, model_validator


class ParserSuggestionOptions(BaseModel):
    """The model may suggest format flags, never routing or vendor rules."""
    model_config = ConfigDict(extra="forbid")
    line_endings: Literal["actual", "escaped_crlf", "escaped_lf"] = "actual"
    request_line_whitespace: Literal["strict", "flexible"] = "strict"
    skip_leading_blank_lines: StrictBool = False


class ParserCaptureProfile(ParserSuggestionOptions):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    waf_vendor: str = Field(min_length=1, max_length=120)

    @field_validator("name", "waf_vendor", mode="before")
    @classmethod
    def no_control_characters(cls, value):
        if isinstance(value, str) and any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("invalid_parser_profile_label")
        return value


class ParserOptions(ParserSuggestionOptions):
    profiles: list[ParserCaptureProfile] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_vendor_format(self):
        seen = set()
        for profile in self.profiles or []:
            key = (profile.waf_vendor.casefold(), profile.line_endings)
            if key in seen:
                raise ValueError("duplicate_parser_vendor_format")
            seen.add(key)
        return self

    @model_serializer(mode="wrap")
    def omit_unconfigured_profiles(self, handler):
        result = handler(self)
        if self.profiles is None:
            result.pop("profiles", None)
        return result


class ParserVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    engine_version: Literal["generic-http-v3", "http-capture-v1"] = "http-capture-v1"
    options: ParserOptions = Field(default_factory=ParserOptions)
    parent_id: str | None = Field(default=None, min_length=1, max_length=36)


class ParserSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    test_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    max_cases: int = Field(default=100, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def one_source(self):
        if (self.analysis_ids is None) == (self.test_run_id is None):
            raise ValueError("exactly_one_parser_source_required")
        if self.analysis_ids is not None:
            if any(not isinstance(value, str) or not 1 <= len(value) <= 36 for value in self.analysis_ids):
                raise ValueError("invalid_parser_analysis_id")
            if len(set(self.analysis_ids)) > self.max_cases:
                raise ValueError("parser_selection_exceeds_limit")
        return self


class ParserComparisonCreate(ParserSelection):
    baseline_version_id: str = Field(min_length=1, max_length=36)
    candidate_version_id: str = Field(min_length=1, max_length=36)


class ParserSuggestionCreate(ParserSelection):
    max_cases: int = Field(default=20, ge=1, le=20, strict=True)
    baseline_version_id: str = Field(min_length=1, max_length=36)
    profile_id: str = Field(min_length=1, max_length=36)
    internal_data_confirmed: Literal[True]

    @field_validator("internal_data_confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit_internal_data_confirmation_required")
        return value


class ParserComparisonReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_review(cls, value):
        if value is not True:
            raise ValueError("explicit_parser_structure_review_required")
        return value


class ParserSuggestionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    engine_version: Literal["http-capture-v1"]
    options: ParserSuggestionOptions
    reason_ko: str = Field(min_length=1, max_length=2000)
    limitations_ko: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def bounded_limitations(self):
        if any(not isinstance(value, str) or not 1 <= len(value) <= 500 for value in self.limitations_ko):
            raise ValueError("invalid_parser_limitations")
        return self
