"""Actual ModuAgent, mocked providers and explicit source-correction staging."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError

from app.agent import executor_v230 as executor
from app.agent.contracts import EvidenceSelectionCorrectionOutput
from app.agent.contracts_v230 import SecurityAnalysisOutputV230
from app.agent.output_schema_v226 import compact_strict_schema
from test_agent_openai import assert_strict_schema, completion, install_http, openai_profile
from test_evidence_interpretations_v227 import request_input
from test_flat_evidence_v228 import wire
from test_source_support_contract_v230 import forbidden


def execute(provider="vllm", *, defer=False, user_input=None, output_model=SecurityAnalysisOutputV230,
            structured_output_recovery=True, max_attempts=4):
    profile = openai_profile(provider=provider,
        base_url="http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1")
    return asyncio.run(executor.execute_structured_agent(profile=profile, api_key="synthetic-key",
        instructions="Synthetic independent analysis.", user_input=request_input() if user_input is None else user_input,
        session_id="synthetic:primary", agent_name="waf-primary", egress_check=lambda: None,
        output_model=output_model, structured_output_recovery=structured_output_recovery,
        output_validation_max_attempts=max_attempts, defer_source_support_validation=defer))


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("normal", [False, True])
def test_forbidden_source_is_staged_once_without_changing_output_or_generation_options(monkeypatch, provider, normal):
    value = forbidden(normal=normal)
    before = deepcopy(value)
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(value))))
    result = execute(provider, defer=True)
    assert result.succeeded and len(requests) == 1
    assert result.output.model_dump(mode="json") == before
    assert value == before
    attempt = result.telemetry["output_validation_retry"]["attempts"][0]
    assert attempt["deferred_source_support_issues"] == [{"index": 0, "reason": "signature_support_not_allowed"}]
    assert attempt["validation_issues"] == []
    assert result.telemetry["output_validation_retry"]["attempt_count"] == 1
    assert result.telemetry["output_validation_retry"]["max_attempts"] == 4
    assert attempt["max_output_tokens"] == 3072
    assert result.telemetry["raw_provider_body_stored"] is False
    body = json.loads(requests[0].content)
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema == compact_strict_schema(SecurityAnalysisOutputV230.model_json_schema())
    assert_strict_schema(schema)
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 3072
    assert "defer_source_support_validation" not in json.dumps(body)
    assert not {"field", "excerpt", "interpretation_ko", "source_id"} & attempt["deferred_source_support_issues"][0].keys()


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_default_executor_keeps_strict_source_support_validation_and_existing_retry_limit(monkeypatch, provider):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(forbidden()))))
    result = execute(provider)
    assert not result.succeeded and result.output is None and len(requests) == 4
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == 4 and retry["stop_reason"] == "attempt_limit"
    assert all(attempt["deferred_source_support_issues"] == [] for attempt in retry["attempts"])
    assert retry["attempts"][-1]["validation_issues"] == [{"field": "evidence", "type": "evidence_source_support_forbidden"}]


def test_payload_allowed_supports_violation_is_staged_using_a_fixed_generic_reason(monkeypatch):
    input_document = json.loads(request_input())
    input_document["evidence_candidates"]["items"][1]["allowed_supports"] = ["context"]
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(wire()))))
    result = execute(defer=True, user_input=json.dumps(input_document))
    assert result.succeeded and len(requests) == 1
    assert result.telemetry["output_validation_retry"]["attempts"][0]["deferred_source_support_issues"] == [
        {"index": 0, "reason": "source_support_not_allowed"}]


@pytest.mark.parametrize("defect, expected", [
    ("unknown", "evidence_source_unavailable"), ("self", "evidence_link_self"),
    ("unknown_link", "evidence_link_unknown"), ("conflict", "evidence_support_conflict"),
    ("metadata_only", "decisive_verdict_requires_directional_content"),
    ("severity", "true_positive_requires_threat_severity"),
])
def test_staging_never_masks_unknown_source_graph_or_domain_failures(monkeypatch, defect, expected):
    value = forbidden()
    if defect == "unknown":
        value["evidence"][1]["source_id"] = "c999"
    elif defect == "self":
        value["evidence"][0]["related_evidence_ids"] = ["e2"]
    elif defect == "unknown_link":
        value["evidence"][0]["related_evidence_ids"] = ["e1"]
    elif defect == "conflict":
        value["evidence"][1]["supports"] = "normal_request"
    elif defect == "metadata_only":
        value["evidence"][0]["source_id"] = "c4"
    else:
        value["threat_analysis"]["severity"] = "NONE"
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(value))))
    result = execute(defer=True)
    assert not result.succeeded and len(requests) == 4
    assert expected in {issue["type"] for issue in result.telemetry["output_validation_retry"]["attempts"][-1]["validation_issues"]}
    assert all(not item["deferred_source_support_issues"] for item in result.telemetry["output_validation_retry"]["attempts"])


@pytest.mark.parametrize("content, reason, expected", [
    ('{"evidence":[', "stop", "json_invalid"),
    ('{"evidence":[],"evidence":[]}', "stop", "json_duplicate_key"),
    ('{"evidence":[', "length", None),
])
def test_opt_in_never_accepts_incomplete_or_ambiguous_json(monkeypatch, content, reason, expected):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(content, finish_reason=reason)))
    result = execute(defer=True)
    assert not result.succeeded and result.output is None and len(requests) == 4
    attempts = result.telemetry["output_validation_retry"]["attempts"]
    assert all(attempt["deferred_source_support_issues"] == [] for attempt in attempts)
    if expected:
        assert expected in {issue["type"] for issue in attempts[-1]["validation_issues"]}


def test_telemetry_contains_only_safe_reason_and_position_not_output_or_input(monkeypatch):
    value = forbidden()
    value["evidence"][0]["interpretation_ko"] = "SYNTHETIC_PRIVATE_INTERPRETATION"
    input_document = json.loads(request_input())
    input_document["evidence_candidates"]["items"][2]["excerpt"] = "SYNTHETIC_PRIVATE_SOURCE"
    input_document["event"]["signature"] = "SYNTHETIC_PRIVATE_SIGNATURE"
    install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(value))))
    result = execute(defer=True, user_input=json.dumps(input_document))
    assert result.succeeded
    serialized = json.dumps(result.telemetry)
    assert not any(secret in serialized for secret in (
        "SYNTHETIC_PRIVATE_INTERPRETATION", "SYNTHETIC_PRIVATE_SOURCE", "SYNTHETIC_PRIVATE_SIGNATURE", "synthetic-key"))
    assert result.output.evidence[0].interpretation_ko == "SYNTHETIC_PRIVATE_INTERPRETATION"


def test_codec_resets_deferred_issues_on_each_decode_and_missing_catalog_stays_invalid():
    codec = executor._TrackingOutputCodec(PydanticOutputCodec(SecurityAnalysisOutputV230),
        output_model=SecurityAnalysisOutputV230, structured_output_recovery=True,
        user_input=request_input(), defer_source_support_validation=True)
    assert codec.decode(json.dumps(forbidden()))
    assert codec.deferred_source_support_issues
    assert codec.decode(json.dumps(wire()))
    assert codec.deferred_source_support_issues == []
    codec.user_input = "{}"
    with pytest.raises(ValueError):
        codec.decode(json.dumps(forbidden()))
    assert codec.deferred_source_support_issues == []


@pytest.mark.parametrize("flag", [None, 0, 1, "true", []])
def test_opt_in_requires_a_real_boolean_before_any_provider_request(monkeypatch, flag):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(wire()))))
    with pytest.raises(ValueError, match="invalid_source_support_validation_defer_flag"):
        execute(defer=flag)
    assert requests == []


def test_source_correction_contract_cannot_enable_the_initial_output_validation_exception(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion("{}")))
    with pytest.raises(ValueError, match="source_support_validation_defer_requires_v230"):
        execute(defer=True, output_model=EvidenceSelectionCorrectionOutput, max_attempts=1)
    assert requests == []


def test_deferred_source_validation_cannot_disable_strict_complete_json_recovery(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion("{}")))
    with pytest.raises(ValueError, match="source_support_validation_defer_requires_recovery"):
        execute(defer=True, structured_output_recovery=False)
    assert requests == []
