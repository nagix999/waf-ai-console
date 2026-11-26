"""Exact capture boundaries, never an unconditional string replacement.

Only an explicitly pinned vendor profile can authorize this interpretation.
Offsets always refer to the unchanged received payload. Ambiguous/incomplete
captures fall back unchanged; body and request-target values are not rewritten.
"""
from dataclasses import dataclass
import re


VERSION = "capture-boundary-v1"
ENGINE = "http-capture-v2"
SEPARATOR = "/@/"
MAX_CAPTURE_CHARS = 262_144
MAX_HEADER_FIELDS = 256
_REQUEST = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+ ([^ \t\r\n]+) HTTP/[0-9](?:\.[0-9])?\Z")
_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[^\r\n\x00-\x08\x0a-\x1f\x7f]*\Z")


@dataclass(frozen=True)
class CaptureLayout:
    boundaries: tuple[tuple[int, int], ...]
    segments: tuple[tuple[int, int], ...]


def scan_capture_boundaries(raw: str) -> CaptureLayout | None:
    """Recognize a bounded complete serialized request head, not body text.

    A complete HTTP version and a double boundary terminating the head are
    required. Tokens within the request target remain inside its raw segment.
    Any unrecognized field makes the entire format ambiguous, not partially
    stripped. Quoted field values cannot cross a capture boundary.
    """
    if not isinstance(raw, str) or len(raw) > MAX_CAPTURE_CHARS:
        return None
    cursor, request_end = 0, None
    for _ in range(MAX_HEADER_FIELDS + 1):
        position = raw.find(SEPARATOR, cursor)
        if position < 0:
            break
        match = _REQUEST.fullmatch(raw[:position])
        if match is not None:
            target = match.group(1)
            method = raw.partition(" ")[0]
            if target.startswith(("/", "http://", "https://")) or (method == "OPTIONS" and target == "*") or (method == "CONNECT" and ":" in target):
                request_end = position
            break
        # Only the request-target may contain literal delimiter text. Actual
        # newline framing is not this profile's serialization.
        if "\r" in raw[:position] or "\n" in raw[:position]:
            return None
        cursor = position + len(SEPARATOR)
    if request_end is None:
        return None
    spans = [(request_end, request_end + len(SEPARATOR))]
    segments = [(0, request_end)]
    cursor = request_end + len(SEPARATOR)
    for _ in range(MAX_HEADER_FIELDS + 1):
        if raw.startswith(SEPARATOR, cursor):
            spans.append((cursor, cursor + len(SEPARATOR)))
            body_start = cursor + len(SEPARATOR)
            if body_start < len(raw):
                segments.append((body_start, len(raw)))
            return CaptureLayout(tuple(spans), tuple(segments))
        end = raw.find(SEPARATOR, cursor)
        if end < 0:
            return None
        field = raw[cursor:end]
        # Treat unresolved delimiters inside values as ambiguity, not a reason
        # to concatenate the value with the following field.
        if _HEADER.fullmatch(field) is None or field.count('"') % 2:
            return None
        segments.append((cursor, end))
        spans.append((end, end + len(SEPARATOR)))
        cursor = end + len(SEPARATOR)
    return None


def confirmed_capture_boundaries(raw: str, parsed: dict) -> CaptureLayout | None:
    """Revalidate metadata produced by a pinned capture-v2 vendor profile."""
    if not isinstance(parsed, dict):
        return None
    selection, options, metadata = (parsed.get(key) for key in
        ("capture_selection", "capture_format", "capture_boundaries"))
    if (not isinstance(selection, dict) or selection.get("status") != "matched"
            or type(selection.get("profile_index")) is not int
            or not isinstance(options, dict) or options.get("field_separator") != "slash_at_slash"
            or not isinstance(metadata, dict) or metadata.get("engine_version") != ENGINE
            or metadata.get("version") != VERSION or metadata.get("status") != "confirmed"):
        return None
    layout = scan_capture_boundaries(raw)
    if layout is None or metadata.get("spans") != [list(pair) for pair in layout.boundaries]:
        return None
    return layout


def subtract_boundaries(spans, boundaries):
    """Keep continuous original ranges, never join across omitted framing."""
    result = []
    for start, end in spans:
        cursor = start
        for a, b in boundaries:
            if b <= cursor or a >= end:
                continue
            if cursor < a:
                result.append((cursor, a))
            cursor = min(end, b)
        if cursor < end:
            result.append((cursor, end))
    return result
