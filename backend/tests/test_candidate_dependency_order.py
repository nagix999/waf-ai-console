"""Synthetic, offline regressions for the separately pinned v2.16 wire contract."""
import asyncio
import copy
import hashlib
import json

import httpx
import pytest
from pydantic import ValidationError

from app import worker
from app.agent import prompts
from app.agent.contracts import SecurityAnalysisOutput, SecurityAnalysisOutputV216, StrictContract
from app.agent.executor import AgentCallResult, _retry_instructions, _safe_validation_issues, execute_structured_agent
from app.models import Analysis
from app.services.http_parser import parse_http_payload
from app.services.provider_options import strict_json_schema
from test_agent_executor import profile
from test_agent_openai import assert_strict_schema, completion, install_http, openai_profile
from test_evidence_selection import RAW, build, patch
from test_security_analysis_v3 import output


ORDER = ["signature_assessment", "evidence", "interpretation_conflict", "input_truncated", "summary_ko",
         "verdict", "confidence_score", "threat_analysis", "decision_issue"]
MISSING = "decisive_verdict_disallows_missing_condition"
SEVERITY = "inconclusive_requires_unknown_severity"


def attempt(code=MISSING, field="decision_issue.missing_condition_ko"):
    return {"retry_kind": "schema_correction", "validation_issues": [{"field": field, "type": code}],
            "error_code": "output_validation_failed", "provider_finish_reason": "stop"}


def test_v215_schema_and_feedback_remain_byte_frozen():
    schema = strict_json_schema(SecurityAnalysisOutput.model_json_schema())
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "df7de8c4515f345f5a07c38acca768eb5ac92dee1e51b90ab4358ecda8cbebc7"
    compact = {"retry_kind": "compact_output", "validation_issues": [],
               "error_code": "openai_output_incomplete", "provider_finish_reason": "length"}
    for previous, expected in [(attempt(), "ffb2fad919feb620c6029fd5d7ac5b4d793494cc3a9a4fb07ba249f7d7316919"),
                               (compact, "c7706590384c4efe62f34352ab0b48316c31f2a6750c92b0e0047b77e9e1f2f1")]:
        assert hashlib.sha256(_retry_instructions([previous], 3072, SecurityAnalysisOutput).encode()).hexdigest() == expected


def test_candidate_changes_only_wire_order_not_fields_or_constraints():
    assert issubclass(SecurityAnalysisOutputV216, StrictContract)
    assert not issubclass(SecurityAnalysisOutputV216, SecurityAnalysisOutput)
    old = strict_json_schema(SecurityAnalysisOutput.model_json_schema())
    new = strict_json_schema(SecurityAnalysisOutputV216.model_json_schema())
    assert list(new["properties"]) == ORDER == new["required"]
    assert new["properties"] == old["properties"]
    assert set(new["required"]) == set(old["required"])
    assert new["$defs"] == old["$defs"]
    assert new["additionalProperties"] is False
    assert_strict_schema(new)
    for verdict in ("true_positive", "false_positive", "inconclusive"):
        value = output(verdict=verdict).model_dump(mode="json")
        assert SecurityAnalysisOutputV216.model_validate(value).legacy_bridge() == SecurityAnalysisOutput.model_validate(value).legacy_bridge()


@pytest.mark.parametrize("failure", ["missing_condition", "severity", "evidence", "summary", "signature", "extra", "support"])
def test_candidate_and_frozen_contract_reject_the_same_invalid_semantics(failure):
    value = output(verdict="inconclusive" if failure == "severity" else "true_positive").model_dump(mode="json")
    if failure == "missing_condition":
        value["decision_issue"] = {"point_ko": "입력 처리 방식을 구분해야 합니다.", "evidence_indexes": [0],
                                   "missing_condition_ko": "대상 처리 규격이 확인되지 않았습니다."}
    elif failure == "severity":
        value["threat_analysis"]["severity"] = "HIGH"
    elif failure == "evidence":
        value["evidence"] = []
    elif failure == "summary":
        value["summary_ko"] = "정탐입니다."
    elif failure == "signature":
        value["signature_assessment"]["mismatched_points"] = ["원문과 탐지 설명의 대상이 다릅니다."]
    elif failure == "extra":
        value["invented"] = "synthetic-only"
    else:
        value["evidence"][0]["supports"] = "true_positive"
    issues = []
    for contract in (SecurityAnalysisOutput, SecurityAnalysisOutputV216):
        with pytest.raises(ValidationError) as error:
            contract.model_validate(copy.deepcopy(value))
        issues.append(_safe_validation_issues(error.value, contract))
    assert issues[0] == issues[1]


@pytest.mark.parametrize("code,field", [(MISSING, "decision_issue.missing_condition_ko"), (SEVERITY, "threat_analysis.severity")])
def test_short_dependency_feedback_is_candidate_only_and_does_not_game_the_verdict(code, field):
    previous = attempt(code, field)
    old = _retry_instructions([previous], 3072, SecurityAnalysisOutput)
    new = _retry_instructions([previous], 3072, SecurityAnalysisOutputV216)
    assert len(new) < len(old)
    assert "판정 의존 필드" in new and "판정·신뢰도를 바꾸거나" in new
    assert "실제 미확인 조건을 무조건 삭제하지 않는다" in new
    assert "판정 의존 필드" not in old
    if code == MISSING:
        assert "missing_condition_ko는 null" in new
    else:
        assert "severity는 UNKNOWN" in new


def test_other_or_mixed_failures_keep_their_safe_failure_locations():
    other = attempt("signature_exact_disallows_mismatch", "signature_assessment")
    feedback = _retry_instructions([other], 3072, SecurityAnalysisOutputV216)
    assert "signature_exact_disallows_mismatch" in feedback and "판정 의존 필드" not in feedback
    mixed = attempt()
    mixed["validation_issues"].extend(other["validation_issues"])
    feedback = _retry_instructions([mixed], 3072, SecurityAnalysisOutputV216)
    assert "signature_exact_disallows_mismatch" in feedback and "missing_condition_ko는 null" in feedback


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("failure", ["missing_condition", "severity"])
def test_real_provider_wire_order_and_retry_use_candidate_contract_without_relaxation(monkeypatch, provider, failure):
    reply = output(verdict="inconclusive" if failure == "severity" else "true_positive").model_dump(mode="json")
    bad = copy.deepcopy(reply)
    if failure == "severity":
        bad["threat_analysis"]["severity"] = "HIGH"
    else:
        bad["decision_issue"] = {"point_ko": "대상 처리 방식을 확인해야 합니다.", "evidence_indexes": [0],
                                  "missing_condition_ko": "synthetic-invalid-output-not-in-retry"}
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(
        json.dumps(bad if count == 1 else reply, ensure_ascii=False))))
    checks = []
    result = asyncio.run(execute_structured_agent(
        profile=openai_profile() if provider == "openai" else profile(), api_key="synthetic-key",
        instructions=prompts.PRIMARY_INSTRUCTIONS, user_input=build().text, session_id="synthetic-candidate",
        agent_name="waf-primary", output_model=SecurityAnalysisOutputV216, egress_check=lambda: checks.append(True)))
    assert result.succeeded and type(result.output) is SecurityAnalysisOutputV216 and len(requests) == 2
    assert result.output.model_dump(mode="json") == reply
    assert len(checks) >= len(requests)
    bodies = [json.loads(request.content) for request in requests]
    assert all(list(body["response_format"]["json_schema"]["schema"]["properties"]) == ORDER for body in bodies)
    system = bodies[1]["messages"][0]["content"]
    assert "판정 의존 필드" in system and "synthetic-invalid-output-not-in-retry" not in system
    attempts = result.telemetry["output_validation_retry"]["attempts"]
    assert attempts[0]["retry_kind"] == "schema_correction"
    assert attempts[0]["validation_issues"] == [{"field": "threat_analysis.severity" if failure == "severity" else "decision_issue.missing_condition_ko",
                                               "type": SEVERITY if failure == "severity" else MISSING}]
    assert RAW not in str(result.telemetry)


@pytest.mark.parametrize("revision,contract", [(None, SecurityAnalysisOutput), ("waf-system-v2.15", SecurityAnalysisOutput),
                                                ("waf-system-v2.16", SecurityAnalysisOutputV216)])
@pytest.mark.parametrize("repair", [False, True])
def test_grounded_selection_uses_exact_pinned_revision_for_primary_and_verifier(monkeypatch, revision, contract, repair):
    calls = []
    async def fake(**kwargs):
        calls.append(kwargs)
        value = patch() if len(calls) % (2 if repair else 1) == 0 and repair else contract.model_validate(
            output(source="c999" if repair else "c1").model_dump(mode="json"))
        return AgentCallResult(value, "synthetic", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    built = build()
    for role in ("waf-primary", "waf-verifier"):
        result = worker._execute_grounded_agent(
            analysis=Analysis(extra_fields={}), raw_payload=RAW, parsed=parse_http_payload(RAW), input_truncated=False,
            submitted_payload_spans=built.retained_payload_spans, assessment_enabled=True, semantic_enabled=True,
            security_analysis_enabled=True, **({"fixed_rules_version": revision} if revision is not None else {}),
            profile=None, api_key=None, instructions="synthetic independent instructions", user_input=built.text,
            session_id="synthetic", agent_name=role, egress_check=lambda: None)
        assert result.succeeded
    initial_calls = calls[::2] if repair else calls
    assert all(call["output_model"] is contract for call in initial_calls)
    assert calls[0]["user_input"] == calls[2 if repair else 1]["user_input"]
    if repair:
        assert all(call["output_validation_max_attempts"] == 1 for call in calls[1::2])


def test_unknown_security_revision_fails_before_any_model_call(monkeypatch):
    async def unexpected(**kwargs):
        pytest.fail("An unsupported pinned contract must not reach the executor")
    monkeypatch.setattr(worker, "execute_structured_agent", unexpected)
    with pytest.raises(worker.WorkerExecutionError, match="unsupported_security_output_contract"):
        worker._execute_grounded_agent(analysis=Analysis(extra_fields={}), raw_payload=RAW, parsed=parse_http_payload(RAW),
            input_truncated=False, security_analysis_enabled=True, fixed_rules_version="waf-system-v2.17",
            user_input=build().text)
