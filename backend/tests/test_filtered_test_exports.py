"""Selected fields and all filtered cases, using synthetic local fixtures only."""
import json
import re
from datetime import timedelta
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import select

from app.models import AccessAudit, Analysis, utcnow
from test_test_statistics_exports import fixture_run, workbook, NS


def preview(client, run, filters=None):
    response = client.post(f"/api/v1/test-runs/{run['id']}/export/preview", json={"filters": filters or {}})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def download(client, run, filters, metadata, columns):
    return client.post(f"/api/v1/test-runs/{run['id']}/export.xlsx", json={"filters": filters,
        "columns": columns, "expected_count": metadata["count"], "preview_scope": metadata["scope"]})


@pytest.mark.parametrize("filters", [{}, {"status": "failed"}, {"difficulty": "hard"}, {"difficulty_missing": True},
    {"evaluation_outcome": "false_negative", "reference_verdict": "true_positive", "verdict": "false_positive"},
    {"group_filters": json.dumps([{"field": "signature", "value": "SQL"}, {"field": "waf_action", "value": "D"}])},
    {"sort_by": "case_name", "sort_order": "desc"},
    {"case_filter": {"op": "or", "rules": [{"field": "verdict", "operator": "eq", "value": "true_positive"},
        {"field": "reference_verdict", "operator": "eq", "value": "inconclusive"}]}}])
def test_exports_every_matching_case_not_just_one_page(client, event_payload, filters):
    run = fixture_run(client, event_payload)
    expected = client.post(f"/api/v1/test-runs/{run['id']}/cases/search", json={**filters, "limit": 200}).json()
    one_page = client.post(f"/api/v1/test-runs/{run['id']}/cases/search", json={**filters, "limit": 1, "offset": 1}).json()
    metadata = preview(client, run, filters)
    assert metadata["count"] == expected["total_items"] == one_page["total_items"]
    _, _, rows = workbook(download(client, run, filters, metadata, ["row_number", "event_id", "verdict", "reference_verdict"]))
    assert [int(row["row_number"]) for row in rows] == [row["row_number"] for row in expected["items"]]
    assert all(list(row) == ["row_number", "event_id", "verdict", "reference_verdict"] for row in rows)


def test_two_sheets_english_headers_and_selected_column_guide_only(client, event_payload):
    run = fixture_run(client, event_payload)
    metadata = preview(client, run)
    columns = ["verdict", "expected_verdict", "initial_verdict", "reference_verdict", "evidence_attack_request"]
    response = download(client, run, {}, metadata, columns)
    archive, _, rows = workbook(response)
    sheets = ET.fromstring(archive.read("xl/workbook.xml")).find("s:sheets", NS)
    assert [sheet.attrib["name"] for sheet in sheets] == ["분석결과", "컬럼안내"]
    assert all(list(row) == columns for row in rows)
    guide = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
    values = [[cell.findtext("s:is/s:t", default="", namespaces=NS) for cell in row.findall("s:c", NS)]
        for row in guide.findall("s:sheetData/s:row", NS)]
    assert values[0] == ["column_name", "column_name_ko", "description", "example"]
    assert [row[0] for row in values[1:]] == columns
    assert all(row[1] and row[2] and row[3] for row in values[1:])
    assert event_payload["payload"] not in str(values)  # illustrative examples only
    assert not any(b"<f>" in archive.read(name) for name in archive.namelist())


@pytest.mark.parametrize("verdict", ["false_positive", "inconclusive"])
def test_v3_explanation_column_is_separate_from_observation_summary(client, event_payload, verdict):
    from test_result_contract_v3 import result_fixture
    from app.services.decision_explanation import decision_explanation

    run = fixture_run(client, event_payload)
    result = result_fixture()
    result["verdict"] = verdict
    if verdict == "inconclusive":
        result["threat_analysis"]["severity"] = "UNKNOWN"
        result["diagnostics"]["inconclusive_reasons"] = ["verifier_failed"]
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        analysis.result_json = result
        analysis.status, analysis.verdict, analysis.summary_ko = "completed", verdict, result["summary_ko"]
        db.commit()
    _, _, rows = workbook(download(client, run, {}, preview(client, run), ["summary_ko", "explanation_ko"]))
    reason = decision_explanation({"status": "completed", "result": result})
    assert rows[0]["summary_ko"] == result["summary_ko"]
    assert rows[0]["explanation_ko"] == (reason["reason_ko"] if reason else "")
    assert rows[0]["explanation_ko"] != rows[0]["summary_ko"]


def test_preview_and_payload_exclusion_do_not_decrypt_or_load_http(client, event_payload, monkeypatch):
    run = fixture_run(client, event_payload)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.payload_ciphertext = "unreadable-synthetic-http"
        db.commit()
    metadata = preview(client, run)
    assert event_payload["payload"] not in json.dumps(metadata)
    response = download(client, run, {}, metadata, ["event_id", "verdict"])
    assert response.status_code == 200
    assert download(client, run, {}, metadata, ["payload"]).status_code == 503
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "export_test_run_xlsx", AccessAudit.resource_id == run["id"]))


@pytest.mark.parametrize("change", ["analysis", "label", "extra_fields"])
def test_preview_detects_changed_cases_or_reference_before_download(client, event_payload, change):
    from app.models import AnalysisLabel
    run = fixture_run(client, event_payload)
    filters = {"status": "completed"} if change == "analysis" else {"reference_verdict": "true_positive"} if change == "label" else {}
    metadata = preview(client, run, filters)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        if change == "analysis":
            row.status = "processing"
        elif change == "extra_fields":
            row.extra_fields = {"new_field": "synthetic"}
        else:
            db.add(AnalysisLabel(analysis_id=row.id, revision=2, verdict="false_positive", source_kind="reference",
                source_ref="synthetic-change", created_by="fixture", attachment_id="synthetic-change", token_digest="a" * 64))
        db.commit()
    response = download(client, run, filters, metadata, ["event_id"])
    assert response.status_code == 409 and response.json()["detail"] == "test_export_selection_changed"
    assert download(client, run, filters, preview(client, run, filters), ["event_id"]).status_code == 200


def test_finishing_unfiltered_cases_does_not_prevent_download(client, event_payload):
    run = fixture_run(client, event_payload)
    metadata = preview(client, run)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.status, row.summary_ko = "processing", "다운로드 시점의 설명"
        row.updated_at = utcnow() + timedelta(seconds=1)
        db.commit()
    _, _, rows = workbook(download(client, run, {}, metadata, ["row_number", "status", "summary_ko"]))
    assert len(rows) == metadata["count"]
    assert rows[0]["status"] == "processing" and rows[0]["summary_ko"] == "다운로드 시점의 설명"


@pytest.mark.parametrize("columns", [[], ["payload_ciphertext"], ["event_id", "event_id"], ["not_real"], ["주요 정탐 근거"]])
def test_invalid_or_internal_fields_are_never_exported(client, event_payload, columns):
    run = fixture_run(client, event_payload)
    response = download(client, run, {}, preview(client, run), columns)
    assert response.status_code == 422


def test_extra_fields_include_non_ascii_names_with_safe_english_columns(client, event_payload):
    run = fixture_run(client, event_payload)
    with client.app.state.session_factory() as db:
        db.get(Analysis, run["items"][0]["analysis_id"]).extra_fields = {"고객사 등급": "A", "vendor_data": {"fixture": True}}
        db.commit()
    metadata = preview(client, run)
    target = next(column for column in metadata["columns"] if column["label_ko"] == "추가 입력: 고객사 등급")
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", target["name"])
    assert "source_key" not in target
    _, _, rows = workbook(download(client, run, {}, metadata, [target["name"]]))
    assert rows[0][target["name"]] == "A"


def test_saved_evaluation_exports_its_pinned_attempt_after_successful_retry(client, event_payload, registered_vllm_target):
    from test_test_retries import fixture_run as failed_run, finish
    from test_analysis_retries_keys import retry
    run, _ = failed_run(client, event_payload, count=1)
    original = run["items"][0]["analysis_id"]
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": "export-saved-synthetic"}).json()
    child = retry(client, original).json()["analysis_id"]
    finish(client, child, "true_positive")
    for filters, identifier, status in (({}, child, "completed"), ({"evaluation_id": saved["id"]}, original, "failed")):
        metadata = preview(client, run, filters)
        _, _, rows = workbook(download(client, run, filters, metadata, ["analysis_id", "status", "reference_verdict"]))
        assert len(rows) == metadata["count"] == 1
        assert rows[0]["analysis_id"] == identifier and rows[0]["status"] == status


@pytest.mark.parametrize("suffix", ["export/preview", "export.xlsx"])
def test_exports_remain_admin_only_and_reject_pagination_and_query_strings(client, event_payload, service_headers, suffix):
    run = fixture_run(client, event_payload)
    base = f"/api/v1/test-runs/{run['id']}/{suffix}"
    metadata = preview(client, run)
    body = {"filters": {}} if suffix.endswith("preview") else {"filters": {}, "columns": ["event_id"], "expected_count": metadata["count"], "preview_scope": metadata["scope"]}
    assert client.post(base, json={**body, "filters": {"limit": 1}}).status_code == 422
    assert client.post(base + "?payload=private", json=body).status_code == 422
    client.cookies.clear()
    assert client.post(base, json=body).status_code == 401
    assert client.post(base, json=body, headers=service_headers).status_code == 403
