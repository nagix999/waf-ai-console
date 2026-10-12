"""Synthetic Test aggregation/export; never contacts a model or live database."""
import json
import re
import uuid
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from app.models import AccessAudit, Analysis, AnalysisLabel, utcnow
from app.services.test_run_exports import EVIDENCE_COLUMNS, evidence_columns
from test_test_runs import login, upload

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def fixture_run(client, event_payload):
    login(client)
    values = [("SQL", "true_positive", "true_positive"), ("SQL", "false_positive", "true_positive"),
        ("SQL", "true_positive", "false_positive"), ("SQL", "inconclusive", "inconclusive"),
        ("XSS", "true_positive", "inconclusive"), (None, "false_positive", "failed"),
        (None, None, "false_positive"), (None, "true_positive", "stub")]
    rows = [{**event_payload, "event_id": f"synthetic-{i}", "signature": signature, "expected_verdict": expected,
        "difficulty": "hard" if i < 4 else "easy", "vendor_data": {"fixture": True}}
        for i, (signature, expected, _result) in enumerate(values)]
    run = upload(client, [*rows, rows[0], {**event_payload, "event_id": "rejected", "src_ip": ""}])
    assert run["accepted"] == 8 and run["duplicates"] == 1 and run["rejected"] == 1
    with client.app.state.session_factory() as db:
        for item, (_, _, result) in zip(run["items"], values):
            row = db.get(Analysis, item["analysis_id"])
            row.model_profile = "fixture-model"
            row.prompt_version = "fixture-instructions"
            row.completed_at = utcnow()
            row.status = "failed" if result == "failed" else "completed"
            row.verdict = "true_positive" if result == "stub" else None if result == "failed" else result
            row.severity = "HIGH" if row.verdict == "true_positive" else "NONE" if row.verdict == "false_positive" else "UNKNOWN"
            row.summary_ko = "합성 테스트 요약"
            row.result_json = {"verdict": row.verdict, "agent": {"framework": "stub" if result == "stub" else "moduagent"},
                "threat_analysis": {"severity": row.severity, "technique_ko": "합성 설명"}}
        db.commit()
    return run


def stats(client, run, **query):
    response = client.get(f"/api/v1/test-runs/{run['id']}/statistics", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def test_signature_statistics_counts_full_cohort_and_hold_answers(client, event_payload, monkeypatch):
    run = fixture_run(client, event_payload)
    def no_decrypt(_):
        raise AssertionError("Statistics must not decrypt raw input")
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", no_decrypt)
    result = stats(client, run, limit=1, offset=0)
    assert result["total_groups"] == 3 and len(result["items"]) == 1
    sql = result["items"][0]
    assert sql["value"] == "SQL" and sql["cases"] == sql["comparable"] == 4
    assert sql["matches"] == 2 and sql["accuracy"] == .5
    assert sql["verdict_rates"] == {"true_positive": .5, "false_positive": .25, "inconclusive": .25}
    assert sql["false_negatives"] == sql["false_positives"] == 1
    assert result["summary"]["cases"] == 8 and result["summary"]["comparable"] == 5
    assert result["summary"]["accuracy"] == .4
    assert result["rejected"] == result["duplicates"] == 1
    missing = next(row for row in stats(client, run)["items"] if row["value"] is None)
    assert missing["accuracy"] is None and missing["failed"] == 1


def test_statistics_scope_sort_missing_group_and_drilldown(client, event_payload):
    run = fixture_run(client, event_payload)
    scoped = stats(client, run, difficulty="hard")
    assert scoped["summary"]["cases"] == 4 and scoped["total_groups"] == 1
    assert stats(client, run, q="S%") ["total_groups"] == 0
    assert stats(client, run, q="SQL")["total_groups"] == 1
    ordered = stats(client, run, sort_by="accuracy", sort_order="asc")["items"]
    assert [row["value"] for row in ordered] == ["XSS", "SQL", None]
    detail = client.get(f"/api/v1/test-runs/{run['id']}", params={"group_field": "signature", "group_value": "SQL"}).json()
    assert detail["total_items"] == 4  # The same unique cases as the statistics group.
    assert detail["evaluation_summary"]["total"] == 8
    missing = client.get(f"/api/v1/test-runs/{run['id']}", params={"group_field": "signature", "group_missing": True}).json()
    assert missing["total_items"] == 3  # Rejected/duplicate admissions are not statistics cases.
    assert client.get(f"/api/v1/test-runs/{run['id']}/statistics", params={"group_by": "payload"}).status_code == 422
    assert client.get(f"/api/v1/test-runs/{run['id']}/statistics", params={"difficulty": "hard", "difficulty_missing": True}).status_code == 422


def combination_run(client, event_payload):
    run = fixture_run(client, event_payload)
    dimensions = [("Company A", "D"), ("Company A", "D"), ("Company A", "A"), ("Company B", "D"),
                  ("Company B", "D"), ("Company A", "D"), ("Company A", "A"), ("Company B", "D")]
    with client.app.state.session_factory() as db:
        for item, (company, action) in zip(run["items"], dimensions):
            row = db.get(Analysis, item["analysis_id"])
            row.company_name, row.waf_action = company, action
        db.get(Analysis, run["items"][6]["analysis_id"]).signature = ""
        db.commit()
    return run


def test_two_and_three_field_statistics_partition_cases_without_changing_totals(client, event_payload, monkeypatch):
    run = combination_run(client, event_payload)
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", lambda _: pytest.fail("Must not decrypt input"))
    single = stats(client, run)
    for fields, groups in [("company_name,signature", 5), ("company_name,signature,waf_action", 7)]:
        result = stats(client, run, group_by=fields)
        assert result["group_fields"] == fields.split(",") and result["total_groups"] == groups
        assert result["summary"] == single["summary"]
        assert sum(row["cases"] for row in result["items"]) == 8
        assert sum(row["comparable"] for row in result["items"]) == 5
        assert all(list(row["values"]) == fields.split(",") for row in result["items"])
    group = stats(client, run, group_by="company_name,signature,waf_action")["items"][0]
    assert group["values"] == {"company_name": "Company A", "signature": "SQL", "waf_action": "D"}
    assert group["cases"] == group["comparable"] == 2 and group["accuracy"] == .5
    assert stats(client, run, group_by="company_name,signature", q="Company B")["total_groups"] == 3
    assert stats(client, run, group_by="company_name,signature", q="XSS")["total_groups"] == 1
    assert stats(client, run, group_by="company_name,signature", q="Company %")["total_groups"] == 0
    scoped = stats(client, run, group_by="company_name,signature,waf_action", difficulty="hard")
    assert scoped["summary"]["cases"] == 4 and scoped["total_groups"] == 3


def test_combination_drilldown_intersects_all_values_and_missing_dimension(client, event_payload):
    run = combination_run(client, event_payload)
    result = stats(client, run, group_by="company_name,signature,waf_action")
    for group in result["items"]:
        criteria = [{"field": field, "value": value} for field, value in group["values"].items()]
        response = client.get(f"/api/v1/test-runs/{run['id']}", params={"group_filters": json.dumps(criteria)})
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["total_items"] == group["cases"]
        assert detail["evaluation_summary"]["total"] == 8
        assert all(item["ingest_status"] == "accepted" for item in detail["items"])


def test_combination_tuple_sort_page_and_literal_values_do_not_collide(client, event_payload):
    run = combination_run(client, event_payload)
    with client.app.state.session_factory() as db:
        for item, company, signature in [(run["items"][0], "A · B", "C"), (run["items"][1], "A", "B · C"),
                                         (run["items"][2], "A", "미입력")]:
            row = db.get(Analysis, item["analysis_id"])
            row.company_name, row.signature = company, signature
        db.commit()
    query = {"group_by": "company_name,signature", "sort_by": "value", "sort_order": "asc"}
    all_rows = stats(client, run, **query)["items"]
    assert len(all_rows) == 7
    paged = [stats(client, run, **query, limit=1, offset=i)["items"][0] for i in range(len(all_rows))]
    assert paged == all_rows
    assert all_rows[0]["values"] == {"company_name": "A", "signature": "B · C"}
    assert {"company_name": "A · B", "signature": "C"} in [row["values"] for row in all_rows]
    assert {"company_name": "A", "signature": "미입력"} in [row["values"] for row in all_rows]
    descending = stats(client, run, **{**query, "sort_order": "desc"})["items"]
    assert [row["values"]["signature"] for row in descending if row["value"] == "A"] == ["미입력", "B · C"]


@pytest.mark.parametrize("fields", ["", "signature,", "signature,signature", "signature,company_name,waf_action,waf_vendor", "payload", "signature,payload"])
def test_statistics_rejects_invalid_group_combinations(client, event_payload, fields):
    run = fixture_run(client, event_payload)
    assert client.get(f"/api/v1/test-runs/{run['id']}/statistics", params={"group_by": fields}).status_code == 422
    assert client.get(f"/api/v1/test-runs/{run['id']}/statistics", params=[("group_by", "signature"), ("group_by", "company_name")]).status_code == 422


@pytest.mark.parametrize("criteria", ["not json", "[]", "{}", '[{"field":"signature"}]',
    '[{"field":"payload","value":"x"}]', '[{"field":"signature","value":true}]',
    '[{"field":"signature","value":""}]', '[{"field":"signature","value":"x","extra":true}]',
    json.dumps([{"field": "signature", "value": "x"}] * 2),
    json.dumps([{"field": field, "value": "x"} for field in ("signature", "company_name", "waf_action", "waf_vendor")]),
    json.dumps([{"field": "signature", "value": "x" * 4097}])])
def test_combination_drilldown_rejects_malformed_or_conflicting_filters(client, event_payload, criteria):
    run = fixture_run(client, event_payload)
    response = client.get(f"/api/v1/test-runs/{run['id']}", params={"group_filters": criteria})
    assert response.status_code == 422 and response.json()["detail"] == "invalid_test_group_filter"


def test_combination_preserves_saved_reference_basis_and_rejects_legacy_filter_conflict(client, event_payload):
    run = combination_run(client, event_payload)
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][1]["analysis_id"])
        db.add(AnalysisLabel(analysis_id=row.id, revision=2, verdict="true_positive", source_kind="reference",
            source_ref="synthetic-correction", ai_visible=False, created_by="fixture", attachment_id=str(uuid.uuid4()), token_digest="b" * 64))
        db.commit()
    assert stats(client, run, group_by="company_name,signature,waf_action", reference_basis="latest")["items"][0]["accuracy"] == 1
    assert stats(client, run, group_by="company_name,signature,waf_action", evaluation_id=saved["id"])["items"][0]["accuracy"] == .5
    criteria = json.dumps([{"field": "signature", "value": "SQL"}, {"field": "company_name", "value": "Company A"}])
    detail = client.get(f"/api/v1/test-runs/{run['id']}", params={"group_filters": criteria, "evaluation_id": saved["id"]})
    assert detail.status_code == 200 and detail.json()["total_items"] == 3
    for legacy in [{"group_field": "signature", "group_value": "SQL"}, {"group_missing": True}, {"group_value": "SQL"}]:
        response = client.get(f"/api/v1/test-runs/{run['id']}", params={"group_filters": criteria, **legacy})
        assert response.status_code == 422 and response.json()["detail"] == "conflicting_test_group_filter"


def test_reference_corrections_and_saved_evaluation_use_same_basis_as_detail(client, event_payload):
    run = fixture_run(client, event_payload)
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][1]["analysis_id"])
        db.add(AnalysisLabel(analysis_id=row.id, revision=2, verdict="true_positive", source_kind="reference",
            source_ref="synthetic-correction", ai_visible=False, created_by="fixture", attachment_id=str(uuid.uuid4()), token_digest="a" * 64))
        db.commit()
    assert stats(client, run, reference_basis="latest")["summary"]["matches"] == 3
    assert stats(client, run, reference_basis="initial")["summary"]["matches"] == 2
    assert stats(client, run, evaluation_id=saved["id"])["summary"]["matches"] == 2
    assert client.get(f"/api/v1/test-runs/{run['id']}/statistics", params={"evaluation_id": str(uuid.uuid4())}).status_code == 404


def workbook(response):
    assert response.status_code == 200, response.text if response.status_code != 200 else ""
    archive = ZipFile(BytesIO(response.content))
    sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.findall("s:sheetData/s:row", NS):
        rows.append([cell.findtext("s:v", namespaces=NS) if cell.attrib["t"] == "n" else cell.findtext("s:is/s:t", default="", namespaces=NS)
                     for cell in row.findall("s:c", NS)])
    return archive, sheet, [dict(zip(rows[0], values)) for values in rows[1:]]


def test_excel_exports_every_row_all_input_and_three_evidence_columns(client, event_payload):
    run = fixture_run(client, event_payload)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.result_json = {**row.result_json, "analyst_assessment": {"version": "analyst-assessment-v1", "decision_issues": [],
            "evidence": [{"evidence_id": f"{support}-{i}", "supports": support, "field": "payload", "excerpt": f"fixture-{i}",
                          "interpretation_ko": f"근거 설명 {i}"} for support in ("true_positive", "false_positive", "context") for i in range(7)]}}
        db.commit()
    response = client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx")
    archive, sheet, rows = workbook(response)
    assert len(rows) == 10
    first = rows[0]
    assert first["payload"].replace("_x000D_", "\r") == event_payload["payload"]
    assert json.loads(first["extra_fields.vendor_data"]) == {"fixture": True}
    assert first["verdict"] == "true_positive" and first["severity"] == "HIGH"
    assert first["expected_verdict"] == "true_positive"
    for name in EVIDENCE_COLUMNS:
        assert len(first[name].split("\n---\n")) == 5 and "근거 설명 4" in first[name] and "근거 설명 5" not in first[name]
    assert rows[-1]["status"] == "rejected" and rows[-1]["payload"] == ""
    assert sheet.find("s:autoFilter", NS) is not None
    assert sheet.find("s:sheetViews/s:sheetView/s:pane", NS).attrib["state"] == "frozen"
    assert response.headers["cache-control"] == "no-store"
    assert not any(b"<f>" in archive.read(name) for name in archive.namelist())
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "export_test_run_xlsx", AccessAudit.resource_id == run["id"]))
    assert client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx", params={"limit": 1}).status_code == 422


def test_long_payload_is_preserved_across_excel_cells_and_formula_stays_text(client, event_payload):
    run = fixture_run(client, event_payload)
    payload = "=HYPERLINK(\"https://fixture.invalid\")\r\n" + "가😀" * 18000 + "_x000D_ END"
    with client.app.state.session_factory() as db:
        db.get(Analysis, run["items"][0]["analysis_id"]).payload_ciphertext = client.app.state.crypto.encrypt_text(payload)
        db.commit()
    archive, sheet, rows = workbook(client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx"))
    first = rows[0]
    def decode(value):
        return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda match: chr(int(match[1], 16)), value)
    restored = "".join(decode(value) for key, value in first.items() if key == "payload" or key.startswith("payload__part_"))
    assert restored == payload
    assert "payload__part_2" in first
    assert all(len(decode(value).encode("utf-16-le")) // 2 <= 32700 for key, value in first.items() if key.startswith("payload"))
    assert not sheet.findall(".//s:f", NS)


def test_legacy_unclassified_evidence_is_reference_only():
    result = evidence_columns({"evidence": [{"field": "payload", "excerpt": "fixture", "interpretation_ko": "이전 설명"}]})
    assert result[EVIDENCE_COLUMNS[0]] == result[EVIDENCE_COLUMNS[1]] == ""
    assert "이전 설명" in result[EVIDENCE_COLUMNS[2]]


def test_retry_statistics_export_and_saved_record_count_each_case_once(client, event_payload, registered_vllm_target):
    from test_test_retries import fixture_run as failed_run, finish
    from test_analysis_retries_keys import retry
    run, _ = failed_run(client, event_payload, count=1)
    original = run["items"][0]["analysis_id"]
    assert stats(client, run)["summary"]["failed"] == 1
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    response = retry(client, original)
    assert response.status_code == 202, response.text
    child = response.json()["analysis_id"]
    finish(client, child, "true_positive")
    summary = stats(client, run)["summary"]
    assert summary["cases"] == summary["completed"] == summary["matches"] == 1 and summary["failed"] == 0
    assert stats(client, run, evaluation_id=saved["id"])["summary"]["failed"] == 1
    assert stats(client, run, group_by="company_name,signature,waf_action")["summary"] == summary
    assert stats(client, run, group_by="company_name,signature,waf_action", evaluation_id=saved["id"])["summary"]["failed"] == 1
    _, _, rows = workbook(client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx"))
    assert len(rows) == 1 and rows[0]["analysis_id"] == child and rows[0]["retry_count"] == "1"
    _, _, frozen = workbook(client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx", params={"evaluation_id": saved["id"]}))
    assert len(frozen) == 1 and frozen[0]["analysis_id"] == original and frozen[0]["status"] == "failed"


def test_export_decryption_failure_is_safe_and_releases_export_slot(client, event_payload):
    run = fixture_run(client, event_payload)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        original = row.payload_ciphertext
        row.payload_ciphertext = "unreadable encrypted fixture"
        db.commit()
    response = client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx")
    assert response.status_code == 503 and response.json()["detail"] == "test_export_unavailable"
    assert "unreadable" not in response.text
    with client.app.state.session_factory() as db:
        db.get(Analysis, run["items"][0]["analysis_id"]).payload_ciphertext = original
        db.commit()
    assert client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx").status_code == 200


def test_export_limit_rejects_whole_file_and_large_integer_remains_exact(client, event_payload, monkeypatch):
    from app.services import test_run_exports as exports
    run = fixture_run(client, event_payload)
    with monkeypatch.context() as patch:
        patch.setattr(exports, "MAX_SOURCE_BYTES", 1)
        response = client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx")
        assert response.status_code == 413 and response.json()["detail"] == "test_export_too_large"
    number = 10 ** 100 + 1
    with client.app.state.session_factory() as db:
        db.get(Analysis, run["items"][0]["analysis_id"]).extra_fields = {"large_integer": number}
        db.commit()
    _, _, rows = workbook(client.get(f"/api/v1/test-runs/{run['id']}/export.xlsx"))
    assert rows[0]["extra_fields.large_integer"] == str(number)


def test_empty_run_has_valid_empty_statistics_and_excel_headers(client):
    from app.services.test_runs import create_run_record
    login(client)
    with client.app.state.session_factory() as db:
        row, _ = create_run_record(db, client.app.state.crypto, client.app.state.settings,
            name="Empty synthetic session", idempotency_key=str(uuid.uuid4()), request_hash="a" * 64, kind="api", actor="fixture")
        identifier = row.id
        db.commit()
    run = {"id": identifier}
    result = stats(client, run)
    assert result["total_groups"] == result["summary"]["cases"] == 0 and result["summary"]["accuracy"] is None
    archive, sheet, rows = workbook(client.get(f"/api/v1/test-runs/{identifier}/export.xlsx"))
    assert rows == [] and sheet.find("s:autoFilter", NS).attrib["ref"].startswith("A1:")


@pytest.mark.parametrize("suffix", ["statistics", "export.xlsx"])
def test_statistics_and_raw_export_are_admin_only(client, event_payload, service_headers, suffix):
    run = fixture_run(client, event_payload)
    client.cookies.clear()
    assert client.get(f"/api/v1/test-runs/{run['id']}/{suffix}").status_code == 401
    assert client.get(f"/api/v1/test-runs/{run['id']}/{suffix}", headers=service_headers).status_code == 403
    login(client)
    assert client.get(f"/api/v1/test-runs/{uuid.uuid4()}/{suffix}").status_code == 404
