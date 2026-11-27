"""Grounding, source-only repair and decision review preserve mixed dependencies."""
from copy import deepcopy
import json

import httpx
import pytest

from app import worker_v238 as worker
from app.agent.contracts import AgentVerdict
from app.agent.evidence_relations_v238 import build_evidence_relations
from app.agent.security_analysis_v234 import canonical_evidence
from app.models import AgentRun, Analysis, VLLMProfile
from app.result_contracts import validate_new_result
from app.services.parser_engine import DEFAULT_OPTIONS
from app.services.parser_versions import create_version, pin_parser
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from app.services.worker_runtime_v238 import process_moduagent
from test_agent_openai import completion, install_http
from test_contracts_v238 import mixed
from test_source_support_worker_v230 import scenario, profile


pytestmark = pytest.mark.usefixtures("legacy_v238_admissions")


def case(*, forbidden_leaf=False, independent=False):
    data = scenario()
    value = data["value"]
    value["evidence"][0]["source_id"] = data["actual"]["source_id"]
    value["evidence"][1]["supports"] = "normal_request"
    if forbidden_leaf:
        value["evidence"][1]["source_id"] = data["reference"]["source_id"]
    if independent:
        value["evidence"].append({"evidence_id": "e1", "source_id": data["actual"]["source_id"],
            "interpretation_ko": "별도 요청 값은 페이지와 개수를 지정하는 조회 입력의 쓰임입니다.",
            "supports": "normal_request", "related_evidence_ids": []})
    return data


def run(data, provider="vllm"):
    return worker._execute_grounded_agent(analysis=None, inference_event=data["event"],
        raw_payload=data["raw"], parsed=data["parsed"], input_truncated=False,
        submitted_payload_spans=data["built"].retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        user_input=json.dumps(data["document"]), profile=profile(provider), api_key="synthetic-key",
        instructions="Synthetic independent analysis.", agent_name="waf-primary",
        session_id="synthetic:primary", egress_check=lambda: None,
        decision_review_char_budget=65536)


def respond(monkeypatch, documents):
    return install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(documents[min(count - 1, len(documents) - 1)]))))[0]


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_valid_mixed_relation_is_grounded_once_and_retains_role_origins(monkeypatch, provider):
    data = case()
    original = deepcopy(data["value"])
    requests = respond(monkeypatch, [original])
    call = run(data, provider)
    assert call.succeeded and len(requests) == 1
    assert call.output.verdict == AgentVerdict.true_positive
    assert call.grounding_history[0]["output"] == original
    assert call.grounding_history[0]["assessment"]["interpretation_conflict"] is False
    evidence, _, _ = canonical_evidence(call, None)
    assert [item["supports"] for item in evidence] == ["attack_request", "normal_request"]
    links = build_evidence_relations(call, evidence=evidence)
    assert links["version"] == "evidence-relations-v2"
    assert links["groups"] == [{"role": "primary", "representative_evidence_id": "e1",
        "related_evidence_ids": ["e2"]}]
    for item in evidence:
        assert all(origin["role"] == "primary" for origin in item["origins"])
    assert data["raw"] not in json.dumps(call.telemetry)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_source_only_repair_preserves_opposing_support_and_dependency(monkeypatch, provider):
    data = case(forbidden_leaf=True)
    original = deepcopy(data["value"])
    correction = {"corrections": [{"index": 1, "source_id": data["actual"]["source_id"]}],
        "requires_reanalysis": False}
    requests = respond(monkeypatch, [original, correction])
    call = run(data, provider)
    assert call.succeeded and len(requests) == 2
    assert call.output.verdict == AgentVerdict.true_positive
    assert call.grounding_history[0]["output"] == original
    assert call.grounding_history[0]["rejected_evidence"] == [
        {"index": 1, "reason": "signature_support_not_allowed"}]
    final = call.grounding_history[-1]
    assert final["rejected_evidence"] == []
    assert final["assessment"]["evidence"][1]["source_id"] == data["actual"]["source_id"]
    assert final["assessment"]["evidence"][1]["supports"] == "false_positive"
    assert final["assessment"]["evidence_relations"] == [{"representative_index": 0, "related_indexes": [1]}]
    evidence, _, _ = canonical_evidence(call, None)
    assert len(build_evidence_relations(call, evidence=evidence)["groups"]) == 1
    assert all(row["output_validation_retry"]["attempt_count"] == 1
               for row in call.telemetry["evidence_grounding_retry"]["attempts"])


def test_lost_opposing_dependency_drops_root_and_review_keeps_independent_evidence(monkeypatch):
    data = case(forbidden_leaf=True, independent=True)
    original = deepcopy(data["value"])
    review = {"retained_evidence_ids": ["e1"], "verdict": "false_positive", "confidence_score": .8,
        "summary_ko": "남은 요청 입력은 페이지 조회 범위를 지정하는 업무 데이터로 해석됩니다.",
        "threat_analysis": {"severity": "NONE", "category": "synthetic", "target": "payload",
            "technique_ko": "남은 요청 값의 페이지 조회 문맥을 검토했습니다.", "obfuscations": [],
            "potential_impact_ko": "실제 서버 처리 결과는 수신 자료만으로 확인되지 않았습니다."},
        "decision_issue": None, "interpretation_conflict": False}
    requests = respond(monkeypatch, [original, {"corrections": [], "requires_reanalysis": True}, review])
    call = run(data)
    assert call.succeeded and len(requests) == 3
    assert call.output.verdict == AgentVerdict.false_positive
    assert call.grounding_history[0]["output"] == original
    assert set(call.grounding_history[1]["excluded_relation_indexes"]) == {0, 1}
    assert call.grounding_history[-1]["decision_review_validated"] is True
    evidence, _, _ = canonical_evidence(call, None)
    assert len(evidence) == 1 and evidence[0]["supports"] == "normal_request"
    assert evidence[0]["origins"][0]["index"] == 0
    assert build_evidence_relations(call, evidence=evidence)["groups"] == []
    original_body = json.loads(requests[0].content)
    review_body = json.loads(requests[2].content)
    assert original_body["model"] == review_body["model"]
    assert original["summary_ko"] not in json.dumps(review_body)


def test_reviewed_surviving_mixed_group_reindexes_final_sources_without_resurrecting_old_claim(monkeypatch):
    data = case()
    bad = deepcopy(data["value"]["evidence"][0])
    bad.update(evidence_id="e1", source_id=data["reference"]["source_id"],
        interpretation_ko="SYNTHETIC_EXCLUDED_SIGNATURE_CLAIM", related_evidence_ids=[])
    root = deepcopy(data["value"]["evidence"][0])
    root.update(evidence_id="e3", related_evidence_ids=["e4"])
    dependency = deepcopy(data["value"]["evidence"][1])
    dependency.update(evidence_id="e4")
    data["value"]["evidence"] = [bad, root, dependency]
    original = deepcopy(data["value"])
    reviewed = {"retained_evidence_ids": ["e4", "e3"], "verdict": "true_positive", "confidence_score": .8,
        "summary_ko": "남은 입력 동작과 반대 정황을 연결하여 요청의 보안 의미를 검토했습니다.",
        "threat_analysis": deepcopy(data["value"]["threat_analysis"]), "decision_issue": None,
        "interpretation_conflict": False}
    requests = respond(monkeypatch, [original, {"corrections": [], "requires_reanalysis": True}, reviewed])
    call = run(data)
    assert call.succeeded and len(requests) == 3
    assert call.grounding_history[0]["output"] == original
    last = call.grounding_history[-1]
    assert last["decision_review_validated"] is True
    assert last["output"]["retained_evidence_ids"] == ["e4", "e3"]
    assert last["expanded_output"]["evidence_relations"] == [{"representative_index": 0, "related_indexes": [1]}]
    assert [entry["index"] for entry in last["source_resolutions"]] == [0, 1]
    evidence, _, _ = canonical_evidence(call, None)
    assert [item["supports"] for item in evidence] == ["attack_request", "normal_request"]
    assert [item["origins"][0]["index"] for item in evidence] == [0, 1]
    assert "SYNTHETIC_EXCLUDED_SIGNATURE_CLAIM" not in json.dumps(evidence)
    assert build_evidence_relations(call, evidence=evidence)["groups"] == [
        {"role": "primary", "representative_evidence_id": "e1", "related_evidence_ids": ["e2"]}]


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("waf_action", ["A", "D"])
def test_full_pinned_capture_candidate_and_public_v3_preserve_independent_mixed_dependencies(
        client, event_payload, service_headers, monkeypatch, caplog, waf_action):
    raw = "POST /synthetic/catalog HTTP/1.1/@/Host: example.invalid/@/Content-Type: application/x-www-form-urlencoded/@//@/page=1&size=20"
    event = {**event_payload, "payload": raw, "waf_vendor": "Synthetic Vendor", "waf_action": waf_action}
    expected_requests = 2 if waf_action == "A" else 1
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="Synthetic238", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="production"))
        row = db.get(Analysis, identifier)
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.38"
        row.prompt_version, row.prompt_policy_version_id = prompt.prompt_version, prompt.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(prompt.model_dump_json())
        parser = create_version(db, crypto, name="Synthetic explicit capture", engine_version="http-capture-v2",
            options={**DEFAULT_OPTIONS, "profiles": [{"name": "Synthetic capture", "waf_vendor": "Synthetic Vendor",
                **DEFAULT_OPTIONS, "field_separator": "slash_at_slash"}]})
        row.parser_version_id, row.parser_snapshot_json = None, None
        pin_parser(db, crypto, row, parser.id, selection_origin="candidate")
        db.commit()

    def user_document(request):
        return json.loads(next(message["content"] for message in json.loads(request.content)["messages"]
                               if message["role"] == "user"))

    def model(request, _count):
        document = user_document(request)
        assert "/@/" not in json.dumps(document)
        sources = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
        value = mixed()
        value["evidence"][0].update(source_id=sources[-1]["source_id"],
            interpretation_ko="요청 값의 처리 동작과 상반되는 정황을 함께 검토한 합성 보안 해석입니다.")
        value["evidence"][1]["source_id"] = sources[0]["source_id"]
        value["confidence_score"] = .99
        value["signature_assessment"].update(relation="exact", matched_points=["합성 요청 문맥과 탐지 설명을 비교했습니다."],
            mismatched_points=[], uncertainty_ko=None)
        return httpx.Response(200, json=completion(json.dumps(value)))

    requests, _ = install_http(monkeypatch, model)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        process_moduagent(db, crypto, row, "", .75)
        assert row.status == "completed"
        result = validate_new_result(row.result_json)
        assert result["schema_version"] == "waf-analysis-v3"
        assert result["agent"]["parser"]["engine_version"] == "http-capture-v2"
        assert result["verifier"]["executed"] is (waf_action == "A")
        assert result["verifier"]["agreement"] is (True if waf_action == "A" else None)
        assert result["verdict"] == "true_positive" and result["interpretation_conflict"] is False
        assert len(requests) == expected_requests
        if waf_action == "A":
            assert user_document(requests[0]) == user_document(requests[1])
        assert crypto.decrypt_text(row.payload_ciphertext) == raw
        assert {item["supports"] for item in result["evidence"]} == {"attack_request", "normal_request"}
        for item in result["evidence"]:
            for span in item["source_spans"]:
                assert raw[span["start"]:span["end"]] == item["excerpt"]
        run_record = db.query(AgentRun).filter_by(analysis_id=identifier).one()
        for step in run_record.steps:
            if step.step_type in {"llm_primary", "llm_verifier"}:
                output = json.loads(crypto.decrypt_text(step.output_ciphertext))
                assert output["grounding_history"][0]["output"]["evidence"][0]["related_evidence_ids"] == ["e5"]
                assert raw not in json.dumps(step.metadata_json)
            if step.step_type == "finalize":
                saved = json.loads(crypto.decrypt_text(step.output_ciphertext))
                assert saved["evidence_relations"]["version"] == "evidence-relations-v2"
                assert {group["role"] for group in saved["evidence_relations"]["groups"]} == (
                    {"primary", "verifier"} if waf_action == "A" else {"primary"})
        assert raw not in caplog.text
        db.commit()
    public = client.get(f"/api/v1/analyses/{identifier}?result_version=v3", headers=service_headers)
    assert public.status_code == 200, public.text
    assert public.json()["result"]["schema_version"] == "waf-analysis-v3"
    assert public.json()["result"]["verdict"] == "true_positive"
    assert {item["supports"] for item in public.json()["result"]["evidence"]} == {"attack_request", "normal_request"}
    assert "related_evidence_ids" not in public.text and "contributes_to_evidence_ids" not in public.text
    assert len(requests) == expected_requests
