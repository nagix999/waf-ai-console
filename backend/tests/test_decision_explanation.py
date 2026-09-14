import copy
import json
from pathlib import Path

import pytest

from app.services.decision_explanation import decision_explanation, GENERIC_CHECK, GENERIC_TUNING_RISK
from app.services.analysis_exports import build_report, render_xlsx, render_pdf
from test_analysis_exports import detail_fixture, report_text, xlsx_cells

CASES = json.loads((Path(__file__).parent / "fixtures/decision_explanation_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["name"])
def test_recorded_cause_not_input_signals(case):
    before = copy.deepcopy(case["detail"])
    value = decision_explanation(case["detail"])
    assert (value["code"] if value else None) == case["expected_code"]
    assert case["detail"] == before
    assert "synthetic-do-not-display" not in str(value)


@pytest.mark.parametrize("reason", ["verifier_failed", "primary_evidence_rejected", "verdict_disagreement", "primary_model_inconclusive"])
def test_pdf_excel_use_final_cause_without_inventing_work_or_mutating_result(reason):
    detail = detail_fixture()
    detail["result"].update(verdict="inconclusive", summary_ko="현재 분석에서는 정탐·오탐 판정을 보류했습니다.",
        diagnostics={"inconclusive_reasons": [reason]},
        analyst_guidance={"checks": [GENERIC_CHECK]},
        tuning_recommendation={"recommended": False, "risk_ko": GENERIC_TUNING_RISK})
    detail["result"]["threat_analysis"]["severity"] = "UNKNOWN"
    before = copy.deepcopy(detail)
    report = build_report(detail)
    text = report_text(report)
    explanation = decision_explanation(detail)
    assert explanation["reason_ko"] in text
    assert explanation["action_ko"] in text
    assert GENERIC_CHECK["check_ko"] not in text
    assert "검토한 유형" in text
    assert report.sections[-1].title == "추가 확인 사항"
    assert "WAF 정책 검토" not in [section.title for section in report.sections]
    assert "PRIMARY-MUST-NOT-EXPORT" not in text
    assert "VERIFIER-MUST-NOT-EXPORT" not in text
    assert xlsx_cells(render_xlsx(report))
    assert render_pdf(report).startswith(b"%PDF")
    assert detail == before


def test_explicit_checks_remain_and_false_positive_is_not_labeled_attack():
    detail = detail_fixture()
    detail["result"]["verdict"] = "false_positive"
    detail["result"]["threat_analysis"]["severity"] = "NONE"
    detail["result"]["analyst_guidance"]["checks"] = [{**GENERIC_CHECK, "check_ko": "확인할 요청의 본문 수집 여부"}]
    text = report_text(build_report(detail))
    assert "탐지 유형" in text
    assert "공격 유형" not in text
    assert "확인할 요청의 본문 수집 여부" in text
