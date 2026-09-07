import asyncio
import json

import httpx
import pytest

from app.services.provider_options import generation_options, provider_name
from app.services.vllm_test_runner import chat_payload, run_vllm_test
from test_agent_openai import (
    assert_strict_schema,
    completion,
    install_http,
    openai_profile,
    synthetic_output,
)


class SyntheticCrypto:
    def decrypt_text(self, ciphertext):
        assert ciphertext == "synthetic-encrypted-key"
        return "synthetic-key"


def profile_for(provider):
    return openai_profile(
        provider=provider,
        base_url="https://api.openai.com/v1" if provider == "openai" else "http://vllm.internal:8000/v1",
        model_name="gpt-4.1" if provider == "openai" else "google/gemma-4-26B-A4B-it",
    )


def successful_handler(profile, override=None):
    def receive(request, count):
        if override:
            response = override(request, count)
            if response is not None:
                return response
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": profile.model_name}]})
        body = json.loads(request.content)
        schema = body.get("response_format", {}).get("json_schema", {})
        if schema.get("name") == "waf_test":
            text = json.dumps({"status": "ok", "result": {"code": 200, "message": "synthetic-content-must-not-be-stored"}})
        elif schema.get("name") == "WAFAnalysisOutput":
            text = json.dumps(synthetic_output())
        else:
            text = "WAF_TEST_OK SYSTEM_ROLE_OK synthetic-content-must-not-be-stored"
        return httpx.Response(200, json=completion(text))
    return receive


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("mode", ["quick", "full"])
def test_provider_checks_use_matching_options_and_store_no_response_text(monkeypatch, provider, mode):
    profile = profile_for(provider)
    requests, options = install_http(monkeypatch, successful_handler(profile))
    result = asyncio.run(run_vllm_test(profile, SyntheticCrypto(), mode, egress_check=lambda: None))
    assert result.passed is True
    names = [check["name"] for check in result.checks]
    assert names[:3] == ["models", "basic_chat", "nested_json_schema"]
    if mode == "full":
        if provider == "openai":
            assert names[3:] == ["waf_analysis_schema", "system_role", "near_configured_context", "concurrency"]
        else:
            assert names[3:] == ["system_role", "near_32k_context", "concurrency"]
    else:
        assert len(names) == 3
    assert options[0]["verify"] is True
    assert options[0]["follow_redirects"] is False
    assert options[0]["trust_env"] is False
    assert result.metrics["provider"] == provider
    assert result.metrics["thinking_enabled"] is (None if provider == "openai" else False)
    for request in requests:
        assert request.headers["authorization"] == "Bearer synthetic-key"
        if request.method == "GET":
            continue
        body = json.loads(request.content)
        if provider == "openai":
            for key, value in generation_options(profile).items():
                assert body[key] == value
            assert not {"max_tokens", "chat_template_kwargs", "temperature", "reasoning_effort"} & body.keys()
            if "response_format" in body:
                assert_strict_schema(body["response_format"]["json_schema"]["schema"])
        else:
            assert body["temperature"] == 0
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            assert body["max_tokens"] in {8, 64}
            assert "max_completion_tokens" not in body and "store" not in body
    stored = json.dumps(result.__dict__)
    for excluded in ["synthetic-content-must-not-be-stored", "synthetic-key", "synthetic-encrypted-key", "provider-metadata-must-not-be-stored"]:
        assert excluded not in stored


@pytest.mark.parametrize("mode,failed_check", [
    ("quick", "basic_chat"), ("quick", "nested_json_schema"),
    ("full", "waf_analysis_schema"), ("full", "system_role"),
    ("full", "near_configured_context"), ("full", "concurrency"),
])
@pytest.mark.parametrize("failure", ["refusal", "length"])
def test_refusal_and_truncation_are_rejected_at_every_openai_check(monkeypatch, mode, failed_check, failure):
    profile = openai_profile()
    indices = {"basic_chat": 2, "nested_json_schema": 3, "waf_analysis_schema": 4, "system_role": 5, "near_configured_context": 6, "concurrency": 7}

    def override(request, count):
        if count == indices[failed_check]:
            return httpx.Response(200, json=completion(
                refusal="synthetic-refusal-must-not-leak" if failure == "refusal" else None,
                finish_reason="length" if failure == "length" else "stop",
            ))

    install_http(monkeypatch, successful_handler(profile, override))
    result = asyncio.run(run_vllm_test(profile, SyntheticCrypto(), mode))
    assert result.passed is False
    assert result.metrics["failed_check"] == failed_check
    assert result.error_code == ("openai_refusal" if failure == "refusal" else "openai_output_incomplete")
    assert "synthetic-refusal-must-not-leak" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("invalid", [
    {"status": "ok", "result": {"code": True, "message": "synthetic"}},
    {"status": "ok", "result": {"code": 200, "message": "synthetic", "extra": 1}},
    {"status": "ok", "result": {"code": 200, "message": "synthetic"}, "extra": 1},
    [],
])
def test_nested_schema_check_rejects_bool_integer_and_extra_properties(monkeypatch, invalid):
    profile = openai_profile()
    install_http(monkeypatch, successful_handler(profile, lambda request, count:
        httpx.Response(200, json=completion(json.dumps(invalid))) if count == 3 else None
    ))
    result = asyncio.run(run_vllm_test(profile, SyntheticCrypto(), "quick"))
    assert result.passed is False
    assert result.error_code == "json_schema_validation_failed"


def test_full_openai_check_rejects_actual_waf_semantic_contract_failure(monkeypatch):
    profile = openai_profile()
    invalid = synthetic_output()
    invalid["verdict"] = "true_positive"
    invalid["summary_ko"] = "synthetic-schema-error-must-not-leak"
    install_http(monkeypatch, successful_handler(profile, lambda request, count:
        httpx.Response(200, json=completion(json.dumps(invalid))) if count == 4 else None
    ))
    result = asyncio.run(run_vllm_test(profile, SyntheticCrypto(), "full"))
    assert result.passed is False
    assert result.error_code == "waf_schema_validation_failed"
    assert result.metrics["failed_check"] == "waf_analysis_schema"
    assert "synthetic-schema-error-must-not-leak" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("status", [307, 400, 401, 403, 429, 500])
def test_openai_http_errors_are_safe_and_redirects_not_followed(monkeypatch, status):
    profile = openai_profile()
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(
        status, headers={"location": "https://unapproved.example/steal"},
        json={"error": {"message": "synthetic-provider-error-must-not-leak"}},
    ))
    result = asyncio.run(run_vllm_test(profile, SyntheticCrypto(), "quick"))
    assert result.passed is False
    assert result.error_code == f"openai_http_{status}"
    assert len(requests) == 1
    assert "synthetic-provider-error-must-not-leak" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("changes", [
    {"external_data_approved": False}, {"api_key_ciphertext": None},
    {"tls_verify": False}, {"base_url": "https://unapproved.example/v1"},
])
def test_runner_openai_settings_fail_closed_before_http(monkeypatch, changes):
    requests, options = install_http(monkeypatch, lambda request, count: pytest.fail("HTTP must not be reached"))
    result = asyncio.run(run_vllm_test(openai_profile(**changes), SyntheticCrypto(), "quick"))
    assert result.passed is False
    assert requests == options == []


def test_legacy_provider_defaults_and_unknown_provider_fail_closed():
    profile = openai_profile(provider=None)
    assert provider_name(profile) == "vllm"
    del profile.provider
    assert provider_name(profile) == "vllm"
    for invalid in ["", "external", "OpenAI"]:
        profile.provider = invalid
        with pytest.raises(ValueError, match="model_provider_not_supported"):
            chat_payload(profile, [{"role": "user", "content": "synthetic"}])
