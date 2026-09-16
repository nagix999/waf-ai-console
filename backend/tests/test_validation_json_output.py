"""No network/model calls: fixed JSON probes and content-free diagnostics."""

import json

import httpx
import pytest

from app.services.validation_output_diagnostics import MAX_INSPECTION_CHARS, MAX_INSPECTION_DEPTH, inspect_json_output
from test_agent_openai import completion, install_http
from test_provider_test_runner import profile_for, successful_handler
from test_validation_probe_diagnostics import run


FIXED_JSON = '{"status":"ok","result":{"code":200,"message":"OK"}}'


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_probe_requests_exact_short_json_and_preserves_provider_options(monkeypatch, provider):
    profile = profile_for(provider)
    before = vars(profile).copy()
    requests, _ = install_http(monkeypatch, successful_handler(profile))
    result = run(profile, "quick")
    assert result.passed
    assert len(requests) == 3  # No repair call or fallback.
    body = json.loads(requests[-1].content)
    assert FIXED_JSON in body["messages"][0]["content"]
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True
    props = schema["schema"]["properties"]["result"]["properties"]
    assert props["message"]["enum"] == ["OK"]
    assert props["code"]["enum"] == [200]
    if provider == "vllm":
        assert body["max_tokens"] == 1024
        assert body["temperature"] == 0
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
    else:
        assert body["max_completion_tokens"] == profile.max_output_tokens
        assert "chat_template_kwargs" not in body
    assert vars(profile) == before
    assert "json_output" not in result.checks[1]["response_diagnostics"][0]
    assert result.checks[2]["response_diagnostics"][0]["json_output"]["status"] == "complete"


@pytest.mark.parametrize("profile_cap,passed", [(3072, True), (256, False)])
def test_mocked_256_token_truncation_can_finish_with_higher_probe_cap(monkeypatch, profile_cap, passed):
    profile = profile_for("vllm")
    profile.max_output_tokens = profile_cap

    def override(request, count):
        if count == 3:
            cap = json.loads(request.content)["max_tokens"]
            # Simulates a server that uses 300 tokens including formatting;
            # this is not evidence about any real Qwen tokenizer/server.
            body = completion(FIXED_JSON + " " * 300, finish_reason="stop" if cap >= 300 else "length")
            body["usage"] = {"prompt_tokens": 27, "completion_tokens": min(300, cap)}
            return httpx.Response(200, json=body)

    requests, _ = install_http(monkeypatch, successful_handler(profile, override))
    result = run(profile, "quick")
    assert result.passed is passed
    assert len(requests) == 3
    assert result.error_code == (None if passed else "vllm_output_incomplete")
    assert profile.max_output_tokens == profile_cap


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("content,status", [
    (FIXED_JSON, "complete"),
    (FIXED_JSON + " \n" * 100, "complete"),
    (FIXED_JSON + "synthetic-never-store" * 10, "complete_with_trailing_content"),
    ('{"status":"ok","result":', "invalid_or_incomplete"),
    ("", "empty"),
])
def test_length_never_passes_even_when_json_is_complete(monkeypatch, provider, content, status):
    profile = profile_for(provider)

    def override(request, count):
        if count == 3:
            body = completion(content, finish_reason="length")
            body["choices"][0]["message"]["reasoning_content"] = "synthetic-reasoning-never-store"
            body["choices"][0]["message"]["reasoning"] = "synthetic-other-reasoning-never-store"
            return httpx.Response(200, json=body)

    requests, _ = install_http(monkeypatch, successful_handler(profile, override))
    result = run(profile, "quick")
    assert not result.passed
    assert result.error_code == f"{provider}_output_incomplete"
    record = result.checks[-1]["response_diagnostics"][0]
    assert record["json_output"]["status"] == status
    assert record["finish_reason"] == "length"
    assert len(requests) == 3
    assert "never-store" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("content", [
    '{"status":', "not-json-synthetic-never-store", FIXED_JSON + " additional text",
    '{"status":"bad","status":"ok","result":{"code":200,"message":"OK"}}',
    '{"status":"ok","result":{"code":NaN,"message":"OK"}}',
    pytest.param("[" * 2000 + "]" * 2000, id="too-deep"),
    pytest.param(FIXED_JSON + " " * MAX_INSPECTION_CHARS, id="too-long"),
])
def test_completed_but_malformed_json_is_schema_error_not_incomplete(monkeypatch, content):
    profile = profile_for("vllm")
    requests, _ = install_http(monkeypatch, successful_handler(profile, lambda request, count:
        httpx.Response(200, json=completion(content)) if count == 3 else None))
    result = run(profile, "quick")
    assert not result.passed
    assert result.error_code == "json_schema_validation_failed"
    assert result.checks[-1]["response_diagnostics"][0]["finish_reason"] == "stop"
    assert len(requests) == 3
    assert "never-store" not in json.dumps(result.__dict__)


@pytest.mark.parametrize("content,status", [
    (FIXED_JSON, "complete"), (" \n" + FIXED_JSON + "\r\n", "complete"),
    (FIXED_JSON + " trailing", "complete_with_trailing_content"),
    (FIXED_JSON + FIXED_JSON, "complete_with_trailing_content"),
    ("{", "invalid_or_incomplete"), ('{"a":1,"a":2}', "invalid_or_incomplete"),
    ("NaN", "invalid_or_incomplete"), ("Infinity", "invalid_or_incomplete"),
    pytest.param("[" * 2000 + "]" * 2000, "inspection_limit", id="too-deep"),
    ("", "empty"), (" \t\n", "empty"),
    (None, "unavailable"), (["synthetic-never-store"], "unavailable"),
    pytest.param("x" * MAX_INSPECTION_CHARS, "invalid_or_incomplete", id="at-length-limit"),
    pytest.param("x" * (MAX_INSPECTION_CHARS + 1), "inspection_limit", id="over-length-limit"),
])
def test_syntax_inspection_is_bounded_and_does_not_claim_schema_validity(content, status):
    body = completion()
    body["choices"][0]["message"]["content"] = content
    result = inspect_json_output(body)
    assert result["status"] == status
    assert result["content_chars"] == (len(content) if isinstance(content, str) else None)
    assert set(result) == {"status", "content_chars", "trailing_whitespace_chars", "trailing_content_chars",
                           "repeated_suffix_unit_chars", "repeated_suffix_count"}
    assert "never-store" not in json.dumps(result)


def test_depth_limit_counts_structure_not_brackets_inside_escaped_strings():
    nested = "[" * MAX_INSPECTION_DEPTH + "]" * MAX_INSPECTION_DEPTH
    assert inspect_json_output(completion(nested))["status"] == "complete"
    assert inspect_json_output(completion("[" + nested + "]"))["status"] == "inspection_limit"
    quoted = json.dumps({"synthetic": '"' + "[\\\"" * 200 + "}" * 200})
    assert inspect_json_output(completion(quoted))["status"] == "complete"


def test_exact_suffix_repetition_and_whitespace_are_counts_not_response_text():
    unit = "synthetic-never-store"
    result = inspect_json_output(completion(FIXED_JSON + unit * 12 + " \n\t"))
    assert result["status"] == "complete_with_trailing_content"
    assert result["trailing_content_chars"] == len(unit) * 12
    assert result["trailing_whitespace_chars"] == 3
    assert result["repeated_suffix_unit_chars"] == len(unit)
    assert result["repeated_suffix_count"] == 12
    assert unit not in json.dumps(result)
    spaces = inspect_json_output(completion(FIXED_JSON + " " * 200))
    assert spaces["status"] == "complete"
    assert spaces["trailing_whitespace_chars"] == 200
    assert spaces["repeated_suffix_count"] == 0
    assert inspect_json_output(completion(FIXED_JSON))["repeated_suffix_count"] == 0


@pytest.mark.parametrize("body", [None, [], {}, {"choices": []}, {"choices": [None]},
    {"choices": [{"message": None}]}, {"choices": [{}, {}]},
])
def test_invalid_envelope_keeps_json_observations_unknown(body):
    result = inspect_json_output(body)
    assert result["status"] == "unavailable"
    assert all(value is None for key, value in result.items() if key != "status")
