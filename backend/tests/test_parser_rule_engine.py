"""Synthetic-only contracts for immutable vendor-rule payload extraction."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from app.agent.evidence import EvidenceSourceResolver
from app.parser_rule_schemas import RuleProposalV1, VendorRuleBundle
from app.services import parser_rule_engine as engine
from app.services.http_parser import parse_http_payload
from app.services.parser_engine_v1 import valid_source_spans
from app.services.request_integrity import assess_request_integrity


CAPTURE = {"line_endings": "actual", "request_line_whitespace": "strict", "skip_leading_blank_lines": False}
REQUEST = "POST /api?q=one HTTP/1.1\r\nHost: fixture\r\nContent-Length: 3\r\n\r\nabc"


def proposal(*, match=None, extract=None, capture=None):
    return {"schema_version": "parser-rule-proposal-v1", "name": "Synthetic format",
        "match": match or {"all": [{"kind": "starts_with", "value": "POST "}]},
        "extract": extract or {"kind": "raw_payload"}, "capture": capture or deepcopy(CAPTURE),
        "reason_ko": "합성 형식의 요청 구간을 확인합니다.", "limitations_ko": "실제 운영 검증 전 초안입니다."}


def rule(identifier="rule-1", *, definition=None, vendor="Vendor A", **kwargs):
    return {"rule_id": identifier, "rule_version": 1, "waf_vendor": vendor,
        "definition": definition or proposal(**kwargs)}


def bundle(*rules):
    return {"schema_version": "vendor-parser-bundle-v1", "selection_policy_version": "vendor-rule-selection-v1",
        "rules": list(rules) or [rule()]}


def json_rule(identifier="rule-json", pointer="/request", **kwargs):
    return rule(identifier, match={"all": [{"kind": "json_string_exists", "json_pointer": pointer}]},
        extract={"kind": "json_string", "json_pointer": pointer}, **kwargs)


def marker_rule(identifier="rule-marker", *, start="request=", end="/@/END", anchor="payload_start"):
    operation = {"start_marker": start, "end_marker": end, "start_anchor": anchor}
    return rule(identifier, match={"all": [{"kind": "marker_pair_exists", **operation}]},
        extract={"kind": "between_markers", **operation})


def parse(raw, definition=None, vendor="Vendor A"):
    return engine.parse_rule_bundle(raw, definition or bundle(), waf_vendor=vendor)


def assert_fallback(raw, parsed, reason=None):
    assert parsed["capture_selection"]["status"] == "generic_fallback"
    if reason:
        assert parsed["capture_selection"]["reason_code"] == reason
    assert {key: value for key, value in parsed.items() if key != "capture_selection"} == parse_http_payload(raw)


def test_rule_schema_is_closed_and_bundle_round_trip_preserves_null():
    definition = bundle(rule(match={"all": [{"kind": "json_equals", "json_pointer": "/type", "value": None}]},
        extract={"kind": "json_string", "json_pointer": "/request"}))
    canonical = engine.validate_bundle(definition)
    assert canonical["rules"][0]["definition"]["match"]["all"][0]["value"] is None
    assert "name" not in canonical["rules"][0]
    assert engine.validate_bundle(canonical) == canonical
    schema = RuleProposalV1.model_json_schema()
    assert schema["additionalProperties"] is False
    assert all(node.get("additionalProperties") is False for node in schema["$defs"].values()
        if node.get("type") == "object")


@pytest.mark.parametrize("field", ["waf_vendor", "rule_id", "rule_version", "status", "engine_version",
    "limits", "ranking", "priority", "selection_policy_version", "raw_source_spans", "code", "steps"])
def test_model_cannot_set_server_envelope_or_arbitrary_code(field):
    definition = proposal()
    definition[field] = "forbidden"
    with pytest.raises(ValidationError):
        RuleProposalV1.model_validate(definition)


@pytest.mark.parametrize("match", [{"all": []}, {"any": []}, {"all": [{"kind": "starts_with", "value": "x"}] * 9},
    {"all": [{"kind": "starts_with", "value": "x"}], "any": [{"kind": "starts_with", "value": "x"}]},
    {"all": [{"any": [{"kind": "starts_with", "value": "x"}]}]}])
def test_match_ast_is_bounded_nonempty_and_not_nested(match):
    definition = proposal()
    definition["match"] = match
    with pytest.raises(ValidationError):
        RuleProposalV1.model_validate(definition)


@pytest.mark.parametrize("extract", [[{"kind": "json_string", "json_pointer": "/request"}],
    {"kind": "json_string", "json_pointer": "/request", "then": {"kind": "raw_payload"}},
    {"kind": "python", "code": "raise RuntimeError"}, {"kind": "raw_payload", "url": "https://fixture.invalid"}])
def test_exactly_one_extraction_operation_no_chain_or_executable(extract):
    definition = proposal()
    definition["extract"] = extract
    with pytest.raises(ValidationError):
        RuleProposalV1.model_validate(definition)


@pytest.mark.parametrize("pointer", ["request", "$.request", "/bad~", "/bad~2key", "/x" * 17])
def test_pointer_syntax_and_depth_are_restricted(pointer):
    with pytest.raises(ValidationError):
        RuleProposalV1.model_validate(proposal(extract={"kind": "json_string", "json_pointer": pointer}))


def test_rule_count_unique_ids_strict_version_and_capture_bounds():
    for definition in (bundle(*[rule(str(index)) for index in range(21)]), bundle(rule(), rule()),
        bundle({**rule(), "rule_version": True}), bundle(rule(capture={**CAPTURE, "skip_leading_blank_lines": "true"})),
        bundle(rule(capture={**CAPTURE, "line_endings": "auto"})), bundle({**rule(), "rule_id": "bad\nlabel"})):
        with pytest.raises(engine.ParserRuleError, match="^parser_rule_bundle_invalid$"):
            engine.validate_bundle(definition)


def test_raw_request_preserves_existing_wire_integrity_and_exact_source():
    parsed = parse(REQUEST)
    baseline = parse_http_payload(REQUEST)
    assert parsed["capture_selection"]["status"] == "matched"
    for field in baseline:
        assert parsed[field] == baseline[field]
    assert parsed["capture_extraction"] == {"source_kind": "raw_payload", "transformed": False,
        "mapping_mode": "original_character_tokens-v1"}
    assert assess_request_integrity(REQUEST, parsed) == assess_request_integrity(REQUEST, baseline)
    assert valid_source_spans(REQUEST, parsed)


def test_same_vendor_same_line_endings_multiple_formats_and_unknown_vendor():
    definition = bundle(json_rule(), marker_rule(), rule("rule-native"))
    samples = [(json.dumps({"request": REQUEST}), "rule-json"), ("request=" + REQUEST + "/@/END", "rule-marker"),
        (REQUEST, "rule-native")]
    for raw, identifier in samples:
        parsed = parse(raw, definition, vendor=" vendor a ")
        assert parsed["capture_selection"]["rule_id"] == identifier
        assert parsed["method"] == "POST" and parsed["uri"] == "/api"
    assert_fallback(REQUEST, parse(REQUEST, definition, vendor="Vendor B"), "parser_vendor_unmatched")
    assert_fallback(REQUEST, parse(REQUEST, definition, vendor=None), "parser_vendor_unmatched")


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_json_unicode_escapes_surrogate_pair_and_raw_evidence_provenance(ensure_ascii):
    request = 'POST /경로/😀?q=한글 HTTP/1.1\r\nHost: fixture\r\nX-Note: "literal"\r\n\r\n{"v":"한글\\n"}'
    raw = json.dumps({"request": request}, ensure_ascii=ensure_ascii)
    parsed = parse(raw, bundle(json_rule()))
    assert parsed["uri"] == "/경로/😀"
    assert parsed["capture_selection"]["status"] == "matched"
    assert parsed["parse_status"] == "partial"
    assert parsed["capture_extraction"]["source_kind"] == "json_string"
    assert parsed["capture_extraction"]["transformed"] is True
    assert valid_source_spans(raw, parsed)
    for field, ranges in parsed["raw_source_spans"].items():
        for start, end in ranges:
            assert 0 <= start <= end <= len(raw)
        if field in {"body", "request_line"}:
            start, end = ranges[0]
            assert parsed[field] == raw[start:end]
    start, end = parsed["raw_source_spans"]["path"][0]
    expected = "/\\uacbd\\ub85c/\\ud83d\\ude00" if ensure_ascii else "/경로/😀"
    assert raw[start:end] == expected
    resolver = EvidenceSourceResolver(raw, {}, parsed)
    assert resolver.matches("payload.path", expected)
    if ensure_ascii:
        assert not resolver.matches("payload.path", "/경로/😀")
    # A decoded body is a hint, never treated as the envelope's wire body.
    assert assess_request_integrity(raw, parsed)["status"] == "not_checked"


@pytest.mark.parametrize("value", ['slash\\quote"', "tabs\tline\nreturn\r", "back\bform\f", "한글", "😀"])
def test_every_json_string_escape_maps_back_to_original_tokens(value):
    request = "POST / HTTP/1.1\r\nHost: fixture\r\n\r\n" + value
    raw = json.dumps({"request": request})
    parsed = parse(raw, bundle(json_rule()))
    assert parsed["capture_selection"]["status"] == "matched"
    start, end = parsed["raw_source_spans"]["body"][0]
    assert raw[start:end] == json.dumps(value)[1:-1]
    assert parsed["body"] == raw[start:end]


def test_json_pointer_arrays_slash_tilde_and_root_string():
    raw = json.dumps({"outer": [{"http/request~": REQUEST}]})
    parsed = parse(raw, bundle(json_rule(pointer="/outer/0/http~1request~0")))
    assert parsed["method"] == "POST"
    assert parsed["capture_selection"]["status"] == "matched"
    assert parse(json.dumps(REQUEST), bundle(json_rule(pointer="")))["method"] == "POST"
    assert_fallback(raw, parse(raw, bundle(json_rule(pointer="/outer/00/http~1request~0"))))


@pytest.mark.parametrize("raw", ['{"request":"x","request":"y"}', '{"request":"x","requ\\u0065st":"y"}',
    '{"nested":{"a":1,"a":2},"request":"x"}', '{"request":"\\ud800"}', '{"request":"\\udc00"}',
    '{"request":"\\ud800\\u0020"}', '{"request":"\\x41"}', '{"request":"bad\nline"}',
    '{"request":"x",}', '{"request":"x"}tail', '{"request":"x","n":NaN}', '{"request":"x","n":Infinity}',
    '{"request":"x","n":01}', '{"request":"x","n":1e999}', '{"request":"x","n":' + "1" * 129 + '}'])
def test_strict_json_rejects_duplicate_keys_invalid_numbers_escapes_and_trailing_data(raw):
    parsed = parse(raw, bundle(json_rule()))
    assert_fallback(raw, parsed, "parser_no_qualified_rule")


@pytest.mark.parametrize("value", [None, True, 42, {}, [], ""])
def test_json_extraction_is_only_nonempty_string_no_auto_stringification(value):
    raw = json.dumps({"request": value})
    assert_fallback(raw, parse(raw, bundle(json_rule())))


@pytest.mark.parametrize("value", [None, True, False, 7, 1.5, "fixture"])
def test_json_equals_scalar_types_are_not_coerced(value):
    definition = bundle(rule(match={"all": [{"kind": "json_equals", "json_pointer": "/type", "value": value}]},
        extract={"kind": "json_string", "json_pointer": "/request"}))
    raw = json.dumps({"type": value, "request": REQUEST})
    assert parse(raw, definition)["capture_selection"]["status"] == "matched"
    other = not value if type(value) is bool else "different"
    assert_fallback(json.dumps({"type": other, "request": REQUEST}), parse(json.dumps({"type": other, "request": REQUEST}), definition))


def test_boolean_and_integer_json_equals_are_distinct():
    definition = bundle(rule(match={"all": [{"kind": "json_equals", "json_pointer": "/flag", "value": 1}]},
        extract={"kind": "json_string", "json_pointer": "/request"}))
    raw = json.dumps({"flag": True, "request": REQUEST})
    assert_fallback(raw, parse(raw, definition))


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 10**1000, "x" * 513])
def test_json_equals_values_are_bounded_without_unsafe_numeric_conversion(value):
    definition = bundle(rule(match={"all": [{"kind": "json_equals", "json_pointer": "/flag", "value": value}]}))
    with pytest.raises(engine.ParserRuleError, match="^parser_rule_bundle_invalid$"):
        engine.validate_bundle(definition)


def test_invalid_unicode_in_server_persisted_definition_is_rejected():
    definition = bundle(rule())
    definition["rules"][0]["definition"]["reason_ko"] = "unpaired-\ud800"
    with pytest.raises(engine.ParserRuleError, match="^parser_rule_bundle_invalid$"):
        engine.validate_bundle(definition)


def test_match_any_and_all_have_explicit_different_semantics():
    yes, no = {"kind": "starts_with", "value": "POST "}, {"kind": "starts_with", "value": "GET "}
    assert parse(REQUEST, bundle(rule(match={"any": [no, yes]})))["method"] == "POST"
    assert_fallback(REQUEST, parse(REQUEST, bundle(rule(match={"all": [no, yes]}))))


def test_marker_extraction_preserves_literal_delimiters_inside_request():
    request = 'POST /@/api HTTP/1.1\r\nHost: fixture\r\n\r\n{"value":"/@/","more":"GET /fake HTTP/1.1"}'
    raw = "request=" + request + "/@/END"
    parsed = parse(raw, bundle(marker_rule()))
    assert parsed["uri"] == "/@/api"
    assert parsed["body"] == '{"value":"/@/","more":"GET /fake HTTP/1.1"}'
    assert parsed["capture_selection"]["status"] == "matched"
    assert parsed["capture_extraction"]["source_kind"] == "between_markers"
    assert valid_source_spans(raw, parsed)
    assert "capture_extracted_not_wire_format" in parsed["warnings"]
    assert assess_request_integrity(raw, parsed)["status"] == "not_checked"


@pytest.mark.parametrize("raw", ["prefix request=" + REQUEST + "/@/END", "request=" + REQUEST,
    "request=" + REQUEST + "/@/END /@/END", "request=" + REQUEST + "\nrequest=" + REQUEST + "/@/END",
    "request=/@/END", "request=" + REQUEST + '/@/END in body /@/END'])
def test_marker_boundaries_unique_end_and_anchor_fail_closed(raw):
    definition = bundle(marker_rule(anchor="line_start"))
    assert_fallback(raw, parse(raw, definition))


def test_line_anchor_allows_envelope_prefix_but_not_embedded_start_marker():
    raw = "vendor=fixture\nrequest=" + REQUEST + "/@/END"
    parsed = parse(raw, bundle(marker_rule(anchor="line_start")))
    assert parsed["capture_selection"]["status"] == "matched"
    assert parsed["raw_source_spans"]["method"][0] == [raw.index("POST"), raw.index("POST") + 4]
    assert_fallback(raw, parse(raw, bundle(marker_rule(anchor="payload_start"))))


def test_multiple_qualified_different_values_never_selected_by_order_or_quality():
    raw = json.dumps({"first": "GET /one HTTP/1.1\r\n\r\n", "second": "GET /two\r\n\r\n"})
    first, second = json_rule("a-rule", "/first"), json_rule("z-rule", "/second")
    for definition in (bundle(first, second), bundle(second, first)):
        assert_fallback(raw, parse(raw, definition), "parser_rule_ambiguous")


def test_versionless_unframed_half_request_is_not_a_qualified_candidate():
    raw = json.dumps({"request": "GET /one"})
    parsed = parse(raw, bundle(json_rule()))
    assert_fallback(raw, parsed)
    assert parsed["capture_selection"]["candidates"][0]["reason_code"] == "parser_request_not_qualified"
    # Existing generic hints remain available even when rule selection declines.
    assert_fallback("GET /one", parse("GET /one", bundle(rule(match={"all": [{"kind": "starts_with", "value": "GET "}]}))))


def test_identical_request_in_different_original_occurrences_is_ambiguous():
    raw = json.dumps({"one": REQUEST, "two": REQUEST})
    assert_fallback(raw, parse(raw, bundle(json_rule("one", "/one"), json_rule("two", "/two"))), "parser_rule_ambiguous")


def test_equivalent_rules_use_stable_id_not_order_and_can_differ_in_redundant_options():
    first = rule("a-first")
    other = rule("z-last", capture={**CAPTURE, "request_line_whitespace": "flexible"})
    for definition in (bundle(first, other), bundle(other, first)):
        parsed = parse(REQUEST, definition)
        assert parsed["capture_selection"]["status"] == "equivalent"
        assert parsed["capture_selection"]["rule_id"] == "a-first"
        assert parsed["capture_selection"]["candidates"][0]["rule_id"] == "a-first"


def test_unrecognizable_extraction_is_not_promoted_as_a_good_match():
    raw = json.dumps({"request": "arbitrary body text"})
    parsed = parse(raw, bundle(json_rule()))
    assert_fallback(raw, parsed)
    assert parsed["capture_selection"]["candidates"][0]["reason_code"] == "parser_request_not_qualified"


def test_capture_adapters_after_extraction_preserve_original_serialized_body():
    request = r"POST\t/api\tHTTP/1.1\r\nHost: fixture\r\n\r\nbody\r\nliteral"
    # Tabs are actual capture formatting; body escapes are opaque content.
    request = request.replace(r"\t", "\t")
    raw = "request=" + request + "/@/END"
    current = marker_rule()
    current["definition"]["capture"] = {**CAPTURE, "line_endings": "escaped_crlf", "request_line_whitespace": "flexible"}
    parsed = parse(raw, bundle(current))
    assert parsed["method"] == "POST" and parsed["body"] == r"body\r\nliteral"
    assert parsed["request_line"] == request.partition(r"\r\n")[0]
    assert valid_source_spans(raw, parsed)


def test_source_json_depth_nodes_and_byte_budgets_fallback_without_clipping(monkeypatch):
    raw = json.dumps({"request": REQUEST})
    monkeypatch.setattr(engine, "MAX_SOURCE_BYTES", len(raw.encode()) - 1)
    assert_fallback(raw, parse(raw, bundle(json_rule())), "parser_rule_source_limit")
    monkeypatch.setattr(engine, "MAX_SOURCE_BYTES", 65_536)
    monkeypatch.setattr(engine, "MAX_JSON_DEPTH", 1)
    nested = json.dumps({"outer": {"request": REQUEST}})
    assert_fallback(nested, parse(nested, bundle(json_rule(pointer="/outer/request"))))
    monkeypatch.setattr(engine, "MAX_JSON_DEPTH", 32)
    monkeypatch.setattr(engine, "MAX_JSON_NODES", 1)
    assert_fallback(raw, parse(raw, bundle(json_rule())))


def test_elapsed_budget_falls_back_before_selection_and_after_work(monkeypatch):
    monkeypatch.setattr(engine, "MAX_SECONDS", -1)
    assert_fallback(REQUEST, parse(REQUEST), "parser_rule_time_limit")


def test_safe_diagnostics_never_include_raw_request_exception_or_proposal_reason():
    secret = "SYNTHETIC-PRIVATE-PAYLOAD"
    raw = json.dumps({"request": secret})
    current = json_rule()
    current["definition"]["reason_ko"] = secret
    parsed = parse(raw, bundle(current))
    diagnostics = json.dumps(parsed["capture_selection"])
    assert secret not in diagnostics
    assert set(parsed["capture_selection"]["candidates"][0]) == {"rule_id", "rule_version", "name", "quality_codes", "reason_code"}


def test_source_and_definitions_are_not_mutated_and_json_parsing_is_reused(monkeypatch):
    raw = json.dumps({"request": REQUEST})
    definition = bundle(json_rule("first"), json_rule("second"))
    original = deepcopy(definition)
    calls = []
    original_init = engine._JsonDocument.__init__

    def tracked(self, *args, **kwargs):
        calls.append(1)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(engine._JsonDocument, "__init__", tracked)
    assert parse(raw, definition)["capture_selection"]["status"] == "equivalent"
    assert definition == original and len(calls) == 1


def test_native_re2_capture_and_unicode_character_offsets():
    assert engine._re2 is not None, "Run native contract tests with the approved offline google-re2 wheel"
    raw = "머리:" + REQUEST.replace("/api", "/한글/😀") + ":끝"
    definition = bundle(rule(match={"all": [{"kind": "regex_match", "pattern": r"^머리:"}]},
        extract={"kind": "regex_capture", "pattern": r"(?s)^머리:(?P<request>.*):끝$"}))
    parsed = parse(raw, definition)
    assert parsed["capture_selection"]["status"] == "matched" and parsed["uri"] == "/한글/😀"
    assert parsed["raw_source_spans"]["method"] == [[3, 7]]
    assert valid_source_spans(raw, parsed)
    assert parsed["capture_extraction"]["source_kind"] == "regex_capture"


@pytest.mark.parametrize("pattern", [r"(POST .*)", r"(?P<wrong>POST .*)", r"(?P<request>POST )(.*)",
    r"(?P<request>POST )(?=x)", r"(?P<request>(POST)\2)", "("])
def test_native_regex_requires_exactly_one_named_request_and_rejects_unsupported_syntax(pattern):
    definition = bundle(rule(extract={"kind": "regex_capture", "pattern": pattern}))
    with pytest.raises(engine.ParserRuleError) as caught:
        engine.validate_bundle(definition)
    assert str(caught.value) in {"parser_regex_capture_invalid", "parser_regex_invalid"}
    assert pattern not in str(caught.value)


def test_regex_predicates_do_not_allow_capture_groups():
    with pytest.raises(engine.ParserRuleError, match="^parser_regex_match_capture_forbidden$"):
        engine.validate_bundle(bundle(rule(match={"all": [{"kind": "regex_match", "pattern": r"(POST)"}]})))


@pytest.mark.parametrize("pattern", [r"(?P<request>GET [^\n]+)", r"(?P<request>)", r"(?P<request>NOT-FOUND)"])
def test_regex_capture_is_unique_nonempty_and_matched(pattern):
    raw = "POST /fixture HTTP/1.1\n\nGET /one HTTP/1.1\nGET /two HTTP/1.1"
    current = rule(extract={"kind": "regex_capture", "pattern": pattern})
    assert_fallback(raw, parse(raw, bundle(current)))


def test_regex_support_is_explicitly_unavailable_without_native_engine(monkeypatch):
    definition = bundle(rule(extract={"kind": "regex_capture", "pattern": r"(?P<request>POST .*)"}))
    engine.validate_bundle(definition)  # Prove a cache cannot silently bypass availability.
    monkeypatch.setattr(engine, "_re2", None)
    with pytest.raises(engine.ParserRuleError, match="^parser_regex_unavailable$"):
        engine.validate_bundle(definition)
    assert parse(REQUEST)["method"] == "POST"  # Non-regex rules still work.


def test_regex_pattern_and_native_memory_are_server_bounded():
    with pytest.raises(engine.ParserRuleError, match="^parser_rule_bundle_invalid$"):
        engine.validate_bundle(bundle(rule(extract={"kind": "regex_capture", "pattern": "x" * 1025})))
    compiled = engine._compile(r"(?P<request>POST .*)", True)
    assert compiled.options.max_mem == engine.MAX_REGEX_MEMORY_BYTES


def test_bundle_and_selection_version_literals_are_protected():
    for field in ("schema_version", "selection_policy_version"):
        definition = bundle()
        definition[field] = "arbitrary-latest"
        with pytest.raises(ValidationError):
            VendorRuleBundle.model_validate(definition)
