"""Bounded, local text decoding hints; never an HTTP/application interpretation.

Explicit URL/legacy percent-u escapes, HTML entities, Unicode escapes,
conservative Base64/Base64url candidates and static Log4j expressions are supported.
Offsets are Python character offsets in the unchanged original payload. Different
occurrences remain separate items; nested transforms stay in the same item.

No decoded content is evaluated, rendered, fetched, decompressed, or logged. The
returned data is as sensitive and untrusted as the input and must use the same
encrypted storage and administrator-only, audited read path. Resource limits
apply independently of the caller's LLM input budget. The serialized size bound
uses json.dumps(..., ensure_ascii=True), including its default spaces.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from html.entities import html5
from urllib.parse import unquote_to_bytes

from app.services.log4j_normalization import normalize_log4j


DECODER_VERSION = "waf-text-decoder-v2"
MAX_SCAN_CHARS = 262_144
MAX_ORIGINAL_CHARS = 1_024
MAX_DECODED_CHARS = 4_096
MAX_ITEMS = 20
MAX_CANDIDATES = 256
MAX_DECODE_STEPS = 3
MAX_SERIALIZED_BYTES = 32_768
_SERIALIZATION_RESERVE = 2_048

_HEX = frozenset("0123456789abcdefABCDEF")
_DELIMITERS = frozenset("\"'&?=,;:{}[]<>")
_ENTITY = re.compile(r"&(?:#[xX]?[0-9A-Za-z]{1,10}|[A-Za-z][A-Za-z0-9]{0,31});")
_INCOMPLETE_ENTITY = re.compile(r"&(?:#[xX]?[0-9A-Za-z]{0,10}|amp|lt|gt|quot|apos)(?=$|[\s\"'<>&])")
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}\Z")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}\Z")


class _DecodeIssue(Exception):
    """Internal exception containing a static code only, never input text."""


def _add_warning(warnings: list[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)


def _is_delimiter(character: str) -> bool:
    return character.isspace() or character in _DELIMITERS


def _fragments(text: str):
    """Linear lexical scan; entity lookahead is bounded by the regex itself.

    HTTP/JSON-style separators split independent values. Explicit entities are
    kept together with adjacent text, as are bounded lookup/escape braces.
    Trailing '=' runs are retained so malformed padding is rejected whole,
    while query/assignment separators still split independent values.
    This is intentionally not a vendor parser or an attempt to repair input.
    """
    start = None
    index = 0
    brace_depth = 0
    encoded_prefix = False
    while index < len(text):
        character = text[index]
        # Keep a lookup (including separators and nested expressions) intact.
        # A malformed expression ends at the line boundary, never by decoding
        # a plausible-looking child fragment out of its surrounding expression.
        if brace_depth:
            if character in "\r\n":
                yield start, index
                start, brace_depth, encoded_prefix = None, 0, False
            else:
                if character == "{":
                    brace_depth += 1
                elif character == "}":
                    brace_depth -= 1
                index += 1
                continue
        if character == "{" and start is not None and (
            text[index - 1] == "$" or text[max(start, index - 2):index] == "\\u" or encoded_prefix
        ):
            brace_depth = 1
            index += 1
            continue
        if character == "&":
            entity = _ENTITY.match(text, index)
            if entity:
                if start is None:
                    start = index
                encoded_prefix = True
                index = entity.end()
                continue
        if character == "=" and start is not None:
            padding_end = index + 1
            while padding_end < len(text) and text[padding_end] == "=":
                padding_end += 1
            if padding_end == len(text) or (
                text[padding_end] != "=" and _is_delimiter(text[padding_end])
            ):
                yield start, padding_end
                start = None
                encoded_prefix = False
                index = padding_end
                continue
        if _is_delimiter(character):
            if start is not None:
                yield start, index
                start = None
                encoded_prefix = False
        elif start is None:
            start = index
        if character in "%\\":
            encoded_prefix = True
        index += 1
    if start is not None:
        yield start, len(text)


def _base64_shape(value: str) -> bool:
    # Padding is an explicit hint. Unpadded candidates need a longer token and
    # later must decode to structured text, not another ordinary identifier.
    minimum = 8 if value.endswith("=") else 12
    return (
        minimum <= len(value) <= MAX_ORIGINAL_CHARS
        and (len(value) % 4 == 0 if "=" in value else len(value) % 4 != 1)
        and (_BASE64.fullmatch(value) is not None or _BASE64URL.fullmatch(value) is not None)
    )


def _unicode_marker(value: str) -> bool:
    # Do not reinterpret an escaped backslash (e.g. a JSON literal \\u003c).
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            if value[index + 1] == "\\":
                index += 2
                continue
            if value[index + 1] in "ux":
                return True
        index += 1
    return False


def _has_marker(value: str) -> bool:
    return "%" in value or _unicode_marker(value) or _ENTITY.search(value) is not None or "${" in value


def _url_percent_u(value: str) -> str:
    """Legacy %u UTF-16 candidates plus strict UTF-8 percent bytes, not unescape()."""
    output = []
    index = 0
    while index < len(value):
        if not value.startswith("%u", index):
            output.append(value[index])
            index += 1
            continue
        end = index + 6
        digits = value[index + 2:end]
        if len(digits) != 4 or any(char not in _HEX for char in digits):
            raise _DecodeIssue("invalid_unicode_escape")
        codepoint = int(digits, 16)
        if 0xD800 <= codepoint <= 0xDBFF:
            low_end = end + 6
            low_digits = value[end + 2:low_end]
            if value[end:end + 2] != "%u" or len(low_digits) != 4 or any(char not in _HEX for char in low_digits):
                raise _DecodeIssue("unpaired_unicode_surrogate")
            low = int(low_digits, 16)
            if not 0xDC00 <= low <= 0xDFFF:
                raise _DecodeIssue("unpaired_unicode_surrogate")
            codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00
            end = low_end
        elif 0xDC00 <= codepoint <= 0xDFFF:
            raise _DecodeIssue("unpaired_unicode_surrogate")
        output.append("".join(f"%{byte:02X}" for byte in chr(codepoint).encode("utf-8")))
        index = end
    return _url_percent("".join(output))


def _url_percent(value: str) -> str:
    index = 0
    while index < len(value):
        if value[index] == "%":
            if index + 2 >= len(value) or any(char not in _HEX for char in value[index + 1:index + 3]):
                raise _DecodeIssue("invalid_url_percent_escape")
            index += 3
        else:
            index += 1
    try:
        # Deliberately not unquote_plus: '+' has context-dependent semantics.
        return unquote_to_bytes(value).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, UnicodeEncodeError):
        raise _DecodeIssue("url_percent_not_utf8") from None


def _html_entity(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        entity = match.group()[1:-1]
        if not entity.startswith("#"):
            decoded = html5.get(entity + ";")
            if decoded is None:
                raise _DecodeIssue("invalid_html_entity")
            return decoded
        try:
            is_hex = len(entity) > 1 and entity[1] in "xX"
            digits = entity[2:] if is_hex else entity[1:]
            if not digits or any(character not in (_HEX if is_hex else "0123456789") for character in digits):
                raise ValueError
            codepoint = int(digits, 16 if is_hex else 10)
            # HTML's legacy replacement rules can change invalid codepoints.
            # Do not silently substitute U+FFFD or Windows-1252 characters.
            if codepoint == 0 or 0x80 <= codepoint <= 0x9F or 0xD800 <= codepoint <= 0xDFFF:
                raise ValueError
            return chr(codepoint)
        except (ValueError, OverflowError):
            raise _DecodeIssue("invalid_html_codepoint") from None

    return _ENTITY.sub(replace, value)


def _unicode_escape(value: str) -> str:
    output = []
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\" or index + 1 == len(value):
            output.append(character)
            index += 1
            continue
        kind = value[index + 1]
        if kind == "\\":
            output.append(value[index:index + 2])
            index += 2
            continue
        if kind not in "ux":
            output.append(character)
            index += 1
            continue
        if kind == "u" and value[index + 2:index + 3] == "{":
            closing = value.find("}", index + 3)
            digits = value[index + 3:closing] if closing >= 0 else ""
            if not digits or any(char not in _HEX for char in digits):
                raise _DecodeIssue("invalid_unicode_escape")
            codepoint = int(digits, 16)
            if codepoint > 0x10FFFF:
                raise _DecodeIssue("invalid_unicode_escape")
            if 0xD800 <= codepoint <= 0xDFFF:
                raise _DecodeIssue("unpaired_unicode_surrogate")
            output.append(chr(codepoint))
            index = closing + 1
            continue
        width = 4 if kind == "u" else 2
        end = index + 2 + width
        digits = value[index + 2:end]
        if len(digits) != width or any(char not in _HEX for char in digits):
            raise _DecodeIssue("invalid_unicode_escape")
        codepoint = int(digits, 16)
        if 0xD800 <= codepoint <= 0xDBFF:
            low_end = end + 6
            low_digits = value[end + 2:low_end]
            if value[end:end + 2] != "\\u" or len(low_digits) != 4 or any(char not in _HEX for char in low_digits):
                raise _DecodeIssue("unpaired_unicode_surrogate")
            low = int(low_digits, 16)
            if not 0xDC00 <= low <= 0xDFFF:
                raise _DecodeIssue("unpaired_unicode_surrogate")
            codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00
            end = low_end
        elif 0xDC00 <= codepoint <= 0xDFFF:
            raise _DecodeIssue("unpaired_unicode_surrogate")
        output.append(chr(codepoint))
        index = end
    return "".join(output)


def _base64_decode(value: str) -> str | None:
    urlsafe = "-" in value or "_" in value
    padded = value + "=" * (-len(value) % 4)
    try:
        binary = base64.b64decode(padded, altchars=b"-_" if urlsafe else None, validate=True)
    except (ValueError, binascii.Error):
        return None
    canonical = base64.b64encode(binary, altchars=b"-_" if urlsafe else None).decode("ascii")
    if canonical != padded:
        return None
    try:
        decoded = binary.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        if value.endswith("="):
            raise _DecodeIssue("base64_not_utf8") from None
        return None
    if len(decoded) < 4:
        return None
    if any(not (character.isprintable() or character in "\t\r\n") for character in decoded):
        raise _DecodeIssue("base64_not_printable")
    if not value.endswith("=") and all(character.isascii() and character.isalnum() for character in decoded):
        return None
    return decoded


def _decode_fragment(original: str) -> tuple[str, list[dict], list[str]]:
    current = original
    steps = []
    warnings: list[str] = []
    seen = {original}
    for _ in range(MAX_DECODE_STEPS):
        try:
            if "%u" in current:
                encoding, decoded = "url_percent_u", _url_percent_u(current)
                _add_warning(warnings, "legacy_percent_u_candidate")
            elif "%" in current:
                encoding, decoded = "url_percent", _url_percent(current)
            elif _ENTITY.search(current):
                encoding, decoded = "html_entity", _html_entity(current)
            elif _unicode_marker(current):
                encoding, decoded = "unicode_escape", _unicode_escape(current)
                if "\\u{" in current:
                    _add_warning(warnings, "unicode_codepoint_escape_candidate")
            elif _base64_shape(current):
                encoding = "base64url" if "-" in current or "_" in current else "base64"
                decoded = _base64_decode(current)
                if decoded is not None and len(current) % 4:
                    _add_warning(warnings, "base64_padding_inferred")
            elif "${" in current:
                encoding = "log4j_lookup_static"
                decoded, lookup_warnings = normalize_log4j(current)
                for code in lookup_warnings:
                    _add_warning(warnings, code)
            else:
                break
        except _DecodeIssue as issue:
            _add_warning(warnings, str(issue))
            break
        if decoded is None or decoded == current:
            break
        if len(decoded) > MAX_DECODED_CHARS:
            _add_warning(warnings, "decoded_text_too_long")
            break
        if any(0xD800 <= ord(character) <= 0xDFFF for character in decoded):
            _add_warning(warnings, "decoded_text_invalid_unicode")
            break
        if decoded in seen:
            _add_warning(warnings, "decoding_cycle_detected")
            break
        if encoding in {"base64", "base64url"}:
            _add_warning(warnings, "base64_candidate")
        if any(not character.isprintable() for character in decoded):
            _add_warning(warnings, "decoded_control_characters")
        steps.append({"encoding": encoding, "input": current, "output": decoded})
        seen.add(decoded)
        current = decoded
        # Static lookup normalization is one bounded pass, not execution. Never
        # feed its output back through another interpretation order or unescape
        # a lazy lookup in a later pass.
        if encoding == "log4j_lookup_static":
            break
    if len(steps) == MAX_DECODE_STEPS and steps[-1]["encoding"] != "log4j_lookup_static" and (_has_marker(current) or _base64_shape(current)):
        _add_warning(warnings, "max_decode_steps_reached")
    if steps or ("${" in original and warnings):
        _add_warning(warnings, "application_decoding_unverified")
    return current, steps, warnings


def _serialized_size(value: dict) -> int:
    return len(json.dumps(value, ensure_ascii=True).encode("ascii"))


def decode_payload(payload: str) -> dict:
    """Return bounded, JSON-safe decoding hints without modifying ``payload``.

    Incomplete, invalid or oversized candidates are skipped with static warning
    codes. If a later nested layer is invalid, earlier exact transforms remain
    available with that warning. ``scan_truncated`` means not all input could be
    examined; absence of items never means absence of encoded or harmful data.
    """
    if not isinstance(payload, str):
        raise ValueError("payload_must_be_text")
    text = payload[:MAX_SCAN_CHARS]
    result = {
        "decoder_version": DECODER_VERSION,
        "items": [],
        "warnings": [],
        "scan_truncated": len(payload) > len(text),
        "scanned_chars": len(text),
        "total_chars": len(payload),
    }
    if result["scan_truncated"]:
        _add_warning(result["warnings"], "scan_limit_reached")
    if _INCOMPLETE_ENTITY.search(text):
        _add_warning(result["warnings"], "incomplete_html_entity")
    candidates = 0
    for start, end in _fragments(text):
        # A fragment cut at the scan boundary must not be presented as complete.
        if end == len(text) and len(payload) > len(text):
            _add_warning(result["warnings"], "candidate_crosses_scan_boundary")
            break
        original = text[start:end]
        explicit = _has_marker(original)
        if len(original) > MAX_ORIGINAL_CHARS:
            if explicit or (original.endswith("=") and (_BASE64.fullmatch(original) or _BASE64URL.fullmatch(original))):
                _add_warning(result["warnings"], "candidate_too_long")
            continue
        if not explicit and not _base64_shape(original):
            continue
        if candidates >= MAX_CANDIDATES or len(result["items"]) >= MAX_ITEMS:
            _add_warning(result["warnings"], "candidate_limit_reached" if candidates >= MAX_CANDIDATES else "item_limit_reached")
            result["scan_truncated"] = True
            result["scanned_chars"] = end
            break
        candidates += 1
        decoded, steps, warnings = _decode_fragment(original)
        # Unchanged lookup expressions are useful static observations too. Do
        # not manufacture a conversion step for something we did not resolve.
        if not steps and not ("${" in original and warnings):
            for code in warnings:
                _add_warning(result["warnings"], code)
            continue
        item = {
            "id": f"decoded-{len(result['items']) + 1}",
            "field": "payload",
            "start": start,
            "end": end,
            "original": original,
            "decoded": decoded,
            "steps": steps,
            "warnings": warnings,
        }
        result["items"].append(item)
        if _serialized_size(result) > MAX_SERIALIZED_BYTES - _SERIALIZATION_RESERVE:
            result["items"].pop()
            _add_warning(result["warnings"], "serialized_output_limit_reached")
            result["scan_truncated"] = True
            result["scanned_chars"] = end
            break
    return result
