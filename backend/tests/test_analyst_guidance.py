import copy

import pytest
from pydantic import ValidationError

from app.agent.analyst_guidance import EVIDENCE_SOURCE_CHECK, EVIDENCE_SOURCE_NOTICE, build_analyst_guidance
from app.agent.contracts import AnalystCheck, WAFAnalysisOutput
from app.agent.policy import finalize_with_verifier
from app.agent.prompts import BASE_INSTRUCTIONS, PROMPT_VERSION
from test_agent_policy import output


def check(source="대상 애플리케이션의 요청 처리 규격"):
    return AnalystCheck(
        source_ko=source,
        check_ko="검색어가 일반 문자열로만 사용되는지 SQL 구문으로 해석될 수 있는지 확인하세요.",
        why_ko="정상 검색 데이터와 SQL 조건 변경 시도를 구분하는 데 도움이 됩니다. 요청 성공 여부만으로 공격 시도를 배제하지 마세요.",
    )


def test_new_check_contract_is_additive_and_bounded():
    old = output().model_dump(mode="json")
    old.pop("analyst_checks")
    assert WAFAnalysisOutput.model_validate(old).analyst_checks == []
    old["analyst_checks"] = [check().model_dump()]
    assert WAFAnalysisOutput.model_validate(old).analyst_checks == [check()]
    old["analyst_checks"] *= 6
    with pytest.raises(ValidationError):
        WAFAnalysisOutput.model_validate(old)


@pytest.mark.parametrize("field,limit", [("source_ko", 240), ("check_ko", 800), ("why_ko", 800)])
def test_check_rejects_missing_or_oversized_text(field, limit):
    data = check().model_dump()
    data[field] = "x" * (limit + 1)
    with pytest.raises(ValidationError):
        AnalystCheck.model_validate(data)
    data[field] = ""
    with pytest.raises(ValidationError):
        AnalystCheck.model_validate(data)


def test_disagreement_preserves_abstention_and_merges_actionable_checks_without_internal_text():
    primary = output("true_positive").model_copy(update={"analyst_checks": [check()]})
    verifier = output("false_positive").model_copy(update={"analyst_checks": [check(), check("해당 기능의 담당자")]})
    before = copy.deepcopy((primary, verifier))
    final = finalize_with_verifier(primary, verifier, ["true_positive_but_allowed"])
    assert final.output.verdict.value == "inconclusive"
    assert final.output.threat_analysis.severity.value == "UNKNOWN"
    assert final.agreement is False
    assert len(final.output.analyst_checks) == 2
    guidance = build_analyst_guidance(final.output)
    assert all(item["source_ko"] and item["check_ko"] and item["why_ko"] for item in guidance["checks"])
    assert "판정을 보류" in guidance["summary_ko"]
    assert not any(term in str(guidance) for term in ["Primary", "Verifier", "독립 검증", "서로 다", "불일치"])
    assert (primary, verifier) == before


def test_failed_verification_is_not_disguised_as_success_or_claimed_missing_evidence():
    final = finalize_with_verifier(output(), None, ["low_confidence"], "synthetic-failure")
    guidance = build_analyst_guidance(final.output, incomplete_execution=True)
    assert "완료하지 못했습니다" in guidance["limitations"][0]
    assert guidance["checks"]
    assert final.verifier_failure == "synthetic-failure"
    assert final.output.verdict.value == "inconclusive"
    assert "자료가 부족" not in guidance["summary_ko"]


def test_internal_check_is_not_turned_into_an_analyst_investigation_task():
    model_output = output("inconclusive").model_copy(update={
        "analyst_checks": [check("Verifier 모델")],
        "recommended_checks": ["검증 모델 연결과 실패 ID를 확인한 뒤 다시 분석하세요."],
        "input_truncated": True,
    })
    guidance = build_analyst_guidance(model_output)
    assert guidance["checks"][0]["source_ko"] == "대상 애플리케이션의 요청 처리 규격 또는 담당자"
    assert "생략된 구간" in guidance["limitations"][0]
    assert "Verifier" not in str(guidance)


def test_decisive_summary_remains_the_final_summary_not_a_new_decision():
    result = output().model_copy(update={"analyst_checks": [check()]})
    assert build_analyst_guidance(result)["summary_ko"] == result.summary_ko
    assert result.verdict.value == "true_positive"


def test_prompt_requires_specific_followups_and_untrusted_derived_hints():
    assert PROMPT_VERSION == "waf-judgment-v2.6"
    for term in ["source_ko", "check_ko", "why_ko", "decoded_payload_hints", "디코딩 전 원문", "공격 성공"]:
        assert term in BASE_INSTRUCTIONS


def test_inconclusive_preserves_informative_summary_without_leading_with_checks():
    summary = "조회식은 확인되지만 요청 처리 경로가 확인되지 않아 공격 시도인지 구분하기 어렵습니다."
    result = output("inconclusive").model_copy(update={"summary_ko": summary})
    assert build_analyst_guidance(result)["summary_ko"] == summary


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
@pytest.mark.parametrize("confidence", [0.7, 0.97])
def test_decisive_checks_are_not_invented_or_suppressed_by_confidence(verdict, confidence):
    result = output(verdict, confidence).model_copy(update={"recommended_checks": []})
    assert build_analyst_guidance(result)["checks"] == []
    with_check = result.model_copy(update={"analyst_checks": [check()]})
    assert build_analyst_guidance(with_check)["checks"] == [check().model_dump(mode="json")]


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
def test_legacy_decisive_followups_are_explicitly_optional(verdict):
    guidance = build_analyst_guidance(output(verdict))
    assert "선택적 확인" in guidance["checks"][0]["why_ko"]
    assert "필수 조건이 아니라" in guidance["checks"][0]["why_ko"]


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
def test_internal_summary_fallback_preserves_the_actual_verdict(verdict):
    result = output(verdict).model_copy(update={"summary_ko": "Primary와 Verifier의 분석 결과가 서로 다릅니다."})
    guidance = build_analyst_guidance(result)
    assert "Primary" not in guidance["summary_ko"]
    assert ("보류" in guidance["summary_ko"]) == (verdict == "inconclusive")


def test_prompt_distinguishes_check_purpose_and_discourages_repeated_evidence():
    # v2.6 keeps these boundaries in shorter wording, without prescribing a
    # fixed sentence count or requiring follow-ups for decisive verdicts.
    for term in ["보류는 구분에 필요한 1~3개", "확정은 기본 빈 배열", "유용할 때만 선택적 1~2개", "두 목록 모두 빈 배열", "보정된 정탐 확률이 아닌", "같은 관찰은 한 근거에 모으고", "별개 출처·반대 근거는 보존"]:
        assert term in BASE_INSTRUCTIONS


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
@pytest.mark.parametrize("structured", [True, False])
def test_worker_source_diagnostic_is_a_notice_not_an_investigation_task(verdict, structured):
    result = output(verdict).model_copy(update={
        "recommended_checks": [EVIDENCE_SOURCE_CHECK],
        "analyst_checks": [check().model_copy(update={"check_ko": EVIDENCE_SOURCE_CHECK})] if structured else [],
    })
    before = copy.deepcopy(result)
    guidance = build_analyst_guidance(result)
    assert guidance["limitations"] == [EVIDENCE_SOURCE_NOTICE]
    assert all(item["check_ko"] != EVIDENCE_SOURCE_CHECK for item in guidance["checks"])
    assert bool(guidance["checks"]) == (verdict == "inconclusive")
    assert result == before


def test_source_diagnostic_does_not_discard_a_real_followup():
    result = output().model_copy(update={
        "recommended_checks": [EVIDENCE_SOURCE_CHECK], "analyst_checks": [check()],
    })
    guidance = build_analyst_guidance(result)
    assert guidance["checks"] == [check().model_dump(mode="json")]
    assert guidance["limitations"] == [EVIDENCE_SOURCE_NOTICE]
