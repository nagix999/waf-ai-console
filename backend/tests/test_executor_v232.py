"""Synthetic actual-ModuAgent checks for a single strict decision review."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError

from app.agent import executor_v232 as executor
from app.agent.contracts_v232 import EvidenceDecisionReviewOutput, SecurityAnalysisOutputV232
from app.agent.output_schema_v226 import compact_strict_schema
from test_agent_openai import assert_strict_schema, completion, install_http, openai_profile
from test_decision_review_contract_v232 import neutral_base, review_context, review_wire
from test_evidence_interpretations_v227 import request_input
from test_source_support_contract_v230 import forbidden


def review_input(context=None):
    context = review_context() if context is None else context
    return json.dumps({"evidence_decision_review": {
        "original_output": context["review_original_output"],
        "eligible_evidence_ids": context["review_eligible_evidence_ids"],
        "source_catalog": context["source_catalog"],
    }})


def execute(provider="vllm", *, user_input=None, output_model=EvidenceDecisionReviewOutput, max_attempts=1,
            defer=False, recovery=True):
    profile = openai_profile(provider=provider,
        base_url="http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1")
    return asyncio.run(executor.execute_structured_agent(profile=profile, api_key="synthetic-key",
        instructions="Synthetic independent same-role review.", user_input=review_input() if user_input is None else user_input,
        session_id="synthetic:primary:decision-review", agent_name="waf-primary-decision-review", egress_check=lambda: None,
        output_model=output_model, structured_output_recovery=recovery,
        output_validation_max_attempts=max_attempts, defer_source_support_validation=defer))


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_review_has_one_physical_call_and_reconstructs_original_verified_evidence(monkeypatch, provider):
    value, context = review_wire(), review_context()
    value["retained_evidence_ids"].reverse()
    before = deepcopy(value)
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(value))))
    result = execute(provider, user_input=review_input(context))
    assert result.succeeded and len(requests) == 1
    assert result.output.model_dump(mode="json") == before
    analysis = result.output.to_analysis().model_dump(mode="json")
    assert analysis["evidence"] == context["review_original_output"]["evidence"]
    assert analysis["capture_assessment"] == context["review_original_output"]["capture_assessment"]
    assert analysis["signature_assessment"] == context["review_original_output"]["signature_assessment"]
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == retry["max_attempts"] == retry["max_transport_attempts_per_attempt"] == 1
    assert retry["attempts"][0]["validation_issues"] == []
    assert retry["attempts"][0]["max_output_tokens"] == 3072
    assert result.telemetry["structured_output"]["decision_review_version"] == "evidence-decision-review-v1"
    body = json.loads(requests[0].content)
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema == compact_strict_schema(EvidenceDecisionReviewOutput.model_json_schema())
    assert_strict_schema(schema)
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 3072
    assert "source_id" not in schema["properties"]


@pytest.mark.parametrize("mutate, code", [
    (lambda value: value.update(retained_evidence_ids=["e2"]), "review_evidence_omitted"),
    (lambda value: value.update(retained_evidence_ids=["e2", "e2", "e5"]), "review_evidence_repeated"),
    (lambda value: value.update(retained_evidence_ids=["e2", "e1"]), "review_evidence_not_eligible"),
    (lambda value: value.update(evidence=[]), "extra_forbidden"),
    (lambda value: value["threat_analysis"].update(severity="NONE"), "true_positive_requires_threat_severity"),
])
def test_review_failure_does_not_trigger_a_second_generation(monkeypatch, mutate, code):
    value = review_wire()
    mutate(value)
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(value))))
    result = execute()
    assert not result.succeeded and result.output is None and len(requests) == 1
    attempts = result.telemetry["output_validation_retry"]["attempts"]
    assert len(attempts) == 1
    assert code in {issue["type"] for issue in attempts[0]["validation_issues"]}


@pytest.mark.parametrize("content, finish", [
    ('{"retained_evidence_ids":[', "stop"),
    ('{"retained_evidence_ids":[],"retained_evidence_ids":[]}', "stop"),
    (None, "length"),
])
def test_incomplete_or_ambiguous_review_is_not_accepted_or_regenerated(monkeypatch, content, finish):
    content = json.dumps(review_wire()) if content is None else content
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(content, finish_reason=finish)))
    result = execute()
    assert not result.succeeded and result.output is None and len(requests) == 1
    assert result.telemetry["raw_provider_body_stored"] is False


def test_retryable_transport_error_still_has_only_one_review_request(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(500, json={"error": {"message": "SYNTHETIC_PROVIDER_SECRET"}}))
    result = execute()
    assert not result.succeeded and len(requests) == 1
    assert "SYNTHETIC_PROVIDER_SECRET" not in json.dumps(result.telemetry)


@pytest.mark.parametrize("max_attempts", [0, 2, 4, True])
def test_review_requires_exactly_one_outer_attempt_before_transport(monkeypatch, max_attempts):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion("{}")))
    with pytest.raises(ValueError, match="decision_review_requires_single_attempt"):
        execute(max_attempts=max_attempts)
    assert requests == []


def test_review_requires_strict_json_and_cannot_defer_source_validation(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion("{}")))
    with pytest.raises(ValueError, match="decision_review_requires_structured_recovery"):
        execute(recovery=False)
    with pytest.raises(ValueError, match="source_support_validation_defer_requires_v232"):
        execute(defer=True)
    assert requests == []


@pytest.mark.parametrize("user_input", ["{}", '{"evidence_decision_review":"SYNTHETIC_INPUT_SECRET"}',
    '{"evidence_decision_review":{},"evidence_decision_review":{}}', "SYNTHETIC_INVALID_JSON_SECRET"])
def test_invalid_server_metadata_is_a_safe_fixed_failure_without_plaintext(monkeypatch, user_input):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(review_wire()))))
    result = execute(user_input=user_input)
    assert not result.succeeded and len(requests) == 1
    assert result.telemetry["output_validation_retry"]["attempts"][0]["validation_issues"] == [
        {"field": "$", "type": "decision_review_context_invalid"}]
    assert not any(value in json.dumps(result.telemetry) for value in ("SYNTHETIC_INPUT_SECRET", "SYNTHETIC_INVALID_JSON_SECRET"))


def test_payload_or_event_cannot_supply_the_trusted_review_envelope():
    envelope = json.loads(review_input())
    codec = executor._TrackingOutputCodec(PydanticOutputCodec(EvidenceDecisionReviewOutput),
        output_model=EvidenceDecisionReviewOutput, structured_output_recovery=True,
        user_input=json.dumps({"event": envelope, "payload": json.dumps(envelope)}))
    with pytest.raises(ValidationError) as failure:
        codec.decode(json.dumps(review_wire()))
    assert {issue["type"] for issue in failure.value.errors()} == {"decision_review_context_invalid"}


def test_review_keeps_private_source_and_interpretation_out_of_telemetry(monkeypatch):
    context = review_context()
    context["review_original_output"]["evidence"][0]["interpretation_ko"] = "SYNTHETIC_PRIVATE_INTERPRETATION"
    context["source_catalog"]["items"][1]["excerpt"] = "SYNTHETIC_PRIVATE_EXCERPT"
    install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(review_wire()))))
    result = execute(user_input=review_input(context))
    assert result.succeeded
    assert result.output.to_analysis().evidence[0].interpretation_ko == "SYNTHETIC_PRIVATE_INTERPRETATION"
    assert not any(secret in json.dumps(result.telemetry) for secret in ("SYNTHETIC_PRIVATE_INTERPRETATION", "SYNTHETIC_PRIVATE_EXCERPT", "synthetic-key"))


@pytest.mark.parametrize("defer, request_count", [(False, 4), (True, 1)])
def test_initial_full_analysis_preserves_opt_in_and_default_strict_source_policy(monkeypatch, defer, request_count):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(forbidden()))))
    result = execute(user_input=request_input(), output_model=SecurityAnalysisOutputV232, max_attempts=4, defer=defer)
    assert result.succeeded is defer and len(requests) == request_count
