"""Archived v237 ModuAgent with mocked HTTP and isolated synthetic provenance.

These checks exercise source repair, same-role review, v3 consumers and pinned
capture preprocessing. They do not measure actual model interpretation quality.
"""
from copy import deepcopy
import json

import httpx
import pytest

from app import worker_v237 as worker
from app.agent.input_builder_v237 import build_agent_input
from app.agent.evidence_relations_v237 import build_evidence_relations
from app.agent.security_analysis_v234 import canonical_evidence, decision_evidence_ids, role_result
from app.models import AgentRun, Analysis, VLLMProfile
from app.result_contracts import validate_new_result
from app.services.core_evidence_v3 import build_core_result
from app.services.http_parser import parse_http_payload
from app.services.parser_engine import DEFAULT_OPTIONS
from app.services.parser_versions import create_version, pin_parser
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from app.services.worker_runtime_v237 import process_moduagent
from test_agent_openai import completion, install_http
from test_contracts_v237 import wire
from test_result_contract_v3 import result_fixture
from test_source_support_worker_v230 import profile


pytestmark = pytest.mark.usefixtures("legacy_v237_admissions")


EXCLUDED = "SYNTHETIC_REMOVED_ROOT"
DEPENDENT = "SYNTHETIC_DEPENDENT_ROOT"
ORPHAN = "SYNTHETIC_ORPHAN_CONTRIBUTION"


def scenario():
    raw = "POST /synthetic/catalog HTTP/1.1\r\nHost: example.invalid\r\nContent-Type: application/x-www-form-urlencoded\r\n\r\npage=1&size=20"
    event = {"payload": raw, "waf_action": "D", "signature": "Synthetic detection"}
    parsed = parse_http_payload(raw)
    built = build_agent_input(event, raw, parsed, 32768, 3072, evidence_selection=True,
                             fixed_rules_version="waf-system-v2.37")
    document = json.loads(built.text)
    items = document["evidence_candidates"]["items"]
    payloads = [item for item in items if item["field"] == "payload"]
    body = next(item for item in payloads if "page=1&size=20" in item["excerpt"])
    signature = next(item for item in items if item["field"] == "signature")
    value = wire()
    value["evidence"][0].update(source_id=signature["source_id"], interpretation_ko=EXCLUDED)
    value["evidence"][1].update(source_id=payloads[0]["source_id"],
        interpretation_ko="요청 위치가 본문의 목록 조회 값이 쓰이는 위치를 설명합니다.")
    return dict(raw=raw, event=event, parsed=parsed, built=built, document=document,
                value=value, payloads=payloads, body=body)


def run(case, provider="vllm"):
    return worker._execute_grounded_agent(analysis=None, inference_event=case["event"],
        raw_payload=case["raw"], parsed=case["parsed"], input_truncated=False,
        submitted_payload_spans=case["built"].retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        user_input=json.dumps(case["document"]), profile=profile(provider), api_key="synthetic-key",
        instructions="Synthetic independent analysis.", agent_name="waf-primary", session_id="synthetic:primary",
        decision_review_char_budget=65536, egress_check=lambda: None)


def review(verdict="false_positive", retained=("e5", "e1")):
    value = wire()
    value.update(verdict=verdict, summary_ko="페이지와 개수 값으로 목록의 조회 범위를 전달합니다.",
                 confidence_score=.9, decision_issue=None, interpretation_conflict=False)
    value["threat_analysis"]["severity"] = {"false_positive": "NONE", "true_positive": "HIGH", "inconclusive": "UNKNOWN"}[verdict]
    return {"retained_evidence_ids": list(retained), **{key: value[key] for key in (
        "verdict", "summary_ko", "confidence_score", "decision_issue", "interpretation_conflict", "threat_analysis")}}


def install_responses(monkeypatch, documents):
    return install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(documents[min(count - 1, len(documents) - 1)]))))[0]


def user_document(request):
    return json.loads(next(message["content"] for message in json.loads(request.content)["messages"] if message["role"] == "user"))


def public_projection(call):
    evidence, issues, _ = canonical_evidence(call, None)
    links = build_evidence_relations(call, evidence=evidence)
    result = result_fixture()
    result.update(verdict=call.output.verdict.value, confidence_score=call.output.confidence_score,
        summary_ko=call.output.summary_ko, threat_analysis=call.output.threat_analysis.model_dump(mode="json"),
        evidence=evidence, decision_issues=issues, primary=role_result(call, "primary", evidence, issues))
    result["policy"]["decision_evidence_ids"] = decision_evidence_ids(call.output, evidence)
    result["diagnostics"]["roles"]["primary"]["validated_verdict"] = call.output.verdict.value
    canonical = validate_new_result(result)
    return build_core_result(canonical, relations=links)["result"], links


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_source_only_repair_preserves_native_memberships_without_a_new_model_analysis(monkeypatch, provider):
    case = scenario()
    initial = deepcopy(case["value"])
    requests = install_responses(monkeypatch, [initial, {"corrections": [{"index": 0,
        "source_id": case["body"]["source_id"]}], "requires_reanalysis": False}])
    call = run(case, provider)
    assert call.succeeded and len(requests) == 2
    assert call.output.verdict.value == "true_positive"
    assert call.grounding_history[0]["output"] == initial
    final = call.grounding_history[-1]["assessment"]
    assert final["evidence"][0]["source_id"] == case["body"]["source_id"]
    assert final["evidence"][0]["interpretation_ko"] == initial["evidence"][0]["interpretation_ko"]
    assert final["evidence_relations"] == [{"representative_index": 0, "related_indexes": [1]}]
    assert call.telemetry["evidence_grounding_retry"]["recovered"]
    assert not call.telemetry["evidence_decision_review"]["attempted"]
    result, links = public_projection(call)
    assert len(result["evidence"]) == 2 and len(links["groups"]) == 1
    assert "contributes_to_evidence_ids" not in json.dumps(result)
    assert case["raw"] not in json.dumps(call.telemetry)
    assert "evidence_correction" in user_document(requests[1])


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_bad_root_removal_keeps_shared_leaf_and_independent_root_but_drops_orphan(monkeypatch, provider):
    case = scenario()
    value = case["value"]
    value["evidence"][0]["contributes_to_evidence_ids"] = []
    value["evidence"][1]["contributes_to_evidence_ids"] = ["e2", "e1"]
    value["evidence"].extend([
        {"evidence_id": "e1", "source_id": case["body"]["source_id"], "supports": "normal_request",
         "interpretation_ko": "페이지와 개수 값이 목록의 조회 범위를 지정하는 업무 데이터입니다.", "contributes_to_evidence_ids": []},
        {"evidence_id": "e3", "source_id": case["payloads"][1]["source_id"], "supports": "context",
         "interpretation_ko": ORPHAN, "contributes_to_evidence_ids": ["e2"]},
    ])
    original = deepcopy(value)
    requests = install_responses(monkeypatch, [value, {"corrections": [], "requires_reanalysis": True}, review()])
    call = run(case, provider)
    assert call.succeeded and len(requests) == 3
    assert call.output.verdict.value == "false_positive"
    assert call.telemetry["evidence_decision_review"]["recovered"]
    assert call.grounding_history[0]["output"] == original
    envelope = user_document(requests[2])["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e5", "e1"]
    assert envelope["original_output"]["evidence"][0]["contributes_to_evidence_ids"] == ["e1"]
    assert EXCLUDED not in json.dumps(envelope) and ORPHAN not in json.dumps(envelope)
    last = call.grounding_history[-1]
    assert [item["index"] for item in last["source_resolutions"]] == [0, 1]
    assert [item["source_id"] for item in last["source_resolutions"]] == [case["payloads"][0]["source_id"], case["body"]["source_id"]]
    result, links = public_projection(call)
    assert [item["supports"] for item in result["evidence"]] == ["context", "normal_request"]
    assert links["groups"] == [{"role": "primary", "representative_evidence_id": "e2", "related_evidence_ids": ["e1"]}]
    assert [item["origins"][0]["index"] for item in result["evidence"]] == [0, 1]
    assert EXCLUDED not in json.dumps(result) and ORPHAN not in json.dumps(result)


def test_rejected_leaf_invalidates_dependent_root_but_preserves_independent_opposite_root(monkeypatch):
    case = scenario()
    value = case["value"]
    value["evidence"][0].update(source_id=case["body"]["source_id"], interpretation_ko=DEPENDENT)
    value["evidence"].append({"evidence_id": "e1", "source_id": case["body"]["source_id"],
        "supports": "normal_request", "interpretation_ko": "목록 조회 범위를 지정한 실제 입력 문맥입니다.",
        "contributes_to_evidence_ids": []})
    bad_source = next(item for item in case["document"]["evidence_candidates"]["items"]
        if item["source_id"] == value["evidence"][1]["source_id"])
    bad_source["start"] += 1  # Deliberately invalid exact raw provenance, not an unknown model ID.
    requests = install_responses(monkeypatch, [value, {"corrections": [], "requires_reanalysis": True},
        review(retained=("e1",))])
    call = run(case)
    assert call.succeeded and len(requests) == 3
    assert call.output.verdict.value == "false_positive"
    assert call.telemetry["evidence_decision_review"]["recovered"]
    envelope = user_document(requests[2])["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e1"]
    assert DEPENDENT not in json.dumps(envelope)
    result, links = public_projection(call)
    assert len(result["evidence"]) == 1 and links["groups"] == []
    assert result["evidence"][0]["supports"] == "normal_request"
    assert result["evidence"][0]["origins"][0]["index"] == 0
    assert DEPENDENT not in json.dumps(result)


@pytest.mark.usefixtures("registered_vllm_target")
def test_full_candidate_worker_pinned_capture_v2_preserves_public_v3_and_encrypted_original(
        client, event_payload, service_headers, monkeypatch, caplog):
    raw = "POST /synthetic/catalog HTTP/1.1/@/Host: example.invalid/@/Content-Type: application/x-www-form-urlencoded/@//@/page=1&size=20"
    event = {**event_payload, "payload": raw, "waf_vendor": "Synthetic Vendor", "waf_action": "A"}
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="Synthetic237", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="production"))
        row = db.get(Analysis, identifier)
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.37"
        row.prompt_version, row.prompt_policy_version_id = prompt.prompt_version, prompt.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(prompt.model_dump_json())
        parser = create_version(db, crypto, name="Synthetic explicit capture", engine_version="http-capture-v2",
            options={**DEFAULT_OPTIONS, "profiles": [{"name": "Synthetic capture", "waf_vendor": "Synthetic Vendor",
                **DEFAULT_OPTIONS, "field_separator": "slash_at_slash"}]})
        row.parser_version_id, row.parser_snapshot_json = None, None
        pin_parser(db, crypto, row, parser.id, selection_origin="candidate")
        db.commit()

    def model(request, _count):
        document = user_document(request)
        assert "/@/" not in json.dumps(document)
        sources = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
        value = wire()
        value["evidence"][0].update(source_id=sources[-1]["source_id"],
            interpretation_ko="합성 요청의 처리 동작과 입력값을 원문에서 대조한 해석입니다.")
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
        assert "contributes_to_evidence_ids" not in json.dumps(result)
        assert result["verifier"]["executed"] is True
        assert len(requests) == 2
        assert user_document(requests[0]) == user_document(requests[1])
        assert crypto.decrypt_text(row.payload_ciphertext) == raw
        for item in result["evidence"]:
            for span in item["source_spans"]:
                assert raw[span["start"]:span["end"]] == item["excerpt"]
        run_record = db.query(AgentRun).filter_by(analysis_id=identifier).one()
        for step in run_record.steps:
            if step.step_type in {"llm_primary", "llm_verifier"}:
                output = json.loads(crypto.decrypt_text(step.output_ciphertext))
                assert "contributes_to_evidence_ids" in json.dumps(output["grounding_history"][0]["output"])
                assert raw not in json.dumps(step.metadata_json)
        assert raw not in caplog.text
        db.commit()
    public = client.get(f"/api/v1/analyses/{identifier}?result_version=v3", headers=service_headers)
    assert public.status_code == 200, public.text
    assert public.json()["result"]["schema_version"] == "waf-analysis-v3"
    assert public.json()["result"]["agent"]["parser"]["engine_version"] == "http-capture-v2"
    assert "contributes_to_evidence_ids" not in public.text
    assert len(requests) == 2  # Reading a finished result never starts model work.
