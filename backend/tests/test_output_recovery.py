"""Synthetic, offline responses through the real ModuAgent adapter."""
import asyncio
import json

import httpx
import pytest

from app.agent.contracts import EvidenceCorrectionOutput
from app.agent.grounding_repair import failure_category
from app.agent.evidence_editor import EvidenceEditorOutput
from test_agent_openai import completion, execute, install_http, openai_profile, synthetic_output


def profile(provider="vllm"):
    return openai_profile(provider=provider, base_url=(
        "http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1"))


@pytest.mark.parametrize("provider,reason", [
    ("vllm", "length"), ("vllm", "max_tokens"), ("vllm", "timeout"), ("openai", "length"),
])
def test_incomplete_response_recovers_with_specific_feedback(monkeypatch, provider, reason):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=(
        completion("synthetic-partial-private-output", finish_reason=reason) if count == 1 else completion())))
    result = execute(profile(provider), egress_check=lambda: None)
    assert result.succeeded and len(requests) == 2
    retry = result.telemetry["output_validation_retry"]
    assert retry["version"] == "structured-output-repair-v2"
    assert retry["recovered"] and retry["stop_reason"] == "completed"
    first = retry["attempts"][0]
    assert first["provider_finish_reason"] == reason
    assert first["retry_kind"] == ("timeout_recovery" if reason == "timeout" else "compact_output")
    assert first["validation_issues"] == []  # Not a schema conversion failure.
    assert first["usage"] == {"input_tokens": 28000, "output_tokens": 250, "total_tokens": 28250}
    assert first["duration_ms"] >= 0
    bodies = [json.loads(request.content) for request in requests]
    user = lambda body: [m for m in body["messages"] if m["role"] == "user"]
    assert user(bodies[0]) == user(bodies[1])
    assert all(body["model"] == profile(provider).model_name for body in bodies)
    output_limit = "max_tokens" if provider == "vllm" else "max_completion_tokens"
    assert all(body[output_limit] == 3072 for body in bodies)
    repair = json.dumps(bodies[1], ensure_ascii=False)
    assert reason in repair and "완전한 JSON" in repair and "1~2문장" in repair
    assert "보류를 선택하지 않는다" in repair
    assert "synthetic-partial-private-output" not in repair + json.dumps(result.telemetry)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_fourth_attempt_repairs_repeated_schema_failures_without_history_growth(monkeypatch, provider):
    invalid = synthetic_output()
    del invalid["threat_analysis"]["severity"]
    invalid["summary_ko"] = "synthetic-invalid-private-output"
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(
        json.dumps(invalid) if count < 4 else None)))
    result = execute(profile(provider), egress_check=lambda: None)
    assert result.succeeded and len(requests) == 4
    retry = result.telemetry["output_validation_retry"]
    assert retry["recovered"] and retry["max_attempts"] == retry["attempt_count"] == 4
    bodies = [json.loads(request.content) for request in requests]
    for index, body in enumerate(bodies[1:], 1):
        repair = json.dumps(body, ensure_ascii=False)
        assert "threat_analysis.severity: missing" in repair
        assert f"교정 단계 {index}" in repair
        assert repair.count("이전 실행은 output_validation_failed") == 1
        assert "synthetic-invalid-private-output" not in repair
    assert len({json.dumps(body["messages"]) for body in bodies}) == 4
    assert "synthetic-invalid-private-output" not in json.dumps(result.telemetry)


def test_mixed_errors_use_latest_feedback_and_retain_compact_instruction(monkeypatch):
    invalid = synthetic_output()
    del invalid["confidence_score"]
    responses = [completion(finish_reason="length"), completion(json.dumps(invalid)),
                 completion(finish_reason="max_tokens"), completion()]
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=responses[count - 1]))
    result = execute(profile(), egress_check=lambda: None)
    assert result.succeeded
    assert [a["retry_kind"] for a in result.telemetry["output_validation_retry"]["attempts"]] == [
        "compact_output", "schema_correction", "compact_output", None]
    third = json.dumps(json.loads(requests[2].content), ensure_ascii=False)
    assert "confidence_score: missing" in third and "1문장" in third


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_malformed_json_repaired_without_echoing_content(monkeypatch, provider):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(
        '{"private-broken-output":' if count < 3 else None)))
    result = execute(profile(provider), egress_check=lambda: None)
    assert result.succeeded and len(requests) == 3
    repair = json.dumps(json.loads(requests[1].content))
    assert "private-broken-output" not in repair + json.dumps(result.telemetry)
    assert result.telemetry["output_validation_retry"]["attempts"][0]["validation_issues"]


def test_incomplete_usage_counted_once_in_comparisons(monkeypatch):
    from app.services.test_comparisons import _step_usage
    def handler(request, count):
        body = completion(finish_reason="stop" if count == 4 else "length")
        body["usage"] = {"prompt_tokens": count * 100, "completion_tokens": count * 10, "total_tokens": count * 110}
        return httpx.Response(200, json=body)
    install_http(monkeypatch, handler)
    result = execute(profile(), egress_check=lambda: None)
    assert result.succeeded
    assert result.telemetry["usage"] == {"input_tokens": 400, "output_tokens": 40, "total_tokens": 440}
    assert _step_usage(result.telemetry) == {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}


@pytest.mark.parametrize("reason", ["length", "max_tokens", "timeout"])
def test_incomplete_valid_json_never_accepted_after_limit(monkeypatch, reason):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(finish_reason=reason)))
    result = execute(profile(), egress_check=lambda: None)
    assert not result.succeeded and result.output is None and len(requests) == 4
    assert failure_category(result) == "output_incomplete"
    assert result.telemetry["error_summary"]["provider_finish_reason"] == reason
    retry = result.telemetry["output_validation_retry"]
    assert retry["stop_reason"] == "attempt_limit" and not retry["recovered"]


@pytest.mark.parametrize("body", [completion(refusal="private-refusal", finish_reason="length"),
                                   completion(finish_reason="content_filter"),
                                   completion(finish_reason="private-provider-reason"),
                                   completion(content=""), {"error": {"message": "private-http-error"}}])
def test_nonrepairable_responses_stop_without_exposing_body(monkeypatch, body):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=body))
    result = execute(profile(), egress_check=lambda: None)
    assert not result.succeeded and len(requests) == 1
    assert result.telemetry["output_validation_retry"]["stop_reason"] == "not_retryable"
    assert "private-" not in json.dumps(result.telemetry) + str(result.error)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_http_errors_do_not_start_outer_correction_loop(monkeypatch, status):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(status, json={
        "error": {"message": "private-http-body"}}))
    result = execute(profile(), egress_check=lambda: None)
    assert not result.succeeded and len(requests) == (2 if status == 500 else 1)
    assert result.telemetry["output_validation_retry"]["attempt_count"] == 1
    assert "private-http-body" not in json.dumps(result.telemetry) + str(result.error)


def test_nested_transport_retries_are_bounded(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(
        500 if count % 2 else 200, json={} if count % 2 else completion(finish_reason="length")))
    result = execute(profile(), egress_check=lambda: None)
    assert not result.succeeded and len(requests) == 8
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == 4 and retry["max_transport_attempts_per_attempt"] == 2


def test_revoked_egress_is_checked_before_corrective_request(monkeypatch):
    from app.services.vllm_profiles import TargetNotAllowedError
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(finish_reason="length")))
    def check():
        if requests:
            raise TargetNotAllowedError("vllm_target_not_allowed")
    result = execute(profile(), egress_check=check)
    assert not result.succeeded and len(requests) == 1
    assert result.telemetry["output_validation_retry"]["stop_reason"] == "not_retryable"


@pytest.mark.parametrize("output_model", [EvidenceCorrectionOutput, EvidenceEditorOutput])
def test_auxiliary_agents_keep_single_attempt(monkeypatch, output_model):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(finish_reason="length")))
    result = execute(profile(), egress_check=lambda: None, output_model=output_model, output_validation_max_attempts=1)
    assert not result.succeeded and len(requests) == 1


@pytest.mark.parametrize("limit", [0, 5, -1, True, 2.0, None])
def test_invalid_attempt_limits_rejected_without_http(monkeypatch, limit):
    requests, _ = install_http(monkeypatch, lambda request, count: pytest.fail("No HTTP expected"))
    with pytest.raises(ValueError, match="invalid_output_validation_attempt_limit"):
        execute(profile(), egress_check=lambda: None, output_validation_max_attempts=limit)
    assert requests == []


def test_cancellation_during_correction_does_not_restart(monkeypatch):
    def handler(request, count):
        if count > 1:
            raise asyncio.CancelledError()
        return httpx.Response(200, json=completion(finish_reason="length"))
    requests, _ = install_http(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        execute(profile(), egress_check=lambda: None)
    assert len(requests) == 2
