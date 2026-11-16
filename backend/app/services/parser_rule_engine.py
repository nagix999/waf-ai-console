"""Bounded, versioned vendor format selection over the original payload only.

No model runs here. User patterns are compiled only by native RE2, never by
Python's regular-expression engine. Ambiguous request extractions keep the
generic interpretation rather than guessing one rule. Every public diagnostic
is a fixed code or a server-owned rule label, never an exception/payload value.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import re
import time

from pydantic import ValidationError

from ..parser_rule_schemas import VendorRuleBundle
from .http_parser import parse_http_payload
from .parser_engine_v1 import CAPTURE_ENGINE, parse_capture_payload

try:
    import re2 as _re2
except ImportError:  # Deployment explicitly reports unsupported regex rules.
    _re2 = None

RULE_ENGINE_VERSION = "vendor-http-v2"
RULE_SELECTION_VERSION = "vendor-rule-selection-v1"
MAX_SOURCE_BYTES = 65_536
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 4096
MAX_REGEX_MEMORY_BYTES = 1_048_576
MAX_SECONDS = 0.1
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_JSON_WHITESPACE = " \t\r\n"
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class ParserRuleError(ValueError):
    """Only fixed safe codes may cross this boundary."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=128)
def _compile(pattern, capture):
    if _re2 is None:
        raise ParserRuleError("parser_regex_unavailable")
    try:
        options = _re2.Options()
        options.log_errors = False
        options.max_mem = MAX_REGEX_MEMORY_BYTES
        compiled = _re2.compile(pattern, options=options)
        if capture and (compiled.groups != 1 or compiled.groupindex != {"request": 1}):
            raise ParserRuleError("parser_regex_capture_invalid")
        if not capture and compiled.groups:
            raise ParserRuleError("parser_regex_match_capture_forbidden")
        return compiled
    except ParserRuleError:
        raise
    except Exception:
        raise ParserRuleError("parser_regex_invalid") from None


def validate_bundle(definition):
    """Validate the entire immutable bundle, including native regex support."""
    try:
        bundle = VendorRuleBundle.model_validate(definition).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        raise ParserRuleError("parser_rule_bundle_invalid") from None
    for rule in bundle["rules"]:
        if rule["name"] is None:
            rule.pop("name")
        proposal = rule["definition"]
        for predicate in next(iter(proposal["match"].values())):
            if predicate["kind"] == "regex_match":
                if _re2 is None:
                    raise ParserRuleError("parser_regex_unavailable")
                _compile(predicate["pattern"], False)
        extraction = proposal["extract"]
        if extraction["kind"] == "regex_capture":
            if _re2 is None:
                raise ParserRuleError("parser_regex_unavailable")
            _compile(extraction["pattern"], True)
    return bundle


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS

    def check(self):
        if time.monotonic() > self.deadline:
            raise ParserRuleError("parser_rule_time_limit")


@dataclass
class _Extracted:
    text: str
    # One decoded character maps to an exact, contiguous original token range.
    units: list[tuple[int, int]]
    kind: str


class _JsonDocument:
    """Small strict JSON reader that retains exact string-token provenance.

    Escaped Unicode (including a valid surrogate pair) maps back to its complete
    original escape token. Duplicate decoded keys are rejected everywhere.
    """

    def __init__(self, raw, budget):
        self.raw, self.budget, self.nodes = raw, budget, 0
        self.strings = {}
        self.root, position = self._value(0, (), 0)
        if self._space(position) != len(raw):
            raise ParserRuleError("parser_json_invalid")

    def _space(self, position):
        while position < len(self.raw) and self.raw[position] in _JSON_WHITESPACE:
            position += 1
        return position

    def _string(self, position):
        raw, result, units = self.raw, [], []
        position += 1
        while position < len(raw):
            if position % 256 == 0:
                self.budget.check()
            start, char = position, raw[position]
            if char == '"':
                return "".join(result), units, position + 1
            if char == "\\":
                if position + 1 >= len(raw):
                    break
                escape = raw[position + 1]
                if escape in _ESCAPES:
                    char, position = _ESCAPES[escape], position + 2
                elif escape == "u":
                    token = raw[position + 2:position + 6]
                    if len(token) != 4 or any(value not in "0123456789abcdefABCDEF" for value in token):
                        raise ParserRuleError("parser_json_invalid")
                    number, position = int(token, 16), position + 6
                    if 0xD800 <= number <= 0xDBFF:
                        low = raw[position + 2:position + 6]
                        if (raw[position:position + 2] != "\\u" or len(low) != 4
                                or any(value not in "0123456789abcdefABCDEF" for value in low)
                                or not 0xDC00 <= int(low, 16) <= 0xDFFF):
                            raise ParserRuleError("parser_json_invalid")
                        number = 0x10000 + ((number - 0xD800) << 10) + int(low, 16) - 0xDC00
                        position += 6
                    elif 0xDC00 <= number <= 0xDFFF:
                        raise ParserRuleError("parser_json_invalid")
                    char = chr(number)
                else:
                    raise ParserRuleError("parser_json_invalid")
            else:
                if ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF:
                    raise ParserRuleError("parser_json_invalid")
                position += 1
            result.append(char)
            units.append((start, position))
        raise ParserRuleError("parser_json_invalid")

    def _value(self, position, path, depth):
        self.budget.check()
        self.nodes += 1
        if depth > MAX_JSON_DEPTH or self.nodes > MAX_JSON_NODES:
            raise ParserRuleError("parser_json_limit")
        position = self._space(position)
        if position == len(self.raw):
            raise ParserRuleError("parser_json_invalid")
        raw, char = self.raw, self.raw[position]
        if char == '"':
            value, units, end = self._string(position)
            self.strings[path] = _Extracted(value, units, "json_string")
            return value, end
        if char in "[{":
            is_object, position = char == "{", self._space(position + 1)
            result, closing = ({} if is_object else []), ("}" if is_object else "]")
            if position < len(raw) and raw[position] == closing:
                return result, position + 1
            while position < len(raw):
                if is_object:
                    if raw[position] != '"':
                        raise ParserRuleError("parser_json_invalid")
                    key, _, position = self._string(position)
                    if key in result:
                        raise ParserRuleError("parser_json_duplicate_keys")
                    position = self._space(position)
                    if position == len(raw) or raw[position] != ":":
                        raise ParserRuleError("parser_json_invalid")
                    value, position = self._value(position + 1, (*path, key), depth + 1)
                    result[key] = value
                else:
                    value, position = self._value(position, (*path, str(len(result))), depth + 1)
                    result.append(value)
                position = self._space(position)
                if position < len(raw) and raw[position] == closing:
                    return result, position + 1
                if position == len(raw) or raw[position] != ",":
                    break
                position = self._space(position + 1)
            raise ParserRuleError("parser_json_invalid")
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if raw.startswith(literal, position):
                return value, position + len(literal)
        end = position
        while end < len(raw) and raw[end] in "-+0123456789.eE":
            end += 1
            if end - position > 128:
                raise ParserRuleError("parser_json_limit")
        token = raw[position:end]
        if not _NUMBER.fullmatch(token):
            raise ParserRuleError("parser_json_invalid")
        number = json.loads(token)
        if type(number) is float and not math.isfinite(number):
            raise ParserRuleError("parser_json_invalid")
        return number, end

    def resolve(self, pointer):
        path = tuple(part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")) if pointer else ()
        value = self.root
        for part in path:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and (part == "0" or part.isascii() and part.isdecimal() and not part.startswith("0")):
                if len(part) > 8 or int(part) >= len(value):
                    raise ParserRuleError("parser_json_pointer_missing")
                value = value[int(part)]
            else:
                raise ParserRuleError("parser_json_pointer_missing")
        return path, value


class _Context:
    def __init__(self, raw, budget):
        self.raw, self.budget, self.document, self.json_error = raw, budget, None, None
        self.extractions = {}

    def json(self):
        if self.json_error:
            raise ParserRuleError(self.json_error)
        if self.document is None:
            try:
                self.document = _JsonDocument(self.raw, self.budget)
            except ParserRuleError as exc:
                self.json_error = exc.code
                raise
        return self.document


def _marker_range(raw, operation):
    start, end, anchor = operation["start_marker"], operation["end_marker"], operation["start_anchor"]
    positions, position = [], 0
    while True:
        found = raw.find(start, position)
        if found < 0:
            break
        if found == 0 or anchor == "line_start" and raw[found - 1] == "\n":
            positions.append(found + len(start))
            if len(positions) > 1:
                raise ParserRuleError("parser_marker_ambiguous")
        position = found + 1
    if len(positions) != 1:
        raise ParserRuleError("parser_marker_missing")
    first = raw.find(end, positions[0])
    if first < 0:
        raise ParserRuleError("parser_marker_missing")
    if first == positions[0] or raw.find(end, first + 1) >= 0:
        raise ParserRuleError("parser_marker_ambiguous")
    return positions[0], first


def _matches(predicate, context):
    kind, raw = predicate["kind"], context.raw
    if kind == "starts_with":
        return raw.startswith(predicate["value"])
    if kind == "marker_pair_exists":
        _marker_range(raw, predicate)
        return True
    if kind == "regex_match":
        return _compile(predicate["pattern"], False).search(raw) is not None
    document = context.json()
    _, value = document.resolve(predicate["json_pointer"])
    if kind == "json_string_exists":
        return isinstance(value, str) and bool(value)
    expected = predicate["value"]
    return type(value) is type(expected) and value == expected


def _rule_matches(match, context):
    mode, predicates = next(iter(match.items()))
    outcomes = []
    for predicate in predicates:
        context.budget.check()
        try:
            outcomes.append(_matches(predicate, context))
        except ParserRuleError as exc:
            if exc.code in {"parser_rule_time_limit", "parser_regex_unavailable", "parser_regex_invalid"}:
                raise
            outcomes.append(False)
    return all(outcomes) if mode == "all" else any(outcomes)


def _extract(operation, context):
    key = json.dumps(operation, sort_keys=True, separators=(",", ":"))
    if key not in context.extractions:
        context.extractions[key] = _extract_once(operation, context)
    return context.extractions[key]


def _extract_once(operation, context):
    kind, raw = operation["kind"], context.raw
    if kind == "json_string":
        document = context.json()
        path, value = document.resolve(operation["json_pointer"])
        if not isinstance(value, str) or not value:
            raise ParserRuleError("parser_extraction_not_string")
        return document.strings[path]
    if kind == "raw_payload":
        start, end = 0, len(raw)
    elif kind == "between_markers":
        start, end = _marker_range(raw, operation)
    else:
        iterator = iter(_compile(operation["pattern"], True).finditer(raw))
        match = next(iterator, None)
        if match is None:
            raise ParserRuleError("parser_regex_no_match")
        if next(iterator, None) is not None:
            raise ParserRuleError("parser_regex_ambiguous")
        # google-re2's span() accepts only a numeric index; validation above
        # guarantees that group 1 is the sole named request capture.
        start, end = match.span(1)
        if start < 0 or start == end:
            raise ParserRuleError("parser_regex_empty_capture")
    if start == end:
        raise ParserRuleError("parser_extraction_empty")
    return _Extracted(raw[start:end], [(index, index + 1) for index in range(start, end)], kind)


def _parse_extracted(raw, extracted, options):
    parsed = parse_capture_payload(extracted.text, CAPTURE_ENGINE, options)
    spans = {}
    for field, ranges in parsed["raw_source_spans"].items():
        mapped = []
        for start, end in ranges:
            if not (0 <= start <= end <= len(extracted.units)):
                raise ParserRuleError("parser_source_mapping_invalid")
            if start == end:
                point = extracted.units[start][0] if start < len(extracted.units) else extracted.units[-1][1]
                mapped.append([point, point])
            else:
                # Consecutive decoded tokens must not bridge an unowned gap.
                if any(extracted.units[index][1] != extracted.units[index + 1][0] for index in range(start, end - 1)):
                    raise ParserRuleError("parser_source_mapping_invalid")
                mapped.append([extracted.units[start][0], extracted.units[end - 1][1]])
        spans[field] = mapped
    parsed["raw_source_spans"] = spans
    for field in ("request_line", "body"):
        if spans.get(field):
            start, end = spans[field][0]
            parsed[field] = raw[start:end]
    is_whole_original = extracted.text == raw and all(pair == (index, index + 1) for index, pair in enumerate(extracted.units))
    parsed["capture_extraction"] = {"source_kind": extracted.kind,
        "transformed": extracted.kind == "json_string" or not is_whole_original,
        "mapping_mode": "original_character_tokens-v1"}
    if not is_whole_original:
        # Old integrity observers inspect raw[body_start:] to EOF. An envelope
        # suffix / JSON escaping is not HTTP wire data and must remain unknown.
        parsed["warnings"] = list(dict.fromkeys([*parsed["warnings"], "capture_extracted_not_wire_format"]))
        if parsed["parse_status"] == "success":
            parsed["parse_status"] = "partial"
    return parsed


def _quality(parsed):
    spans = parsed["raw_source_spans"]
    if (parsed["parse_status"] == "failed" or not parsed.get("method") or not parsed.get("uri")
            or not spans.get("method") or not spans.get("request_target")
            or "request_target_not_recognized" in parsed["warnings"]
            or (not parsed.get("protocol") and "body" not in spans)):
        return None
    codes = ["request_recognized", "source_spans_mapped"]
    if parsed.get("protocol"):
        codes.append("protocol_recognized")
    if "body" in spans:
        codes.append("header_boundary_recognized")
    if "capture_extracted_not_wire_format" in parsed["warnings"]:
        codes.append("extracted_capture_hint")
    return codes


def _interpretation_key(parsed, extracted):
    fields = ("request_line", "method", "uri", "query", "protocol", "headers", "body",
              "warnings", "raw_source_spans", "raw_query_parameter_names")
    # Different options are equivalent only when their actual interpretation,
    # exact original occurrence and decoded request agree, not merely field count.
    return (extracted.text, tuple(extracted.units),
            json.dumps({field: parsed[field] for field in fields}, sort_keys=True,
                       ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def _fallback(raw, reason, candidates):
    parsed = parse_http_payload(raw)
    parsed["capture_selection"] = {"status": "generic_fallback", "reason_code": reason,
        "selection_policy_version": RULE_SELECTION_VERSION, "candidates": candidates}
    return parsed


def parse_rule_bundle(raw, definition, *, waf_vendor=None):
    """Select only unambiguous, structurally supported request interpretations.

    Rule order is never priority. Two candidates are equivalent only when the
    decoded request, original character provenance and actual interpretation
    agree. Different interpretations always fall back, regardless of ranking.
    HTTP success is not a verdict or proof of capture completeness.
    """
    budget = _Budget()
    bundle = validate_bundle(definition)
    if not isinstance(raw, str):
        raise ParserRuleError("parser_source_invalid")
    try:
        within_limit = len(raw) <= MAX_SOURCE_BYTES and len(raw.encode("utf-8")) <= MAX_SOURCE_BYTES
    except UnicodeError:
        return _fallback(raw, "parser_source_encoding_invalid", [])
    if not within_limit:
        return _fallback(raw, "parser_rule_source_limit", [])
    diagnostics, qualified = [], []
    context = _Context(raw, budget)
    vendor = waf_vendor.strip().casefold() if isinstance(waf_vendor, str) else None
    rules = sorted((rule for rule in bundle["rules"] if rule["waf_vendor"].casefold() == vendor), key=lambda rule: rule["rule_id"])
    if not rules:
        return _fallback(raw, "parser_vendor_unmatched", [])
    cache = {}
    try:
        for rule in rules:
            budget.check()
            proposal = rule["definition"]
            diagnostic = {"rule_id": rule["rule_id"], "rule_version": rule["rule_version"],
                "name": rule.get("name", proposal["name"]), "quality_codes": []}
            try:
                if not _rule_matches(proposal["match"], context):
                    diagnostic["reason_code"] = "parser_rule_not_matched"
                else:
                    extracted = _extract(proposal["extract"], context)
                    key = (extracted.text, tuple(extracted.units), tuple(sorted(proposal["capture"].items())))
                    cache_key = (key, extracted.kind)
                    if cache_key not in cache:
                        cache[cache_key] = _parse_extracted(raw, extracted, proposal["capture"])
                    parsed = cache[cache_key]
                    quality = _quality(parsed)
                    diagnostic["quality_codes"] = quality or []
                    diagnostic["reason_code"] = "parser_rule_qualified" if quality else "parser_request_not_qualified"
                    if quality:
                        qualified.append((_interpretation_key(parsed, extracted), rule, parsed, quality))
            except ParserRuleError as exc:
                if exc.code == "parser_rule_time_limit":
                    raise
                diagnostic["reason_code"] = exc.code
            diagnostics.append(diagnostic)
        budget.check()
    except ParserRuleError as exc:
        return _fallback(raw, exc.code, diagnostics)
    if not qualified:
        return _fallback(raw, "parser_no_qualified_rule", diagnostics)
    if len({candidate[0] for candidate in qualified}) != 1:
        return _fallback(raw, "parser_rule_ambiguous", diagnostics)
    # Boundary / provenance quality, then stable ID; never field count or order.
    _, selected, parsed, _ = min(qualified, key=lambda candidate: (
        -int("header_boundary_recognized" in candidate[3]),
        -int("protocol_recognized" in candidate[3]), candidate[1]["rule_id"]))
    parsed = {**parsed, "capture_selection": {"status": "equivalent" if len(qualified) > 1 else "matched",
        "reason_code": "parser_equivalent_rules" if len(qualified) > 1 else "parser_unique_rule",
        "selection_policy_version": RULE_SELECTION_VERSION, "rule_id": selected["rule_id"],
        "rule_version": selected["rule_version"], "name": selected.get("name", selected["definition"]["name"]),
        "candidates": diagnostics}}
    return parsed
