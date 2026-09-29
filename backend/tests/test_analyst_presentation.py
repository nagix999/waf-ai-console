import copy
import json
from pathlib import Path

import pytest

from app.agent.analyst_guidance import build_analyst_guidance
from app.services.analyst_presentation import assessment_view, analyst_text, EVIDENCE_LABELS
from app.services.analysis_exports import build_report, render_xlsx, render_pdf
from test_agent_policy import output

CASES = json.loads((Path(__file__).parent / "fixtures/analyst_assessment_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["name"])
def test_shared_read_only_presentation(case):
    before = copy.deepcopy(case["result"])
    assert assessment_view(case["result"]) == case["expected"]
    assert case["result"] == before


def detail():
    return {"id": "synthetic", "status": "completed", "result": {
        **output("inconclusive").model_dump(mode="json"), **copy.deepcopy(CASES[0]["result"]),
        "diagnostics": {"inconclusive_reasons": ["verdict_disagreement"]},
        "agent": {"framework": "moduagent"}, "analyst_guidance": {"checks": [], "limitations": []}}}


def test_exports_include_both_sides_and_issue_but_no_raw_snapshots():
    value = detail()
    value["result"]["primary"] = {"secret": "NOT_EXPORTED"}
    report = build_report(value)
    text = str(report)
    for label in ["공격 해석", "정상 해석", "참고 내용", "판정 확정에 필요한 조건", "관련 근거", "확인 내용", "로그 발췌", "판단 이유"]:
        assert label in text
    for item in CASES[0]["expected"]["evidence"]:
        assert item["interpretation_ko"] in text
    assert "NOT_EXPORTED" not in text and "연결할 수 없는 쟁점" not in text
    assert report.sections[-1].title == "판정 확정에 필요한 조건"
    assert render_xlsx(report).startswith(b"PK")
    assert render_pdf(report).startswith(b"%PDF")


@pytest.mark.parametrize("reason", ["verifier_failed", "primary_evidence_rejected"])
def test_system_failure_does_not_present_business_issue_as_the_hold_cause(reason):
    value = detail()
    value["result"]["diagnostics"]["inconclusive_reasons"] = [reason]
    assert "code_verifier의 값이 인증 데이터인지 구문으로 해석되는지 구분해야 합니다." not in str(build_report(value))


@pytest.mark.parametrize("text", [
    "code_verifier 파라미터는 인증 요청의 검증 값입니다.", "primary_email 필드는 연락처 데이터입니다.",
    "검증 실패 값은 오류 코드 데이터이며 실행 명령이 아닙니다.",
    "분석 결과가 서로 다릅니다. q의 실제 값과 SQL 문법을 대조해야 합니다.",
])
def test_no_word_based_loss_in_guidance_or_exports(text):
    value = output().model_copy(update={"summary_ko": text})
    assert analyst_text(text) == text
    assert build_analyst_guidance(value)["summary_ko"] == text
    report = build_report({"status": "completed", "result": value.model_dump(mode="json")})
    assert text in str(report)
