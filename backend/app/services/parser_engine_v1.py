"""Bundled, bounded capture-format adapters; no executable user/model rules.

Only the explicitly selected header serialization is interpreted. Body bytes,
raw evidence text and every source offset remain in the original capture.
"""
import re

from .http_parser import parse_http_payload

LEGACY_ENGINE = "generic-http-v3"
CAPTURE_ENGINE = "http-capture-v1"
MAX_CAPTURE_CHARS = 262_144
DEFAULT_OPTIONS = {"line_endings": "actual", "request_line_whitespace": "strict",
                   "skip_leading_blank_lines": False}
_REQUEST = re.compile(r"([!#$%&'*+.^_`|~0-9A-Za-z-]+)([ \t]+)([^ \t\r\n]+)(?:([ \t]+)(HTTP/[0-9](?:\.[0-9])?))?\Z")


def validate_options(engine, options):
    if not isinstance(options, dict):
        raise ValueError("parser_definition_invalid")
    options = {key: value for key, value in options.items() if key != "profiles" or value is not None}
    if engine not in {LEGACY_ENGINE, CAPTURE_ENGINE} or set(options) not in (set(DEFAULT_OPTIONS), {*DEFAULT_OPTIONS, "profiles"}):
        raise ValueError("parser_definition_invalid")
    if (options["line_endings"] not in {"actual", "escaped_crlf", "escaped_lf"}
            or options["request_line_whitespace"] not in {"strict", "flexible"}
            or type(options["skip_leading_blank_lines"]) is not bool):
        raise ValueError("parser_definition_invalid")
    if engine == LEGACY_ENGINE and options != DEFAULT_OPTIONS:
        raise ValueError("parser_legacy_options_protected")
    result = dict(options)
    if "profiles" in options:
        profiles, seen = options["profiles"], set()
        if not isinstance(profiles, list) or not 1 <= len(profiles) <= 20:
            raise ValueError("parser_profiles_invalid")
        result["profiles"] = []
        for profile in profiles:
            if not isinstance(profile, dict) or set(profile) != {*DEFAULT_OPTIONS, "name", "waf_vendor"}:
                raise ValueError("parser_profile_invalid")
            for key in ("name", "waf_vendor"):
                if (not isinstance(profile[key], str) or not profile[key].strip() or len(profile[key]) > 120
                        or re.search(r"[\x00-\x1f\x7f]", profile[key])):
                    raise ValueError("parser_profile_invalid")
            clean = validate_options(CAPTURE_ENGINE, {key: profile[key] for key in DEFAULT_OPTIONS})
            selector = (profile["waf_vendor"].strip().casefold(), clean["line_endings"])
            if selector in seen:
                raise ValueError("parser_profile_selector_duplicate")
            seen.add(selector)
            result["profiles"].append({"name": profile["name"].strip(), "waf_vendor": profile["waf_vendor"].strip(), **clean})
    return result


def _limited(raw, warning):
    parsed = parse_http_payload(raw)
    parsed["warnings"] = list(dict.fromkeys([*parsed["warnings"], warning]))
    if parsed["parse_status"] == "success":
        parsed["parse_status"] = "partial"
    return parsed


def _actual_request_line_recognized(raw):
    # Escapes inside the request-target/header values are data, not framing.
    # Recognize only the first actual line, never an HTTP-looking body line.
    # A headerless/versionless serialized request whose body happens to make
    # this same shape is ambiguous; keep the generic interpretation then.
    head = raw[:MAX_CAPTURE_CHARS]
    first = re.match(r"(?:\r?\n)*([^\r\n]*)", head).group(1)
    match = _REQUEST.fullmatch(first)
    if match is None or (match.group(5) is None and "\n" not in head):
        return False
    method, target = match.group(1), match.group(3)
    return (target.startswith(("/", "http://", "https://"))
            or (method == "OPTIONS" and target == "*")
            or (method == "CONNECT" and ":" in target))


def _capture_line_endings(raw):
    if _actual_request_line_recognized(raw):
        return "actual"
    candidates = [(position, mode) for token, mode in (("\n", "actual"), ("\\r\\n", "escaped_crlf"), ("\\n", "escaped_lf"))
                  if (position := raw.find(token, 0, MAX_CAPTURE_CHARS)) >= 0]
    return min(candidates)[1] if candidates else "actual"


def parse_capture_payload(raw, engine, options, *, waf_vendor=None):
    options = validate_options(engine, options)
    if engine == LEGACY_ENGINE:
        return parse_http_payload(raw)
    if options.get("profiles"):
        mode = _capture_line_endings(raw)
        vendor = waf_vendor.strip().casefold() if isinstance(waf_vendor, str) else None
        for index, profile in enumerate(options["profiles"]):
            if profile["waf_vendor"].casefold() == vendor and profile["line_endings"] == mode:
                parsed = parse_capture_payload(raw, engine, {key: profile[key] for key in DEFAULT_OPTIONS})
                parsed["capture_selection"] = {"status": "matched", "profile_index": index,
                    "profile_name": profile["name"], "line_endings": mode}
                return parsed
        parsed = parse_http_payload(raw)
        parsed["capture_selection"] = {"status": "generic_fallback", "reason_code": "capture_profile_unmatched", "line_endings": mode}
        return parsed
    if len(raw) > MAX_CAPTURE_CHARS:
        return _limited(raw, "capture_inspection_limit")
    mode = options["line_endings"]
    token = {"escaped_crlf": "\\r\\n", "escaped_lf": "\\n"}.get(mode)
    if token:
        if _actual_request_line_recognized(raw):
            return _limited(raw, "capture_line_endings_ambiguous")
        marker = raw.find(token + token)
        head_end = marker + len(token) * 2 if marker >= 0 else len(raw)
        head = raw[:head_end]
        # Mixed actual/serialized separators cannot establish one interpretation.
        if "\r" in head or "\n" in head or (mode == "escaped_lf" and "\\r" in head):
            return _limited(raw, "capture_line_endings_ambiguous")
        units, position = [], 0
        while position < head_end:
            if raw.startswith(token, position):
                units.append(("\n", position, position + len(token)))
                position += len(token)
            else:
                units.append((raw[position], position, position + 1))
                position += 1
        # Do not unescape body strings or invent a body without a delimiter.
        units.extend((raw[index], index, index + 1) for index in range(head_end, len(raw)))
    else:
        units = [(character, index, index + 1) for index, character in enumerate(raw)]
    if options["skip_leading_blank_lines"]:
        start = 0
        while start < len(units):
            if units[start][0] == "\n":
                start += 1
            elif units[start][0] == "\r" and start + 1 < len(units) and units[start + 1][0] == "\n":
                start += 2
            else:
                break
        units = units[start:]
    if options["request_line_whitespace"] == "flexible":
        first_end = next((index for index, unit in enumerate(units) if unit[0] in "\r\n"), len(units))
        first = "".join(unit[0] for unit in units[:first_end])
        match = _REQUEST.fullmatch(first)
        if match:
            spaces = [match.span(group) for group in (2, 4) if match.start(group) >= 0]
            for start, end in reversed(spaces):
                units[start:end] = [(" ", units[start][1], units[end - 1][2])]
    view = "".join(unit[0] for unit in units)
    parsed = parse_http_payload(view)
    spans = {}
    for field, ranges in parsed["raw_source_spans"].items():
        mapped = []
        for start, end in ranges:
            raw_start = units[start][1] if start < len(units) else len(raw)
            raw_end = units[end - 1][2] if end > start else raw_start
            mapped.append([raw_start, raw_end])
        spans[field] = mapped
    parsed["raw_source_spans"] = spans
    if spans.get("request_line"):
        start, end = spans["request_line"][0]
        parsed["request_line"] = raw[start:end]
    if spans.get("body"):
        start, end = spans["body"][0]
        parsed["body"] = raw[start:end]
    parsed["capture_format"] = dict(options)
    if mode != "actual":
        # Header recognition alone cannot prove how the body was serialized.
        # Keep independent verification until wire/body support is established;
        # the management feature must not silently weaken that safety condition.
        parsed["warnings"] = list(dict.fromkeys([*parsed["warnings"], "capture_serialization_unverified"]))
        if parsed["parse_status"] == "success":
            parsed["parse_status"] = "partial"
    return parsed


def valid_source_spans(raw, parsed):
    """A structural check, not a proof of semantic parsing correctness."""
    spans = parsed.get("raw_source_spans")
    return isinstance(spans, dict) and all(
        isinstance(field, str) and isinstance(ranges, list) and all(
            isinstance(pair, list) and len(pair) == 2 and all(type(value) is int for value in pair)
            and 0 <= pair[0] <= pair[1] <= len(raw) for pair in ranges)
        for field, ranges in spans.items())
