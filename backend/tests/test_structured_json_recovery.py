"""Synthetic, offline JSON recovery through the real ModuAgent 0.6.2 codec."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from moduagent.messages import Message
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError

from app.agent.contracts import SecurityAnalysisOutputV220
from app.agent.executor_v221 import _TrackingOutputCodec, _structured_repair_instructions, execute_structured_agent
from app.agent.structured_json import (
    MAX_JSON_BYTES, MAX_JSON_DEPTH, StructuredJSONRejected, normalize_structured_json,
)
from test_agent_openai import completion, install_http, openai_profile
from test_evidence_relations import wire


def output_text():
    return json.dumps(wire(), ensure_ascii=False)


def execute(profile, **kwargs):
    return asyncio.run(execute_structured_agent(profile=profile, api_key="synthetic-key",
        instructions="Synthetic independent WAF analysis.", user_input="synthetic-input-only",
        session_id="synthetic-analysis:primary", agent_name="waf-primary", **kwargs))


def codec(*, enabled=True):
    return _TrackingOutputCodec(PydanticOutputCodec(SecurityAnalysisOutputV220),
        output_model=SecurityAnalysisOutputV220, structured_output_recovery=enabled)


def profile(provider):
    return openai_profile(provider=provider, base_url=(
        "http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1"))


@pytest.mark.parametrize("wrapper,expected", [
    (lambda text: text, []),
    (lambda text: " \n" + text + "\t\r\n", []),
    (lambda text: "\ufeff" + text, ["utf8_bom"]),
    (lambda text: "```json\n" + text + "\n```", ["markdown_json_fence"]),
    (lambda text: "```JSON\r\n" + text + "\r\n```", ["markdown_json_fence"]),
    (lambda text: "```\n" + text + "\n```", ["markdown_json_fence"]),
    (lambda text: "\ufeff```json\n" + text + "\n```\n", ["utf8_bom", "markdown_json_fence"]),
])
def test_only_unambiguous_outer_wrappers_removed(wrapper, expected):
    output_codec = codec()
    text = output_text()
    result = output_codec.decode(wrapper(text))
    assert result == SecurityAnalysisOutputV220.model_validate_json(text)
    assert output_codec.json_normalizations == expected
    assert output_codec.validation_issues == []


@pytest.mark.parametrize("shape", [
    lambda text: text.encode("utf-8"),
    lambda text: bytearray(text.encode("utf-8")),
    lambda text: {"message": {"content": text}},
    lambda text: {"content": text},
    lambda text: Message.assistant(text),
    lambda text: SimpleNamespace(message=Message.assistant(text)),
])
def test_same_public_moduagent_response_shapes(shape):
    text = "```json\n" + output_text() + "\n```"
    assert codec().decode(shape(text)) == SecurityAnalysisOutputV220.model_validate(wire())


@pytest.mark.parametrize("text,code", [
    ('{"synthetic-private-key": 1, "synthetic-private-key": 2}', "json_duplicate_key"),
    ('{"inner":{"key":1,"key":2}}', "json_duplicate_key"),
    ('{"value": NaN}', "json_non_finite"),
    ('{"value": Infinity}', "json_non_finite"),
    ('{"value": -Infinity}', "json_non_finite"),
    ('{"value": 1e10000}', "json_non_finite"),
    ('{"value": -1e10000}', "json_non_finite"),
    ('{"synthetic-private-output":', "json_invalid"),
    ("{'value': 1}", "json_invalid"),
    ('{"value":1,}', "json_invalid"),
    ('{"value":"bad\\xescape"}', "json_invalid"),
    ('{"value": "a\nb"}', "json_invalid"),
    ('{"value": "unterminated}', "json_invalid"),
    ('{"value":1}{"value":2}', "json_invalid"),
    ('Here is JSON:\n{"value":1}', "json_invalid"),
    ('{"value":1}\nExplanation', "json_invalid"),
    ('```json\n{"value":1}', "json_invalid"),
    ('```python\n{"value":1}\n```', "json_invalid"),
    ('```json\n{"value":1}\n```\n{"extra":2}', "json_invalid"),
    ('\ufeff\ufeff{"value":1}', "json_invalid"),
    ('[{"value":1}]', "json_top_level_object_required"),
    ('"{\\"value\\":1}"', "json_top_level_object_required"),
    ('null', "json_top_level_object_required"),
    ('true', "json_top_level_object_required"),
    ('1', "json_top_level_object_required"),
    (b'\xff\xfe{"value":1}', "json_encoding_invalid"),
    ('{"value":"\ud800"}', "json_encoding_invalid"),
])
def test_invalid_or_ambiguous_json_never_guessed(text, code):
    output_codec = codec()
    with pytest.raises(ValueError) as error:
        output_codec.decode(text)
    assert str(error.value) == code
    assert output_codec.validation_issues == [{"field": "$", "type": code}]
    assert "synthetic-private" not in str(error.value)


def test_reject_escaped_duplicate_key_without_echoing_key():
    with pytest.raises(StructuredJSONRejected, match="^json_duplicate_key$"):
        normalize_structured_json('{"private": 1, "\\u0070rivate":2}')


def test_length_and_depth_are_bounded_before_pydantic(monkeypatch):
    import app.agent.structured_json as helper
    called = []
    original = helper.json.loads

    def loads(*args, **kwargs):
        called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(helper.json, "loads", loads)
    for text, code in [
        (" " * (MAX_JSON_BYTES + 1), "json_size_limit"),
        ('{"key":"' + "가" * (MAX_JSON_BYTES // 3) + '"}', "json_size_limit"),
        ("[" * (MAX_JSON_DEPTH + 1) + "0" + "]" * (MAX_JSON_DEPTH + 1), "json_depth_limit"),
    ]:
        with pytest.raises(StructuredJSONRejected, match="^" + code + "$"):
            normalize_structured_json(text)
    assert called == []


def test_json_string_brackets_and_escapes_are_not_counted_as_depth():
    value = {"value": "[" * 100 + '{"escape":\\' + "]" * 100}
    encoded = json.dumps(value)
    assert normalize_structured_json(encoded).content == encoded


@pytest.mark.parametrize("mutate,expected", [
    (lambda value: value.update(invented="synthetic-private-extra"), "extra_forbidden"),
    (lambda value: value.pop("summary_ko"), "missing"),
    (lambda value: value.update(verdict="invented"), "enum"),
    (lambda value: value.update(summary_ko="정탐입니다."), "summary_must_describe_request"),
    (lambda value: value.update(evidence_relations=[{"representative_index": 4, "related_indexes": [0]}]), "evidence_relation_invalid"),
])
def test_wrapper_recovery_never_relaxes_schema_or_semantic_checks(mutate, expected):
    value = wire()
    mutate(value)
    output_codec = codec()
    with pytest.raises(ValidationError):
        output_codec.decode("```json\n" + json.dumps(value) + "\n```")
    assert expected in {issue["type"] for issue in output_codec.validation_issues}
    assert "synthetic-private-extra" not in json.dumps(output_codec.validation_issues)


def test_legacy_codec_is_not_opted_in_by_default():
    output_codec = codec(enabled=False)
    with pytest.raises(ValidationError):
        output_codec.decode("```json\n" + output_text() + "\n```")
    assert output_codec.json_normalizations == []
    assert output_codec.validation_issues[0]["type"] == "json_invalid"


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_completed_fenced_output_recovers_without_another_model_call(monkeypatch, provider):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion("\ufeff```json\n" + output_text() + "\n```")))
    result = execute(profile(provider), egress_check=lambda: None,
        output_model=SecurityAnalysisOutputV220, structured_output_recovery=True)
    assert result.succeeded and len(requests) == 1
    retry = result.telemetry["output_validation_retry"]
    assert retry["version"] == "structured-output-repair-v3"
    assert retry["attempt_count"] == 1 and not retry["attempted"]
    assert retry["attempts"][0]["json_normalizations"] == ["utf8_bom", "markdown_json_fence"]
    assert result.telemetry["structured_output"]["version"] == "bounded-structured-json-v1"
    assert result.telemetry["raw_provider_body_stored"] is False


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("bad,code,feedback", [
    ('{"synthetic-private-output":', "json_invalid", "이스케이프"),
    ('{"synthetic-private-key":1,"synthetic-private-key":2}', "json_duplicate_key", "키를 중복"),
    ('{"value":NaN}', "json_non_finite", "유한한 값"),
    ('[]', "json_top_level_object_required", "단일 최상위 객체"),
])
def test_cause_specific_corrective_feedback_keeps_original_input_and_budget(
    monkeypatch, provider, bad, code, feedback,
):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion(bad if count == 1 else output_text())))
    result = execute(profile(provider), egress_check=lambda: None,
        output_model=SecurityAnalysisOutputV220, structured_output_recovery=True)
    assert result.succeeded and len(requests) == 2
    attempt = result.telemetry["output_validation_retry"]["attempts"][0]
    assert attempt["validation_issues"] == [{"field": "$", "type": code}]
    bodies = [json.loads(request.content) for request in requests]
    assert [item for item in bodies[0]["messages"] if item["role"] == "user"] == [
        item for item in bodies[1]["messages"] if item["role"] == "user"]
    assert all(body["model"] == profile(provider).model_name for body in bodies)
    limit = "max_tokens" if provider == "vllm" else "max_completion_tokens"
    assert all(body[limit] == 3072 for body in bodies)
    repair = json.dumps(bodies[1], ensure_ascii=False)
    assert feedback in repair and code in repair
    assert "synthetic-private" not in repair + json.dumps(result.telemetry) + str(result.error)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_recovery_stops_at_existing_four_attempt_budget(monkeypatch, provider):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion('{"synthetic-private-output":')))
    result = execute(profile(provider), egress_check=lambda: None,
        output_model=SecurityAnalysisOutputV220, structured_output_recovery=True)
    assert not result.succeeded and len(requests) == 4
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == retry["max_attempts"] == 4
    assert retry["stop_reason"] == "attempt_limit"
    assert result.error == "output_validation_failed"
    assert "synthetic-private" not in json.dumps(result.telemetry) + str(result.error)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("reason", ["length", "max_tokens", "timeout", "content_filter"])
def test_complete_json_in_incomplete_or_filtered_envelope_never_salvaged(monkeypatch, provider, reason):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion("```json\n" + output_text() + "\n```", finish_reason=reason)))
    result = execute(profile(provider), egress_check=lambda: None,
        output_model=SecurityAnalysisOutputV220, structured_output_recovery=True)
    assert not result.succeeded and result.output is None
    # OpenAI's existing completion guard accepts only its documented `length`
    # truncation reason for retry; unknown provider-specific reasons stay fatal.
    retryable = reason != "content_filter" and (provider == "vllm" or reason == "length")
    assert len(requests) == (4 if retryable else 1)
    assert all(not attempt["json_normalizations"] for attempt in result.telemetry["output_validation_retry"]["attempts"])


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_refusal_never_recovered_or_corrected(monkeypatch, provider):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion(output_text(), refusal="synthetic-private-refusal")))
    result = execute(profile(provider), egress_check=lambda: None,
        output_model=SecurityAnalysisOutputV220, structured_output_recovery=True)
    assert not result.succeeded and len(requests) == 1
    assert "synthetic-private-refusal" not in str(result.error) + json.dumps(result.telemetry)


def test_feedback_defensively_rejects_non_schema_paths_and_raw_error_types():
    text = _structured_repair_instructions([
        {"field": "synthetic-private-value", "type": "synthetic-private-error"},
        {"field": "evidence.[].source_id", "type": "missing"},
    ], SecurityAnalysisOutputV220)
    assert "synthetic-private" not in text
    assert "evidence.[].source_id: missing" in text


@pytest.mark.parametrize("flag", [1, 0, "true", None])
def test_flag_is_not_coerced_or_an_external_configuration(monkeypatch, flag):
    requests, _ = install_http(monkeypatch, lambda *_: pytest.fail("No HTTP expected"))
    with pytest.raises(ValueError, match="invalid_structured_output_recovery_flag"):
        execute(profile("vllm"), egress_check=lambda: None, structured_output_recovery=flag)
    assert requests == []
