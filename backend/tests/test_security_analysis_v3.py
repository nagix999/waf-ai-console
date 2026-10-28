"""Synthetic-only v3 source, policy, strict-schema and worker regressions."""
import copy
import hashlib
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app import worker
from app.agent import legacy_prompts_v214, legacy_prompts_v215, prompts
from app.agent.contracts import SecurityAnalysisOutput, SecurityAnalysisOutputV216
from app.agent.executor import AgentCallResult
from app.agent.policy import VerifierPolicyContext, verifier_reasons, finalize_with_verifier
from app.agent.security_analysis import canonical_evidence, role_result, observational_summary
from app.models import Analysis, VLLMProfile
from app.services.http_parser import parse_http_payload
from app.services.provider_options import strict_json_schema
from test_evidence_selection import RAW, build, patch


def output(*supports, verdict="true_positive", conflict=False, source="c1", issue=None):
    return SecurityAnalysisOutput.model_validate({
        "verdict": verdict, "confidence_score": .93,
        "summary_ko": "요청에서 URL 인코딩된 스크립트 구문이 확인됩니다.",
        "threat_analysis": {"severity": {"true_positive": "HIGH", "false_positive": "NONE", "inconclusive": "UNKNOWN"}[verdict],
            "category": "synthetic", "target": "payload", "technique_ko": "입력된 구문과 사용 문맥을 비교했습니다.",
            "obfuscations": [], "potential_impact_ko": "실행 여부는 대상의 처리 방식에 따라 달라집니다."},
        "signature_assessment": {"version": "signature-assessment-v2", "relation": "exact", "explanation_ko": None,
            "matched_points": ["요청의 구문 유형이 탐지 설명과 일치합니다."], "mismatched_points": [], "uncertainty_ko": None},
        "evidence": [{"source_id": source, "interpretation_ko": f"요청 문맥의 {support} 해석 {index}", "supports": support}
                     for index, support in enumerate(supports or ["attack_request"])],
        "decision_issue": issue, "interpretation_conflict": conflict, "input_truncated": False,
    })


def grounded(monkeypatch, *outputs):
    calls = []
    async def fake(**kwargs):
        calls.append(kwargs)
        value = outputs[len(calls) - 1]
        return AgentCallResult(value, "synthetic-v3", None, "completed" if value else "error", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    built = build()
    call = worker._execute_grounded_agent(analysis=Analysis(extra_fields={}), raw_payload=RAW,
        parsed=parse_http_payload(RAW), input_truncated=False, submitted_payload_spans=built.retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        profile=None, api_key=None, instructions="synthetic instructions", user_input=built.text,
        session_id="synthetic", agent_name="waf-primary", egress_check=lambda: None)
    return call, calls


@pytest.mark.parametrize("field", ["tuning_recommendation", "analyst_checks", "recommended_checks", "conflicting_evidence", "invented"])
def test_new_wire_contract_forbids_retired_and_unknown_fields(field):
    value = output().model_dump(mode="json")
    value[field] = []
    with pytest.raises(ValidationError) as error:
        SecurityAnalysisOutput.model_validate(value)
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_new_supports_are_required_and_old_direction_values_are_rejected():
    value = output().model_dump(mode="json")
    for support in ("true_positive", "false_positive", "invented"):
        value["evidence"][0]["supports"] = support
        with pytest.raises(ValidationError):
            SecurityAnalysisOutput.model_validate(value)
    del value["evidence"][0]["supports"]
    with pytest.raises(ValidationError):
        SecurityAnalysisOutput.model_validate(value)
    schema = strict_json_schema(SecurityAnalysisOutput.model_json_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(output().model_dump())
    assert list(schema["properties"])[-2:] == ["verdict", "confidence_score"]
    assert list(schema["properties"]).index("evidence") < list(schema["properties"]).index("verdict")


@pytest.mark.parametrize("summary", ["정탐입니다. 테스트입니다.", "오탐입니다.", "현재 분석에서는 정탐·오탐 판정을 보류했습니다."])
def test_summary_verdict_declarations_are_correctable_contract_errors(summary):
    value = output().model_dump(mode="json")
    value["summary_ko"] = summary
    with pytest.raises(ValidationError) as error:
        SecurityAnalysisOutput.model_validate(value)
    assert error.value.errors()[0]["type"] == "summary_must_describe_request"


def test_decisive_result_cannot_declare_missing_prerequisite():
    value = output().model_dump(mode="json")
    value["decision_issue"] = {"point_ko": "허용된 기능인지 구분해야 합니다.", "evidence_indexes": [0],
                                "missing_condition_ko": "권한 범위가 확인되지 않았습니다."}
    with pytest.raises(ValidationError) as error:
        SecurityAnalysisOutput.model_validate(value)
    assert error.value.errors()[0]["type"] == "decisive_verdict_disallows_missing_condition"


def test_both_roles_keep_ten_classified_evidence_without_policy_subset_expansion(monkeypatch):
    primary, _ = grounded(monkeypatch, output(*(["attack_request"] * 5)))
    verifier, _ = grounded(monkeypatch, output(*(["normal_request"] * 5), verdict="false_positive"))
    evidence, issues, omitted = canonical_evidence(primary, verifier)
    assert len(evidence) == 10 and not issues and omitted == 0
    assert [item["supports"] for item in evidence] == ["attack_request"] * 5 + ["normal_request"] * 5
    assert all(item["source_spans"] and item["origins"] for item in evidence)
    final = finalize_with_verifier(primary.output, verifier.output, ["synthetic"])
    assert len(final.output.evidence) == 5 and final.output.verdict.value == "inconclusive"
    assert role_result(primary, "primary", evidence, issues)["evidence_ids"] == [f"e{i}" for i in range(1, 6)]
    assert "판정" not in observational_summary(primary, verifier, final)


def test_citation_only_retry_preserves_direction_and_issue(monkeypatch):
    first = output("normal_request", verdict="inconclusive", source="c999", issue={
        "point_ko": "해당 값이 실행되는지 데이터로 저장되는지 구분해야 합니다.",
        "evidence_indexes": [0], "missing_condition_ko": "대상의 입력 처리 규격이 확인되지 않았습니다."})
    before = copy.deepcopy(first.model_dump(mode="json"))
    call, calls = grounded(monkeypatch, first, patch(0, "c1"))
    evidence, issues, omitted = canonical_evidence(call, None)
    assert len(calls) == 2 and calls[0]["output_model"] is SecurityAnalysisOutput
    assert evidence[0]["supports"] == "normal_request" and issues[0]["evidence_ids"] == ["e1"]
    assert omitted == 0 and first.model_dump(mode="json") == before


def test_conflict_trigger_is_explicit_not_derived_from_both_directions(monkeypatch):
    call, _ = grounded(monkeypatch, output("attack_request", "normal_request"))
    context = VerifierPolicyContext("D", "success", False, interpretation_conflict=False)
    assert verifier_reasons(call.output, context) == []
    assert verifier_reasons(call.output, replace(context, interpretation_conflict=True)) == ["interpretation_conflict"]
    assert not call.output.tuning_recommendation.recommended


@pytest.mark.parametrize("verdict,action,status,truncated,confidence,relation,grounding,expected", [
    ("inconclusive", "D", "success", False, .9, "exact", False, "primary_inconclusive"),
    ("true_positive", "D", "success", False, .4, "exact", False, "low_confidence"),
    ("true_positive", "A", "success", False, .9, "exact", False, "true_positive_but_allowed"),
    ("false_positive", "D", "success", False, .9, "exact", False, "false_positive_but_denied"),
    ("true_positive", "D", "partial", False, .9, "exact", False, "parser_incomplete"),
    ("true_positive", "D", "success", True, .9, "exact", False, "input_truncated"),
    ("true_positive", "D", "success", False, .9, "partial", False, "signature_not_exact"),
    ("true_positive", "D", "success", False, .9, "exact", True, "evidence_grounding_failed"),
])
def test_nonretired_verifier_conditions_are_unchanged(verdict, action, status, truncated, confidence, relation, grounding, expected):
    value = output(verdict=verdict).legacy_bridge()
    value.confidence_score = confidence
    from app.agent.contracts import SignatureRelation
    value.signature_assessment.relation = SignatureRelation(relation)
    context = VerifierPolicyContext(action, status, truncated, evidence_grounding_failed=grounding,
                                    interpretation_conflict=False)
    assert expected in verifier_reasons(value, context)


def test_pinned_v214_module_and_candidate_versions_are_separate():
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.16"
    assert legacy_prompts_v215.FIXED_RULES_VERSION == "waf-system-v2.15"
    assert legacy_prompts_v214.FIXED_RULES_VERSION == "waf-system-v2.14"
    from pathlib import Path
    assert hashlib.sha256(Path(legacy_prompts_v214.__file__).read_bytes()).hexdigest() == "88cca42412a29e8b366eaa3cb5d291ff6faccf31e491763fa3a228b9c42adb68"
    assert hashlib.sha256(Path(legacy_prompts_v215.__file__).read_bytes()).hexdigest() == "6308f189fbf5a9ed94e35738eb3fc8aaf697f374979deb6fdc454be03a72dd32"
    assert len(prompts.FIXED_INSTRUCTIONS) < len(legacy_prompts_v214.FIXED_INSTRUCTIONS)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("repair", [False, True])
def test_real_executor_uses_closed_v3_schema_and_corrects_invalid_output(monkeypatch, provider, repair):
    import asyncio
    import httpx
    from app.agent.executor import execute_structured_agent
    from test_agent_openai import completion, install_http, openai_profile, assert_strict_schema
    from test_agent_executor import profile
    reply = output()
    bad = reply.model_dump(mode="json")
    bad["evidence"][0]["supports"] = "true_positive"
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion(json.dumps(bad) if repair and count == 1 else reply.model_dump_json())))
    result = asyncio.run(execute_structured_agent(profile=openai_profile() if provider == "openai" else profile(),
        api_key="synthetic-key", instructions=prompts.PRIMARY_INSTRUCTIONS, user_input=build().text,
        session_id="synthetic", agent_name="waf-primary", output_model=SecurityAnalysisOutput, egress_check=lambda: None))
    assert result.succeeded and result.output == reply
    assert len(requests) == (2 if repair else 1)
    schema = json.loads(requests[0].content)["response_format"]["json_schema"]["schema"]
    assert "tuning_recommendation" not in schema["properties"]
    assert "decision_issue" in schema["required"]
    if provider == "openai":
        assert_strict_schema(schema)
    if repair:
        assert result.telemetry["output_validation_retry"]["recovered"]


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("verifier_verdict", [None, "true_positive", "false_positive", "failed"])
def test_worker_persists_only_canonical_v3_and_observational_summary(
    client, event_payload, service_headers, monkeypatch, verifier_verdict):
    event_payload["payload"] = RAW
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    calls = []
    async def fake(**kwargs):
        calls.append(kwargs)
        is_verifier = kwargs["agent_name"] == "waf-verifier"
        value = None if is_verifier and verifier_verdict == "failed" else output(
            "normal_request" if is_verifier else "attack_request",
            verdict=verifier_verdict if is_verifier else "true_positive", conflict=not is_verifier and verifier_verdict is not None)
        assert kwargs["output_model"] is SecurityAnalysisOutputV216
        if value is not None:
            value = kwargs["output_model"].model_validate(value.model_dump(mode="json"))
        return AgentCallResult(value, "synthetic-v3", None, "completed" if value else "error", None,
                               "synthetic_failure" if value is None else None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-v3", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production"))
        db.commit()
        row = db.get(Analysis, created["id"])
        worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        result = row.result_json
        assert result["schema_version"] == "waf-analysis-v3"
        assert set(result["analyst_guidance"]) == {"summary_ko", "limitations"}
        assert not {"analyst_assessment", "tuning_recommendation", "analyst_checks", "recommended_checks", "conflicting_evidence"} & result.keys()
        assert result["evidence"][0]["supports"] == "attack_request"
        assert result["policy"]["decision_evidence_ids"] and result["policy"]["retired_verifier_triggers"] == ["tuning_recommended"]
        assert "evidence" not in result["primary"] and result["primary"]["evidence_ids"]
        assert row.summary_ko == result["summary_ko"] and "판정" not in row.summary_ko
        assert row.verdict == ("inconclusive" if verifier_verdict in {"false_positive", "failed"} else "true_positive")
    assert len(calls) == (1 if verifier_verdict is None else 2)
    if len(calls) == 2:
        assert calls[0]["user_input"] == calls[1]["user_input"]
        assert "primary" not in json.loads(calls[1]["user_input"])


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("mode", ["group", "skip", "invalid"])
def test_canonical_editor_is_evidence_only_and_skips_unnecessary_calls(client, event_payload, monkeypatch, mode):
    from app.agent.evidence_editor import EvidenceEditorOutput
    from app.services.analysis_retries import load_execution_snapshot
    from test_model_profiles import login_admin
    from test_model_test_role import create_verified
    from test_evidence_editor import configure
    login_admin(client)
    primary = create_verified(client, "synthetic-v3-editor")
    assert configure(client, primary, primary).status_code == 200
    created = client.post("/api/v1/analyses", json={**event_payload, "payload": RAW}).json()
    calls = []
    async def fake(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        if kwargs["output_model"] is EvidenceEditorOutput:
            document = json.loads(kwargs["user_input"])
            assert set(document) == {"evidence", "allowed_groups"}
            assert "checks" not in document and "check_groups" not in kwargs["instructions"]
            ids = [item["evidence_id"] for item in document["evidence"]]
            value = EvidenceEditorOutput(groups=[{"member_ids": ids[:1] if mode == "invalid" else ids,
                                                   "representative_id": ids[0]}])
        else:
            value = output()
            value.confidence_score = .6
            if mode != "skip" and kwargs["agent_name"] == "waf-verifier":
                value.evidence[0].interpretation_ko = "같은 원문 위치와 공격 의미에 대한 다른 설명입니다."
            assert kwargs["output_model"] is SecurityAnalysisOutputV216
            value = kwargs["output_model"].model_validate(value.model_dump(mode="json"))
        return AgentCallResult(value, "synthetic-v3-editor", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, created["id"])
        worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        assert row.status == "completed" and row.verdict == "true_positive"
        assert load_execution_snapshot(row, client.app.state.crypto).evidence_editor.version == "evidence-editor-v2"
        assert "follow_up_presentation" not in row.result_json
        assert "checks" not in row.result_json["analyst_guidance"]
        assert row.result_json["evidence_presentation"]["status"] == {
            "group": "completed", "skip": "skipped", "invalid": "fallback"}[mode]
        assert len(row.result_json["evidence"]) == (1 if mode == "skip" else 2)
    assert len(calls) == (2 if mode == "skip" else 3)
