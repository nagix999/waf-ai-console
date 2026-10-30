"""Synthetic fixtures for the closed result and explicit service transition."""
from copy import deepcopy
import os

import pytest
from pydantic import ValidationError

from app.models import Analysis
from app.result_contracts import AnalysisResultV3, legacy_result_projection, validate_new_result
from app.schemas import AnalysisDetail
from app.services.analysis_exports import build_report
from app.services.analyst_presentation import assessment_view
from app.services.test_run_exports import evidence_columns, selected_columns
from app.services.test_export_columns import column_catalog


def result_fixture():
    evidence = {"evidence_id": "e1", "field": "payload.query", "excerpt": "q=sample",
                "interpretation_ko": "검색어가 쿼리 항목에 기록되어 있습니다.", "supports": "normal_request",
                "origins": [{"role": "primary", "index": 0, "source_id": "c1"}],
                "source_spans": [{"start": 5, "end": 13}]}
    threat = {"severity": "NONE", "category": "request_data", "target": "payload.query",
              "technique_ko": "검색어 입력을 전달하는 형태입니다.", "potential_impact_ko": "실제 처리 결과는 기록되지 않았습니다.", "obfuscations": []}
    signature = {"version": "signature-assessment-v2", "relation": "mismatch", "explanation_ko": None,
                 "matched_points": [], "mismatched_points": ["탐지 설명과 다른 데이터 입력입니다."], "uncertainty_ko": None}
    core = {"verdict": "false_positive", "confidence_score": .9, "summary_ko": "쿼리 항목에 검색어가 전달되어 있습니다.",
            "threat_analysis": threat, "signature_assessment": signature, "input_truncated": False,
            "interpretation_conflict": False}
    profile = {"model_profile_id": "synthetic-profile", "model_profile": "Synthetic", "model_name": "synthetic",
               "llm_provider": "vllm", "profile_fingerprint": "fixture-fingerprint", "external_data_approved": False}
    return {**core, "schema_version": "waf-analysis-v3", "evidence": [evidence], "decision_issues": [],
            "omitted_issue_count": 0, "primary": {**deepcopy(core), "evidence_ids": ["e1"], "decision_issues": []},
            "verifier": {"executed": False, "agreement": None, "reasons": [], "failure_id": None,
                         "error": None, "output": None, "framework_run_id": None},
            "analyst_guidance": {"summary_ko": core["summary_ko"], "limitations": []},
            "diagnostics": {"version": "evidence-selection-v1", "inconclusive_reasons": [], "input_signals": [],
                            "roles": {"primary": {"initial_model_verdict": "false_positive", "validated_verdict": "false_positive",
                                "grounding_rejected_count": 0, "grounding_downgraded": False,
                                "repair_attempted": False, "repair_recovered": False, "correction_requires_review": False,
                                "resolved_source_count": 1, "failure_category": None}}},
            "policy": {"policy_version_id": "synthetic-policy", "policy_version_number": 1, "policy_hash": "fixture",
                       "prompt_version": "waf-judgment-v2.15/policy-1", "fixed_rules_version": "waf-system-v2.15",
                       "fixed_rules_hash": "fixture", "instructions_hash": "fixture", "reserved_tokens": 4096,
                       "selection_origin": "enqueue", "verifier_confidence_threshold": .75,
                       "disagreement_or_failure_becomes_inconclusive": True, "decision_evidence_ids": ["e1"],
                       "verifier_policy_version": "security-analysis-policy-v1", "retired_verifier_triggers": ["tuning_recommended"]},
            "agent": {"role_profiles": {"primary": profile, "verifier": deepcopy(profile)}, "framework": "moduagent",
                      "input_schema": {"version_id": "synthetic-schema", "version_number": 1, "content_hash": "fixture",
                                       "selection_origin": "enqueue", "field_metadata_usage": "history_only",
                                       "field_count": 11, "field_metadata_sent_to_model": False},
                      "framework_version": "0.6.2", "execution": "standard", "tools": [], "preprocessors": [],
                      "llm_provider": "vllm", "model_name": "synthetic", "model_profile_id": "synthetic-profile",
                      "profile_fingerprint": "fixture-fingerprint", "external_data_approved": False, "thinking_enabled": False}}


@pytest.mark.parametrize("path", [(), ("agent",), ("agent", "input_schema"), ("policy",), ("primary",),
                                  ("diagnostics",), ("diagnostics", "roles", "primary"), ("evidence", 0),
                                  ("evidence", 0, "origins", 0), ("evidence", 0, "source_spans", 0)])
def test_new_result_rejects_unknown_fields_at_every_layer(path):
    data = result_fixture()
    target = data
    for key in path:
        target = target[key]
    target["unexpected"] = "synthetic"
    with pytest.raises(ValidationError):
        validate_new_result(data)


def test_new_result_nulls_and_reference_integrity():
    data = validate_new_result(result_fixture())
    assert data["verifier"]["output"] is None
    assert data["signature_assessment"]["explanation_ko"] is None
    assert validate_new_result(data) == data
    for mutate in (lambda x: x["primary"]["evidence_ids"].append("missing"),
                   lambda x: x["evidence"].append(deepcopy(x["evidence"][0])),
                   lambda x: x["policy"].update(decision_evidence_ids=[]),
                   lambda x: x["evidence"][0].update(supports="true_positive"),
                   lambda x: x["evidence"][0]["origins"][0].update(role="verifier")):
        invalid = deepcopy(data)
        mutate(invalid)
        with pytest.raises(ValidationError):
            validate_new_result(invalid)


def test_v3_cannot_fall_through_legacy_dict_union():
    # field validation must fail even without unrelated AnalysisSummary fields.
    data = result_fixture()
    data["unknown"] = True
    with pytest.raises(ValidationError) as error:
        AnalysisDetail.model_validate({"result": data})
    assert any(item["loc"][:1] == ("result",) for item in error.value.errors())
    schema = AnalysisResultV3.model_json_schema()
    assert schema["additionalProperties"] is False
    assert all(node.get("additionalProperties") is False for node in schema["$defs"].values() if node.get("type") == "object")


def test_shared_evidence_requires_both_executed_role_references():
    data = result_fixture()
    data["evidence"][0]["origins"].append({"role": "verifier", "index": 0, "source_id": "c1"})
    data["verifier"].update(executed=True, agreement=True, output=deepcopy(data["primary"]))
    assert validate_new_result(data)["verifier"]["output"]["evidence_ids"] == ["e1"]
    for role in ("primary", "verifier"):
        invalid = deepcopy(data)
        record = invalid["primary"] if role == "primary" else invalid["verifier"]["output"]
        record["evidence_ids"] = []
        with pytest.raises(ValidationError, match="result_role_evidence_reference_invalid"):
            validate_new_result(invalid)
    invalid = deepcopy(data)
    invalid["verifier"]["executed"] = False
    with pytest.raises(ValidationError, match="result_unexecuted_verifier_output"):
        validate_new_result(invalid)


def test_decision_issue_cannot_claim_evidence_from_a_different_role():
    data = result_fixture()
    data["decision_issues"] = [{"point_ko": "입력의 쓰임을 구분해야 합니다.", "evidence_ids": ["e1"],
                                "missing_condition_ko": "처리 경로를 확인하세요.", "origins": ["verifier"]}]
    with pytest.raises(ValidationError, match="result_issue_origin_invalid"):
        validate_new_result(data)


def test_explicit_compatibility_projection_preserves_source_and_saved_result():
    data = result_fixture()
    original = deepcopy(data)
    old = legacy_result_projection(data)
    assert data == original
    assert old["schema_version"] == "waf-analysis-v2"
    assert old["evidence"] == [{key: data["evidence"][0][key] for key in ("field", "excerpt", "interpretation_ko")}]
    assert old["analyst_assessment"]["evidence"][0]["supports"] == "false_positive"
    assert old["tuning_recommendation"]["recommended"] is False
    assert old["analyst_checks"] == []


def test_new_and_legacy_display_same_fixed_classification_without_rewrites():
    data = result_fixture()
    assert assessment_view(data) == assessment_view(legacy_result_projection(data))
    cols = evidence_columns(data)
    assert cols["evidence_normal_request"]
    assert not cols["evidence_attack_request"]
    catalog = column_catalog({})
    assert selected_columns(catalog, ["evidence_false_positive"])[0]["name"] == "evidence_normal_request"
    with pytest.raises(ValueError):
        selected_columns(catalog, ["evidence_false_positive", "evidence_normal_request"])


def test_held_report_retains_observation_summary_and_separate_reason():
    result = result_fixture()
    result.update(verdict="inconclusive", threat_analysis={**result["threat_analysis"], "severity": "UNKNOWN"})
    result["diagnostics"]["inconclusive_reasons"] = ["verifier_failed"]
    result["verifier"].update(executed=True, agreement=False, error="synthetic_failure")
    detail = {"id": "synthetic", "status": "completed", "result": result}
    report = build_report(detail)
    summary = report.sections[0]
    assert ("요약", result["summary_ko"]) in summary.rows
    assert any(section.title == "최종 판정 이유" for section in report.sections)
    assert any(section.title == "정상 요청 근거" for section in report.sections)
    assert not any(section.title == "WAF 정책 검토" for section in report.sections)


def test_pdf_formatter_keeps_only_canonical_issues_and_diagnostics():
    from app.services.report_pdf import pdf_document
    result = result_fixture()
    result.update(verdict="inconclusive", threat_analysis={**result["threat_analysis"], "severity": "UNKNOWN"},
                  decision_issues=[{"point_ko": "입력 값의 처리 경로를 구분해야 합니다.", "evidence_ids": ["e1"],
                                    "missing_condition_ko": "값의 사용 범위를 확인하세요.", "origins": ["primary"]}])
    result["diagnostics"]["inconclusive_reasons"] = ["primary_model_inconclusive"]
    result["primary"]["summary_ko"] = "PRIVATE-ROLE-SNAPSHOT"
    document = pdf_document({"id": "synthetic", "status": "completed", "result": result})
    assert document["detail"]["result"]["decision_issues"] == result["decision_issues"]
    assert "primary" not in document["detail"]["result"]
    assert "PRIVATE-ROLE-SNAPSHOT" not in str(document)


@pytest.mark.parametrize("purpose", ["production", "test"])
def test_analysis_get_defaults_v3_and_explicit_v2_preserves_saved_result(client, event_payload, service_headers, purpose):
    if purpose == "test":
        from app.api_key_schemas import ServiceApiKeyCreate
        from app.services.service_api_keys import issue_key
        with client.app.state.session_factory() as db:
            _, raw = issue_key(db, ServiceApiKeyCreate(name="Synthetic v3 Test", source_system="synthetic-v3-test",
                purpose="test", scopes=["ingest"]), "synthetic-fixture")
            db.commit()
        service_headers = {"x-api-key": raw}
    response = client.post("/api/v1/analyses", json=event_payload, headers=service_headers)
    assert response.status_code == 202
    identifier = response.json()["id"]
    canonical = result_fixture()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.result_json, row.status, row.verdict = canonical, "completed", "false_positive"
        db.commit()
    for query in ("", "?result_version=v3"):
        new = client.get(f"/api/v1/analyses/{identifier}{query}", headers=service_headers)
        assert new.status_code == 200
        # FastAPI includes declared optional nulls in its response model.
        assert AnalysisResultV3.model_validate(new.json()["result"]).model_dump(mode="json") == (
            AnalysisResultV3.model_validate(canonical).model_dump(mode="json"))
    old = client.get(f"/api/v1/analyses/{identifier}?result_version=v2", headers=service_headers)
    assert old.status_code == 200
    assert old.json()["result"]["schema_version"] == "waf-analysis-v2"
    if purpose == "production":
        # Changing GET does not implicitly change the POST compatibility default.
        posted = client.post("/api/v1/analyses", json=event_payload, headers=service_headers)
        assert posted.status_code == 200
        assert posted.json()["result"]["schema_version"] == "waf-analysis-v2"
        posted_v3 = client.post("/api/v1/analyses?result_version=v3", json=event_payload, headers=service_headers)
        assert posted_v3.status_code == 200
        assert posted_v3.json()["result"]["schema_version"] == "waf-analysis-v3"
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200
    assert client.get(f"/api/v1/analyses/{identifier}").json()["result"]["schema_version"] == "waf-analysis-v3"
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).result_json == canonical


@pytest.mark.parametrize("version", ["waf-analysis-v1", "waf-analysis-v2"])
def test_analysis_get_keeps_historical_versions_for_every_response_selection(client, event_payload, service_headers, version):
    identifier = client.post("/api/v1/analyses", json=event_payload, headers=service_headers).json()["id"]
    historical = {"schema_version": version, "future": {"keep": True}}
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.result_json = "completed", historical
        db.commit()
    for query in ("", "?result_version=v3", "?result_version=v2"):
        response = client.get(f"/api/v1/analyses/{identifier}{query}", headers=service_headers)
        assert response.status_code == 200
        assert response.json()["result"] == historical
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).result_json == historical


def test_openapi_analysis_get_declares_v3_default_without_changing_post_default(client):
    paths = client.get("/openapi.json").json()["paths"]
    get_parameters = paths["/api/v1/analyses/{analysis_id}"]["get"]["parameters"]
    get_version = next(parameter for parameter in get_parameters if parameter["name"] == "result_version")
    assert get_version["schema"]["default"] == "v3"
    assert get_version["schema"]["enum"] == ["v2", "v3"]
    assert get_version["required"] is False
    post_parameters = paths["/api/v1/analyses"]["post"]["parameters"]
    post_version = next(parameter for parameter in post_parameters if parameter["name"] == "result_version")
    assert post_version["schema"].get("default") is None


def test_wait_polls_only_status_and_assembles_detail_once(client, event_payload, service_headers, monkeypatch):
    from app.api import analyses
    status_reads, detail_reads = [], []
    real_status, real_detail = analyses.fetch_analysis_status, analyses.fetch_analysis
    def status(db, identifier, scope):
        status_reads.append(scope)
        if len(status_reads) == 3:
            db.get(Analysis, identifier).status = "completed"
            db.commit()
        return real_status(db, identifier, scope)
    def detail(db, identifier, scope):
        detail_reads.append(scope)
        return real_detail(db, identifier, scope)
    async def sleep(_):
        return None
    monkeypatch.setattr(analyses, "fetch_analysis_status", status)
    monkeypatch.setattr(analyses, "fetch_analysis", detail)
    monkeypatch.setattr(analyses.asyncio, "sleep", sleep)
    response = client.post("/api/v1/analyses?wait_seconds=1", json=event_payload, headers=service_headers)
    assert response.status_code == 200
    assert len(status_reads) == 3 and len(detail_reads) == 1
    assert all(scope is not None for scope in [*status_reads, *detail_reads])


def test_invalid_new_stored_result_does_not_use_legacy_fallback_or_echo_payload(client, event_payload, service_headers):
    identifier = client.post("/api/v1/analyses", json=event_payload, headers=service_headers).json()["id"]
    invalid = result_fixture()
    invalid["not_declared"] = "SYNTHETIC-PRIVATE-MARKER"
    with client.app.state.session_factory() as db:
        db.get(Analysis, identifier).result_json = invalid
        db.commit()
    for query in ("", "?result_version=v3", "?result_version=v2"):
        response = client.get(f"/api/v1/analyses/{identifier}{query}", headers=service_headers)
        assert response.status_code == 503
        assert response.json() == {"detail": "analysis_result_invalid"}
        assert "SYNTHETIC-PRIVATE-MARKER" not in response.text


@pytest.mark.skipif(os.environ.get("WAF_TEST_REPORT_RENDERER") != "1", reason="Release-only local Node/ReportLab renderer")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_real_v3_pdf_uses_shared_formatter_without_browser(tmp_path, theme):
    from app.services.report_pdf import pdf_document, render_report_pdf
    data = result_fixture()
    extra = deepcopy(data["evidence"][0])
    extra.update(evidence_id="e2", supports="attack_request", interpretation_ko="같은 입력 값의 실행 조건을 확인하는 보조 해석입니다.")
    data["evidence"].append(extra)
    document = pdf_document({"id": "synthetic", "status": "completed", "result": data}, theme=theme)
    body = render_report_pdf(document)
    assert body.startswith(b"%PDF-") and body.rstrip().endswith(b"%%EOF")
    assert b"/ToUnicode" in body and b"/FontFile2" in body
    assert all(marker not in body for marker in (b"/URI", b"/JavaScript", b"/Launch"))
    (tmp_path / f"v3-{theme}.pdf").write_bytes(body)
