from types import SimpleNamespace

import pytest

from app.agent.evidence import EvidenceSourceResolver
from app.services.http_parser import parse_http_payload
from app.services.parser_engine import (CAPTURE_ENGINE, DEFAULT_OPTIONS, LEGACY_ENGINE,
                                       parse_capture_payload, valid_source_spans, validate_options)
from app.services.parser_versions import (ParserVersionError, activate_parser_version, apply_parser_snapshot,
    copy_parser_snapshot, create_version, ensure_parser_state, legacy_parser_snapshot,
    parser_metadata, pin_parser, read_parser_snapshot)
from app.services.request_integrity import assess_request_integrity


def parse(raw, **options):
    return parse_capture_payload(raw, CAPTURE_ENGINE, {**DEFAULT_OPTIONS, **options})


@pytest.mark.parametrize("raw", ["GET / HTTP/1.1\r\nHost: fixture\r\n\r\n", "GET /", "body only",
    r"POST / HTTP/1.1\r\nHost: fixture\r\n\r\nbody", "GET /x?q=%27 HTTP/1.1\nHost: a\nCookie: b\n\n"])
def test_legacy_engine_is_byte_for_byte_compatible(raw):
    assert parse_capture_payload(raw, LEGACY_ENGINE, DEFAULT_OPTIONS) == parse_http_payload(raw)


@pytest.mark.parametrize("mode,separator", [("actual", "\r\n"), ("escaped_crlf", r"\r\n"), ("escaped_lf", r"\n")])
def test_configured_serialization_maps_every_field_to_original(mode, separator):
    body = r'{"note":"literal\nvalue","q":"%27"}'
    raw = separator.join(["POST /api?q=%27 HTTP/1.1", "Host: fixture", "Cookie: a=1", "Cookie: b=2", "", body])
    parsed = parse(raw, line_endings=mode)
    assert parsed["parse_status"] == ("success" if mode == "actual" else "partial")
    assert parsed["body"] == body and parsed["headers"]["cookie"] == ["a=1", "b=2"]
    assert valid_source_spans(raw, parsed)
    resolver = EvidenceSourceResolver(raw, {}, parsed)
    assert resolver.matches("payload.query.q", "q=%27")
    assert not resolver.matches("payload.query.q", "q='")
    assert resolver.matches("payload.headers.Cookie", "Cookie: b=2")
    assert resolver.matches("payload.body", body)
    start, end = parsed["raw_source_spans"]["body"][0]
    assert raw[start:end] == body


def test_body_literal_escapes_are_never_unescaped():
    raw = r"POST / HTTP/1.1\r\nContent-Length: 8\r\n\r\na\r\nb"
    parsed = parse(raw, line_endings="escaped_crlf")
    assert parsed["body"] == r"a\r\nb"
    assessment = assess_request_integrity(raw, parsed)
    assert assessment["status"] == "not_checked"
    assert assessment["limitations"] == ["capture_serialization_not_wire_format"]


def test_actual_wire_integrity_checks_remain_enabled():
    raw = "POST / HTTP/1.1\r\nContent-Length: 20\r\n\r\nx"
    assessed = assess_request_integrity(raw, parse(raw))
    assert assessed["status"] == "issues_observed"
    assert any(item["code"] == "body_shorter_than_content_length" for item in assessed["issues"])


def test_whitespace_and_leading_blank_options_preserve_offsets():
    raw = "\r\n\r\nGET\t\t/x?q=1\tHTTP/1.1\r\nHost: fixture\r\n\r\n"
    parsed = parse(raw, request_line_whitespace="flexible", skip_leading_blank_lines=True)
    assert parsed["parse_status"] == "success"
    assert parsed["request_line"] == "GET\t\t/x?q=1\tHTTP/1.1"
    assert parsed["raw_source_spans"]["method"] == [[4, 7]]
    assert EvidenceSourceResolver(raw, {}, parsed).matches("payload.query.q", "q=1")
    assert parse(raw)["parse_status"] == "failed"


def test_missing_protocol_and_terminator_are_not_fabricated_or_hidden():
    parsed = parse(r"GET /x\r\nHost: fixture", line_endings="escaped_crlf")
    assert parsed["parse_status"] == "partial"
    assert parsed["protocol"] is None and parsed["body"] == ""
    assert "body" not in parsed["raw_source_spans"]
    assert set(parsed["warnings"]) == {"request_protocol_not_recognized", "headers_terminator_missing", "capture_serialization_unverified"}


def test_mixed_line_endings_fail_closed_without_silent_conversion():
    parsed = parse("GET / HTTP/1.1\\r\\nHost: fixture\n\\r\\n", line_endings="escaped_crlf")
    assert parsed["parse_status"] != "success"
    assert "capture_line_endings_ambiguous" in parsed["warnings"]


def test_unrecognized_prefix_is_not_guessed_as_a_request():
    assert parse("SYSLOG fixture GET / HTTP/1.1\r\n\r\n")["parse_status"] == "failed"


@pytest.mark.parametrize("options", [{"python": "print('do not run')"}, {**DEFAULT_OPTIONS, "regex": ".*"},
    {**DEFAULT_OPTIONS, "skip_leading_blank_lines": "true"}, {**DEFAULT_OPTIONS, "line_endings": "auto"}])
def test_only_supported_declarative_options_are_accepted(options):
    with pytest.raises(ValueError):
        validate_options(CAPTURE_ENGINE, options)


def test_original_span_validator_rejects_bool_and_out_of_range():
    for spans in ({"body": [[True, 1]]}, {"body": [[0, 4]]}, {"body": [[2, 1]]}):
        assert not valid_source_spans("abc", {"raw_source_spans": spans})


def test_vendor_and_capture_format_select_independent_profiles_and_fallback():
    options = {**DEFAULT_OPTIONS, "profiles": [
        {"name": "Vendor A serialized", "waf_vendor": "Vendor A", **DEFAULT_OPTIONS, "line_endings": "escaped_crlf"},
        {"name": "Vendor A native", "waf_vendor": "Vendor A", **DEFAULT_OPTIONS, "request_line_whitespace": "flexible"},
        {"name": "Vendor B serialized LF", "waf_vendor": "Vendor B", **DEFAULT_OPTIONS, "line_endings": "escaped_lf"},
    ]}
    examples = [(r"GET /a HTTP/1.1\r\nHost: fixture\r\n\r\n", " vendor a ", 0),
        ("GET\t/a\tHTTP/1.1\r\nHost: fixture\r\n\r\n", "Vendor A", 1),
        (r"GET /b HTTP/1.1\nHost: fixture\n\n", "Vendor B", 2)]
    for raw, vendor, expected in examples:
        parsed = parse_capture_payload(raw, CAPTURE_ENGINE, options, waf_vendor=vendor)
        assert parsed["capture_selection"]["status"] == "matched"
        assert parsed["capture_selection"]["profile_index"] == expected
        assert parsed["method"] == "GET" and valid_source_spans(raw, parsed)
    raw = "GET /a HTTP/1.1\r\nHost: fixture\r\n\r\n"
    fallback = parse_capture_payload(raw, CAPTURE_ENGINE, options, waf_vendor="unregistered vendor")
    assert fallback["capture_selection"]["status"] == "generic_fallback"
    assert {key: value for key, value in fallback.items() if key != "capture_selection"} == parse_http_payload(raw)


def test_duplicate_profile_selectors_and_nested_executable_options_are_rejected():
    first = {"name": "First", "waf_vendor": "Vendor A", **DEFAULT_OPTIONS}
    for other in ({**first, "name": "Second", "waf_vendor": " vendor a "}, {**first, "python": "forbidden"}):
        with pytest.raises(ValueError):
            validate_options(CAPTURE_ENGINE, {**DEFAULT_OPTIONS, "profiles": [first, other]})


@pytest.mark.parametrize("literal", [r"\n\n", r"\r\n\r\n"])
@pytest.mark.parametrize("location", ["target", "header"])
@pytest.mark.parametrize("protocol", [" HTTP/1.1", ""])
def test_actual_framing_is_not_selected_from_literal_escape_data(literal, location, protocol):
    target = "/x" + literal + "data" if location == "target" else "/x"
    header = "note=" + literal + "data" if location == "header" else "fixture"
    raw = f"GET {target}{protocol}\r\nHost: fixture\r\nX-Note: {header}\r\n\r\nbody"
    options = {**DEFAULT_OPTIONS, "profiles": [
        {"name": mode, "waf_vendor": "Vendor A", **DEFAULT_OPTIONS, "line_endings": mode}
        for mode in ("escaped_lf", "escaped_crlf", "actual")
    ]}
    parsed = parse_capture_payload(raw, CAPTURE_ENGINE, options, waf_vendor="Vendor A")
    assert parsed["capture_selection"]["line_endings"] == "actual"
    assert parsed["capture_selection"]["profile_index"] == 2
    assert parsed["body"] == "body" and parsed["request_line"] == f"GET {target}{protocol}"
    assert parsed["raw_source_spans"] == parse_http_payload(raw)["raw_source_spans"]
    assert valid_source_spans(raw, parsed)


@pytest.mark.parametrize("mode,literal", [("escaped_lf", r"\n\n"), ("escaped_crlf", r"\r\n\r\n")])
@pytest.mark.parametrize("location", ["target", "header"])
@pytest.mark.parametrize("protocol", [" HTTP/1.1", ""])
def test_explicit_serialized_mode_rejects_actual_framing_before_literal_terminator(mode, literal, location, protocol):
    target = "/x" + literal + "data" if location == "target" else "/x"
    header = "note=" + literal + "data" if location == "header" else "fixture"
    raw = f"GET {target}{protocol}\r\nX-Note: {header}\r\n\r\nbody"
    parsed = parse(raw, line_endings=mode)
    baseline = parse_http_payload(raw)
    assert parsed["warnings"] == [*baseline["warnings"], "capture_line_endings_ambiguous"]
    assert {key: value for key, value in parsed.items() if key not in {"warnings", "parse_status"}} == {
        key: value for key, value in baseline.items() if key not in {"warnings", "parse_status"}}
    assert parsed["parse_status"] != "success"


def test_actual_framing_with_no_matching_profile_uses_generic_not_literal_escape_route():
    raw = "GET /x\\n\\nfoo HTTP/1.1\r\nHost: fixture\r\n\r\nbody"
    options = {**DEFAULT_OPTIONS, "profiles": [
        {"name": "serialized", "waf_vendor": "Vendor A", **DEFAULT_OPTIONS, "line_endings": "escaped_lf"},
    ]}
    parsed = parse_capture_payload(raw, CAPTURE_ENGINE, options, waf_vendor="Vendor A")
    assert parsed["capture_selection"] == {"status": "generic_fallback", "reason_code": "capture_profile_unmatched", "line_endings": "actual"}
    assert {key: value for key, value in parsed.items() if key != "capture_selection"} == parse_http_payload(raw)


@pytest.mark.parametrize("mode,separator", [("escaped_lf", r"\n"), ("escaped_crlf", r"\r\n")])
@pytest.mark.parametrize("protocol", [" HTTP/1.1", ""])
def test_serialized_header_and_opaque_body_actual_newlines_remain_supported(mode, separator, protocol):
    body = "first body line\nGET /body-only HTTP/1.1\r\nHost: body-data\r\n\r\nend"
    raw = separator.join(["POST /api" + protocol, "Host: fixture", "", body])
    options = {**DEFAULT_OPTIONS, "profiles": [
        {"name": "native", "waf_vendor": "Vendor A", **DEFAULT_OPTIONS},
        {"name": "serialized", "waf_vendor": "Vendor A", **DEFAULT_OPTIONS, "line_endings": mode},
    ]}
    for parsed in (parse(raw, line_endings=mode),
                   parse_capture_payload(raw, CAPTURE_ENGINE, options, waf_vendor="Vendor A")):
        assert parsed["method"] == "POST"
        assert parsed["body"] == body
        assert parsed["parse_status"] == "partial"
        assert "capture_line_endings_ambiguous" not in parsed["warnings"]
        start, end = parsed["raw_source_spans"]["body"][0]
        assert raw[start:end] == body
        assert valid_source_spans(raw, parsed)


def test_versions_pin_and_copy_without_changing_current_production(client):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        state = ensure_parser_state(db)
        row = create_version(db, crypto, name="Escaped capture", options={**DEFAULT_OPTIONS, "line_endings": "escaped_crlf"})
        target = SimpleNamespace(parser_version_id=None, parser_snapshot_json=None)
        snapshot = pin_parser(db, crypto, target, row.id, "candidate")
        assert state.active_version_id == LEGACY_ENGINE
        assert parser_metadata(snapshot)["version_id"] == row.id
        child = SimpleNamespace(parser_version_id=None, parser_snapshot_json=None)
        copy_parser_snapshot(target, child)
        activate_parser_version(db, row.id)  # Internal promotion hook, not a public API.
        assert read_parser_snapshot(crypto, child) == snapshot
        assert apply_parser_snapshot(r"GET / HTTP/1.1\r\n\r\n", snapshot)["parse_status"] == "partial"
        legacy = SimpleNamespace(parser_version_id=None, parser_snapshot_json=None)
        assert read_parser_snapshot(crypto, legacy) == legacy_parser_snapshot()
        bad = SimpleNamespace(parser_version_id=row.id, parser_snapshot_json={**snapshot, "content_hash": "a" * 64})
        with pytest.raises(ParserVersionError, match="parser_snapshot_invalid"):
            read_parser_snapshot(crypto, bad)
        with pytest.raises(ParserVersionError, match="parser_snapshot_missing"):
            read_parser_snapshot(crypto, SimpleNamespace(parser_version_id=row.id, parser_snapshot_json=None))


def test_engine_artifact_drift_blocks_snapshot_and_definition(client, monkeypatch):
    import app.services.parser_versions as registry
    with client.app.state.session_factory() as db:
        row = create_version(db, client.app.state.crypto, name="Frozen engine")
        target = SimpleNamespace(parser_version_id=None, parser_snapshot_json=None)
        pin_parser(db, client.app.state.crypto, target, row.id, "candidate")
        monkeypatch.setattr(registry, "engine_artifact_hash", lambda engine: "a" * 64)
        with pytest.raises(ParserVersionError, match="parser_snapshot_invalid"):
            read_parser_snapshot(None, target)
        with pytest.raises(ParserVersionError, match="parser_definition_invalid"):
            registry.get_version(db, row.id)
