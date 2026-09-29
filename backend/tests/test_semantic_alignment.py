"""Offline semantic presentation regressions; no result/policy migration."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.agent.contracts import WAFAnalysisOutput
from app.agent.policy import finalize_with_verifier
from app.models import ValidationDatasetWorkingItem as Item
from app.services.analysis_exports import build_report
from app.services.decision_semantics import SIGNATURE_SUMMARIES, evidence_sections
from test_agent_policy import output
from test_analysis_exports import detail_fixture, report_text
from test_validation_data import login, create_dataset
from test_ground_truth_working import save, draft, publish
from test_r5_contract import finished_run


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
@pytest.mark.parametrize("action", ["D", "A"])
def test_waf_observation_followup_export_does_not_change_final_verdict(verdict, action):
    detail = detail_fixture()
    detail["waf_action"] = action
    detail["result"]["verdict"] = verdict
    detail["result"]["analyst_guidance"]["checks"] = [{"source_ko": "서비스 기록", "check_ko": "요청 값의 처리 범위", "why_ko": "입력의 처리 문맥을 대조합니다."}]
    before = deepcopy(detail)
    report = build_report(detail)
    assert report.sections[-1].title == ("판정에 필요한 확인" if verdict == "inconclusive" else "영향·대응 확인")
    assert ("차단으로 기록됐습니다" if action == "D" else "허용으로 기록됐습니다") in report_text(report)
    assert "공격 시도가 있었는지 확인" not in report_text(report)
    assert detail == before


@pytest.mark.parametrize("relation", ["exact", "partial", "mismatch", "unknown"])
def test_legacy_signature_remains_parseable_and_original_is_not_authoritative_summary(relation):
    saved = output(relation=relation).model_dump(mode="json")
    saved.pop("analyst_checks")
    saved["signature_assessment"]["explanation_ko"] = "명확히 일치합니다."
    before = deepcopy(saved)
    assert WAFAnalysisOutput.model_validate(saved).verdict.value == "true_positive"
    detail = detail_fixture()
    detail["result"] = saved
    report = build_report(detail)
    rows = next(s.rows for s in report.sections if s.title == "탐지 내용 검토")
    assert ("관계 설명", SIGNATURE_SUMMARIES[relation]) in rows
    assert ("기존 분석 설명", "명확히 일치합니다.") in rows
    assert saved == before


def test_disagreement_keeps_policy_and_both_evidence_sides_not_primary_canonical_analysis():
    primary, verifier = output(), output("false_positive")
    combined = finalize_with_verifier(primary, verifier, ["low_confidence"])
    assert combined.output.verdict.value == "inconclusive"
    detail = detail_fixture()
    detail["result"] = combined.output.model_dump(mode="json")
    detail["result"].update(primary=primary.model_dump(mode="json"), verifier={"output": verifier.model_dump(mode="json")},
        diagnostics={"inconclusive_reasons": ["verdict_disagreement"]},
        analyst_assessment={"version": "analyst-assessment-v1", "evidence": [
            {"evidence_id": "e1", "field": "payload", "excerpt": "example", "interpretation_ko": "공격 문법으로 해석", "supports": "true_positive"},
            {"evidence_id": "e2", "field": "payload", "excerpt": "example", "interpretation_ko": "정상 데이터로 해석", "supports": "false_positive"}], "decision_issues": []})
    before = deepcopy(detail)
    report = build_report(detail)
    text = report_text(report)
    assert "자동 분석 해석이 서로 달랐습니다." in text
    assert ("Primary 판정", "정탐") in next(s.rows for s in report.sections if s.title == "세부 분석")
    assert ("Verifier 판정", "오탐") in next(s.rows for s in report.sections if s.title == "세부 분석")
    assert primary.threat_analysis.technique_ko not in text
    assert {"공격 해석", "정상 해석"} <= {s.title for s in report.sections}
    assert "공격 문법으로 해석" in text and "정상 데이터로 해석" in text
    assert detail == before


@pytest.mark.parametrize("verdict,first", [("true_positive", "true_positive"), ("false_positive", "false_positive"), ("inconclusive", "true_positive")])
def test_evidence_order_is_display_only(verdict, first):
    assert evidence_sections(verdict)[0][0] == first
    assert {side for side, _ in evidence_sections(verdict)} == {"true_positive", "false_positive", "context", "unclassified"}


@pytest.mark.parametrize("verdict,label", [("true_positive", "정탐"), ("false_positive", "오탐")])
def test_export_hold_reference_direction_respects_server_evaluation_eligibility(verdict, label):
    detail = detail_fixture()
    detail["result"]["verdict"] = verdict
    detail["evaluation"] = {"outcome": "expected_abstention_mismatch", "reference_label": {"verdict": "inconclusive"}}
    assert f"보류 답안 → {label} 확정" in report_text(build_report(detail))
    detail["evaluation"]["outcome"] = "unknown_provenance"
    assert "보류 답안의 확정 방향" not in report_text(build_report(detail))


def test_reference_origin_filters_the_same_value_shown_in_rows_without_changing_legacy_source(client, event_payload, monkeypatch):
    login(client)
    dataset = create_dataset(client)
    origins = ["manual", "reference_label", "published_ground_truth", "none"]
    ids = []
    for index, origin in enumerate(origins):
        response = save(client, dataset, {**event_payload, "signature": f"case-{index}"}, verdict=None if origin == "none" else "true_positive")
        assert response.status_code == 201
        ids.append(response.json()["item"]["item_id"])
    with client.app.state.session_factory() as db:
        for identifier, origin in zip(ids, origins):
            row = db.scalar(select(Item).where(Item.item_id == identifier))
            row.reference_origin, row.source_kind = origin, "synthetic_expected"
        db.commit()
    assert publish(client, dataset, ack=True).status_code == 201
    revision = draft(client, dataset)["working_revision"]
    assert client.post(f"/api/v1/validation-datasets/{dataset['id']}/working/bulk", json={"expected_working_revision": revision, "action": "delete", "case_ids": [ids[2]]}).status_code == 200
    # All four origins, including removed published metadata, use SQL-only filtering.
    monkeypatch.setattr(client.app.state.crypto, "decrypt_text", lambda *_: pytest.fail("search decrypted raw data"))
    path = f"/api/v1/validation-datasets/{dataset['id']}/working/search"
    for identifier, origin in zip(ids, origins):
        response = client.post(path, json={"reference_origin": origin, "limit": 1})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["filtered_total"] == 1 and body["items"][0]["item_id"] == identifier
        assert body["items"][0]["reference_origin"] == origin
        assert body["items"][0]["source_kind"] == "synthetic_expected"
    assert client.post(path, json={"reference_origin": "invented"}).status_code == 422
    assert client.post(path, json={"source_kind": "synthetic_expected"}).json()["filtered_total"] == 4
    assert client.post(path, json={"source_kind": "reference", "reference_origin": "manual"}).json()["filtered_total"] == 0


def test_preview_counts_remain_compatible_and_preview_does_not_add_cases(client, event_payload):
    run = finished_run(client, event_payload, expected=None)
    response = client.post(f"/api/v1/test-runs/{run['id']}/ground-truth-import/preview", json={"target": "create_new_dataset"})
    assert response.status_code == 200
    preview = response.json()
    assert preview["new_count"] == preview["missing_reference_count"] == 1
    exclusive_new = preview["new_count"] - preview["missing_reference_count"]
    assert sum([exclusive_new, preview["missing_reference_count"], preview["duplicate_count"], preview["reference_conflict_count"], preview["unavailable_count"]]) == preview["source_total"]
    with client.app.state.session_factory() as db:
        assert list(db.scalars(select(Item))) == []
