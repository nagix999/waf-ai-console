"""Closed, declarative payload-extraction contracts; never executable code.

Vendor identity, revision, activation, budgets and source offsets belong to the
server envelope. A model may propose only one extraction operation and bounded
format predicates. JSON pointers address the JSON document inside payload only.
"""
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

RULE_PROPOSAL_VERSION = "parser-rule-proposal-v1"
RULE_BUNDLE_VERSION = "vendor-parser-bundle-v1"
RULE_SELECTION_VERSION = "vendor-rule-selection-v1"
MAX_RULES = 20
MAX_PREDICATES = 8
MAX_PATTERN_CHARS = 1024


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*")
    @classmethod
    def valid_unicode(cls, value):
        if isinstance(value, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("parser_rule_text_invalid")
        return value


def _label(value):
    if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("parser_rule_label_invalid")
    return value.strip()


def _pointer(value):
    if value and not value.startswith("/"):
        raise ValueError("parser_json_pointer_invalid")
    if len(value.split("/")) > 17:
        raise ValueError("parser_json_pointer_limit")
    position = 0
    while position < len(value):
        if value[position] == "~":
            if position + 1 == len(value) or value[position + 1] not in "01":
                raise ValueError("parser_json_pointer_invalid")
            position += 1
        position += 1
    return value


class JsonPointerModel(ClosedModel):
    json_pointer: str = Field(max_length=512)
    _valid_pointer = field_validator("json_pointer")(_pointer)


class JsonStringExists(JsonPointerModel):
    kind: Literal["json_string_exists"]


class JsonEquals(JsonPointerModel):
    kind: Literal["json_equals"]
    value: str | StrictBool | StrictInt | StrictFloat | None

    @field_validator("value")
    @classmethod
    def scalar_bound(cls, value):
        if isinstance(value, str) and len(value) > 512:
            raise ValueError("parser_json_value_limit")
        if type(value) is int and abs(value) > 2**63 - 1:
            raise ValueError("parser_json_value_limit")
        if type(value) is float and (not math.isfinite(value) or abs(value) > 2**63 - 1):
            raise ValueError("parser_json_value_limit")
        return value


class StartsWith(ClosedModel):
    kind: Literal["starts_with"]
    value: str = Field(min_length=1, max_length=512)


class MarkerPair(ClosedModel):
    start_marker: str = Field(min_length=1, max_length=256)
    end_marker: str = Field(min_length=1, max_length=256)
    start_anchor: Literal["payload_start", "line_start"]

    @model_validator(mode="after")
    def distinct_markers(self):
        if self.start_marker == self.end_marker or "\x00" in self.start_marker + self.end_marker:
            raise ValueError("parser_markers_invalid")
        return self


class MarkerPairExists(MarkerPair):
    kind: Literal["marker_pair_exists"]


class RegexModel(ClosedModel):
    pattern: str = Field(min_length=1, max_length=MAX_PATTERN_CHARS)

    @field_validator("pattern")
    @classmethod
    def no_null_pattern(cls, value):
        if "\x00" in value:
            raise ValueError("parser_regex_invalid")
        return value


class RegexMatch(RegexModel):
    kind: Literal["regex_match"]


Predicate = Annotated[JsonStringExists | JsonEquals | StartsWith | MarkerPairExists | RegexMatch,
                      Field(discriminator="kind")]


class AllPredicates(ClosedModel):
    all: list[Predicate] = Field(min_length=1, max_length=MAX_PREDICATES)


class AnyPredicates(ClosedModel):
    any: list[Predicate] = Field(min_length=1, max_length=MAX_PREDICATES)


class RawPayload(ClosedModel):
    kind: Literal["raw_payload"]


class JsonString(JsonPointerModel):
    kind: Literal["json_string"]


class BetweenMarkers(MarkerPair):
    kind: Literal["between_markers"]


class RegexCapture(RegexModel):
    kind: Literal["regex_capture"]


Extraction = Annotated[RawPayload | JsonString | BetweenMarkers | RegexCapture,
                       Field(discriminator="kind")]


class CaptureOptions(ClosedModel):
    line_endings: Literal["actual", "escaped_crlf", "escaped_lf"] = "actual"
    request_line_whitespace: Literal["strict", "flexible"] = "strict"
    skip_leading_blank_lines: StrictBool = False


class RuleProposalV1(ClosedModel):
    schema_version: Literal["parser-rule-proposal-v1"]
    name: str = Field(min_length=1, max_length=120)
    match: AllPredicates | AnyPredicates
    extract: Extraction
    capture: CaptureOptions
    reason_ko: str = Field(max_length=2000)
    limitations_ko: str = Field(max_length=2000)
    _valid_name = field_validator("name")(_label)


class VendorRule(ClosedModel):
    rule_id: str = Field(min_length=1, max_length=36)
    rule_version: StrictInt = Field(ge=1, le=2**31 - 1)
    waf_vendor: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    definition: RuleProposalV1
    _valid_labels = field_validator("rule_id", "waf_vendor")(_label)

    @field_validator("name")
    @classmethod
    def optional_name(cls, value):
        return _label(value) if value is not None else value


class VendorRuleBundle(ClosedModel):
    schema_version: Literal["vendor-parser-bundle-v1"]
    selection_policy_version: Literal["vendor-rule-selection-v1"]
    rules: list[VendorRule] = Field(min_length=1, max_length=MAX_RULES)

    @model_validator(mode="after")
    def unique_rule_ids(self):
        if len({rule.rule_id for rule in self.rules}) != len(self.rules):
            raise ValueError("parser_rule_id_duplicate")
        return self


RuleProposal = RuleProposalV1
