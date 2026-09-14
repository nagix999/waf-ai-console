import json

import pytest

from app import worker
from app.agent.evidence import EvidenceSourceResolver
from app.agent.executor import AgentCallResult
from app.agent.grounding_repair import correction_input, decision_diagnostics
from app.agent.input_builder import build_agent_input
from app.agent.policy import finalize_with_verifier
from app.agent.contracts import AgentVerdict, EvidenceCorrectionOutput, ThreatSeverity
from app.models import Analysis
from app.services.http_parser import parse_http_payload
from app.services.test_comparisons import _step_usage
from test_moduagent_worker import primary_output


@pytest.mark.parametrize("raw", [
    "GET /?q=fixture\r\nCookie: example=value\r\n\r\nbody-marker",
    "GET /?q=fixture HTTP/2\r\nCookie: example=value\r\n\r\nbody-marker",
    "GET /?q=fixture HTTP/1.1\r\nCookie: example=value",
])
def test_partial_source_alignment(raw):
    parsed = parse_http_payload(raw)
    resolver = EvidenceSourceResolver(raw, {}, parsed)
    assert resolver.matches("payload.query.q", "fixture")
    assert resolver.matches("payload.headers.Cookie", "example=value")
    assert not resolver.matches("payload.query.q", "example=value")
    for field, ranges in parsed["raw_source_spans"].items():
        for start, end in ranges:
            if raw[start:end].strip():
                assert resolver.matches("payload." + field, raw[start:end])
    assert resolver.matches("payload.body", "body-marker") == ("body-marker" in raw)


def test_encoded_keys_are_shared_but_decoded_values_still_rejected():
    raw = "GET /?encoded%20key=%3Cfixture%3E HTTP/1.1\r\n\r\n"
    parsed = parse_http_payload(raw)
    document = json.loads(build_agent_input({}, raw, parsed, 32768, 3072).text)
    assert document["generic_http_parser_hints"]["query_parameter_names"] == ["encoded%20key"]
    resolver = EvidenceSourceResolver(raw, {}, parsed)
    assert resolver.matches("payload.query.encoded%20key", "%3Cfixture%3E")
    assert not resolver.matches("payload.query.encoded key", "%3Cfixture%3E")
    assert not resolver.matches("payload.query.encoded%20key", "<fixture>")


@pytest.mark.parametrize("raw", [r"GET /?q=fixture\r\nCookie: secret\r\n\r\nbody", "vendor=fixture body=marker"])
def test_ambiguous_export_does_not_invent_body_or_header(raw):
    resolver = EvidenceSourceResolver(raw, {})
    assert resolver.matches("payload", "fixture")
    assert not resolver.matches("payload.body", "body")
    assert not resolver.matches("payload.headers.Cookie", "secret")


def correction(excerpt="q=fixture", field="payload.query", index=0, *, requires_reanalysis=False):
    return EvidenceCorrectionOutput(corrections=[{"index": index, "field": field, "excerpt": excerpt}],
                                    requires_reanalysis=requires_reanalysis)


def execute(monkeypatch, outputs, *, raw="GET /?q=fixture HTTP/1.1\r\n\r\n", analysis=None, built=None, assessment_enabled=False):
    calls = []
    async def fake(**kwargs):
        calls.append(kwargs)
        output = outputs[len(calls) - 1]
        return AgentCallResult(output, "fixture-run", None, "completed" if output else "error", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    analysis = analysis or Analysis(extra_fields={})
    built = built or build_agent_input(worker._event_document(analysis), raw, parse_http_payload(raw), 32768, 3072)
    result = worker._execute_grounded_agent(analysis=analysis, raw_payload=raw,
        parsed=parse_http_payload(raw), input_truncated=built.input_truncated, profile=None, api_key=None,
        submitted_payload_spans=built.retained_payload_spans,
        assessment_enabled=assessment_enabled,
        instructions="fixed-fixture-policy", user_input=built.text,
        session_id="fixture-session", agent_name="waf-primary", egress_check=lambda: None)
    return result, calls


def test_failed_correction_is_bounded_and_preserves_original_output(monkeypatch):
    first = primary_output("not-in-input", "payload.query")
    call, calls = execute(monkeypatch, [first, correction("not-in-input")])
    assert len(calls) == 2
    assert call.output.verdict.value == "inconclusive"
    assert call.telemetry["evidence_grounding"]["downgraded_to_inconclusive"]
    assert call.grounding_history[0]["output"]["verdict"] == "true_positive"
    assert first.verdict.value == "true_positive"
    assert "not-in-input" not in str(call.telemetry)
    assert "not-in-input" not in calls[1]["instructions"]
    assert "not-in-input" in calls[1]["user_input"]


def test_correction_can_request_reanalysis_but_cannot_rewrite_judgment(monkeypatch):
    first = primary_output("not-in-input")
    call, calls = execute(monkeypatch, [first, correction(requires_reanalysis=True)])
    assert call.output.verdict.value == "inconclusive"
    assert not call.telemetry["evidence_grounding_retry"]["recovered"]
    assert call.output.evidence[0].excerpt == "q=fixture"
    assert call.output.evidence[0].interpretation_ko == first.evidence[0].interpretation_ko
    assert calls[1]["output_model"] is EvidenceCorrectionOutput
    assert "requires_reanalysis=true" in calls[1]["instructions"]


def test_no_retry_for_valid_evidence_and_no_fabricated_grounding_failure(monkeypatch):
    call, calls = execute(monkeypatch, [primary_output("q=fixture")])
    assert len(calls) == 1
    assert not call.telemetry["evidence_grounding_retry"]["attempted"]


def test_repair_failure_does_not_pass_old_invalid_result(monkeypatch):
    call, calls = execute(monkeypatch, [primary_output("missing"), None])
    assert len(calls) == 2 and not call.succeeded and call.output is None
    assert not call.telemetry["evidence_grounding_retry"]["recovered"]


def test_usage_sums_schema_and_evidence_attempts_without_last_attempt_duplication():
    usage = lambda value: {"input_tokens": value, "output_tokens": value, "total_tokens": value * 2}
    metadata = {"usage": usage(99), "output_validation_retry": {"attempts": [{"usage": usage(99)}]},
        "evidence_grounding_retry": {"attempt_count": 2, "attempts": [
            {"usage": usage(2), "output_validation_retry": {"attempt_count": 2, "attempts": [{"usage": usage(1)}, {"usage": usage(2)}]}},
            {"usage": usage(3)},
        ]}}
    assert _step_usage(metadata) == usage(6)


def test_input_signals_are_not_mislabeled_as_abstention_causes(monkeypatch):
    call, _ = execute(monkeypatch, [primary_output("q=fixture")])
    final = finalize_with_verifier(call.output, call.output, ["parser_incomplete"])
    diagnostics = decision_diagnostics(call, call, final, {"parse_status": "partial"}, True)
    assert diagnostics["inconclusive_reasons"] == []
    assert diagnostics["input_signals"] == ["parser_incomplete", "input_truncated"]


def test_malformed_lone_carriage_return_does_not_create_extra_header_sources():
    raw = "GET / HTTP/1.1\r\nX-Fixture: safe\rCookie: invented\r\n\r\n"
    parsed = parse_http_payload(raw)
    assert parsed["headers"] == {}
    resolver = EvidenceSourceResolver(raw, {}, parsed)
    assert not resolver.matches("payload.headers.Cookie", "invented")
    assert not resolver.matches("payload.headers.X-Fixture", "safe")
    assert resolver.matches("payload.headers", "invented")


def test_oversized_untrusted_correction_is_bounded_without_inventing_clipped_quotes():
    output = primary_output("\u0000" * 300, "x" * 120)
    output.evidence = [output.evidence[0].model_copy() for _ in range(5)]
    issues = [{"index": index, "reason": "excerpt_not_in_source"} for index in range(5)]
    document = json.loads(correction_input('{"event":{"payload":"fixture"}}', output, issues))
    assert document["event"] == {"payload": "fixture"}
    feedback = document["evidence_correction"]
    assert feedback["omitted_items"] and 0 < len(feedback["items"]) < 5
    assert all(item["excerpt"] == output.evidence[0].excerpt for item in feedback["items"])
    assert len(json.dumps(feedback["items"], ensure_ascii=False)) <= 4096
