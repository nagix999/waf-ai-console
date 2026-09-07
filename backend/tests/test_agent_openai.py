"""Real ModuAgent request serialization, using synthetic offline HTTP responses."""

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agent.contracts import WAFAnalysisOutput
from app.agent.executor import execute_structured_agent
from app.services.provider_options import generation_options, safe_openai_usage, strict_json_schema


HTTP_CLIENT = httpx.AsyncClient


def openai_profile(**changes):
    values = {
        "provider": "openai", "external_data_approved": True,
        "base_url": "https://api.openai.com/v1", "model_name": "gpt-4.1",
        "max_output_tokens": 3072, "context_window": 32768,
        "timeout_seconds": 10, "tls_verify": True, "test_concurrency": 2,
        "api_key_ciphertext": "synthetic-encrypted-key",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def synthetic_output():
    return WAFAnalysisOutput.model_validate({
        "verdict": "inconclusive", "confidence_score": 0,
        "summary_ko": "합성 검증에 원문 정보가 없어 보류합니다.",
        "threat_analysis": {
            "severity": "UNKNOWN", "category": "unknown", "target": "unknown",
            "technique_ko": "원문 정보가 없어 기법을 확인할 수 없습니다.",
            "potential_impact_ko": "잠재 영향을 확인할 수 없습니다.",
        },
        "signature_assessment": {"relation": "unknown", "explanation_ko": "시그니처가 없습니다."},
        "tuning_recommendation": {"recommended": False},
    }).model_dump(mode="json")


def completion(content=None, *, finish_reason="stop", refusal=None):
    return {
        "id": "provider-metadata-must-not-be-stored", "model": "provider-metadata-must-not-be-stored",
        "choices": [{"finish_reason": finish_reason, "message": {
            "role": "assistant", "content": json.dumps(synthetic_output(), ensure_ascii=False) if content is None else content,
            "refusal": refusal,
        }}],
        "usage": {"prompt_tokens": 28000, "completion_tokens": 250, "total_tokens": 28250},
    }


def install_http(monkeypatch, handler):
    requests = []
    client_options = []

    def receive(request):
        requests.append(request)
        return handler(request, len(requests))

    def client_factory(**kwargs):
        client_options.append(dict(kwargs))
        return HTTP_CLIENT(transport=httpx.MockTransport(receive), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    return requests, client_options


def execute(profile=None, api_key="synthetic-key", **kwargs):
    return asyncio.run(execute_structured_agent(
        profile=profile or openai_profile(), api_key=api_key,
        instructions="Synthetic independent WAF analysis.", user_input="synthetic-input-only",
        session_id="synthetic-analysis:primary", agent_name="waf-primary", **kwargs,
    ))


def assert_strict_schema(schema):
    if isinstance(schema, dict):
        assert "default" not in schema
        if schema.get("type") == "object" or "properties" in schema:
            assert schema["additionalProperties"] is False
            assert schema["required"] == list(schema.get("properties", {}))
        for value in schema.values():
            assert_strict_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            assert_strict_schema(value)


def test_strict_adapter_does_not_modify_local_contract_or_nullable_fields():
    original = WAFAnalysisOutput.model_json_schema()
    before = copy.deepcopy(original)
    strict = strict_json_schema(original)
    assert original == before
    assert_strict_schema(strict)
    tuning = strict["$defs"]["TuningRecommendation"]
    assert tuning["properties"]["scope"]["anyOf"][-1] == {"type": "null"}
    assert "input_truncated" not in original["required"]
    assert "input_truncated" in strict["required"]
    assert WAFAnalysisOutput.model_validate(synthetic_output()).input_truncated is False


def test_strict_adapter_preserves_field_names_matching_schema_keywords():
    schema = {"type": "object", "properties": {
        "default": {"type": "integer", "default": 0},
        "properties": {"type": "string"},
    }}
    strict = strict_json_schema(schema)
    assert strict["required"] == ["default", "properties"]
    assert strict["properties"]["default"] == {"type": "integer"}


def test_actual_moduagent_openai_wire_options_schema_auth_and_transport(monkeypatch):
    requests, options = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion()))
    result = execute()
    assert result.succeeded is True
    assert len(requests) == 1
    assert options == [{"verify": True, "follow_redirects": False, "trust_env": False}]
    request = requests[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer synthetic-key"
    body = json.loads(request.content)
    assert body["model"] == "gpt-4.1"
    assert body["store"] is False
    assert body["max_completion_tokens"] == 3072
    assert not {"max_tokens", "temperature", "chat_template_kwargs", "reasoning_effort"} & body.keys()
    assert body["stream"] is False
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert_strict_schema(body["response_format"]["json_schema"]["schema"])
    assert result.telemetry["thinking_enabled"] is None
    assert result.telemetry["provider"] == "openai"
    assert "provider-metadata-must-not-be-stored" not in json.dumps(result.telemetry)


def test_openai_usage_retains_only_numeric_counters_not_provider_text(monkeypatch):
    body = completion()
    body["usage"].update({
        "untrusted": "synthetic-usage-secret",
        "completion_tokens_details": {"reasoning_tokens": 10, "untrusted": "synthetic-usage-secret"},
    })
    install_http(monkeypatch, lambda request, count: httpx.Response(200, json=body))
    result = execute()
    assert result.succeeded is True
    assert "synthetic-usage-secret" not in json.dumps(result.telemetry)
    expected_usage = {"input_tokens": 28000, "output_tokens": 250, "total_tokens": 28250}
    assert result.telemetry["usage"] == expected_usage
    assert result.telemetry["output_validation_retry"]["attempts"][0]["usage"] == expected_usage
    # ModuAgent aggregates top-level counts; nested counters need not survive.
    assert safe_openai_usage(body["usage"])["completion_tokens_details"] == {"reasoning_tokens": 10}


def test_actual_vllm_usage_is_structured_without_provider_contents(monkeypatch):
    body = completion()
    body["usage"]["untrusted"] = "synthetic-provider-usage-text"
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=body))
    result = execute(openai_profile(provider="vllm", base_url="http://10.0.0.10:8000/v1"), api_key=None, egress_check=lambda: None)
    assert result.succeeded and len(requests) == 1
    expected = {"input_tokens": 28000, "output_tokens": 250, "total_tokens": 28250}
    assert result.telemetry["usage"] == expected
    assert result.telemetry["output_validation_retry"]["attempts"][0]["usage"] == expected
    assert "synthetic-provider-usage-text" not in json.dumps(result.telemetry)


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}])
def test_actual_missing_or_zero_usage_is_unknown_not_free(monkeypatch, usage):
    body = completion()
    if usage is None:
        del body["usage"]
    else:
        body["usage"] = usage
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=body))
    result = execute()
    assert result.succeeded is True
    assert len(requests) == 1
    assert result.telemetry["usage"] is None
    assert result.telemetry["output_validation_retry"]["attempts"][0]["usage"] is None


def test_partial_provider_usage_retains_framework_aggregate_not_billing_provenance(monkeypatch):
    body = completion()
    body["usage"] = {"prompt_tokens": 100}
    install_http(monkeypatch, lambda request, count: httpx.Response(200, json=body))
    result = execute()
    assert result.succeeded
    # Known framework limitation: it defaults missing output to 0 and derives
    # total before aggregation. Phase1 serialization cannot recover presence.
    assert result.telemetry["usage"] == {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100}


def test_actual_retry_usage_preserves_each_attempt_without_double_counting(monkeypatch):
    invalid = synthetic_output()
    del invalid["threat_analysis"]["severity"]

    def response(request, count):
        body = completion(json.dumps(invalid) if count == 1 else None)
        body["usage"] = {"prompt_tokens": count * 100, "completion_tokens": count * 10, "total_tokens": count * 110}
        return httpx.Response(200, json=body)

    requests, _ = install_http(monkeypatch, response)
    result = execute()
    assert result.succeeded and len(requests) == 2
    attempts = result.telemetry["output_validation_retry"]["attempts"]
    assert [attempt["usage"] for attempt in attempts] == [
        {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220},
    ]
    # Top-level usage remains the last attempt, not an extra model call.
    assert result.telemetry["usage"] == attempts[-1]["usage"]
    assert sum(attempt["usage"]["total_tokens"] for attempt in attempts) == 330


@pytest.mark.parametrize("usage", [
    "synthetic-invalid-usage", {"prompt_tokens": "synthetic-invalid-usage"},
    {"completion_tokens": True}, {"total_tokens": -1},
    {"completion_tokens_details": {"reasoning_tokens": "synthetic-invalid-usage"}},
])
def test_openai_invalid_usage_never_succeeds_or_leaks(monkeypatch, usage):
    body = completion()
    body["usage"] = usage
    install_http(monkeypatch, lambda request, count: httpx.Response(200, json=body))
    result = execute()
    assert result.succeeded is False
    assert result.telemetry["error_summary"]["code"] == "openai_invalid_response"
    assert "synthetic-invalid-usage" not in json.dumps(result.telemetry) + str(result.error)


@pytest.mark.parametrize("changes,key", [
    ({"external_data_approved": False}, "synthetic-key"),
    ({"base_url": "https://api.openai.com.evil.example/v1"}, "synthetic-key"),
    ({"base_url": "http://api.openai.com/v1"}, "synthetic-key"),
    ({"base_url": "https://api.openai.com/v1?secret=synthetic"}, "synthetic-key"),
    ({"tls_verify": False}, "synthetic-key"),
    ({}, None), ({}, " "), ({}, "synthetic key"),
])
def test_openai_rejects_unapproved_or_unsafe_direct_calls_without_http(monkeypatch, changes, key):
    requests, options = install_http(monkeypatch, lambda request, count: pytest.fail("HTTP must not be reached"))
    with pytest.raises(ValueError):
        execute(openai_profile(**changes), api_key=key)
    assert requests == options == []


@pytest.mark.parametrize("body,expected_code", [
    (completion(refusal="synthetic-refusal-must-not-leak"), "openai_refusal"),
    (completion(finish_reason="length"), "openai_output_incomplete"),
    (completion(finish_reason="content_filter"), "openai_completion_not_finished"),
    (completion(finish_reason=None), "openai_completion_not_finished"),
    (completion(content=""), "openai_invalid_response"),
    ({"error": {"message": "synthetic-response-secret"}}, "openai_invalid_response"),
])
def test_openai_refusal_or_incomplete_valid_json_is_not_success(monkeypatch, body, expected_code):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=body))
    result = execute()
    assert result.succeeded is False
    assert result.output is None
    assert result.telemetry["error_summary"]["code"] == expected_code
    assert len(requests) == 1
    assert result.telemetry["output_validation_retry"]["attempted"] is False
    diagnostics = json.dumps(result.telemetry) + str(result.error)
    assert "synthetic-refusal-must-not-leak" not in diagnostics
    assert "synthetic-response-secret" not in diagnostics


def test_actual_openai_validation_retry_reuses_input_without_previous_output(monkeypatch):
    invalid = synthetic_output()
    del invalid["threat_analysis"]["severity"]
    invalid["summary_ko"] = "synthetic-invalid-output-must-not-leak"
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(
        200, json=completion(json.dumps(invalid) if count == 1 else None),
    ))
    result = execute()
    assert result.succeeded is True
    assert len(requests) == 2
    bodies = [json.loads(request.content) for request in requests]
    assert [message for message in bodies[0]["messages"] if message["role"] == "user"] == [
        message for message in bodies[1]["messages"] if message["role"] == "user"
    ]
    repaired = json.dumps(bodies[1], ensure_ascii=False)
    assert "threat_analysis.severity: missing" in repaired
    assert "synthetic-invalid-output-must-not-leak" not in repaired
    assert "synthetic-invalid-output-must-not-leak" not in json.dumps(result.telemetry)
    assert result.telemetry["output_validation_retry"]["recovered"] is True


def test_openai_redirect_is_not_followed_and_error_body_is_not_saved(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(
        307, headers={"location": "https://unapproved.example/steal"},
        json={"error": {"message": "synthetic-error-body-must-not-leak"}},
    ))
    result = execute()
    assert result.succeeded is False
    assert len(requests) == 1
    assert result.telemetry["error_summary"]["code"] == "openai_http_307"
    assert "synthetic-error-body-must-not-leak" not in json.dumps(result.telemetry) + str(result.error)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_actual_openai_http_failure_keeps_status_not_response_body(monkeypatch, status):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(
        status, json={"error": {"message": "synthetic-http-body-must-not-leak"}},
    ))
    result = execute()
    assert result.succeeded is False
    assert result.telemetry["error_summary"]["code"] == f"openai_http_{status}"
    assert 1 <= len(requests) <= 2
    assert result.telemetry["output_validation_retry"]["attempted"] is False
    assert "synthetic-http-body-must-not-leak" not in json.dumps(result.telemetry) + str(result.error)


def test_vllm_generation_options_are_unchanged():
    assert generation_options(openai_profile(provider="vllm")) == {
        "temperature": 0, "max_tokens": 3072, "chat_template_kwargs": {"enable_thinking": False},
    }
