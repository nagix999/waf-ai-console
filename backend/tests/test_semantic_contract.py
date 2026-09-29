"""Versioned contracts and recovery; synthetic inputs and offline HTTP only."""
import asyncio
import copy
import json

import httpx
import pytest
from pydantic import ValidationError
from moduagent.output import PydanticOutputCodec

from app.agent.contracts import (
    AnalystCheck, SemanticAssessmentOutput, SignatureAssessment, SignatureAssessmentV2,
    WAFAnalysisOutput, EvidenceAssessmentOutput,
)
from app.agent.executor import _TrackingOutputCodec, execute_structured_agent
from app.agent.analyst_guidance import build_analyst_guidance, merge_analyst_checks
from app.agent import result_editor
from app.services.provider_options import strict_json_schema
from test_agent_openai import completion, install_http, openai_profile, assert_strict_schema
from test_prompt_snapshots import fake_output
from test_result_editor import checks as legacy_checks, CHECK_GROUPS


def signature(relation="exact"):
    return {"version": "signature-assessment-v2", "relation": relation, "explanation_ko": None,
        "matched_points": ["SQL 조건 조작 구문이 탐지 설명과 관련됩니다."] if relation in {"exact", "partial"} else [],
        "mismatched_points": ["탐지 설명은 SQL이지만 요청에는 HTML 실행 구문이 있습니다."] if relation in {"partial", "mismatch"} else [],
        "uncertainty_ko": "탐지 설명이 없어 요청과 비교하지 못했습니다." if relation == "unknown" else None}


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_signature_wire_schema_has_no_single_character_pattern(provider):
    codec = _TrackingOutputCodec(PydanticOutputCodec(SemanticAssessmentOutput), strict=provider == "openai", output_model=SemanticAssessmentOutput)
    schema = codec.schema()
    fields = schema["$defs"]["SignatureAssessmentV2"]["properties"]
    for name in ("matched_points", "mismatched_points"):
        item = fields[name]["items"]
        assert "pattern" not in item and item["maxLength"] == 400 and fields[name]["maxItems"] == 5
    limit = next(value for value in fields["uncertainty_ko"]["anyOf"] if value["type"] == "string")
    assert "pattern" not in limit and limit["maxLength"] == 500
    value = signature("partial")
    value["matched_points"] = ['요청의 "q" 값과 탐지 대상이 같습니다.\n공백과 줄바꿈도 보존합니다.']
    value["uncertainty_ko"] = "탐지 규칙 원문이 없어 매치 조건은 확인하지 못했습니다."
    assert SignatureAssessmentV2.model_validate(value).model_dump(mode="json") == value


@pytest.mark.parametrize("field,short", [("matched_points", ["요"]), ("mismatched_points", ["탐"]), ("uncertainty_ko", "시")])
def test_one_character_new_response_repairs_but_stored_record_is_preserved(field, short):
    value = {**signature("partial"), field: short}
    assert SignatureAssessment.model_validate(value).model_dump(mode="json") == value
    with pytest.raises(ValidationError) as exc:
        SignatureAssessmentV2.model_validate(value)
    assert exc.value.errors()[0]["type"] == "signature_detail_requires_explanation"


@pytest.mark.parametrize("field", ["matched_points", "mismatched_points", "uncertainty_ko"])
@pytest.mark.parametrize("blank", [" ", "\t\r\n", "\u3000"])
def test_removed_wire_pattern_keeps_nonblank_validation(field, blank):
    value = {**signature("partial"), field: blank if field == "uncertainty_ko" else [blank]}
    with pytest.raises(ValidationError):
        SignatureAssessmentV2.model_validate(value)


def output(verdict="true_positive", relation="exact"):
    value = fake_output().model_dump(mode="json")
    value.update(verdict=verdict, signature_assessment=signature(relation), decision_issue=None,
        evidence=[{"source_id": "c1", "supports": "true_positive", "interpretation_ko": "입력의 구문 조작 시도를 확인했습니다."}])
    value["threat_analysis"]["severity"] = {"true_positive": "HIGH", "false_positive": "NONE", "inconclusive": "UNKNOWN"}[verdict]
    return value


def check(purpose):
    return {**legacy_checks()[0], "purpose": purpose}


def resolved(verdict="inconclusive"):
    value = output(verdict)
    value.pop("decision_issue")
    value["evidence"] = fake_output().model_dump(mode="json")["evidence"]
    return value


def test_legacy_result_and_null_purpose_roundtrip_without_backfill():
    value = fake_output().model_dump(mode="json")
    value["signature_assessment"] = {"relation": "mismatch", "explanation_ko": "기존의 모순된 일치 설명도 원본으로 보존합니다."}
    value["analyst_checks"] = legacy_checks()
    before = copy.deepcopy(value)
    assert WAFAnalysisOutput.model_validate(value).model_dump(mode="json") == before
    assert AnalystCheck.model_validate(legacy_checks()[0]).purpose is None
    assert value == before


@pytest.mark.parametrize("relation", ["exact", "partial", "mismatch", "unknown"])
def test_structured_signature_accepts_relations_without_deciding_verdict(relation):
    for verdict in ("true_positive", "false_positive", "inconclusive"):
        parsed = SemanticAssessmentOutput.model_validate(output(verdict, relation))
        assert parsed.verdict.value == verdict
        assert SignatureAssessment.model_validate(parsed.signature_assessment.model_dump()).version == "signature-assessment-v2"


@pytest.mark.parametrize("relation,changes,code", [
    ("exact", {"matched_points": []}, "signature_exact_requires_match"),
    ("exact", {"mismatched_points": ["대상이 다릅니다"]}, "signature_exact_disallows_mismatch"),
    ("exact", {"uncertainty_ko": "대상 미확인"}, "signature_exact_disallows_mismatch"),
    ("partial", {"matched_points": []}, "signature_partial_requires_match_and_mismatch"),
    ("partial", {"mismatched_points": []}, "signature_partial_requires_match_and_mismatch"),
    ("mismatch", {"mismatched_points": []}, "signature_mismatch_requires_difference"),
    ("unknown", {"uncertainty_ko": None}, "signature_unknown_requires_reason"),
])
def test_signature_structure_rejected_with_fixed_codes(relation, changes, code):
    with pytest.raises(ValidationError) as exc:
        SignatureAssessmentV2.model_validate({**signature(relation), **changes})
    assert exc.value.errors()[0]["type"] == code


@pytest.mark.parametrize("change", [{"version": None}, {"matched_points": ["   "]}, {"explanation_ko": "일치합니다"}, {"matched_points": ["가" * 401]}])
def test_new_signature_rejects_optout_blank_and_unbounded_details(change):
    with pytest.raises(ValidationError):
        SignatureAssessmentV2.model_validate({**signature(), **change})


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
@pytest.mark.parametrize("purpose", ["decision_condition", "impact_followup", "tuning_validation"])
def test_purpose_contract_is_deterministic_not_a_text_blacklist(verdict, purpose):
    value = output(verdict)
    value["analyst_checks"] = [check(purpose)]
    if purpose == "decision_condition" and verdict != "inconclusive":
        with pytest.raises(ValidationError) as exc:
            SemanticAssessmentOutput.model_validate(value)
        assert exc.value.errors()[0]["type"] == "decisive_verdict_disallows_decision_condition"
    else:
        assert SemanticAssessmentOutput.model_validate(value).analyst_checks[0].purpose.value == purpose


def test_new_wire_requires_version_purpose_and_empty_legacy_list():
    schema = strict_json_schema(SemanticAssessmentOutput.model_json_schema())
    assert_strict_schema(schema)
    assert "version" in schema["$defs"]["SignatureAssessmentV2"]["required"]
    assert "purpose" in schema["$defs"]["PurposeAnalystCheck"]["required"]
    assert "anyOf" not in schema["$defs"]["PurposeAnalystCheck"]["properties"]["purpose"]
    for checks in ([legacy_checks()[0]], [check(None)]):
        with pytest.raises(ValidationError):
            SemanticAssessmentOutput.model_validate({**output(), "analyst_checks": checks})
    with pytest.raises(ValidationError, match="recommended_checks"):
        SemanticAssessmentOutput.model_validate({**output(), "recommended_checks": ["legacy"]})


def test_old_wire_does_not_require_or_offer_new_fields():
    for strict in (False, True):
        schema = _TrackingOutputCodec(PydanticOutputCodec(EvidenceAssessmentOutput), strict=strict, output_model=EvidenceAssessmentOutput).schema()
        assert set(schema["$defs"]["SignatureAssessment"]["properties"]) == {"relation", "explanation_ko"}
        assert set(schema["$defs"]["AnalystCheck"]["properties"]) == {"source_ko", "check_ko", "why_ko"}
        assert schema["$defs"]["SignatureAssessment"]["properties"]["explanation_ko"]["type"] == "string"


def test_empty_new_checks_do_not_invent_a_condition_or_reuse_legacy_text():
    value = WAFAnalysisOutput.model_validate(resolved())
    assert build_analyst_guidance(value)["checks"] == []
    corrupt = value.model_copy(update={"recommended_checks": ["LEGACY_SHOULD_NOT_BECOME_NEW_TASK"]})
    assert build_analyst_guidance(corrupt)["checks"] == []
    assert build_analyst_guidance(value, evidence_rejected=True)["limitations"]


def test_editor_and_dedup_never_cross_purpose_or_modify_originals():
    tasks = [check("decision_condition"), check("impact_followup"), {**check("decision_condition"), "check_ko": "같은 입력 처리 규칙을 점검합니다."}]
    before = copy.deepcopy(tasks)
    assert result_editor.check_buckets(result_editor.check_items(tasks)) == [["c1", "c3"], ["c2"]]
    with pytest.raises(ValueError, match="purpose_mismatch"):
        result_editor.validate_check_groups(tasks, [{"member_ids": ["c1", "c2", "c3"], "representative_id": "c1"}])
    presentation = {"version": result_editor.CHECK_VERSION, "status": "completed", "items": result_editor.check_items(tasks), "groups": CHECK_GROUPS}
    assert result_editor.present_checks(tasks, presentation) == [tasks[2], tasks[1]]
    value = WAFAnalysisOutput.model_validate({**resolved(), "analyst_checks": tasks})
    assert len(merge_analyst_checks(value, value)) == 3
    assert tasks == before


@pytest.mark.parametrize("provider", ["openai", "vllm"])
@pytest.mark.parametrize("role", ["primary", "verifier"])
@pytest.mark.parametrize("fault,code", [("signature", "signature_mismatch_requires_difference"), ("purpose", "decisive_verdict_disallows_decision_condition"), ("short_signature", "signature_detail_requires_explanation")])
def test_contract_retry_reuses_input_and_does_not_echo_bad_output(monkeypatch, provider, role, fault, code):
    valid = output(relation="mismatch")
    invalid = copy.deepcopy(valid)
    invalid["summary_ko"] = "PRIVATE_INVALID_OUTPUT_CANARY"
    if fault == "signature":
        invalid["signature_assessment"]["mismatched_points"] = []
    elif fault == "short_signature":
        invalid["signature_assessment"]["mismatched_points"] = ["탐"]
    else:
        invalid["analyst_checks"] = [check("decision_condition")]
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(json.dumps(invalid if count == 1 else valid))))
    profile = openai_profile(**({"provider": "vllm", "base_url": "http://10.0.0.10:8000/v1", "external_data_approved": False} if provider == "vllm" else {}))
    result = asyncio.run(execute_structured_agent(profile=profile, api_key="synthetic", instructions="PINNED_SYNTHETIC_RULES",
        user_input="SAME_SYNTHETIC_INPUT", session_id="synthetic:"+role, agent_name="waf-"+role,
        egress_check=lambda: None, output_model=SemanticAssessmentOutput))
    assert result.succeeded and len(requests) == 2
    body = json.loads(requests[1].content)
    assert code in str(body) and "PRIVATE_INVALID_OUTPUT_CANARY" not in str(body)
    assert "SAME_SYNTHETIC_INPUT" in str(body) and "PINNED_SYNTHETIC_RULES" in str(body)
    assert code in str(result.telemetry) and "PRIVATE_INVALID_OUTPUT_CANARY" not in str(result.telemetry)


@pytest.mark.parametrize("provider", ["openai", "vllm"])
def test_unrepaired_new_contract_stops_at_existing_attempt_limit(monkeypatch, provider):
    invalid = output(relation="mismatch")
    invalid["signature_assessment"]["mismatched_points"] = []
    invalid["summary_ko"] = "PRIVATE_INVALID_OUTPUT_CANARY"
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(invalid))))
    profile = openai_profile(**({"provider": "vllm", "base_url": "http://10.0.0.10:8000/v1", "external_data_approved": False} if provider == "vllm" else {}))
    result = asyncio.run(execute_structured_agent(profile=profile, api_key="synthetic", instructions="PINNED_RULES",
        user_input="SYNTHETIC_INPUT", session_id="synthetic:primary", agent_name="waf-primary",
        egress_check=lambda: None, output_model=SemanticAssessmentOutput))
    assert not result.succeeded and result.output is None
    assert len(requests) == 4
    assert "PRIVATE_INVALID_OUTPUT_CANARY" not in str(result.telemetry)
    for request in requests[1:]:
        body = json.loads(request.content)
        assert "signature_mismatch_requires_difference" in str(body)
        assert "PRIVATE_INVALID_OUTPUT_CANARY" not in str(body)
