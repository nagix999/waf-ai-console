"""Synthetic, offline capture context and input-fitting regression checks."""

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from app.agent import input_builder_v223, input_builder_v224, source_blocks
from app.agent.contracts import SecurityAnalysisOutputV220
from app.agent.contracts_v224 import CaptureAssessment, SecurityAnalysisOutputV224
from app.services.http_parser import parse_http_payload
from app.services.payload_decoding import decode_payload
from app.services.provider_options import strict_json_schema
from app.services.request_integrity import assess_request_integrity
from test_evidence_relations import wire


NEW = "waf-system-v2.24"


def build(raw, event=None, *, context_window=32768, max_output_tokens=3072, parsed=None, **kwargs):
    return input_builder_v224.build_agent_input(
        event or {}, raw, parse_http_payload(raw) if parsed is None else parsed,
        context_window, max_output_tokens,
        evidence_selection=True, fixed_rules_version=NEW, **kwargs,
    )


def output_wire(status="unknown"):
    return {**wire(), "capture_assessment": {
        "status": status, "reason_ko": "수신한 범위만으로 원래 요청의 전체 내용을 확인하기 어렵습니다.",
    }}


def assert_sources(raw, built):
    document = json.loads(built.text)
    items = document["evidence_candidates"]["items"]
    payload_items = [item for item in items if item["field"] == "payload"]
    assert len(built.text) <= built.estimated_input_token_budget * 3
    assert len(items) <= source_blocks.MAX_SOURCE_ITEMS
    assert len({item["source_id"] for item in items}) == len(items)
    assert built.submitted_payload_chars == sum(end - start for start, end in built.retained_payload_spans)
    for item in payload_items:
        assert 0 < len(item["excerpt"]) <= source_blocks.MAX_BLOCK_CHARS
        assert raw[item["start"]:item["end"]] == item["excerpt"]
    for start, end in built.retained_payload_spans:
        selected = [item for item in payload_items if start <= item["start"] < item["end"] <= end]
        assert "".join(item["excerpt"] for item in selected) == raw[start:end]
        cursor = start
        for item in selected:
            assert item["start"] == cursor
            cursor = item["end"]
        assert cursor == end
    assert document["capture_context"] == {
        "received_payload_bytes": len(raw.encode("utf-8")),
        "received_tail_submitted": bool(raw) and any(
            start < end == len(raw) for start, end in built.retained_payload_spans),
        "payload_omitted": built.submitted_payload_chars < len(raw),
    }
    assert document["capture_context"]["payload_omitted"] == document["input_truncation"]["payload"]
    return document


@pytest.mark.parametrize("raw", [
    "", "A" * 1024, "A" * 1023, "A" * 2048, "A" * 2053,
    "POST /fixture HTTP/1.1\r\n\r\n😀한글é",
    'POST /fixture HTTP/1.1\r\nContent-Type: application/json\r\n\r\n{"key":"unfinished',
])
def test_capture_context_records_only_received_and_submitted_facts(raw):
    document = assert_sources(raw, build(raw))
    assert document["input_policy_version"] == "agent-input-v3"
    assert not document["capture_context"]["payload_omitted"]
    assert set(document["capture_context"]) == {
        "received_payload_bytes", "received_tail_submitted", "payload_omitted",
    }
    assert "capture_assessment" not in document
    assert "payload" not in document["event"]


@pytest.mark.parametrize("raw", [
    "GET /fixture HTTP/1.1\r\nCookie: synthetic-only\r\n\r\n",
    "POST /fixture HTTP/1.1\r\n\r\n" + "😀한글é" * 700,
    'POST /fixture HTTP/1.1\r\n\r\n' + '\\"\x00\t' * 900,
])
def test_full_fit_preserves_v223_sources_hints_and_serialization_except_capture_fields(raw, monkeypatch):
    event = {"event_id": "synthetic-only", "signature": "synthetic signature", "waf_action": "D",
             "signature_explanation": "합성 시그니처 참고 설명입니다."}
    parsed = parse_http_payload(raw)
    kwargs = {"decoding": decode_payload(raw), "request_integrity": assess_request_integrity(raw, parsed)}
    calls = []
    original = source_blocks.build_source_catalog

    def counted(*args, **kwargs):
        calls.append(sum(end - start for start, end in args[1]))
        return original(*args, **kwargs)

    monkeypatch.setattr(source_blocks, "build_source_catalog", counted)
    old = input_builder_v223.build_agent_input(event, raw, parsed, 32768, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.23", **kwargs)
    old_calls = len(calls)
    calls.clear()
    new = build(raw, event, parsed=parsed, **kwargs)
    assert len(calls) == 1 < old_calls
    document = assert_sources(raw, new)
    document.pop("capture_context")
    document["input_policy_version"] = "agent-input-v2"
    assert input_builder_v224._serialize(document) == old.text
    assert new.retained_payload_spans == old.retained_payload_spans
    assert new.request_integrity == old.request_integrity
    assert new.input_truncated == old.input_truncated
    assert new.submitted_payload_chars == old.submitted_payload_chars


@pytest.mark.parametrize("window", [5200, 5632, 8192, 32768])
def test_partial_submission_preserves_real_tail_not_whole_capture_and_counts_all_metadata(window):
    raw = "POST /fixture HTTP/1.1\r\nContent-Type: text/plain\r\n\r\n" + "body%3Cscript%3E" * 8000
    parsed = parse_http_payload(raw)
    built = build(raw, {"signature_explanation": "합성 참고 설명" * 400}, parsed=parsed,
                  context_window=window, max_output_tokens=0,
                  decoding=decode_payload(raw), request_integrity=assess_request_integrity(raw, parsed))
    document = assert_sources(raw, built)
    assert document["capture_context"]["payload_omitted"]
    assert document["capture_context"]["received_tail_submitted"]
    assert document["request_integrity"] == built.request_integrity
    for hint in document["decoded_payload_hints"]["items"]:
        assert raw[hint["start"]:hint["end"]] == hint["original"]
        assert any(start <= hint["start"] < hint["end"] <= end for start, end in built.retained_payload_spans)
    for item in document["evidence_candidates"]["items"]:
        for index in item.get("decoded_hint_indexes", []):
            hint = document["decoded_payload_hints"]["items"][index]
            assert hint["start"] < item["end"] and item["start"] < hint["end"]


def test_new_capture_envelope_consumes_budget_without_exceeding_it():
    raw = "x" * 3000
    parsed = parse_http_payload(raw)
    old = input_builder_v223._build_source_block_input({}, raw, parsed, 2000, 6000,
                                                     decoding=None, request_integrity=None)
    exact_budget = len(old.text)
    new = input_builder_v224._build_source_block_input({}, raw, parsed, 2000, exact_budget,
                                                     decoding=None, request_integrity=None)
    assert len(new.text) <= exact_budget
    assert new.submitted_payload_chars < old.submitted_payload_chars
    assert_sources(raw, new)


def test_metadata_omission_is_not_mislabeled_as_payload_omission():
    raw = "GET /fixture HTTP/1.1\r\n\r\n"
    event = {"extra_fields": {f"field_{index}": "synthetic" * 500 for index in range(150)}}
    document = assert_sources(raw, build(raw, event))
    assert document["input_truncated"]
    assert document["input_truncation"]["event_metadata"]
    assert not document["capture_context"]["payload_omitted"]
    assert document["capture_context"]["received_tail_submitted"]


def test_huge_payload_and_advertised_context_do_not_bypass_catalog_caps(monkeypatch):
    raw = "한" * 2_000_000
    sizes = []
    original = source_blocks.build_source_catalog

    def bounded(*args, **kwargs):
        retained = sum(end - start for start, end in args[1])
        assert retained <= source_blocks.MAX_SOURCE_ITEMS * source_blocks.MAX_BLOCK_CHARS
        sizes.append(retained)
        return original(*args, **kwargs)

    monkeypatch.setattr(source_blocks, "build_source_catalog", bounded)
    built = build(raw, parsed={"parse_status": "failed", "raw_source_spans": {}},
                  context_window=10_000_000, max_output_tokens=0)
    document = assert_sources(raw, built)
    assert 1 <= len(sizes) <= 21
    assert document["capture_context"]["received_payload_bytes"] == 6_000_000
    assert document["capture_context"]["payload_omitted"]


def test_upper_catalog_overflow_falls_back_to_bounded_search():
    raw = "x" * 151 + "\n"
    raw *= 5000
    built = build(raw, parsed={"parse_status": "failed", "raw_source_spans": {}},
                  context_window=10_000_000, max_output_tokens=0)
    assert_sources(raw, built)
    assert built.input_truncated


def test_unsubmitted_received_tail_is_reported_without_inventing_capture_loss(monkeypatch):
    raw = "synthetic head and tail"
    monkeypatch.setattr(input_builder_v224, "_select_payload", lambda *args, **kwargs: (raw[:9], [(0, 9)]))
    document = assert_sources(raw, build(raw))
    assert document["capture_context"] == {
        "received_payload_bytes": len(raw), "received_tail_submitted": False, "payload_omitted": True,
    }


def test_bad_payload_encoding_returns_only_a_fixed_error_code():
    with pytest.raises(ValueError) as captured:
        build("synthetic-sensitive-marker\ud800", parsed={"parse_status": "failed"})
    assert str(captured.value) == "agent_payload_encoding_invalid"


@pytest.mark.parametrize("version", [None, "waf-system-v2.23", "waf-system-v2.25"])
def test_new_input_builder_requires_its_own_explicit_candidate_version(version):
    with pytest.raises(ValueError, match="unsupported_candidate_input_contract"):
        input_builder_v224.build_agent_input({}, "fixture", {}, 32768, 3072,
            evidence_selection=True, fixed_rules_version=version)


@pytest.mark.parametrize("status", ["possible", "no_clear_signs", "unknown"])
@pytest.mark.parametrize("verdict,severity", [
    ("true_positive", "HIGH"), ("false_positive", "NONE"), ("inconclusive", "UNKNOWN"),
])
def test_capture_interpretation_does_not_impose_a_verdict_or_leak_to_the_bridge(status, verdict, severity):
    data = output_wire(status)
    data["verdict"] = verdict
    data["threat_analysis"]["severity"] = severity
    if verdict == "false_positive":
        data["evidence"][1]["supports"] = "normal_request"
    old_data = {key: value for key, value in data.items() if key != "capture_assessment"}
    old = SecurityAnalysisOutputV220.model_validate(old_data)
    output = SecurityAnalysisOutputV224.model_validate(data)
    assert output.legacy_bridge() == old.legacy_bridge()
    assert "capture_assessment" not in output.legacy_bridge().model_dump(mode="json")
    assert output.model_dump(mode="json") == data
    assert list(output.model_json_schema()["properties"])[-1] == "capture_assessment"


@pytest.mark.parametrize("capture", [
    None, {}, {"status": "confirmed", "reason_ko": "잘렸다고 확정하면 안 됩니다."},
    {"status": "unknown", "reason_ko": ""}, {"status": "unknown", "reason_ko": " \n\t"},
    {"status": "unknown", "reason_ko": "x" * 241}, {"status": "unknown", "reason_ko": 123},
    {"status": "unknown", "reason_ko": "추정 사유입니다.", "confidence": 0.9},
])
def test_capture_wire_rejects_missing_ambiguous_or_long_extensions(capture):
    data = output_wire()
    data["capture_assessment"] = capture
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV224.model_validate(data)


def test_capture_is_required_bounded_and_not_added_to_the_historical_schema():
    data = output_wire()
    data.pop("capture_assessment")
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV224.model_validate(data)
    assert "capture_assessment" not in SecurityAnalysisOutputV220.model_json_schema()["properties"]
    schema = strict_json_schema(SecurityAnalysisOutputV224.model_json_schema())
    assert schema["required"] == list(schema["properties"])
    capture = schema["$defs"]["CaptureAssessment"]
    assert capture["additionalProperties"] is False
    assert capture["required"] == ["status", "reason_ko"]
    assert capture["properties"]["reason_ko"]["maxLength"] == 240
    assert CaptureAssessment.model_validate({"status": "unknown", "reason_ko": "x" * 240})


@pytest.mark.parametrize("modify", ["missing_condition", "bad_relation", "cross_supports", "bad_severity"])
def test_capture_extension_does_not_weaken_existing_domain_validation(modify):
    data = deepcopy(output_wire("possible"))
    if modify == "missing_condition":
        data["decision_issue"] = {"point_ko": "판단 조건", "evidence_indexes": [0], "missing_condition_ko": "필수 조건"}
    elif modify == "bad_relation":
        data["evidence_relations"][0]["related_indexes"] = [4]
    elif modify == "cross_supports":
        data["evidence"][0]["supports"] = "normal_request"
    else:
        data["threat_analysis"]["severity"] = "NONE"
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV224.model_validate(data)
