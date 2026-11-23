"""Actual ModuAgent serialization and bounded retries, synthetic HTTP only."""
import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError
from moduagent.output import PydanticOutputCodec

from app.agent import executor_v228 as executor
from app.agent.contracts_v228 import SecurityAnalysisOutputV228
from app.agent.output_schema_v226 import compact_strict_schema
from test_agent_openai import assert_strict_schema, completion, install_http, openai_profile
from test_evidence_interpretations_v227 import request_input
from test_flat_evidence_v228 import wire


def execute(provider="vllm", *, budget=3072):
    profile = openai_profile(provider=provider, max_output_tokens=budget,
        base_url="http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1")
    return asyncio.run(executor.execute_structured_agent(profile=profile, api_key="synthetic-key",
        instructions="Synthetic independent analysis.", user_input=request_input(),
        session_id="synthetic:primary", agent_name="waf-primary", egress_check=lambda: None,
        output_model=SecurityAnalysisOutputV228, structured_output_recovery=True))


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_both_providers_generate_only_five_flat_items_and_first_pass_budget(monkeypatch, provider):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(wire()))))
    result = execute(provider, budget=2048)
    assert result.succeeded and len(requests) == 1
    body = json.loads(requests[0].content)
    actual = body["response_format"]["json_schema"]["schema"]
    assert actual == compact_strict_schema(SecurityAnalysisOutputV228.model_json_schema())
    assert_strict_schema(actual)
    assert actual["properties"]["evidence"]["maxItems"] == 5
    assert "related_sources" not in json.dumps(actual)
    assert "2048 출력 토큰" in str(body["messages"])
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 2048
    if provider == "vllm":
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
    else:
        assert "chat_template_kwargs" not in body
    assert result.telemetry["output_validation_retry"]["max_attempts"] == 4


@pytest.mark.parametrize("provider,reason", [("vllm", "length"), ("vllm", "max_tokens"), ("openai", "length")])
def test_incomplete_even_valid_json_retries_same_cap_and_original_input(monkeypatch, provider, reason):
    requests, _ = install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(wire()), finish_reason=reason if count == 1 else "stop")))
    result = execute(provider)
    assert result.succeeded and len(requests) == 2
    bodies = [json.loads(request.content) for request in requests]
    assert all(body.get("max_tokens", body.get("max_completion_tokens")) == 3072 for body in bodies)
    assert [m for m in bodies[0]["messages"] if m["role"] == "user"] == [m for m in bodies[1]["messages"] if m["role"] == "user"]
    feedback = str(bodies[1]["messages"])
    assert "최대 5개" in feedback and "처음부터 다시" in feedback
    assert "길이를 줄이기 위해 판정" in feedback
    assert "primary_output" not in feedback
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == 2 and retry["attempts"][0]["provider_finish_reason"] == reason


def test_openai_unknown_finish_envelope_is_rejected_without_silent_normalization(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(wire()), finish_reason="max_tokens")))
    result = execute("openai")
    assert not result.succeeded and result.output is None and len(requests) == 1


def test_unfinished_json_is_never_patched_or_accepted_and_stops_at_existing_limit(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion('{"evidence":[', finish_reason="length")))
    result = execute()
    assert not result.succeeded and result.output is None and len(requests) == 4
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == 4 and retry["stop_reason"] == "attempt_limit"
    assert all(a["max_output_tokens"] == 3072 for a in retry["attempts"])
    assert "synthetic request" not in str(result.telemetry)


@pytest.mark.parametrize("code", ["evidence_link_unknown", "decisive_verdict_requires_directional_content"])
def test_link_and_direction_errors_use_existing_retry_with_cause_specific_feedback(monkeypatch, code):
    invalid = wire()
    if code == "evidence_link_unknown":
        invalid["evidence"][0]["related_evidence_ids"] = ["e1"]
    else:
        invalid["evidence"][0]["supports"] = "normal_request"
    requests, _ = install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(invalid if count == 1 else wire()))))
    result = execute()
    assert result.succeeded and len(requests) == 2
    feedback = str(json.loads(requests[1].content)["messages"])
    assert code in feedback and "related_evidence_ids" in feedback
    assert "related_sources" not in feedback and "tuning_recommendation" not in feedback


def test_persistent_source_rejection_never_reclassifies_or_leaks_source_text(monkeypatch):
    invalid = wire()
    invalid["evidence"][0]["source_id"] = "c3"
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(invalid))))
    result = execute()
    assert not result.succeeded and result.output is None and len(requests) == 4
    assert "synthetic body" not in json.dumps(result.telemetry)


def test_codec_keeps_complete_fence_normalization_but_never_invents_missing_json():
    codec = executor._TrackingOutputCodec(PydanticOutputCodec(SecurityAnalysisOutputV228),
        output_model=SecurityAnalysisOutputV228, structured_output_recovery=True, user_input=request_input())
    actual = codec.decode("```json\n" + json.dumps(wire()) + "\n```")
    assert actual.model_dump(mode="json") == wire()
    with pytest.raises((ValueError, ValidationError)):
        codec.decode('{"evidence":[')


@pytest.mark.parametrize("repair", ["source_only", "needs_reanalysis"])
def test_exact_source_repair_preserves_flat_dependency_or_removes_unverified_claim(monkeypatch, repair):
    from app import worker_v228 as worker
    from app.agent.contracts import EvidenceSelectionCorrectionOutput
    from app.agent.evidence_relations_v228 import build_evidence_relations
    from app.agent.executor import AgentCallResult
    from app.agent.input_builder_v228 import build_agent_input
    from app.agent.security_analysis_v228 import canonical_evidence
    from app.services.http_parser import parse_http_payload

    raw = "POST /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n" + "a=" + "x" * 1000
    event, parsed = {"payload": raw}, parse_http_payload(raw)
    built = build_agent_input(event, raw, parsed, 32768, 3072, evidence_selection=True,
                              fixed_rules_version="waf-system-v2.28")
    document = json.loads(built.text)
    sources = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
    sources[1]["excerpt"] = "SYNTHETIC_NOT_IN_ORIGINAL"
    value = wire()
    value["evidence"][0]["source_id"] = sources[0]["source_id"]
    value["evidence"][1]["source_id"] = sources[1]["source_id"]
    calls = []

    async def execute_call(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            output = SecurityAnalysisOutputV228.model_validate(value)
        else:
            assert kwargs["output_validation_max_attempts"] == 1
            assert kwargs["output_model"] is EvidenceSelectionCorrectionOutput
            output = EvidenceSelectionCorrectionOutput.model_validate({"corrections": (
                [{"index": 1, "source_id": sources[2]["source_id"]}] if repair == "source_only" else []),
                "requires_reanalysis": repair == "needs_reanalysis"})
        return AgentCallResult(output, "synthetic-repair", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", execute_call)
    call = worker._execute_grounded_agent(analysis=None, inference_event=event, raw_payload=raw, parsed=parsed,
        input_truncated=False, submitted_payload_spans=built.retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        user_input=json.dumps(document), profile=None, api_key=None, instructions="synthetic policy",
        agent_name="waf-primary", session_id="synthetic", egress_check=lambda: None)
    assert call.succeeded and len(calls) == 2
    evidence, _, _ = canonical_evidence(call, None)
    links = build_evidence_relations(call, evidence=evidence)
    if repair == "source_only":
        assert call.output.verdict == "true_positive" and len(evidence) == 2
        assert len(links["groups"]) == 1
        assert call.grounding_history[0]["output"] == value
        assert call.grounding_history[0]["expanded_output"]["evidence_relations"] == [{"representative_index": 0, "related_indexes": [1]}]
        assert call.telemetry["evidence_grounding_retry"]["recovered"] is True
    else:
        assert call.output.verdict == "inconclusive" and links["groups"] == []
        assert value["evidence"][0]["interpretation_ko"] not in [item["interpretation_ko"] for item in evidence]
