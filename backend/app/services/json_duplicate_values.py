"""Bounded duplicate-member observations, not application JSON semantics.

Only a complete, syntactically valid JSON document can produce equality or
difference observations. Object scopes remain separate, including inside arrays.
Strings use JSON escape interpretation only; Unicode normalization is not used.
Numbers retain their literal spelling: identical valid literals can compare
equal, different ordinary integer literals can compare different, and other
different numeric spellings remain uncomparable (for example 2 versus 2.0).
Containers are never compared recursively, and no first/last-member preference
or server-side behavior is inferred.

The public result contains fixed codes and original Python-character offsets
only. Keys, values, paths and parser exception text never leave this module.
There is no logging, cache, network, environment or database access. This helper
does not change the existing request-integrity v1/v2 contract or its callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re


VERSION = "json-duplicate-values-v1"
SAME_CODE = "json_duplicate_scalar_values_equal"
DIFFERENT_CODE = "json_duplicate_scalar_values_differ"
UNCOMPARABLE_CODE = "json_duplicate_values_uncomparable"
MAX_UTF8_BYTES = 65_536
MAX_DEPTH = 64
MAX_NODES = 4_096
MAX_STRING_CHARS = 4_096
MAX_NUMBER_CHARS = 128
MAX_DUPLICATE_GROUPS = 32
MAX_DUPLICATE_MEMBERS = 128
MAX_SOURCE_OFFSET = 2_147_483_647

_WHITESPACE = frozenset(" \t\r\n")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


class _InspectionFailure(Exception):
    """The message is always a fixed code, never source/parser text."""


@dataclass(slots=True, repr=False)
class _Scalar:
    kind: str
    value: str | None = None


@dataclass(slots=True, repr=False)
class _Member:
    key_span: tuple[int, int]
    value_span: tuple[int, int]
    scalar: _Scalar


@dataclass(slots=True, repr=False)
class _Frame:
    kind: str
    start: int
    state: str
    members: dict[str, list[_Member]] = field(default_factory=dict)
    current_key: str | None = None
    current_key_span: tuple[int, int] | None = None


def _comparison(members: list[_Member]) -> str:
    scalars = [member.scalar for member in members]
    if any(item.kind in {"container", "uncomparable"} for item in scalars):
        return UNCOMPARABLE_CODE
    if len({item.kind for item in scalars}) != 1:
        return DIFFERENT_CODE  # In particular, true is not the number 1.
    values = [item.value for item in scalars]
    if all(value == values[0] for value in values[1:]):
        return SAME_CODE
    if scalars[0].kind != "number":
        return DIFFERENT_CODE
    if all(value is not None and value != "-0" and _INTEGER.fullmatch(value) for value in values):
        return DIFFERENT_CODE
    return UNCOMPARABLE_CODE


class _Parser:
    """Iterative parser with bounded input, stack, tokens and retained members."""

    def __init__(self, text: str, source_start: int) -> None:
        self.text = text
        self.source_start = source_start
        self.position = 0
        self.nodes = 0
        self.frames: list[_Frame] = []
        self.complete = False
        self.observations: list[dict] = []

    def _space(self) -> None:
        while self.position < len(self.text) and self.text[self.position] in _WHITESPACE:
            self.position += 1

    def _node(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise _InspectionFailure("json_node_limit")

    def _string(self) -> str:
        start = self.position
        self.position += 1
        while self.position < len(self.text):
            if self.position - start >= MAX_STRING_CHARS:
                raise _InspectionFailure("json_string_limit")
            character = self.text[self.position]
            self.position += 1
            if character == "\\":
                self.position += 1
            elif character == '"':
                try:
                    # Only one bounded string token, never a container or number.
                    return json.loads(self.text[start:self.position])
                except (ValueError, RecursionError):
                    raise _InspectionFailure("json_syntax_invalid") from None
        raise _InspectionFailure("json_syntax_invalid")

    def _finish_value(self, scalar: _Scalar, start: int, end: int) -> None:
        if not self.frames:
            self.complete = True
            return
        parent = self.frames[-1]
        if parent.kind == "object":
            if parent.current_key is None or parent.current_key_span is None:
                raise _InspectionFailure("json_syntax_invalid")
            members = parent.members.setdefault(parent.current_key, [])
            if len(members) >= MAX_DUPLICATE_MEMBERS:
                raise _InspectionFailure("json_duplicate_member_limit")
            members.append(_Member(parent.current_key_span, (start, end), scalar))
            parent.current_key = None
            parent.current_key_span = None
        parent.state = "after_value"

    def _value(self) -> None:
        self._space()
        self._node()
        start = self.position
        if start >= len(self.text):
            raise _InspectionFailure("json_syntax_invalid")
        if self.frames:
            self.frames[-1].state = "waiting"
        character = self.text[start]
        if character in "{[":
            if len(self.frames) >= MAX_DEPTH:
                raise _InspectionFailure("json_depth_limit")
            self.position += 1
            self.frames.append(_Frame("object" if character == "{" else "array", start,
                                      "key_or_end" if character == "{" else "value_or_end"))
            return
        if character == '"':
            value = self._string()
            kind = "uncomparable" if any(0xD800 <= ord(char) <= 0xDFFF for char in value) else "string"
            scalar = _Scalar(kind, value)
        elif character in "tfn":
            literal = {"t": "true", "f": "false", "n": "null"}[character]
            if not self.text.startswith(literal, start):
                raise _InspectionFailure("json_syntax_invalid")
            self.position += len(literal)
            scalar = _Scalar("null" if character == "n" else "boolean", literal)
        else:
            match = _NUMBER.match(self.text, start)
            if match is None:
                raise _InspectionFailure("json_syntax_invalid")
            if match.end() - start > MAX_NUMBER_CHARS:
                raise _InspectionFailure("json_number_limit")
            self.position = match.end()
            scalar = _Scalar("number", self.text[start:self.position])
        self._finish_value(scalar, start, self.position)

    def _close(self) -> None:
        frame = self.frames.pop()
        self.position += 1
        for members in frame.members.values():
            if len(members) < 2:
                continue
            if len(self.observations) >= MAX_DUPLICATE_GROUPS:
                raise _InspectionFailure("json_duplicate_group_limit")
            self.observations.append({
                "code": _comparison(members),
                "key_spans": [[self.source_start + a, self.source_start + b] for a, b in (m.key_span for m in members)],
                "value_spans": [[self.source_start + a, self.source_start + b] for a, b in (m.value_span for m in members)],
            })
        self._finish_value(_Scalar("container"), frame.start, self.position)

    def parse(self) -> list[dict]:
        while not self.complete:
            self._space()
            if not self.frames:
                self._value()
                continue
            if self.position >= len(self.text):
                raise _InspectionFailure("json_syntax_invalid")
            frame = self.frames[-1]
            character = self.text[self.position]
            if frame.kind == "object" and frame.state in {"key_or_end", "key_required"}:
                if character == "}" and frame.state == "key_or_end":
                    self._close()
                    continue
                if character != '"':
                    raise _InspectionFailure("json_syntax_invalid")
                self._node()
                start = self.position
                key = self._string()
                if any(0xD800 <= ord(char) <= 0xDFFF for char in key):
                    raise _InspectionFailure("json_key_unicode_uncomparable")
                frame.current_key, frame.current_key_span = key, (start, self.position)
                self._space()
                if self.position >= len(self.text) or self.text[self.position] != ":":
                    raise _InspectionFailure("json_syntax_invalid")
                self.position += 1
                frame.state = "value"
            elif frame.state in {"value", "value_or_end", "value_required"}:
                if frame.kind == "array" and character == "]" and frame.state == "value_or_end":
                    self._close()
                else:
                    self._value()
            elif frame.state == "after_value":
                if character == ("}" if frame.kind == "object" else "]"):
                    self._close()
                elif character == ",":
                    self.position += 1
                    frame.state = "key_required" if frame.kind == "object" else "value_required"
                else:
                    raise _InspectionFailure("json_syntax_invalid")
            else:
                raise _InspectionFailure("json_syntax_invalid")
        self._space()
        if self.position != len(self.text):
            raise _InspectionFailure("json_syntax_invalid")
        return sorted(self.observations, key=lambda observation: observation["key_spans"][0][0])


def inspect_json_duplicate_values(text: str, *, source_start: int = 0, complete: bool = True) -> dict:
    """Inspect a known JSON text; caller must establish body/content encoding.

    ``source_start`` is its start in the unchanged original payload, measured in
    Python characters, with end-exclusive spans. ``complete=False`` must be used
    when the capture/document may be partial; no partial comparisons are returned.
    A complete result does not prove capture completeness, attack/normal meaning,
    or how an application handles duplicate members. No HTTP decoding occurs.
    """
    result = {"version": VERSION, "status": "uncomparable", "observations": [], "limitations": []}

    def fail(code: str) -> dict:
        result["limitations"] = [code]
        return result

    if not isinstance(text, str) or type(source_start) is not int or type(complete) is not bool:
        return fail("json_inspection_input_invalid")
    if source_start < 0 or source_start > MAX_SOURCE_OFFSET - len(text):
        return fail("json_source_offset_invalid")
    if not complete:
        return fail("json_capture_incomplete")
    # Precheck characters before allocating an encoded copy of attacker input.
    if len(text) > MAX_UTF8_BYTES:
        return fail("json_byte_limit")
    try:
        if len(text.encode("utf-8", errors="strict")) > MAX_UTF8_BYTES:
            return fail("json_byte_limit")
    except UnicodeEncodeError:
        return fail("json_input_unicode_uncomparable")
    try:
        observations = _Parser(text, source_start).parse()
    except _InspectionFailure as failure:
        return fail(str(failure))
    result["status"] = "complete"
    result["observations"] = observations
    return result
