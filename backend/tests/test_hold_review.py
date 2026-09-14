import copy
import json
from pathlib import Path

import pytest

from app.services.analysis_exports import build_report, render_pdf, render_xlsx
from app.services.decision_explanation import GENERIC_CHECK, decision_explanation, input_limit_explanation
from app.services.hold_review import hold_review
from test_analysis_exports import NS, detail_fixture, report_text, xlsx_cells


CASES = json.loads((Path(__file__).parent / "fixtures/hold_review_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["name"])
def test_shared_review_contract_does_not_create_facts_or_change_the_result(case):
    detail = copy.deepcopy(case["detail"])
    before = copy.deepcopy(detail)
    view = hold_review(detail, decision_explanation(detail))
    expected = case["expected"]
    assert [item["evidence_numbers"] for item in view["issues"]] == expected["issue_numbers"]
    assert [item["missing_condition_ko"] for item in view["issues"]] == expected["conditions"]
    assert [item["source_ko"] for item in view["checks"]] == expected["check_sources"]
    for fragment in expected["contains"]:
        assert fragment in json.dumps(view, ensure_ascii=False)
    assert detail == before


@pytest.mark.parametrize("case_name", ["linked_missing_condition", "missing_point_anchors_saved_body", "hostile_condition_is_literal_not_executed"])
def test_report_pdf_and_excel_include_the_same_review_without_role_or_raw_reads(case_name):
    detail = detail_fixture()
    stored = copy.deepcopy(next(case["detail"] for case in CASES if case["name"] == case_name))
    detail["result"].update(stored["result"])
    detail["result"]["threat_analysis"]["severity"] = "UNKNOWN"
    before = copy.deepcopy(detail)
    review = hold_review(detail, decision_explanation(detail))
    report = build_report(detail)
    text = report_text(report)
    assert report.sections[-1].title == "추가 확인 사항"
    for check in review["checks"]:
        assert all(value in text for value in check.values())
    for issue in review["issues"]:
        assert issue["point_ko"] in text
    assert "PRIMARY-MUST-NOT-EXPORT" not in text and "VERIFIER-MUST-NOT-EXPORT" not in text
    assert render_pdf(report).startswith(b"%PDF")
    values = [cell.find("s:is/s:t", NS).text or "" for cell in xlsx_cells(render_xlsx(report))]
    assert all(check["check_ko"] in values for check in review["checks"])
    assert detail == before


def test_specific_checks_are_preserved_and_exact_duplicates_only_are_removed():
    detail = detail_fixture()
    detail["result"].update(copy.deepcopy(CASES[0]["detail"]["result"]))
    check = {"source_ko": "서비스 담당자", "check_ko": "JSON 입력의 허용 형식을 확인하세요.", "why_ko": "같은 요청의 본문 형식과 비교하세요."}
    detail["result"]["analyst_guidance"]["checks"] = [check, check, {**check, "why_ko": "다른 요청 위치는 따로 확인하세요."}]
    report = build_report(detail)
    rows = report.sections[-1].rows
    assert sum(label == "확인 내용" and value == check["check_ko"] for label, value in rows) == 2
    assert "해당 필드의 허용 형식" not in [value for label, value in rows if label == "확인 내용"]
    detail["result"]["analyst_guidance"]["checks"] = [GENERIC_CHECK]
    assert "해당 필드의 허용 형식" in report_text(build_report(detail))


@pytest.mark.parametrize("code,title", [("declared_body_not_captured", "요청 본문 확인 필요"),
                                      ("body_shorter_than_content_length", "본문 길이 확인 필요"),
                                      ("json_container_unclosed", "JSON 본문 확인 필요")])
def test_specific_input_reason_requires_actual_final_downgrade(code, title):
    detail = copy.deepcopy(CASES[0]["detail"])
    detail["result"]["diagnostics"] = {"inconclusive_reasons": ["input_integrity_limited"],
        "request_integrity": {"downgraded_to_inconclusive": True, "affected_issue_codes": [code]}}
    assert decision_explanation(detail) == input_limit_explanation([code])
    assert decision_explanation(detail)["title_ko"] == title
    detail["result"]["diagnostics"]["request_integrity"]["downgraded_to_inconclusive"] = False
    assert decision_explanation(detail)["code"] == "reason_unrecorded"
