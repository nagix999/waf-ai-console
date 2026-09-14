"""Synthetic-only structural tests, not live-model accuracy measurements."""
import copy
import json
from pathlib import Path

import pytest

from app.agent.input_builder import build_agent_input
from app.agent.input_integrity import apply_integrity_guard
from app.agent.contracts import WAFAnalysisOutput
from app.agent.prompts import FIXED_INSTRUCTIONS
from app.services.http_parser import parse_http_payload
from app.services.request_integrity import (
    MAX_CHUNKS, MAX_JSON_DEPTH, MAX_PAYLOAD_CHARS, assess_request_integrity, submitted_integrity,
)
from test_moduagent_worker import primary_output


def request(body="note=hello", headers="Content-Length: 10\r\n", target="/submit?q=hello"):
    return f"POST {target} HTTP/1.1\r\nHost: example.invalid\r\n{headers}\r\n{body}"


def inspect(raw):
    return assess_request_integrity(raw, parse_http_payload(raw))


def codes(raw):
    return {item["code"] for item in inspect(raw)["issues"]}


@pytest.mark.parametrize("body,headers,expected", [
    ("abc", "Content-Length: 3\r\n", set()),
    ("abc", "Content-Length: 3\r\ncontent-length: 003\r\n", set()),
    ("abc", "Content-Length: 003, 3\r\n", set()),
    ("", "Content-Length: 0\r\n", set()),
    ("", "Content-Length: 3\r\n", {"declared_body_not_captured"}),
    ("abc", "Content-Length: 8\r\n", {"body_shorter_than_content_length"}),
    ("abc", "Content-Length: 2\r\n", {"body_exceeds_content_length"}),
    ("abc", "Content-Length: 2, 3\r\n", {"content_length_conflict"}),
    ("abc", "Content-Length: 2\r\nContent-Length: 3\r\n", {"content_length_conflict"}),
    ("abc", "Content-Length: -3\r\n", {"content_length_invalid"}),
    ("abc", "Content-Length: ３\r\n", {"content_length_invalid"}),
    ("abc", "Content-Length: \r\n", {"content_length_invalid"}),
    ("abc", "Content-Length: 1" + "0" * 100 + "\r\n", {"body_shorter_than_content_length"}),
    ("3\r\nabc\r\n0\r\n\r\n", "Transfer-Encoding: chunked\r\n", set()),
    ("3\r\nabc\r\n0\r\nX-End: yes\r\n\r\n", "Transfer-Encoding: chunked\r\n", set()),
    ("3\r\nabc\r\n", "Transfer-Encoding: chunked\r\n", {"chunked_body_incomplete"}),
    ("8\r\nabc", "Transfer-Encoding: chunked\r\n", {"chunked_body_incomplete"}),
    ("0\r\n", "Transfer-Encoding: chunked\r\n", {"chunked_body_incomplete"}),
    ("", "Transfer-Encoding: chunked\r\n", {"chunked_body_incomplete"}),
    ("0\r\n\r\nGET / HTTP/1.1", "Transfer-Encoding: chunked\r\n", {"data_after_chunked_body"}),
    ("0\r\n\r\n", "Content-Length: 5\r\nTransfer-Encoding: chunked\r\n", {"transfer_encoding_content_length"}),
])
def test_framing_observations_preserve_source_and_do_not_change_parse_status(body, headers, expected):
    raw = request(body, headers)
    parsed = parse_http_payload(raw)
    before = copy.deepcopy(parsed)
    result = assess_request_integrity(raw, parsed)
    assert {item["code"] for item in result["issues"]} == expected
    assert parsed == before and parsed["parse_status"] == "success"
    assert "verdict" not in result
    for item in result["issues"]:
        assert all(0 <= a <= b <= len(raw) for a, b in item["source_spans"] + item["affected_spans"])


@pytest.mark.parametrize("body", ["한글", "é", "😀", "\ud800"])
def test_unicode_does_not_assume_wire_encoding(body):
    report = inspect(request(body, "Content-Length: 800\r\n"))
    assert report["issues"] == []
    assert "wire_body_length_not_comparable" in report["limitations"]


def test_encoded_content_and_unsupported_transfer_are_not_guessed():
    report = inspect(request("abc", "Content-Length: 90\r\nContent-Encoding: gzip\r\n"))
    assert report["issues"] == [] and "wire_body_length_not_comparable" in report["limitations"]
    for body, transfer in [("3;name=value\r\nabc\r\n0\r\n\r\n", "chunked"), ("0\n\n", "chunked"), ("abc", "gzip, chunked")]:
        report = inspect(request(body, f"Transfer-Encoding: {transfer}\r\n"))
        assert report["issues"] == [] and report["limitations"]


@pytest.mark.parametrize("raw", [
    "vendor=fixture body={", '{"capture":"POST / HTTP/1.1\\r\\n\\r\\n{"}',
    r"POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\nabc",
    "POST / HTTP/1.1\r\nContent-Length: 100", "GET / HTTP/2\r\n\r\n",
    request("abc", "Content-Length: 100\r\n folded: value\r\n"),
])
def test_unsupported_capture_does_not_create_a_new_hold_reason(raw):
    report = inspect(raw)
    assert report["status"] == "not_checked" and report["issues"] == []


@pytest.mark.parametrize("body,expected", [
    ('{"name":"hello"}', set()),
    ('{"name":"hello"', {"json_container_unclosed"}),
    ('{"name":"hello', {"json_container_unclosed"}),
    ('{"name":[1,2', {"json_container_unclosed"}),
    ('{"note":"braces { [ are text"}', set()),
    ('{"note":"quoted \\\" bracket }"}', set()),
    ('{"name":1,"name":2}', {"json_duplicate_keys"}),
    ('{"name":1,"name":1}', {"json_duplicate_keys"}),
    ('{"outer":{"name":1,"name":2}}', {"json_duplicate_keys"}),
    ('[{"name":1},{"name":2}]', set()),
    ('{"name":]}', set()),
])
def test_json_structure_is_not_attack_semantics(body, expected):
    assert codes(request(body, "Content-Type: application/json\r\n")) == expected


def test_json_looking_code_example_is_not_parsed_as_http_body_json():
    assert not codes(request('Example: {"missing":', "Content-Type: text/plain\r\n"))
    assert not codes(request('{"missing":', "Content-Type: text/plain\r\n"))
    assert codes(request('{"missing":', "Content-Type: application/problem+json; charset=utf-8\r\n")) == {"json_container_unclosed"}


def test_work_limits_do_not_manufacture_eof_issues():
    examples = [
        request("a" * MAX_PAYLOAD_CHARS, "Content-Length: 999999\r\n"),
        request("[" * (MAX_JSON_DEPTH + 1), "Content-Type: application/json\r\n"),
        request("1\r\na\r\n" * MAX_CHUNKS + "0\r\n\r\n", "Transfer-Encoding: chunked\r\n"),
        request("abc", "Content-Length: " + "3," * 100 + "3\r\n"),
        request("abc", "X-Pad: " + "a" * 33000 + "\r\nContent-Length: 500\r\n"),
    ]
    for raw in examples:
        report = inspect(raw)
        assert not report["issues"] and report["limitations"]


def test_codes_only_no_payload_values_or_exception_text():
    raw = request('{"SENSITIVE_CANARY":1,"SENSITIVE_CANARY":2}',
                  "Content-Length: SENSITIVE_CANARY\r\nContent-Type: application/json\r\nCookie: SENSITIVE_CANARY\r\n")
    report = inspect(raw)
    assert "SENSITIVE_CANARY" not in json.dumps(report)
    assert {item["code"] for item in report["issues"]} == {"content_length_invalid", "json_duplicate_keys"}


def build(raw, **kwargs):
    return build_agent_input({}, raw, parse_http_payload(raw), 32768, 3072,
                             evidence_selection=True, request_integrity=inspect(raw), **kwargs)


def output(verdict, field="payload.body", excerpt="note=hello"):
    value = primary_output(excerpt, field).model_dump(mode="json")
    value.update(verdict=verdict, summary_ko="가상 판정", analyst_checks=[], recommended_checks=[])
    value["threat_analysis"]["severity"] = {"true_positive": "HIGH", "false_positive": "NONE", "inconclusive": "UNKNOWN"}[verdict]
    return WAFAnalysisOutput.model_validate(value)


@pytest.mark.parametrize("verdict,expected", [("false_positive", "inconclusive"), ("true_positive", "true_positive"), ("inconclusive", "inconclusive")])
def test_only_affected_normal_output_is_limited(verdict, expected):
    raw = request(headers="Content-Length: 80\r\n")
    initial = output(verdict)
    before = initial.model_dump(mode="json")
    final, metadata = apply_integrity_guard(initial, build(raw).request_integrity, raw, parse_http_payload(raw))
    assert final.verdict.value == expected
    assert initial.model_dump(mode="json") == before and final.evidence == initial.evidence
    assert metadata["downgraded_to_inconclusive"] == (verdict == "false_positive")
    assert "note=hello" not in json.dumps(metadata)
    WAFAnalysisOutput.model_validate(final.model_dump(mode="json"))
    if verdict == "false_positive":
        assert final.analyst_checks and final.recommended_checks
        assert not final.tuning_recommendation.recommended
        assert final.threat_analysis.severity.value == "UNKNOWN"
        assert "Primary" not in final.summary_ko and "Verifier" not in final.summary_ko


@pytest.mark.parametrize("field,excerpt,limited", [
    ("payload", "note=hello", True), ("event.raw_payload", "note=hello", True),
    ("payload.query", "q=hello", False), ("payload", "/submit?q=hello", False),
    ("payload.headers.Host", "Host: example.invalid", False),
    ("payload.headers.Content-Length", "Content-Length: 80", True),
    ("extra_fields.note", "note=hello", False),
])
def test_affected_source_scope_not_arbitrary_interpretation(field, excerpt, limited):
    raw = request(headers="Content-Length: 80\r\n")
    final, meta = apply_integrity_guard(output("false_positive", field, excerpt), build(raw).request_integrity, raw, parse_http_payload(raw))
    assert meta["downgraded_to_inconclusive"] is limited


def test_unrelated_duplicates_extra_bytes_and_missing_business_context_do_not_auto_hold():
    for raw, excerpt in [(request('{"a":1,"a":2}', "Content-Type: application/json\r\n"), '{"a":1'),
                         (request(headers="Content-Length: 2\r\n"), "note=hello"),
                         (request(), "note=hello")]:
        final, meta = apply_integrity_guard(output("false_positive", excerpt=excerpt), build(raw).request_integrity, raw, parse_http_payload(raw))
        assert final.verdict.value == "false_positive" and not meta["downgraded_to_inconclusive"]


def test_omitted_proof_is_not_sent_or_used_for_a_verdict_limit():
    raw = request("note=hello" + "a" * 150000, "Content-Length: 200000\r\n")
    built = build(raw)
    report = built.request_integrity
    assert built.input_truncated and report["omitted_issues"] and not report["issues"]
    final, meta = apply_integrity_guard(output("false_positive"), report, raw, parse_http_payload(raw))
    assert final.verdict.value == "false_positive" and not meta["downgraded_to_inconclusive"]
    assert len(built.text) <= built.estimated_input_token_budget * 3
    assert "request_integrity" in json.loads(built.text)


def test_small_budget_omits_whole_issues_instead_of_clipping_codes():
    raw = request(headers="Content-Length: 80\r\n")
    hints = submitted_integrity(inspect(raw), [(0, len(raw))], 200)
    assert hints["omitted_issues"] and hints["issues"] == []
    assert "request_integrity" in FIXED_INSTRUCTIONS
    assert "다른 온전한 구간의 명확한 공격은 유지" in FIXED_INSTRUCTIONS


def test_all_150_frozen_synthetic_inputs_remain_bounded_and_source_grounded():
    root = Path(__file__).resolve().parents[2] / "samples"
    for name in ["waf-parser-stress-v1/parser_stress_50.json", "waf-dummy-v1/hard_50.json", "waf-dummy-v1/medium_50.json"]:
        records = json.loads((root / name).read_text())
        assert len(records) == 50
        for record in records:
            built = build(record["payload"])
            assert len(built.text) <= built.estimated_input_token_budget * 3
            assert "expected_verdict" not in json.loads(built.text)["event"]
            assert len(json.dumps(built.request_integrity)) < 4096
            for item in built.request_integrity["issues"]:
                assert all(any(a <= start <= end <= b for a, b in built.retained_payload_spans)
                           for start, end in item["source_spans"])
