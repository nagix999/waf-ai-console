"""Synthetic offline capture boundaries, raw provenance and budget tests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.capture_separator_v1 import confirmed_capture_boundaries, scan_capture_boundaries
from app.agent.input_builder_v236 import build_agent_input
from app.agent import input_builder_v235
from app.parser_schemas import ParserVersionCreate
from app.services.parser_engine import DEFAULT_OPTIONS, parse_capture_payload, validate_options, valid_source_spans
from app.services.parser_versions import definition_hash, engine_artifact_hash
from app.services.payload_decoding import decode_payload


ENGINE = "http-capture-v2"
PROFILE = {"name": "Confirmed capture", "waf_vendor": "Synthetic Vendor", **DEFAULT_OPTIONS,
           "field_separator": "slash_at_slash"}
OPTIONS = {**DEFAULT_OPTIONS, "profiles": [PROFILE]}
RAW = "POST /fixture HTTP/1.1/@/Host: example.invalid/@/Content-Type: application/json/@//@/{\"message\":\"hello\"}"


def parse(raw=RAW, vendor="Synthetic Vendor", options=None):
    return parse_capture_payload(raw, ENGINE, options or OPTIONS, waf_vendor=vendor)


def build(raw=RAW, parsed=None, event=None, **kwargs):
    return build_agent_input(event or {}, raw, parse(raw) if parsed is None else parsed,
        kwargs.pop("context_window", 32768), kwargs.pop("max_output_tokens", 3072),
        evidence_selection=True, fixed_rules_version="waf-system-v2.36", **kwargs)


def test_boundary_profile_is_explicit_vendor_scoped_and_defaults_are_unchanged():
    for options, vendor in [(DEFAULT_OPTIONS, "Synthetic Vendor"), (OPTIONS, "Unknown Vendor")]:
        parsed = parse(vendor=vendor, options=options)
        assert confirmed_capture_boundaries(RAW, parsed) is None
        assert "/@/" in build(parsed=parsed).text
    parsed = parse(vendor="  SYNTHETIC VENDOR  ")
    assert confirmed_capture_boundaries(RAW, parsed) is not None
    assert parsed["capture_selection"]["status"] == "matched"
    assert parsed["method"] == "POST"
    assert parsed["headers"]["host"] == ["example.invalid"]
    assert parsed["body"] == '{"message":"hello"}'
    assert parsed["parse_status"] == "partial"
    assert "capture_serialization_unverified" in parsed["warnings"]
    assert valid_source_spans(RAW, parsed)


def test_only_capture_boundaries_leave_model_surfaces_and_raw_offsets_remain_exact():
    parsed = parse()
    result = build(parsed=parsed, decoding=decode_payload(RAW))
    document = json.loads(result.text)
    assert "/@/" not in result.text
    assert document["input_policy_version"] == "agent-input-v4"
    assert document["evidence_candidates"]["version"] == "evidence-selection-v5"
    assert not result.input_truncated
    assert not document["input_truncation"]["payload"]
    assert document["capture_context"] == {"received_payload_bytes": len(RAW.encode()),
        "received_tail_submitted": True, "payload_omitted": False}
    assert result.submitted_payload_chars < result.original_payload_chars
    assert len({item["capture_segment"] for item in document["evidence_candidates"]["items"]}) == 4
    for item in document["evidence_candidates"]["items"]:
        assert item["excerpt"] == RAW[item["start"]:item["end"]]
        assert "/@/" not in item["excerpt"]
    assert "do not concatenate" in document["capture_boundary_context"]["meaning"]


@pytest.mark.parametrize("raw", [
    "GET /fixture HTTP/1.1/@/Host: example.invalid",  # no head terminator
    "GET /fixture/@/Host: example.invalid/@//@/",  # no HTTP version
    "GET /fixture HTTP/1.1\r\nHost: example.invalid\r\n\r\n/@/",  # real body data
    "GET /fixture HTTP/1.1/@/X-Value: literal/@/not-a-field/@//@/",  # ambiguous value
    'GET /fixture HTTP/1.1/@/X-Value: "literal/@/Host: value"/@//@/',
    '{"message":"GET /fixture HTTP/1.1/@/Host: example.invalid/@//@/"}',
])
def test_ambiguous_incomplete_or_non_capture_inputs_are_not_stripped(raw):
    parsed = parse(raw)
    assert scan_capture_boundaries(raw) is None
    assert confirmed_capture_boundaries(raw, parsed) is None
    assert "/@/" in build(raw, parsed).text


@pytest.mark.parametrize("location", ["uri", "header", "body", "metadata"])
def test_literal_value_occurrences_remain_unchanged(location):
    raw = RAW
    event = {}
    if location == "uri":
        raw = raw.replace("/fixture", "/fixture/@/literal")
    elif location == "header":
        raw = raw.replace("example.invalid", "example.invalid/@/literal")
    elif location == "body":
        raw = raw.replace("hello", "literal/@/value")
    else:
        event = {"extra_fields": {"message": "literal/@/value"}}
    result = build(raw, event=event)
    assert "literal/@/" in result.text or "/@/literal" in result.text
    document = json.loads(result.text)
    if location in {"body", "uri", "metadata"}:
        assert confirmed_capture_boundaries(raw, parse(raw)) is not None
    for item in document["evidence_candidates"]["items"]:
        if item["field"] == "payload":
            assert raw[item["start"]:item["end"]] == item["excerpt"]


def test_metadata_must_match_complete_recomputed_raw_layout():
    for mutate in [lambda p: p.pop("capture_boundaries"),
                   lambda p: p["capture_boundaries"].update(spans=[[0, 3]]),
                   lambda p: p["capture_format"].update(field_separator="none"),
                   lambda p: p["capture_selection"].update(status="generic_fallback")]:
        parsed = deepcopy(parse())
        mutate(parsed)
        assert confirmed_capture_boundaries(RAW, parsed) is None
        assert "/@/" in build(parsed=parsed).text


def test_decoder_origins_crossing_capture_boundaries_are_not_fabricated_or_submitted():
    index = RAW.index("/@/")
    before = RAW.index("fixture")
    decoding = {"decoder_version": "synthetic-v1", "items": [
        {"start": index - 2, "end": index + 5, "original": RAW[index - 2:index + 5], "decoded": "not accepted"},
        {"start": before, "end": before + 7, "original": "fixture", "decoded": "unchanged"}],
        "scan_truncated": False, "warnings": []}
    document = json.loads(build(decoding=decoding).text)
    assert len(document["decoded_payload_hints"]["items"]) == 1
    assert document["decoded_payload_hints"]["items"][0]["original"] == "fixture"
    assert document["decoded_payload_hints"]["omitted_items"]


def test_excluded_terminal_capture_token_is_not_claimed_as_submitted_raw_tail():
    raw = "GET /fixture HTTP/1.1/@/Host: example.invalid/@//@/"
    document = json.loads(build(raw).text)
    assert not document["capture_context"]["received_tail_submitted"]
    assert not document["capture_context"]["payload_omitted"]
    assert not document["input_truncated"]


def test_fitting_and_source_item_caps_still_apply_to_payloads_with_capture_boundaries():
    raw = RAW + "한글%3Cscript%3E" * 10000
    result = build(raw, context_window=5632, max_output_tokens=0, decoding=decode_payload(raw))
    assert len(result.text) <= result.estimated_input_token_budget * 3
    document = json.loads(result.text)
    assert len(document["evidence_candidates"]["items"]) <= 999
    assert result.input_truncated
    assert document["capture_context"]["payload_omitted"]
    assert document["capture_context"]["received_payload_bytes"] == len(raw.encode())
    assert "capture_boundary_context" in document
    for item in document["evidence_candidates"]["items"]:
        if item["field"] == "payload":
            assert raw[item["start"]:item["end"]] == item["excerpt"]


def test_legacy_engines_reject_new_flags_and_preserve_their_original_hash_inputs():
    for engine in ["generic-http-v3", "http-capture-v1"]:
        with pytest.raises(ValueError):
            validate_options(engine, OPTIONS)
    root = Path(__file__).parents[1] / "app" / "services"
    assert engine_artifact_hash("generic-http-v3") == hashlib.sha256((root / "http_parser.py").read_bytes()).hexdigest()
    assert engine_artifact_hash("http-capture-v1") == hashlib.sha256(b"\0".join(
        (root / name).read_bytes() for name in ("http_parser.py", "parser_engine_v1.py"))).hexdigest()
    assert definition_hash(ENGINE, OPTIONS) != definition_hash("http-capture-v1", DEFAULT_OPTIONS)
    old = ParserVersionCreate(name="old", options={**DEFAULT_OPTIONS, "profiles": [
        {key: value for key, value in PROFILE.items() if key != "field_separator"}]}).model_dump()
    assert "field_separator" not in old["options"]["profiles"][0]
    with pytest.raises(ValidationError):
        ParserVersionCreate(name="wrong engine", options=OPTIONS)
    assert ParserVersionCreate(name="explicit", engine_version=ENGINE, options=OPTIONS).engine_version == ENGINE


def test_existing_v235_input_preserves_literal_capture_boundary_text():
    result = input_builder_v235.build_agent_input({}, RAW, parse(), 32768, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.35")
    assert "/@/" in result.text


def test_unconfigured_input_preserves_v235_catalog_and_all_source_restrictions():
    raw = "POST /fixture HTTP/1.1\r\nHost: example.invalid\r\n\r\n{\"message\":\"hello\"}"
    parsed = parse(raw, vendor="Unknown Vendor")
    event = {"event_id": "synthetic-only", "source_system": "synthetic-only", "company_name": "Synthetic",
        "waf_vendor": "Unknown Vendor", "signature": "SYNTHETIC_RULE", "waf_action": "D",
        "signature_explanation": "Synthetic rule reference", "extra_fields": {"signature_explanation": "Synthetic extension reference"}}
    kwargs = {"evidence_selection": True, "decoding": decode_payload(raw)}
    old = input_builder_v235.build_agent_input(event, raw, parsed, 32768, 3072,
        fixed_rules_version="waf-system-v2.35", **kwargs)
    new = build_agent_input(event, raw, parsed, 32768, 3072,
        fixed_rules_version="waf-system-v2.36", **kwargs)
    before, after = json.loads(old.text), json.loads(new.text)
    after["input_policy_version"] = before["input_policy_version"]
    assert before == after
    items = after["evidence_candidates"]["items"]
    assert not {"event_id", "source_system", "company_name", "waf_vendor"} & {item["field"] for item in items}
    for item in items:
        if item["field"] in {"signature", "signature_explanation", "extra_fields.signature_explanation"}:
            assert item["allowed_supports"] == ["context"]
