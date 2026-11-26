"""Explicit vendor-scoped capture framing; v1 remains immutable."""
from . import parser_engine_v1 as legacy
from ..agent.capture_separator_v1 import ENGINE, VERSION, scan_capture_boundaries
from .http_parser import parse_http_payload


CAPTURE_ENGINE_V2 = ENGINE


def validate_options(options):
    if not isinstance(options, dict):
        raise ValueError("parser_definition_invalid")
    clean = dict(options)
    profiles = clean.pop("profiles", None)
    base = legacy.validate_options(legacy.CAPTURE_ENGINE, clean)
    if profiles is None:
        return base
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 20:
        raise ValueError("parser_profiles_invalid")
    values, seen = [], set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("parser_profile_invalid")
        flags = dict(profile)
        separator = flags.pop("field_separator", "none")
        if separator not in {"none", "slash_at_slash"}:
            raise ValueError("parser_profile_invalid")
        validated = legacy.validate_options(legacy.CAPTURE_ENGINE,
            {**legacy.DEFAULT_OPTIONS, "profiles": [flags]})["profiles"][0]
        if separator != "none" and (validated["line_endings"] != "actual"
                or validated["request_line_whitespace"] != "strict"
                or validated["skip_leading_blank_lines"]):
            raise ValueError("parser_separator_format_unsupported")
        key = (validated["waf_vendor"].casefold(), validated["line_endings"], separator)
        if key in seen:
            raise ValueError("parser_profile_selector_duplicate")
        seen.add(key)
        values.append({**validated, **({"field_separator": separator} if separator != "none" else {})})
    return {**base, "profiles": values}


def _parse_boundaries(raw, layout):
    # Parsing only uses a derived view. Every interpreted character has its
    # original raw mapping; no two HTTP fields are concatenated.
    units, cursor = [], 0
    for start, end in layout.boundaries:
        units.extend((raw[index], index, index + 1) for index in range(cursor, start))
        units.append(("\n", start, end))
        cursor = end
    units.extend((raw[index], index, index + 1) for index in range(cursor, len(raw)))
    parsed = parse_http_payload("".join(unit[0] for unit in units))
    spans = {}
    for field, ranges in parsed["raw_source_spans"].items():
        spans[field] = [[units[start][1] if start < len(units) else len(raw),
            units[end - 1][2] if end > start else (units[start][1] if start < len(units) else len(raw))]
            for start, end in ranges]
    parsed["raw_source_spans"] = spans
    for field in ("request_line", "body"):
        if spans.get(field):
            start, end = spans[field][0]
            parsed[field] = raw[start:end]
    parsed["warnings"] = list(dict.fromkeys([*parsed["warnings"], "capture_serialization_unverified"]))
    if parsed["parse_status"] == "success":
        parsed["parse_status"] = "partial"
    return parsed


def parse_capture_payload(raw, options, *, waf_vendor=None):
    options = validate_options(options)
    vendor = waf_vendor.strip().casefold() if isinstance(waf_vendor, str) else None
    mode = legacy._capture_line_endings(raw)
    eligible = [(index, profile) for index, profile in enumerate(options.get("profiles", []))
        if profile["waf_vendor"].casefold() == vendor]
    confirmed = [(index, profile, layout) for index, profile in eligible
        if profile.get("field_separator") == "slash_at_slash"
        and (layout := scan_capture_boundaries(raw)) is not None]
    if len(confirmed) == 1:
        index, profile, layout = confirmed[0]
        parsed = _parse_boundaries(raw, layout)
        parsed["capture_format"] = {key: profile[key] for key in legacy.DEFAULT_OPTIONS} | {"field_separator": "slash_at_slash"}
        parsed["capture_selection"] = {"status": "matched", "profile_index": index,
            "profile_name": profile["name"], "line_endings": mode}
        parsed["capture_boundaries"] = {"version": VERSION, "engine_version": ENGINE,
            "status": "confirmed", "spans": [list(pair) for pair in layout.boundaries]}
        return parsed
    plain = [(index, profile) for index, profile in eligible
        if profile.get("field_separator", "none") == "none" and profile["line_endings"] == mode]
    if len(plain) == 1 and not confirmed:
        index, profile = plain[0]
        parsed = legacy.parse_capture_payload(raw, legacy.CAPTURE_ENGINE,
            {key: profile[key] for key in legacy.DEFAULT_OPTIONS})
        parsed["capture_selection"] = {"status": "matched", "profile_index": index,
            "profile_name": profile["name"], "line_endings": mode}
        return parsed
    if not options.get("profiles"):
        return legacy.parse_capture_payload(raw, legacy.CAPTURE_ENGINE, options)
    parsed = parse_http_payload(raw)
    parsed["capture_selection"] = {"status": "generic_fallback", "line_endings": mode,
        "reason_code": "capture_profile_ambiguous" if confirmed else "capture_profile_unmatched"}
    return parsed
