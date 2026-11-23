"""Synthetic offline evidence groups, directional acceptance and wire recovery."""
import asyncio
from copy import deepcopy
import json

import httpx
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError
import pytest

from app.agent import executor_v227 as executor
from app.agent.contracts_v227 import SecurityAnalysisOutputV227
from app.agent.output_schema_v226 import compact_strict_schema
from test_agent_openai import assert_strict_schema, completion, install_http, openai_profile
from test_evidence_relations import wire as old_wire


def wire():
    value = old_wire()
    value.pop("input_truncated")
    value.pop("evidence_relations")
    for key in ("version", "explanation_ko"):
        value["signature_assessment"].pop(key)
    value["evidence"] = [{"source_id": "c2", "related_sources": [{
        "source_id": "c1", "contribution_ko": "값의 처리 방식을 지정하는 입력 위치입니다.", "supports": "context"}],
        "interpretation_ko": "실행 대상으로 지정한 문맥과 명령이 결합되어 서버 처리 변경을 시도하는 입력입니다.",
        "supports": "attack_request"}]
    value["capture_assessment"] = {"status": "no_clear_signs", "reason_ko": "수신본에서 뚜렷한 누락 징후는 관찰되지 않았습니다."}
    return value


def request_input():
    return json.dumps({"event": {"signature": "synthetic"}, "input_truncated": False,
        "evidence_candidates": {"version": "evidence-selection-v5", "items": [
            {"source_id": "c1", "field": "payload", "excerpt": "synthetic request"},
            {"source_id": "c2", "field": "payload", "excerpt": "synthetic body"},
            {"source_id": "c3", "field": "signature", "excerpt": "synthetic", "allowed_supports": ["context"]},
            {"source_id": "c4", "field": "waf_action", "excerpt": "D"},
            {"source_id": "c5", "field": "extra_fields.signature_explanation", "excerpt": "synthetic reference"},
            {"source_id": "c6", "field": "extra_fields.request", "excerpt": "synthetic extra request"},
        ]}})


def codec():
    return executor._TrackingOutputCodec(PydanticOutputCodec(SecurityAnalysisOutputV227),
        output_model=SecurityAnalysisOutputV227, structured_output_recovery=True, user_input=request_input())


def execute(provider="vllm"):
    profile = openai_profile(provider=provider, base_url=("http://10.0.0.10:8000/v1"
        if provider == "vllm" else "https://api.openai.com/v1"))
    return asyncio.run(executor.execute_structured_agent(profile=profile, api_key="synthetic-key",
        instructions="Synthetic independent analysis.", user_input=request_input(), session_id="synthetic:primary",
        agent_name="waf-primary", egress_check=lambda: None, output_model=SecurityAnalysisOutputV227,
        structured_output_recovery=True))


def test_internal_group_expands_once_with_exact_contributions_and_server_constants():
    value = wire()
    before = deepcopy(value)
    value["decision_issue"] = {"point_ko": "입력 문맥에 따른 의미를 검토했습니다.", "source_ids": ["c1"], "missing_condition_ko": None}
    output = codec().decode(json.dumps(value, ensure_ascii=False))
    expanded = output.to_selection(input_truncated=True).model_dump(mode="json")
    assert expanded["evidence_relations"] == [{"representative_index": 0, "related_indexes": [1]}]
    assert expanded["evidence"][0] == {key: before["evidence"][0][key] for key in ("source_id", "interpretation_ko", "supports")}
    assert expanded["evidence"][1] == {"source_id": "c1", "interpretation_ko": before["evidence"][0]["related_sources"][0]["contribution_ko"], "supports": "context"}
    assert expanded["decision_issue"]["evidence_indexes"] == [1]
    assert expanded["signature_assessment"]["version"] == "signature-assessment-v2"
    assert expanded["signature_assessment"]["explanation_ko"] is None
    assert expanded["input_truncated"] is True
    assert output.model_dump(mode="json") == value


@pytest.mark.parametrize("mutate,code", [
    (lambda v: v["evidence"][0].update(source_id="c1"), "evidence_source_repeated"),
    (lambda v: v["evidence"][0]["related_sources"][0].update(supports="normal_request"), "evidence_support_conflict"),
    (lambda v: v["evidence"][0].update(source_id="c999"), "evidence_source_unavailable"),
    (lambda v: v["evidence"][0].update(source_id="c3"), "evidence_source_support_forbidden"),
    (lambda v: v["evidence"][0].update(source_id="c5"), "evidence_source_support_forbidden"),
    (lambda v: v["evidence"][0].update(source_id="c4"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(supports="normal_request"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(supports="context"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(interpretation_ko="   "), "evidence_text_required"),
    (lambda v: v["evidence"].extend(deepcopy(v["evidence"]) * 2), "evidence_expansion_limit"),
    (lambda v: v.update(decision_issue={"point_ko": "구분점 설명", "source_ids": ["c3"], "missing_condition_ko": None}), "decision_source_unselected"),
    (lambda v: v.update(decision_issue={"point_ko": "구분점 설명", "source_ids": ["c1", "c1"], "missing_condition_ko": None}), "decision_source_repeated"),
])
def test_rejects_only_checkable_source_and_direction_inconsistencies(mutate, code):
    value = wire()
    mutate(value)
    decoder = codec()
    with pytest.raises(ValidationError):
        decoder.decode(json.dumps(value))
    assert code in {item["type"] for item in decoder.validation_issues}


@pytest.mark.parametrize("extra", [{"input_truncated": False}, {"evidence_relations": []}, {"unknown": "private-value"}])
def test_no_generated_server_fields_or_undeclared_extensions(extra):
    with pytest.raises(ValidationError):
        codec().decode(json.dumps({**wire(), **extra}))


def test_inconclusive_keeps_attack_support_and_missing_condition():
    value = wire()
    value.update(verdict="inconclusive", decision_issue={"point_ko": "실행 대상의 용도를 구분해야 합니다.",
        "source_ids": ["c2", "c1"], "missing_condition_ko": "사용자 입력을 실행 대상으로 처리하는지 확인해야 합니다."})
    value["threat_analysis"]["severity"] = "UNKNOWN"
    output = codec().decode(json.dumps(value))
    assert output.evidence[0].supports == "attack_request"
    assert output.to_selection(input_truncated=False).decision_issue.evidence_indexes == [0, 1]


def test_real_extension_content_is_not_confused_with_reference_metadata():
    value = wire()
    value["evidence"][0]["source_id"] = "c6"
    assert codec().decode(json.dumps(value)).verdict == "true_positive"


def test_checks_do_not_pretend_to_prove_semantic_correctness():
    value = wire()
    value["evidence"][0].update(interpretation_ko="임의 기능이 존재한다는 사실만 기록했습니다.", supports="normal_request")
    value["verdict"], value["threat_analysis"]["severity"] = "false_positive", "NONE"
    assert codec().decode(json.dumps(value)).verdict == "false_positive"


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_both_providers_use_closed_compact_schema_with_source_first_order(monkeypatch, provider):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(wire()))))
    result = execute(provider)
    assert result.succeeded and len(requests) == 1
    body = json.loads(requests[0].content)
    actual = body["response_format"]["json_schema"]["schema"]
    assert actual == compact_strict_schema(SecurityAnalysisOutputV227.model_json_schema())
    assert_strict_schema(actual)
    assert list(actual["properties"])[0] == "evidence"
    assert list(actual["$defs"]["EvidenceInterpretation"]["properties"]) == ["source_id", "related_sources", "interpretation_ko", "supports"]
    assert not {"input_truncated", "evidence_relations"} & actual["properties"].keys()
    assert not {"version", "explanation_ko"} & actual["$defs"]["SignatureComparison"]["properties"].keys()
    assert result.telemetry["output_validation_retry"]["attempt_count"] == 1
    assert result.telemetry["output_validation_retry"]["max_attempts"] == 4
    if provider == "vllm":
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
    else:
        assert "chat_template_kwargs" not in body


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_direction_error_uses_existing_retry_budget_and_own_original_only(monkeypatch, provider):
    invalid = wire()
    invalid["evidence"][0]["supports"] = "normal_request"
    requests, _ = install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(invalid if count == 1 else wire()))))
    result = execute(provider)
    assert result.succeeded and len(requests) == 2
    bodies = [json.loads(request.content) for request in requests]
    assert [item for item in bodies[0]["messages"] if item["role"] == "user"] == [item for item in bodies[1]["messages"] if item["role"] == "user"]
    feedback = str(bodies[1]["messages"])
    assert "decisive_verdict_requires_directional_content" in feedback
    assert "분류만 바꾸거나" in feedback
    assert "tuning_recommendation" not in feedback
    assert result.telemetry["output_validation_retry"]["attempt_count"] == 2


def test_persistent_inconsistency_stops_at_four_without_silent_direction_rewrite(monkeypatch):
    invalid = wire()
    invalid["evidence"][0]["source_id"] = "c3"
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(invalid))))
    result = execute()
    assert not result.succeeded and result.output is None
    assert len(requests) == 4
    assert result.telemetry["output_validation_retry"]["stop_reason"] == "attempt_limit"
    assert "synthetic body" not in str(result.telemetry)


@pytest.mark.parametrize("content,code", [('{} trailing', 'json_invalid'), ('{"verdict":1,"verdict":2}', 'json_duplicate_key'), ('{"x": NaN}', 'json_non_finite')])
def test_ambiguous_or_invalid_json_never_patched(content, code):
    decoder = codec()
    with pytest.raises(ValueError):
        decoder.decode(content)
    assert decoder.validation_issues == [{"field": "$", "type": code}]


def test_complete_fence_is_safe_but_source_and_interpretation_are_unchanged():
    decoder = codec()
    actual = decoder.decode("```json\n" + json.dumps(wire()) + "\n```")
    assert actual.model_dump(mode="json") == wire()
    assert decoder.json_normalizations


@pytest.mark.parametrize("repair", ["source_only", "needs_reanalysis"])
def test_exact_source_repair_preserves_group_or_excludes_unverified_interpretation(monkeypatch, repair):
    from app import worker_v227 as worker
    from app.agent.contracts import EvidenceSelectionCorrectionOutput
    from app.agent.evidence_relations_v227 import build_evidence_relations
    from app.agent.executor import AgentCallResult
    from app.agent.input_builder_v227 import build_agent_input
    from app.agent.security_analysis_v227 import canonical_evidence
    from app.services.http_parser import parse_http_payload

    raw = "POST /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n" + "a=" + "x" * 1000
    event = {"payload": raw}
    parsed = parse_http_payload(raw)
    built = build_agent_input(event, raw, parsed, 32768, 3072, evidence_selection=True,
                              fixed_rules_version="waf-system-v2.27")
    document = json.loads(built.text)
    sources = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
    # Tamper the synthetic submitted candidate to exercise the later exact
    # grounding check; production's input builder never performs this rewrite.
    sources[1]["excerpt"] = "SYNTHETIC_NOT_IN_ORIGINAL"
    value = wire()
    value["evidence"][0]["source_id"] = sources[0]["source_id"]
    value["evidence"][0]["related_sources"][0]["source_id"] = sources[1]["source_id"]
    calls = []
    async def execute(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            output = SecurityAnalysisOutputV227.model_validate(value)
        else:
            assert kwargs["output_validation_max_attempts"] == 1
            assert kwargs["output_model"] is EvidenceSelectionCorrectionOutput
            output = EvidenceSelectionCorrectionOutput.model_validate({"corrections": (
                [{"index": 1, "source_id": sources[2]["source_id"]}] if repair == "source_only" else []),
                "requires_reanalysis": repair == "needs_reanalysis"})
        return AgentCallResult(output, "synthetic-repair", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    call = worker._execute_grounded_agent(analysis=None, inference_event=event, raw_payload=raw, parsed=parsed,
        input_truncated=False, submitted_payload_spans=built.retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        user_input=json.dumps(document), profile=None, api_key=None, instructions="synthetic policy",
        agent_name="waf-primary", session_id="synthetic", egress_check=lambda: None)
    assert call.succeeded and len(calls) == 2
    evidence, _, _ = canonical_evidence(call, None)
    links = build_evidence_relations(call, evidence=evidence)
    if repair == "source_only":
        assert call.output.verdict == "true_positive"
        assert len(links["groups"]) == 1 and len(evidence) == 2
        assert call.telemetry["evidence_grounding_retry"]["recovered"] is True
        assert call.grounding_history[0]["output"] == value
        assert "evidence_relations" not in call.grounding_history[0]["output"]
        assert call.grounding_history[0]["expanded_output"]["evidence_relations"] == [{"representative_index": 0, "related_indexes": [1]}]
    else:
        assert call.output.verdict == "inconclusive"
        assert links["groups"] == []
        assert value["evidence"][0]["interpretation_ko"] not in [item["interpretation_ko"] for item in evidence]
