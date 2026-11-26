"""Actual ModuAgent 0.6.2 with synthetic mocked HTTP, no live model calls."""
import asyncio
from copy import deepcopy
import json

import httpx
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError
import pytest

from app.agent import executor_v234 as executor
from app.agent.contracts import EvidenceSelectionCorrectionOutput
from app.agent.contracts_v234 import EvidenceDecisionReviewOutput, SecurityAnalysisOutputV234
from app.agent.output_schema_v226 import compact_strict_schema
from test_agent_openai import assert_strict_schema, completion, install_http, openai_profile
from test_contracts_v234 import conflict, diagnostic
from test_decision_review_contract_v232 import review_context, review_wire
from test_evidence_interpretations_v227 import request_input
from test_flat_evidence_v228 import wire


def execute(provider="vllm", *, model=SecurityAnalysisOutputV234, attempts=4, user_input=None,
            defer=False, recovery=True):
    profile = openai_profile(provider=provider,
        base_url="http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1")
    return asyncio.run(executor.execute_structured_agent(
        profile=profile, api_key="synthetic-key", instructions="Synthetic independent same-role analysis.",
        user_input=request_input() if user_input is None else user_input,
        session_id="synthetic:primary", agent_name="waf-primary", egress_check=lambda: None,
        output_model=model, structured_output_recovery=recovery,
        defer_source_support_validation=defer, output_validation_max_attempts=attempts))


def install_sequence(monkeypatch, sequence):
    return install_http(monkeypatch, lambda _request, count: httpx.Response(200, json=completion(
        json.dumps(sequence[min(count - 1, len(sequence) - 1)][0], ensure_ascii=False)
        if type(sequence[min(count - 1, len(sequence) - 1)][0]) is dict
        else sequence[min(count - 1, len(sequence) - 1)][0],
        finish_reason=sequence[min(count - 1, len(sequence) - 1)][1])))[0]


def instructions(request):
    return "\n".join(message["content"] for message in json.loads(request.content)["messages"]
                     if message["role"] == "system")


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_conflict_then_real_length_then_success_has_current_attribution_and_generic_reminder(monkeypatch, provider):
    requests = install_sequence(monkeypatch, [(conflict(), "stop"), ('{"evidence":[', "length"), (wire(), "stop")])
    result = execute(provider)
    assert result.succeeded and len(requests) == 3
    assert result.output.model_dump(mode="json") == wire()
    attempts = result.telemetry["output_validation_retry"]["attempts"]
    incomplete_code = "openai_output_incomplete" if provider == "openai" else "model_output_incomplete"
    assert [item["error_code"] for item in attempts] == ["output_validation_failed", incomplete_code, None]
    assert [item["retry_kind"] for item in attempts] == ["schema_correction", "compact_output", None]
    assert [item["provider_finish_reason"] for item in attempts] == ["stop", "length", "stop"]
    assert attempts[1]["validation_issues"] == []
    assert attempts[0]["validation_issues"][0]["conflict_paths"] == diagnostic(conflict())["ctx"]["conflict_paths"]
    correction = instructions(requests[1])
    assert "e2[c2,attack_request]" in correction and "e5[c1,normal_request]" in correction
    assert "이전 번호를 그대로 수정 대상으로 적용하지 않는다" in correction
    reminder = instructions(requests[2])
    assert "직전 완결 응답의 연결 원칙" in reminder
    assert "e2[c2" not in reminder and "e5[c1" not in reminder
    assert "1~2문장" in reminder and "supports만 뒤집거나" in correction
    assert all(item["max_output_tokens"] == 3072 for item in attempts)
    bodies = [json.loads(request.content) for request in requests]
    users = [[message for message in body["messages"] if message["role"] == "user"] for body in bodies]
    assert users[0] == users[1] == users[2]
    assert "synthetic body" not in json.dumps(result.telemetry)
    assert "synthetic-key" not in json.dumps(result.telemetry)


def test_repeated_domain_errors_stop_at_four_without_increasing_compression_or_changing_direction(monkeypatch):
    value = conflict()
    before = deepcopy(value)
    requests = install_sequence(monkeypatch, [(value, "stop")])
    result = execute()
    assert not result.succeeded and result.output is None and len(requests) == 4
    retry = result.telemetry["output_validation_retry"]
    assert retry["stop_reason"] == "attempt_limit"
    assert retry["max_attempts"] == 4
    assert all(item["error_code"] == "output_validation_failed" for item in retry["attempts"])
    assert all(item["provider_finish_reason"] == "stop" for item in retry["attempts"])
    for request in requests[1:]:
        feedback = instructions(request)
        assert "1문장" not in feedback and "1~2문장" not in feedback
        assert "필요한 관련 원문과 반대 근거를 보존" in feedback
        assert "모든 연결을 지우지 않는다" in feedback
    assert len({instructions(request) for request in requests[1:]}) == 3
    assert "간접 연결과 연결 방향도 확인" in instructions(requests[2])
    assert value == before


def test_new_completed_response_replaces_local_ids_not_accumulates_old_paths(monkeypatch):
    requests = install_sequence(monkeypatch, [(conflict(), "stop"), (conflict(changed_ids=True), "stop"), (wire(), "stop")])
    result = execute()
    assert result.succeeded
    first, second = instructions(requests[1]), instructions(requests[2])
    assert "e2[c2,attack_request]" in first
    assert "e1[c2,attack_request]" in second and "e3[c1,normal_request]" in second
    assert "e2[c2,attack_request]" not in second and "e5[c1,normal_request]" not in second


def test_new_complete_severity_error_replaces_conflict_before_next_length(monkeypatch):
    invalid = wire()
    invalid["verdict"] = "inconclusive"
    requests = install_sequence(monkeypatch, [(conflict(), "stop"), (invalid, "stop"), ('{"evidence":[', "length"), (wire(), "stop")])
    result = execute()
    assert result.succeeded and len(requests) == 4
    assert "inconclusive_requires_unknown_severity" in instructions(requests[2])
    assert "이전 응답의 연결:" not in instructions(requests[2])
    latest = instructions(requests[3])
    assert "직전 완결 응답의 정합성" in latest
    assert "직전 완결 응답의 연결 원칙" not in latest
    assert "e2[c2" not in latest


def test_malformed_provider_completed_json_clears_prior_domain_reminder(monkeypatch):
    requests = install_sequence(monkeypatch, [(conflict(), "stop"), ('{"evidence":[', "stop"), ('{"evidence":[', "length"), (wire(), "stop")])
    result = execute()
    assert result.succeeded
    assert "json_invalid" in instructions(requests[2])
    assert "이전 응답의 연결:" not in instructions(requests[2])
    assert "직전 완결 응답의 연결 원칙" not in instructions(requests[3])
    assert "e2[c2" not in instructions(requests[3])


@pytest.mark.parametrize("reason", ["length", "max_tokens"])
def test_actual_output_limits_never_patch_truncated_json_or_exceed_budget(monkeypatch, reason):
    requests = install_sequence(monkeypatch, [('{"evidence":[', reason)])
    result = execute()
    assert not result.succeeded and len(requests) == 4 and result.output is None
    attempts = result.telemetry["output_validation_retry"]["attempts"]
    assert all(item["retry_kind"] == "compact_output" for item in attempts)
    assert all(item["validation_issues"] == [] for item in attempts)
    assert all(item["max_output_tokens"] == 3072 for item in attempts)
    assert "1~2문장" in instructions(requests[1]) and "핵심 1문장" in instructions(requests[2])
    assert "결합 해석에 필요한 관계 및 중요한 반대 근거" in instructions(requests[3])


def test_conflict_diagnostics_do_not_include_private_text_in_feedback_or_telemetry(monkeypatch):
    value = conflict()
    value["evidence"][0]["interpretation_ko"] = "SYNTHETIC_PRIVATE_INTERPRETATION"
    source_input = json.loads(request_input())
    source_input["evidence_candidates"]["items"][0]["excerpt"] = "SYNTHETIC_PRIVATE_EXCERPT"
    requests = install_sequence(monkeypatch, [(value, "stop"), (wire(), "stop")])
    result = execute(user_input=json.dumps(source_input))
    assert result.succeeded
    for secret in ("SYNTHETIC_PRIVATE_INTERPRETATION", "SYNTHETIC_PRIVATE_EXCERPT", "synthetic-key"):
        assert secret not in json.dumps(result.telemetry)
        assert secret not in instructions(requests[1])
    assert "e2[c2,attack_request]" in instructions(requests[1])


@pytest.mark.parametrize("source", ["c999", "c2\nPRIVATE"])
def test_unknown_or_unsafe_source_identifiers_never_enter_feedback_paths(monkeypatch, source):
    value = conflict()
    value["evidence"][0]["source_id"] = source
    requests = install_sequence(monkeypatch, [(value, "stop"), (wire(), "stop")])
    result = execute()
    assert result.succeeded
    assert "이전 응답의 연결:" not in instructions(requests[1])
    assert "c999" not in instructions(requests[1]) and "PRIVATE" not in instructions(requests[1])
    assert not any("conflict_paths" in issue for issue in result.telemetry["output_validation_retry"]["attempts"][0]["validation_issues"])


def test_forged_context_cannot_inject_plaintext_or_nonexistent_relationship():
    class ForgedFailure(ValueError):
        def errors(self, **_kwargs):
            return [{"type": "evidence_support_conflict", "loc": (), "msg": "SYNTHETIC_PRIVATE_ERROR",
                     "ctx": {"conflict_paths": diagnostic(conflict())["ctx"]["conflict_paths"],
                             "secret": "SYNTHETIC_PRIVATE_CONTEXT"}, "input": "SYNTHETIC_PRIVATE_INPUT"}]

    # Plausible-looking path but no corresponding edge/classification in the
    # current complete response: emit only the fixed field and error code.
    issues = executor._safe_validation_issues(ForgedFailure(), SecurityAnalysisOutputV234,
        selected_source_ids={"c1", "c2"}, selected_evidence=wire()["evidence"])
    assert issues == [{"field": "evidence", "type": "evidence_support_conflict"}]
    feedback = executor._structured_repair_instructions(issues, SecurityAnalysisOutputV234)
    assert "PRIVATE" not in feedback and "이전 응답의 연결:" not in feedback


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_generation_wire_schema_has_same_fields_and_no_diagnostic_contract_extensions(monkeypatch, provider):
    requests = install_sequence(monkeypatch, [(wire(), "stop")])
    result = execute(provider)
    assert result.succeeded
    body = json.loads(requests[0].content)
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema == compact_strict_schema(SecurityAnalysisOutputV234.model_json_schema())
    assert_strict_schema(schema)
    assert "conflict_paths" not in json.dumps(schema)
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 3072
    assert result.telemetry["output_validation_retry"]["version"] == "structured-output-repair-v12"


@pytest.mark.parametrize("content", ['{"evidence":[', '{"verdict":1,"verdict":2}'])
def test_completed_malformed_or_duplicate_json_is_rejected_not_repaired_in_code(monkeypatch, content):
    requests = install_sequence(monkeypatch, [(content, "stop")])
    result = execute()
    assert not result.succeeded and result.output is None and len(requests) == 4
    assert all(item["error_code"] == "output_validation_failed" for item in result.telemetry["output_validation_retry"]["attempts"])


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("invalid", [False, True])
def test_decision_review_keeps_single_physical_call_and_all_evidence(monkeypatch, provider, invalid):
    value = review_wire()
    if invalid:
        value["retained_evidence_ids"] = ["e2"]
    requests = install_sequence(monkeypatch, [(value, "stop")])
    context = review_context()
    user_input = json.dumps({"evidence_decision_review": {
        "source_catalog": context["source_catalog"], "original_output": context["review_original_output"],
        "eligible_evidence_ids": context["review_eligible_evidence_ids"]}})
    result = execute(provider, model=EvidenceDecisionReviewOutput, attempts=1, user_input=user_input)
    assert result.succeeded is not invalid and len(requests) == 1
    if not invalid:
        assert result.output.to_analysis().model_dump(mode="json")["evidence"] == context["review_original_output"]["evidence"]


@pytest.mark.parametrize("inject", [False, True])
def test_source_only_correction_still_cannot_edit_support_or_make_extra_retry(monkeypatch, inject):
    value = {"corrections": [{"index": 0, "source_id": "c2"}], "requires_reanalysis": False}
    if inject:
        value["corrections"][0]["supports"] = "normal_request"
    requests = install_sequence(monkeypatch, [(value, "stop")])
    result = execute(model=EvidenceSelectionCorrectionOutput, attempts=1)
    assert result.succeeded is not inject and len(requests) == 1


@pytest.mark.parametrize("defer, count", [(False, 4), (True, 1)])
def test_source_support_deferral_remains_explicit_and_independent_of_graph_diagnostics(monkeypatch, defer, count):
    value = wire()
    value["evidence"][0]["source_id"] = "c3"
    requests = install_sequence(monkeypatch, [(value, "stop")])
    result = execute(defer=defer)
    assert result.succeeded is defer and len(requests) == count


def test_codec_resets_diagnostics_after_new_complete_valid_or_malformed_response():
    codec = executor._TrackingOutputCodec(PydanticOutputCodec(SecurityAnalysisOutputV234),
        output_model=SecurityAnalysisOutputV234, structured_output_recovery=True, user_input=request_input())
    with pytest.raises(ValidationError):
        codec.decode(json.dumps(conflict()))
    assert codec.validation_issues[0]["conflict_paths"]
    with pytest.raises(ValueError):
        codec.decode('{"evidence":[')
    assert codec.validation_issues == [{"field": "$", "type": "json_invalid"}]
    assert codec.decode(json.dumps(wire())).model_dump(mode="json") == wire()
    assert codec.validation_issues == []
