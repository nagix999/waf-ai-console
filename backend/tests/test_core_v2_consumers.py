"""Synthetic cross-role representative parity at each public consumer boundary."""
from copy import deepcopy
import json
import os

import pytest
from pydantic import ValidationError

from app.models import Analysis
from app.result_contracts import AnalysisResultV3, validate_new_result
from app.schemas import AnalysisDetail, CoreAnalysisDetail
from app.services.analysis_exports import build_report
from app.services.core_result_response import to_core_detail
from app.services.evidence_display_plan_v2 import build_display_plan
from app.services.report_pdf import pdf_document
from app.services.test_run_exports import evidence_columns
from test_analysis_exports import detail_fixture, login, report_text, xlsx_cells
from test_core_evidence_v3 import dense_result
from test_core_result_response_api import saved_analysis
from test_filtered_test_exports import download, preview
from test_test_statistics_exports import fixture_run, workbook

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def pair_result():
    value = dense_result(6, exact=True)
    value["evidence"] = [value["evidence"][0], value["evidence"][5]]
    value["evidence"][0]["interpretation_ko"] = "등록된 검색 용도와 일치하는 입력이므로 정상 사용을 뒷받침합니다."
    value["evidence"][1]["interpretation_ko"] = "등록된 검색 용도로 사용한 입력이므로 정상 사용을 뒷받침합니다."
    value["primary"]["evidence_ids"] = ["e1"]
    value["verifier"]["output"]["evidence_ids"] = ["e6"]
    value["policy"]["decision_evidence_ids"] = ["e1"]
    issue = {"point_ko": "서비스의 검색 입력 사용 여부를 확인합니다.",
             "evidence_ids": ["e1"], "missing_condition_ko": None, "origins": ["primary"]}
    other = {**deepcopy(issue), "evidence_ids": ["e6"], "origins": ["verifier"]}
    value["decision_issues"] = [issue, other]
    value["primary"]["decision_issues"] = [deepcopy(issue)]
    value["verifier"]["output"]["decision_issues"] = [deepcopy(other)]
    return validate_new_result(value)


def view_for(value):
    relations = {"version": "evidence-relations-v1", "groups": []}
    plan = build_display_plan(value, relations, groups=[{
        "member_ids": ["e1", "e6"], "representative_id": "e1"}], status="completed")
    return {"version": "evidence-display-view-v2", "status": "completed",
            "plan": plan, "relations": relations, "metadata": {}}


def install_pair(client, identifier):
    original = pair_result()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.result_json = original
        db.commit()
    return original, view_for(original)


@pytest.mark.parametrize("version", ["v3", "v2"])
def test_public_post_get_by_event_use_one_cross_role_representative_without_storage_changes(
    client, event_payload, service_headers, monkeypatch, version,
):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    original, view = install_pair(client, identifier)
    monkeypatch.setattr("app.services.evidence_display_access.read_display_view", lambda *_args: deepcopy(view))
    responses = [client.get(f"/api/v1/analyses/{identifier}", params={"result_version": version}, headers=service_headers),
                 client.get("/api/v1/analyses/by-event", params={"event_id": event_payload["event_id"], "result_version": version}, headers=service_headers),
                 client.post(f"/api/v1/analyses?wait_seconds=0&result_version={version}", json=event_payload, headers=service_headers)]
    for response in responses:
        assert response.status_code == 200, response.text
        assert response.headers["X-Evidence-View"] == "core-v2"
        assert response.headers["X-Evidence-Original-Count"] == "2"
        assert response.headers["X-Evidence-Returned-Count"] == "1"
        result = response.json()["result"]
        evidence = result["evidence"] if version == "v3" else result["analyst_assessment"]["evidence"]
        assert len(evidence) == 1
        assert evidence[0]["origins"] == original["evidence"][0]["origins"]
        if version == "v3":
            assert result["decision_issues"] == [
                {**issue, "evidence_ids": ["e1"]} for issue in original["decision_issues"]]
    assert responses[0].json()["result"] == responses[1].json()["result"] == responses[2].json()["result"]
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).result_json == original


def test_typed_public_response_survives_reserialization_but_raw_cross_role_dict_never_enters_storage(
    client, event_payload, service_headers,
):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    original, view = install_pair(client, identifier)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        detail, metadata = to_core_detail(db, client.app.state.crypto, row, display_view=view)
    assert isinstance(detail, CoreAnalysisDetail)
    assert CoreAnalysisDetail.model_validate(detail).model_dump(mode="json") == detail.model_dump(mode="json")
    raw = detail.model_dump(mode="json")
    assert "core" not in raw["result"] and "mapping" not in raw["result"]
    with pytest.raises(ValidationError):
        AnalysisResultV3.model_validate(raw["result"])
    for model in (AnalysisDetail, CoreAnalysisDetail):
        with pytest.raises(ValidationError):
            model.model_validate(raw)
    tampered = detail.model_copy(deep=True)
    tampered.result.evidence[0].interpretation_ko = "원래 설명을 임의로 바꾼 합성 응답입니다."
    with pytest.raises(ValidationError):
        CoreAnalysisDetail.model_validate(tampered)
    assert metadata["version"] == "core-evidence-v2"
    assert validate_new_result(original) == original


def test_public_pdf_and_excel_keep_exact_same_cross_role_representative(client, event_payload, service_headers, monkeypatch):
    identifier, _ = saved_analysis(client, event_payload, service_headers)
    original, view = install_pair(client, identifier)
    monkeypatch.setattr("app.services.evidence_display_access.read_display_view", lambda *_args: deepcopy(view))
    received = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers).json()
    login(client)
    excel = client.get(f"/api/v1/analyses/{identifier}/report.xlsx")
    assert excel.status_code == 200, excel.text
    assert excel.headers["X-Evidence-View"] == "core-v2"
    text = "\n".join("".join(cell.itertext()) for cell in xlsx_cells(excel.content))
    assert original["evidence"][0]["interpretation_ko"] in text
    assert original["evidence"][1]["interpretation_ko"] not in text
    documents = []
    monkeypatch.setattr("app.api.analysis_exports.render_report_pdf", lambda document: documents.append(document) or b"%PDF-synthetic-v2")
    pdf = client.get(f"/api/v1/analyses/{identifier}/report.pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["X-Evidence-View"] == "core-v2"
    assert documents[0]["evidenceView"] == "core-v2"
    assert "evidence_display" not in documents[0]["detail"]
    assert documents[0]["detail"]["result"]["evidence"] == received["result"]["evidence"]
    assert evidence_columns(received["result"], evidence_view="core-v2")["evidence_normal_request"].count("판단 이유:") == 1
    assert report_text(build_report(received, evidence_view="core-v2")).count("로그 발췌") == 1
    assert pdf_document(received, evidence_view="core-v2")["detail"]["result"]["decision_issues"] == received["result"]["decision_issues"]


def test_batch_excel_uses_v2_display_projection_instead_of_reintroducing_both_roles(client, event_payload, monkeypatch):
    run = fixture_run(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    original, view = install_pair(client, identifier)
    monkeypatch.setattr("app.services.evidence_display_jobs_v3.get_display_views",
        lambda _db, _crypto, rows, _relations: {row.id: deepcopy(view) for row in rows if row.id == identifier})
    metadata = preview(client, run)
    response = download(client, run, {}, metadata, ["event_id", "evidence_normal_request"])
    assert response.status_code == 200, response.text
    _, _, rows = workbook(response)
    text = next(row["evidence_normal_request"] for row in rows if row["event_id"] == "synthetic-0")
    assert original["evidence"][0]["interpretation_ko"] in text
    assert original["evidence"][1]["interpretation_ko"] not in text
    assert "\n---\n" not in text


@pytest.mark.skipif(os.environ.get("WAF_TEST_REPORT_RENDERER") != "1", reason="Release image renderer check")
def test_real_pdf_accepts_cross_role_core_response_without_restoring_the_original_members():
    from app.services.core_evidence_v2 import build_core_result
    from app.services.report_pdf import render_report_pdf
    original = pair_result()
    selected = build_core_result(original, display_view=view_for(original))["result"].model_dump(mode="json")
    document = pdf_document({**detail_fixture(), "result": selected}, evidence_view="core-v2")
    assert document["detail"]["result"]["evidence"] == [original["evidence"][0]]
    body = render_report_pdf(json.loads(json.dumps(document, default=str)))
    assert body.startswith(b"%PDF-") and b"/FontFile2" in body
