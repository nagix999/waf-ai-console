"""Bounded decision review with synthetic HTTP, never a live model or DB.

Grounded evidence is not a certificate of semantic correctness. These tests
exercise the transport, exclusion graph, strict review contract and consumers.
"""
from copy import deepcopy
from dataclasses import replace
import json

import httpx
import pytest

from app import worker_v232 as worker
from app.agent.contracts import AgentVerdict
from app.agent.contracts_v232 import SecurityAnalysisOutputV232
from app.agent.evidence_relations_v232 import build_evidence_relations
from app.agent.executor import AgentCallResult
from app.agent.security_analysis_v232 import canonical_evidence, decision_evidence_ids, role_result
from app.result_contracts import validate_new_result
from app.services.core_evidence_v3 import build_core_result
from test_agent_openai import completion, install_http
from test_result_contract_v3 import result_fixture
from test_source_support_worker_v230 import scenario, profile


BAD_CLAIM = "SYNTHETIC_EXCLUDED_SIGNATURE_CLAIM"
BAD_SUMMARY = "SYNTHETIC_EXCLUDED_SUMMARY"
DEPENDENT_CLAIM = "SYNTHETIC_EXCLUDED_DEPENDENT_CLAIM"


def case(*, normal=False):
    result = scenario(direction="normal_request" if normal else "attack_request")
    value = result["value"]
    good = deepcopy(value["evidence"][0])
    good.update(evidence_id="e1", source_id=result["actual"]["source_id"], related_evidence_ids=[])
    invalid = deepcopy(good)
    invalid.update(evidence_id="e2", source_id=result["reference"]["source_id"],
                   supports="attack_request", interpretation_ko=BAD_CLAIM)
    dependent = deepcopy(value["evidence"][1])
    dependent.update(evidence_id="e3", interpretation_ko=DEPENDENT_CLAIM, related_evidence_ids=["e2"])
    value.update(evidence=[good, invalid, dependent], verdict="true_positive", summary_ko=BAD_SUMMARY)
    value["threat_analysis"]["severity"] = "HIGH"
    result["good"] = deepcopy(good)
    return result


def review(case, *, verdict="true_positive", retained=None):
    severity = {"true_positive": "HIGH", "false_positive": "NONE", "inconclusive": "UNKNOWN"}[verdict]
    return {"retained_evidence_ids": ["e1"] if retained is None else retained,
        "verdict": verdict, "confidence_score": .7,
        "summary_ko": "요청에 지정된 입력과 그 처리 목적을 연결하여 사용 문맥을 검토했습니다.",
        "threat_analysis": {"severity": severity, "category": "synthetic", "target": "payload",
            "technique_ko": "남은 요청 입력의 동작을 해석했습니다.", "obfuscations": [],
            "potential_impact_ko": "서버에서 실제 처리된 결과는 확인되지 않았습니다."},
        "decision_issue": None, "interpretation_conflict": False}


def source_correction():
    return {"corrections": [], "requires_reanalysis": True}


def run(case, provider="vllm", *, char_budget=65536, pass_budget=True):
    args = dict(analysis=None, inference_event=case["event"], raw_payload=case["raw"],
        parsed=case["parsed"], input_truncated=False,
        submitted_payload_spans=case["built"].retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        user_input=json.dumps(case["document"]), profile=profile(provider), api_key="synthetic-key",
        instructions="Synthetic independent analysis.", agent_name="waf-primary",
        session_id="synthetic:primary", egress_check=lambda: None)
    if pass_budget:
        args["decision_review_char_budget"] = char_budget
    return worker._execute_grounded_agent(**args)


def install_responses(monkeypatch, case, reviewed):
    documents = [case["value"], source_correction(), reviewed]
    return install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(documents[min(count - 1, len(documents) - 1)]))))[0]


def user_document(request):
    body = json.loads(request.content)
    return json.loads(next(message["content"] for message in body["messages"] if message["role"] == "user"))


def assert_excluded_not_public(call):
    original_history = deepcopy(call.grounding_history)
    evidence, issues, _ = canonical_evidence(call, None)
    interpretations = [item["interpretation_ko"] for item in evidence]
    assert BAD_CLAIM not in interpretations and DEPENDENT_CLAIM not in interpretations
    links = build_evidence_relations(call, evidence=evidence)
    assert call.grounding_history == original_history
    value = result_fixture()
    value.update(verdict=call.output.verdict.value, confidence_score=call.output.confidence_score,
        summary_ko=call.output.summary_ko, threat_analysis=call.output.threat_analysis.model_dump(mode="json"),
        evidence=evidence, decision_issues=issues, primary=role_result(call, "primary", evidence, issues))
    value["policy"]["decision_evidence_ids"] = decision_evidence_ids(call.output, evidence)
    value["diagnostics"]["roles"]["primary"]["validated_verdict"] = call.output.verdict.value
    canonical = validate_new_result(value)
    projected = build_core_result(canonical, relations=links)["result"]
    assert projected["evidence"] == evidence
    assert BAD_CLAIM not in json.dumps(projected) and DEPENDENT_CLAIM not in json.dumps(projected)
    assert "retained_evidence_ids" not in json.dumps(projected)
    assert not {"decision_review", "evidence_decision_review"} & projected.keys()
    return evidence, links


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_independent_request_is_reconsidered_once_and_consumers_use_effective_evidence(monkeypatch, provider):
    data = case()
    original = deepcopy(data["value"])
    requests = install_responses(monkeypatch, data, review(data))
    call = run(data, provider)
    assert call.succeeded and call.output.verdict == AgentVerdict.true_positive
    assert len(requests) == 3
    assert call.output.summary_ko == review(data)["summary_ko"]
    assert call.grounding_history[0]["output"] == original
    assert call.grounding_history[1]["output"] == source_correction()
    last = call.grounding_history[-1]
    assert last["kind"] == "decision_review"
    assert last["expanded_output"]["evidence_relations"] == []
    assert len(last["assessment"]["evidence"]) == 1
    assert [source["index"] for source in last["source_resolutions"]] == [0]
    assert last["resolved_output"]["verdict"] == "true_positive"
    assert last["assessment"]["evidence"][0]["interpretation_ko"] == data["good"]["interpretation_ko"]
    assert last["assessment"]["evidence"][0]["supports"] == "true_positive"
    envelope = user_document(requests[2])["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e1"]
    base = envelope["original_output"]
    assert base["verdict"] == "inconclusive" and base["threat_analysis"]["severity"] == "UNKNOWN"
    assert base["decision_issue"] is None
    assert [item["evidence_id"] for item in base["evidence"]] == ["e1"]
    assert base["evidence"][0] == data["good"]
    assert {item["source_id"] for item in envelope["source_catalog"]["items"]} == {data["actual"]["source_id"]}
    request_text = json.dumps(user_document(requests[2]))
    assert BAD_CLAIM not in request_text and DEPENDENT_CLAIM not in request_text and BAD_SUMMARY not in request_text
    assert data["raw"] not in request_text
    bodies = [json.loads(request.content) for request in requests]
    assert all(body["model"] == profile(provider).model_name for body in bodies)
    assert all(body.get("max_tokens", body.get("max_completion_tokens")) == 3072 for body in bodies)
    assert all(str(request.url) == str(requests[0].url) for request in requests)
    evidence, links = assert_excluded_not_public(call)
    assert len(evidence) == 1 and links["groups"] == []


@pytest.mark.parametrize("verdict,normal", [("inconclusive", False), ("false_positive", True)])
def test_review_makes_its_own_decision_not_automatic_restoration(monkeypatch, verdict, normal):
    data = case(normal=normal)
    reviewed = review(data, verdict=verdict)
    if verdict == "inconclusive":
        reviewed["decision_issue"] = {"point_ko": "처리 목적이 요청 해석을 바꿀 수 있습니다.",
            "evidence_ids": ["e1"], "missing_condition_ko": "입력값의 실제 사용 용도를 구분할 정보가 필요합니다."}
    requests = install_responses(monkeypatch, data, reviewed)
    call = run(data)
    assert call.succeeded and len(requests) == 3
    assert call.output.verdict.value == verdict
    assert call.grounding_history[-1]["resolved_output"]["verdict"] == verdict
    evidence, _ = assert_excluded_not_public(call)
    assert evidence[0]["supports"] == ("normal_request" if normal else "attack_request")


def test_no_independent_directional_request_remains_so_review_is_not_called(monkeypatch):
    data = case()
    data["value"]["evidence"] = data["value"]["evidence"][1:]
    requests = install_responses(monkeypatch, data, review(data))
    call = run(data)
    assert call.succeeded and call.output.verdict == AgentVerdict.inconclusive
    assert len(requests) == 2
    evidence, _, _ = canonical_evidence(call, None)
    assert not any(item["supports"] == "attack_request" for item in evidence)


def test_review_cannot_hide_remaining_opposing_evidence(monkeypatch):
    data = case()
    opposite = deepcopy(data["good"])
    opposite.update(evidence_id="e4", supports="normal_request",
        interpretation_ko="합성 요청에서 지정한 값이 정상 기능 입력이라는 반대 해석도 존재합니다.")
    data["value"]["evidence"].append(opposite)
    requests = install_responses(monkeypatch, data, review(data, retained=["e1"]))
    call = run(data)
    assert call.succeeded and call.output.verdict == AgentVerdict.inconclusive and len(requests) == 3
    envelope = user_document(requests[2])["evidence_decision_review"]
    assert set(envelope["eligible_evidence_ids"]) == {"e1", "e4"}
    assert {item["supports"] for item in envelope["original_output"]["evidence"]} == {
        "attack_request", "normal_request"}
    evidence, _, _ = canonical_evidence(call, None)
    assert {item["supports"] for item in evidence} == {"attack_request", "normal_request"}


def test_invalid_dependency_cycles_are_excluded_transitively(monkeypatch):
    data = case()
    fourth = deepcopy(data["value"]["evidence"][2])
    fourth.update(evidence_id="e4", interpretation_ko="두 번째 의존 문맥입니다.", related_evidence_ids=["e3"])
    fifth = deepcopy(fourth)
    fifth.update(evidence_id="e5", interpretation_ko="전이 의존 문맥입니다.", related_evidence_ids=["e4"])
    data["value"]["evidence"][2]["related_evidence_ids"] = ["e2", "e4"]
    data["value"]["evidence"].extend([fourth, fifth])
    requests = install_responses(monkeypatch, data, review(data))
    call = run(data)
    assert call.succeeded and call.output.verdict == AgentVerdict.true_positive and len(requests) == 3
    envelope = user_document(requests[2])["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e1"]
    evidence, links = assert_excluded_not_public(call)
    assert len(evidence) == 1 and not links["groups"]


def test_surviving_independent_group_keeps_its_dependency_and_remaps_positions(monkeypatch):
    data = case()
    related = deepcopy(data["value"]["evidence"][2])
    related.update(evidence_id="e4", interpretation_ko="온전한 요청 경로는 본문 입력의 사용 위치를 설명합니다.",
                   related_evidence_ids=[])
    data["value"]["evidence"][0]["related_evidence_ids"] = ["e4"]
    data["value"]["evidence"].append(related)
    requests = install_responses(monkeypatch, data, review(data, retained=["e4", "e1"]))
    call = run(data)
    assert call.succeeded and call.output.verdict == AgentVerdict.true_positive and len(requests) == 3
    envelope = user_document(requests[2])["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e1", "e4"]
    last = call.grounding_history[-1]
    assert [item["source_id"] for item in last["assessment"]["evidence"]] == [
        data["good"]["source_id"], related["source_id"]]
    assert last["expanded_output"]["evidence_relations"] == [{"representative_index": 0, "related_indexes": [1]}]
    evidence, links = assert_excluded_not_public(call)
    assert len(evidence) == 2 and len(links["groups"]) == 1


def test_reanalysis_requested_claim_is_excluded_even_when_its_source_was_corrected(monkeypatch):
    data = case()
    corrected = {"corrections": [{"index": 1, "source_id": data["actual"]["source_id"]}],
                 "requires_reanalysis": True}
    documents = [data["value"], corrected, review(data)]
    requests, _ = install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(documents[min(count - 1, 2)]))))
    call = run(data)
    assert call.succeeded and call.output.verdict == AgentVerdict.true_positive and len(requests) == 3
    envelope = user_document(requests[2])["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e1"]
    assert_excluded_not_public(call)


@pytest.mark.parametrize("failure", ["failed_verifier", "disagreement"])
def test_successful_same_role_review_never_bypasses_required_verifier(monkeypatch, failure):
    data = case()
    requests = install_responses(monkeypatch, data, review(data))
    primary = run(data)
    assert primary.succeeded and primary.output.verdict == AgentVerdict.true_positive and len(requests) == 3
    verifier = (None if failure == "failed_verifier" else primary.output.model_copy(update={
        "verdict": AgentVerdict.false_positive,
        "threat_analysis": primary.output.threat_analysis.model_copy(update={"severity": "NONE"})}))
    finalized = worker.finalize_with_verifier(primary.output, verifier,
        reasons=["true_positive_but_allowed"], verifier_failure="synthetic_failure" if verifier is None else None)
    assert finalized.output.verdict == AgentVerdict.inconclusive
    assert finalized.verifier_executed and finalized.agreement is False


@pytest.mark.parametrize("defect", ["malformed", "unknown_id", "missing_id", "new_evidence", "length", "http_error"])
def test_failed_review_is_single_attempt_and_keeps_safe_hold_not_job_failure(monkeypatch, defect):
    data = case()
    reviewed = review(data)
    if defect == "unknown_id":
        reviewed["retained_evidence_ids"] = ["e5"]
    elif defect == "missing_id":
        reviewed["retained_evidence_ids"] = []
    elif defect == "new_evidence":
        reviewed["evidence"] = [data["good"]]

    def respond(_request, count):
        if count == 1:
            return httpx.Response(200, json=completion(json.dumps(data["value"])))
        if count == 2:
            return httpx.Response(200, json=completion(json.dumps(source_correction())))
        if defect == "http_error":
            return httpx.Response(503, json={"error": {"message": "Synthetic offline provider failure"}})
        return httpx.Response(200, json=completion("{" if defect == "malformed" else json.dumps(reviewed),
            finish_reason="length" if defect == "length" else "stop"))

    requests, _ = install_http(monkeypatch, respond)
    call = run(data)
    assert call.succeeded and call.output.verdict == AgentVerdict.inconclusive
    assert len(requests) == 3
    assert call.grounding_history[0]["output"] == data["value"]
    evidence, _, _ = canonical_evidence(call, None)
    assert data["good"]["interpretation_ko"] in [item["interpretation_ko"] for item in evidence]
    assert data["raw"] not in json.dumps(call.telemetry)


@pytest.mark.parametrize("pass_budget,char_budget", [(False, 65536), (True, 10)])
def test_missing_or_insufficient_input_budget_skips_review(monkeypatch, pass_budget, char_budget):
    data = case()
    requests = install_responses(monkeypatch, data, review(data))
    call = run(data, pass_budget=pass_budget, char_budget=char_budget)
    assert call.succeeded and call.output.verdict == AgentVerdict.inconclusive and len(requests) == 2


def test_initial_four_generations_and_source_repair_exhaust_original_budget(monkeypatch):
    data = case()

    def respond(_request, count):
        if count <= 3:
            return httpx.Response(200, json=completion("{"))
        if count == 4:
            return httpx.Response(200, json=completion(json.dumps(data["value"])))
        return httpx.Response(200, json=completion(json.dumps(source_correction())))

    requests, _ = install_http(monkeypatch, respond)
    call = run(data)
    assert call.succeeded and call.output.verdict == AgentVerdict.inconclusive
    assert len(requests) == 5
    assert call.telemetry["evidence_grounding_retry"]["attempts"][0]["output_validation_retry"]["attempt_count"] == 4


def test_missing_attempt_telemetry_never_creates_an_unbounded_review_budget(monkeypatch):
    data = case()
    calls = []

    async def synthetic_call(**kwargs):
        from app.agent.contracts import EvidenceSelectionCorrectionOutput
        calls.append(kwargs)
        output = (SecurityAnalysisOutputV232.model_validate(data["value"]) if len(calls) == 1 else
            EvidenceSelectionCorrectionOutput.model_validate(source_correction()))
        return AgentCallResult(output, "synthetic-missing-count", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", synthetic_call)
    call = run(data)
    assert call.succeeded and call.output.verdict == AgentVerdict.inconclusive and len(calls) == 2


@pytest.mark.parametrize("budget", ["exhausted", "unknown_attempts", "short_input"])
def test_rejected_claim_aliases_and_dependencies_are_removed_even_without_review_budget(monkeypatch, budget):
    data = case()
    # After source-only correction, e1 and the e2 requiring reinterpretation
    # have exactly the same source, meaning and direction. Neither alias can
    # revive that claim; e3's declared dependency must also stay excluded.
    data["value"]["evidence"][1]["interpretation_ko"] = data["good"]["interpretation_ko"]
    corrected = {"corrections": [{"index": 1, "source_id": data["actual"]["source_id"]}],
                 "requires_reanalysis": True}
    initial_attempts = 4 if budget == "exhausted" else 1

    def respond(_request, count):
        if count < initial_attempts:
            return httpx.Response(200, json=completion("{"))
        return httpx.Response(200, json=completion(json.dumps(
            data["value"] if count == initial_attempts else corrected)))

    requests, _ = install_http(monkeypatch, respond)
    if budget == "unknown_attempts":
        real_execute = worker.execute_structured_agent

        async def without_attempt_counter(**kwargs):
            result = await real_execute(**kwargs)
            return replace(result, telemetry={key: value for key, value in result.telemetry.items()
                                              if key != "output_validation_retry"})

        monkeypatch.setattr(worker, "execute_structured_agent", without_attempt_counter)
    call = run(data, char_budget=10 if budget == "short_input" else 65536)
    assert call.succeeded and call.output.verdict == AgentVerdict.inconclusive
    assert len(requests) == initial_attempts + 1
    assert not call.telemetry["evidence_decision_review"]["attempted"]
    evidence, _, _ = canonical_evidence(call, None)
    assert evidence == [] and call.output.evidence == []
    assert set(call.grounding_history[-1]["excluded_relation_indexes"]) == {0, 1, 2}
    assert call.grounding_history[0]["output"] == data["value"]

