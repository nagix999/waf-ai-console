"""Bounded, declarative input definitions; never executable JSON Schema."""
from datetime import UTC, datetime
from typing import Any, Literal
import json
import math
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .prompt_schemas import clean_prompt_text

MAX_DEFINITION_BYTES = 65536
MAX_FIELDS = 64
MAX_DEPTH = 4
MAX_ENUM_ITEMS = 50
MAX_STRING_LENGTH = 2097152
MAX_ARRAY_ITEMS = 1000
MAX_VALIDATION_ISSUES = 50
VALIDATION_TOKEN_SECONDS = 300


class TypeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["string", "integer", "number", "boolean", "object", "array"]
    nullable: bool = False
    min_length: int | None = Field(default=None, ge=0, le=MAX_STRING_LENGTH)
    max_length: int | None = Field(default=None, ge=0, le=MAX_STRING_LENGTH)
    minimum: int | float | None = None
    maximum: int | float | None = None
    enum: list[Any] | None = Field(default=None, min_length=1, max_length=MAX_ENUM_ITEMS)
    min_items: int | None = Field(default=None, ge=0, le=MAX_ARRAY_ITEMS)
    max_items: int | None = Field(default=None, ge=0, le=MAX_ARRAY_ITEMS)
    items: "TypeDefinition | None" = None
    properties: list["FieldDefinition"] | None = Field(default=None, max_length=MAX_FIELDS)

    @model_validator(mode="after")
    def check_constraints(self):
        for low, high in ((self.min_length, self.max_length), (self.minimum, self.maximum), (self.min_items, self.max_items)):
            if low is not None and high is not None and low > high:
                raise ValueError("input_schema_range_invalid")
        if self.type != "string" and (self.min_length is not None or self.max_length is not None):
            raise ValueError("input_schema_string_constraint_type")
        if self.type not in {"integer", "number"} and (self.minimum is not None or self.maximum is not None):
            raise ValueError("input_schema_numeric_constraint_type")
        if any(value is not None and (isinstance(value, bool) or not finite_number(value)) for value in (self.minimum, self.maximum)):
            raise ValueError("input_schema_number_invalid")
        if self.type != "array" and any(value is not None for value in (self.min_items, self.max_items, self.items)):
            raise ValueError("input_schema_array_constraint_type")
        if self.type == "array" and self.items is None:
            raise ValueError("input_schema_array_items_required")
        if self.type != "object" and self.properties is not None:
            raise ValueError("input_schema_object_constraint_type")
        if self.properties is not None and len({item.name for item in self.properties}) != len(self.properties):
            raise ValueError("input_schema_duplicate_field")
        if self.enum is not None:
            if self.type in {"array", "object"}:
                raise ValueError("input_schema_enum_scalar_only")
            keys = []
            for value in self.enum:
                if not scalar_matches(self, value):
                    raise ValueError("input_schema_enum_value_invalid")
                keys.append(json.dumps(value, ensure_ascii=False, allow_nan=False))
            if len(keys) != len(set(keys)):
                raise ValueError("input_schema_duplicate_enum_value")
            if self.type == "number" and len(self.enum) != len(set(self.enum)):
                raise ValueError("input_schema_duplicate_enum_value")
        return self


class FieldDefinition(TypeDefinition):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=1000)
    required: bool = False

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value) or value.lower() in {"constructor", "prototype"}:
            raise ValueError("input_schema_field_name_invalid")
        return value

    @field_validator("description", mode="before")
    @classmethod
    def safe_description(cls, value):
        return clean_prompt_text(value, multiline=True) if isinstance(value, str) else value


def finite_number(value: Any) -> bool:
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def scalar_matches(definition: TypeDefinition, value: Any) -> bool:
    if value is None:
        return definition.nullable
    if definition.type == "string":
        if not isinstance(value, str):
            return False
        try:
            value.encode("utf-8")
        except UnicodeError:
            return False
        return (definition.min_length is None or len(value) >= definition.min_length) and (definition.max_length is None or len(value) <= definition.max_length)
    if definition.type in {"integer", "number"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            if not math.isfinite(value) or (definition.type == "integer" and not isinstance(value, int)):
                return False
        except OverflowError:
            return False
        return (definition.minimum is None or value >= definition.minimum) and (definition.maximum is None or value <= definition.maximum)
    return definition.type == "boolean" and isinstance(value, bool)


class InputSchemaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    change_note: str = Field(min_length=1, max_length=1000)
    parent_id: str | None = Field(default=None, min_length=1, max_length=36)
    fields: list[FieldDefinition] = Field(min_length=11, max_length=MAX_FIELDS)

    @field_validator("name", "change_note", mode="before")
    @classmethod
    def safe_text(cls, value):
        return clean_prompt_text(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def bounded_definition(self):
        check_definition_bounds(self.fields)
        return self


def check_definition_bounds(fields: list[FieldDefinition]) -> None:
    pending = [(item, 1) for item in fields]
    count = 0
    if len({item.name for item in fields}) != len(fields):
        raise ValueError("input_schema_duplicate_field")
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_FIELDS:
            raise ValueError("input_schema_complexity_limit")
        pending.extend((child, depth + 1) for child in item.properties or [])
        if item.items is not None:
            pending.append((item.items, depth + 1))
    if len(json.dumps([item.model_dump(exclude_none=True) for item in fields], ensure_ascii=False).encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise ValueError("input_schema_definition_too_large")


class InputSchemaValidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event: dict[str, Any]


class InputSchemaActivate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    validation_token: str = Field(min_length=1, max_length=4096)


class InputSchemaIssue(BaseModel):
    field: str
    type: str
    message: str


class InputSchemaValidationResponse(BaseModel):
    valid: bool
    issues: list[InputSchemaIssue]
    validation_token: str | None = None
    expires_in_seconds: int | None = None


class InputSchemaSummary(BaseModel):
    id: str
    version_number: int
    name: str
    change_note: str
    parent_id: str | None
    content_hash: str
    created_by: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class InputSchemaDetail(InputSchemaSummary):
    fields: list[FieldDefinition]


class InputSchemaActivationResponse(BaseModel):
    active_version_id: str
    revision: int


class InputSchemaListResponse(InputSchemaActivationResponse):
    items: list[InputSchemaSummary]
    default_fields: list[FieldDefinition]
    bounds: dict[str, Any]


class InputSchemaActivationSummary(BaseModel):
    id: str
    previous_version_id: str | None
    active_version_id: str
    revision: int
    actor_id: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_time(cls, value):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


TypeDefinition.model_rebuild()
FieldDefinition.model_rebuild()
