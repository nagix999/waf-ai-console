"""Bounded, content-free diagnostics for technical JSON probes only."""

import json
from typing import Any


MAX_INSPECTION_CHARS = 65_536
MAX_INSPECTION_DEPTH = 64
_JSON_WHITESPACE = " \t\r\n"


def _reject_constant(value: str) -> None:
    raise ValueError("non_json_constant")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def probe_json_decoder() -> json.JSONDecoder:
    return json.JSONDecoder(parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def inspection_limit_exceeded(content: str) -> bool:
    if len(content) > MAX_INSPECTION_CHARS:
        return True
    depth = 0
    in_string = escaped = False
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
            if depth > MAX_INSPECTION_DEPTH:
                return True
        elif char in "]}":
            depth = max(0, depth - 1)
    return False


def _repeated_suffix(content: str) -> tuple[int, int]:
    # Detect only exact, adjacent repeats at the end, not similar wording.
    # The bounded scan never retains the repeated text in diagnostics.
    tail = content.rstrip(_JSON_WHITESPACE)[-4096:]
    for width in range(1, min(64, len(tail) // 4) + 1):
        unit = tail[-width:]
        if not unit.strip(_JSON_WHITESPACE):
            continue
        count = 1
        while (count + 1) * width <= len(tail) and tail[-(count + 1) * width:-count * width] == unit:
            count += 1
        if count >= 4 and width * count >= 32:
            return width, count
    return 0, 0


def inspect_json_output(body: Any) -> dict[str, Any]:
    """Syntax observations, never a successful-completion or schema verdict.

    Invalid and truncated JSON cannot always be distinguished without guessing.
    All labels are fixed; neither response content nor reasoning is returned.
    """
    result = {
        "status": "unavailable", "content_chars": None,
        "trailing_whitespace_chars": None, "trailing_content_chars": None,
        "repeated_suffix_unit_chars": None, "repeated_suffix_count": None,
    }
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return result
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return result
    result["content_chars"] = len(content)
    if inspection_limit_exceeded(content):
        result["status"] = "inspection_limit"
        return result
    result["trailing_whitespace_chars"] = len(content) - len(content.rstrip(_JSON_WHITESPACE))
    unit_chars, count = _repeated_suffix(content)
    result.update(repeated_suffix_unit_chars=unit_chars, repeated_suffix_count=count)
    stripped = content.lstrip(_JSON_WHITESPACE)
    if not stripped:
        result["status"] = "empty"
        return result
    try:
        _, end = probe_json_decoder().raw_decode(stripped)
    except (ValueError, RecursionError):
        result["status"] = "invalid_or_incomplete"
        return result
    trailing = stripped[end:].strip(_JSON_WHITESPACE)
    result["trailing_content_chars"] = len(trailing)
    result["status"] = "complete_with_trailing_content" if trailing else "complete"
    return result
