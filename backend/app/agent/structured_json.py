"""Bounded JSON-envelope handling, without guessing or changing model values.

Only a UTF-8 BOM and one whole Markdown code fence may be removed. JSON must
already be complete and unambiguous. The caller still runs the original strict
Pydantic/domain model validation; this module does not relax its contract.
"""

from dataclasses import dataclass
import json
import math
from typing import Any


VERSION = "bounded-structured-json-v1"
MAX_JSON_BYTES = 1_048_576
MAX_JSON_DEPTH = 32
REJECTION_CODES = frozenset({
    "json_invalid", "json_duplicate_key", "json_non_finite",
    "json_size_limit", "json_depth_limit", "json_encoding_invalid",
    "json_top_level_object_required",
})


class StructuredJSONRejected(ValueError):
    """The exception carries only a fixed code, never a key or model content."""

    def __init__(self, code: str) -> None:
        self.code = code if code in REJECTION_CODES else "json_invalid"
        super().__init__(self.code)


@dataclass(frozen=True)
class StructuredJSON:
    content: str
    normalizations: tuple[str, ...]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StructuredJSONRejected("json_duplicate_key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise StructuredJSONRejected("json_non_finite")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise StructuredJSONRejected("json_non_finite")
    return number


def _check_depth(content: str) -> None:
    # This is only a resource guard, not a replacement JSON parser. String
    # escapes are accounted for; malformed brackets are rejected by json.loads.
    depth = 0
    in_string = False
    escaped = False
    for char in content:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise StructuredJSONRejected("json_depth_limit")
        elif char in "]}":
            depth -= 1


def normalize_structured_json(content: str | bytes | bytearray) -> StructuredJSON:
    """Return identical JSON values with only unambiguous outer wrapping removed.

    Prefix/suffix prose, multiple JSON documents, incomplete JSON, duplicate
    keys, non-finite numbers, invalid escapes and oversized/deep output fail.
    No excerpts, enum values, fields, quotes or closing brackets are invented.
    """
    if len(content) > MAX_JSON_BYTES:
        raise StructuredJSONRejected("json_size_limit")
    if not isinstance(content, str):
        try:
            content = bytes(content).decode("utf-8", errors="strict")
        except UnicodeError:
            raise StructuredJSONRejected("json_encoding_invalid") from None
    try:
        if len(content.encode("utf-8", errors="strict")) > MAX_JSON_BYTES:
            raise StructuredJSONRejected("json_size_limit")
    except UnicodeError:
        raise StructuredJSONRejected("json_encoding_invalid") from None

    value = content.strip()
    normalizations: list[str] = []
    if value.startswith("\ufeff"):
        value = value[1:].strip()
        normalizations.append("utf8_bom")
    # A fence must cover the entire output. Never search within arbitrary
    # explanation text or accept the first JSON-looking substring.
    first_line, separator, rest = value.partition("\n")
    if separator and first_line.rstrip("\r").lower() in {"```", "```json"}:
        inner, closing, last_line = rest.rpartition("\n")
        if closing and last_line.rstrip("\r") == "```":
            value = inner.strip()
            normalizations.append("markdown_json_fence")

    _check_depth(value)
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant, parse_float=_finite_float)
    except StructuredJSONRejected:
        raise
    except (ValueError, RecursionError, OverflowError):
        raise StructuredJSONRejected("json_invalid") from None
    if not isinstance(parsed, dict):
        raise StructuredJSONRejected("json_top_level_object_required")
    return StructuredJSON(content=value, normalizations=tuple(normalizations))
