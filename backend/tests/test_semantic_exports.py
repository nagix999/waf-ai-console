"""New contract exports share purpose boundaries; synthetic data only."""
import copy
import json

import pytest

from app.services.analysis_exports import build_report, render_xlsx
from app.services.report_pdf import pdf_document
from test_analysis_exports import detail_fixture, report_text, xlsx_cells
from test_semantic_contract import signature, check


@pytest.mark.parametrize("action", ["D", "A"])
@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
def test_new_export_separates_purposes_and_keeps_signature_details(action, verdict):
    detail = detail_fixture()
    detail["waf_action"] = action
    detail["result"]["verdict"] = verdict
    detail["result"]["signature_assessment"] = signature("mismatch")
    purposes = (["decision_condition"] if verdict == "inconclusive" else []) + ["impact_followup", "tuning_validation"]
    detail["result"]["analyst_guidance"]["checks"] = [
        {**check(purpose), "check_ko": f"확인 내용 {index}"} for index, purpose in enumerate(purposes)]
    before = copy.deepcopy(detail)
    report = build_report(detail)
    titles = [s.title for s in report.sections]
    assert ("판정에 필요한 확인" in titles) == (verdict == "inconclusive")
    assert titles[-2:] == ["영향·대응 확인", "튜닝 전 검증"]
    assert ("차단으로 기록" if action == "D" else "허용으로 기록") in report_text(report)
    assert "탐지 설명은 SQL이지만 요청에는 HTML 실행 구문이 있습니다." in report_text(report)
    assert "DO-NOT-INVENT" not in report_text(report)
    for index, section in enumerate(report.sections[-len(purposes):]):
        assert [value for key, value in section.rows if key == "확인 내용"] == [f"확인 내용 {index}"]
    cells = [text.text for cell in xlsx_cells(render_xlsx(report)) for text in cell.iter() if text.tag.endswith('}t')]
    assert "다른 부분" in cells
    assert "탐지 설명은 SQL이지만 요청에는 HTML 실행 구문이 있습니다." in cells
    clean = pdf_document(detail)["detail"]["result"]
    assert clean["signature_assessment"] == signature("mismatch")
    assert clean["analyst_guidance"]["checks"] == detail["result"]["analyst_guidance"]["checks"]
    assert detail == before


def test_pdf_disagreement_includes_only_role_verdicts_not_intermediate_text():
    detail = detail_fixture()
    detail["result"].update(verdict="inconclusive", diagnostics={"inconclusive_reasons": ["verdict_disagreement"]})
    detail["result"]["primary"]["verdict"] = "true_positive"
    detail["result"]["verifier"]["output"]["verdict"] = "false_positive"
    value = pdf_document(detail)
    assert value["detail"]["result"]["primary"] == {"verdict": "true_positive"}
    assert value["detail"]["result"]["verifier"]["output"] == {"verdict": "false_positive"}
    serialized = json.dumps(value, default=str)
    assert "PRIMARY-MUST-NOT-EXPORT" not in serialized
    assert "VERIFIER-MUST-NOT-EXPORT" not in serialized
    assert "RAW-PAYLOAD-MUST-NOT-EXPORT" not in serialized


def test_suppressed_final_checks_are_absent_from_xlsx_and_shared_pdf_input():
    detail = detail_fixture()
    detail["result"]["signature_assessment"] = signature()
    detail["result"]["analyst_checks"] = []
    detail["result"]["analyst_guidance"]["checks"] = []
    detail["result"]["primary"]["analyst_checks"] = [{**check("impact_followup"), "check_ko": "SUPPRESSED_CHECK_CANARY"}]
    report = build_report(detail)
    assert not any(s.title in {"영향·대응 확인", "튜닝 전 검증", "판정에 필요한 확인"} for s in report.sections)
    assert "SUPPRESSED_CHECK_CANARY" not in report_text(report)
    assert "SUPPRESSED_CHECK_CANARY" not in json.dumps(pdf_document(detail), default=str)
    assert all("SUPPRESSED_CHECK_CANARY" not in (node.text or "") for cell in xlsx_cells(render_xlsx(report)) for node in cell.iter())
