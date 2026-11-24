"""Synthetic source-role repair through actual ModuAgent and offline HTTP.

The provider responses below are deliberately constructed source-selection
mistakes. Passing these tests does not establish real-model semantic accuracy.
"""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app import worker_v230 as worker
from app.agent import executor_v230 as executor
from app.agent.contracts_v230 import SecurityAnalysisOutputV230
from app.agent.input_builder_v229 import build_agent_input
from app.agent.evidence_relations_v230 import build_evidence_relations
from app.agent.security_analysis_v230 import canonical_evidence
from app.services.http_parser import parse_http_payload
from test_agent_openai import completion, install_http, openai_profile
from test_flat_evidence_v228 import wire


def scenario(*, direction="attack_request", reference_field="signature"):
    """Use real bounded source blocks and only manufactured request content."""
    body = ("command=synthetic_execute" if direction == "attack_request" else "page=1&size=20")
    raw = ("POST /synthetic/catalog HTTP/1.1\r\nHost: synthetic.invalid\r\n"
           "Content-Type: application/x-www-form-urlencoded\r\n\r\n" + body)
    event = {"payload": raw, "signature": "Synthetic detection",
             "signature_explanation": "Synthetic registered description, not an observed request.",
             "waf_action": "D", "extra_fields": {"request_note": "Synthetic auxiliary request context"}}
    parsed = parse_http_payload(raw)
    built = build_agent_input(event, raw, parsed, 32768, 3072, evidence_selection=True,
                              fixed_rules_version="waf-system-v2.29")
    document = json.loads(built.text)
    catalog = document["evidence_candidates"]["items"]
    payloads = [item for item in catalog if item["field"] == "payload"]
    actual = next(item for item in payloads if body in item["excerpt"])
    reference = next(item for item in catalog if item["field"] == reference_field)
    if reference_field == "extra_fields.request_note":
        # Simulate another explicitly restricted source. The canonical field
        # name alone must not be the only protection after source-only repair.
        reference["allowed_supports"] = ["context"]
    value = wire()
    value["evidence"][0].update(source_id=reference["source_id"], supports=direction,
        interpretation_ko=("명령 입력을 처리 동작으로 지정하는 요청 문맥이므로 서버 동작 변경 시도를 뒷받침합니다."
                           if direction == "attack_request" else
                           "페이지와 개수 값이 목록 조회의 범위를 지정하므로 정상적인 페이지 조회를 뒷받침합니다."))
    value["evidence"][1].update(source_id=payloads[0]["source_id"],
        interpretation_ko="입력값을 받는 요청 위치이며 본문에 지정한 동작을 해석하는 문맥입니다.")
    if direction == "normal_request":
        value["verdict"] = "false_positive"
        value["threat_analysis"]["severity"] = "NONE"
    return {"raw": raw, "event": event, "parsed": parsed, "built": built,
            "document": document, "value": value, "actual": actual, "reference": reference}


def profile(provider="vllm"):
    return openai_profile(provider=provider, base_url=("http://10.0.0.10:8000/v1"
        if provider == "vllm" else "https://api.openai.com/v1"))


def grounded(case, provider="vllm"):
    return worker._execute_grounded_agent(analysis=None, inference_event=case["event"],
        raw_payload=case["raw"], parsed=case["parsed"], input_truncated=False,
        submitted_payload_spans=case["built"].retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        user_input=json.dumps(case["document"]), profile=profile(provider), api_key="synthetic-key",
        instructions="Synthetic independent analysis.", agent_name="waf-primary",
        session_id="synthetic:primary", egress_check=lambda: None)


def direct(case, provider="vllm", *, previous=False):
    if previous:
        from app.agent import executor_v229 as selected_executor
        from app.agent.contracts_v229 import SecurityAnalysisOutputV229 as output_model
    else:
        selected_executor, output_model = executor, SecurityAnalysisOutputV230
    return asyncio.run(selected_executor.execute_structured_agent(profile=profile(provider),
        api_key="synthetic-key", instructions="Synthetic independent analysis.",
        user_input=json.dumps(case["document"]), session_id="synthetic:primary",
        agent_name="waf-primary", egress_check=lambda: None, output_model=output_model,
        structured_output_recovery=True))


def correction(case, *, replacement=None, requires_reanalysis=False):
    return {"corrections": ([] if requires_reanalysis else [{"index": 0,
        "source_id": replacement or case["actual"]["source_id"]}]),
        "requires_reanalysis": requires_reanalysis}


def responses(monkeypatch, initial, repaired):
    return install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(initial if count == 1 else repaired), finish_reason="stop")))[0]


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("direction", ["attack_request", "normal_request"])
@pytest.mark.parametrize("reference_field", ["signature", "signature_explanation"])
def test_forbidden_reference_uses_one_source_repair_without_regenerating_analysis(
        monkeypatch, provider, direction, reference_field):
    case = scenario(direction=direction, reference_field=reference_field)
    initial = deepcopy(case["value"])
    requests = responses(monkeypatch, initial, correction(case))
    call = grounded(case, provider)
    assert call.succeeded and len(requests) == 2
    assert call.output.verdict.value == initial["verdict"]
    assert call.output.summary_ko == initial["summary_ko"]
    assert call.output.threat_analysis.model_dump(mode="json") == initial["threat_analysis"]
    assert call.grounding_history[0]["output"] == initial
    assert call.grounding_history[0]["rejected_evidence"] == [
        {"index": 0, "reason": "signature_support_not_allowed"}]
    assert call.grounding_history[1]["rejected_evidence"] == []
    final = call.grounding_history[-1]["assessment"]["evidence"]
    assert [item["interpretation_ko"] for item in final] == [
        item["interpretation_ko"] for item in initial["evidence"]]
    assert final[0]["source_id"] == case["actual"]["source_id"]
    assert final[0]["supports"] == initial["verdict"]
    assert final[1]["supports"] == "context"
    evidence, _, _ = canonical_evidence(call, None)
    assert len(evidence) == 2
    assert evidence[0]["supports"] == direction
    assert evidence[0]["excerpt"] == case["actual"]["excerpt"]
    assert len(build_evidence_relations(call, evidence=evidence)["groups"]) == 1
    retry = call.telemetry["evidence_grounding_retry"]
    assert retry["recovered"] and retry["attempt_count"] == 2
    assert all(item["output_validation_retry"]["attempt_count"] == 1 for item in retry["attempts"])
    bodies = [json.loads(request.content) for request in requests]
    assert all(body.get("max_tokens", body.get("max_completion_tokens")) == 3072 for body in bodies)
    second_input = json.loads(next(message["content"] for message in bodies[1]["messages"]
                                  if message["role"] == "user"))
    assert second_input["evidence_correction"]["items"][0]["index"] == 0
    assert "corrections" in bodies[1]["response_format"]["json_schema"]["schema"]["properties"]
    assert "evidence" not in bodies[1]["response_format"]["json_schema"]["schema"]["properties"]
    assert case["raw"] not in json.dumps(call.telemetry)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_same_synthetic_selection_was_four_failed_generations_now_one_plus_one(monkeypatch, provider):
    case = scenario()
    requests, _ = install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(case["value"] if count <= 5 else correction(case)))))
    previous = direct(case, provider, previous=True)
    assert not previous.succeeded and len(requests) == 4
    assert previous.telemetry["error_summary"]["code"] == "output_validation_failed"
    assert previous.telemetry["output_validation_retry"]["attempts"][-1]["validation_issues"] == [
        {"field": "evidence", "type": "evidence_source_support_forbidden"}]
    current = grounded(case, provider)
    assert current.succeeded and current.output.verdict.value == "true_positive"
    assert len(requests) == 6


@pytest.mark.parametrize("repair_kind", ["needs_reanalysis", "forbidden_again", "no_correction", "unknown_repair"])
def test_unresolved_or_semantically_changed_claim_is_held_and_not_exposed_as_group(monkeypatch, repair_kind):
    case = scenario()
    if repair_kind == "needs_reanalysis":
        case["value"]["evidence"][0]["interpretation_ko"] = (
            "등록된 시그니처 설명에 명령 실행 탐지가 기재되어 있어 이 요청도 명령 실행 공격입니다.")
        repaired = correction(case, requires_reanalysis=True)
    elif repair_kind == "forbidden_again":
        repaired = correction(case, replacement=case["reference"]["source_id"])
    elif repair_kind == "unknown_repair":
        repaired = correction(case, replacement="c999")
    else:
        repaired = {"corrections": [], "requires_reanalysis": False}
    requests = responses(monkeypatch, case["value"], repaired)
    call = grounded(case)
    assert call.succeeded and len(requests) == 2
    assert call.output.verdict.value == "inconclusive"
    assert call.telemetry["evidence_grounding_retry"]["requires_review"]
    assert not call.telemetry["evidence_grounding_retry"]["recovered"]
    evidence, _, _ = canonical_evidence(call, None)
    assert case["value"]["evidence"][0]["interpretation_ko"] not in [
        item["interpretation_ko"] for item in evidence]
    assert all(item["field"] not in {"signature", "signature_explanation"} for item in evidence)
    assert build_evidence_relations(call, evidence=evidence)["groups"] == []


@pytest.mark.parametrize("corrected", [False, True])
def test_general_allowed_supports_is_checked_before_and_after_source_only_repair(monkeypatch, corrected):
    case = scenario(reference_field="extra_fields.request_note")
    repaired = correction(case, replacement=(case["actual"]["source_id"] if corrected
                                             else case["reference"]["source_id"]))
    requests = responses(monkeypatch, case["value"], repaired)
    call = grounded(case)
    assert call.succeeded and len(requests) == 2
    assert call.grounding_history[0]["rejected_evidence"] == [
        {"index": 0, "reason": "source_support_not_allowed"}]
    assert call.output.verdict.value == ("true_positive" if corrected else "inconclusive")
    evidence, _, _ = canonical_evidence(call, None)
    assert all(item["field"] != "extra_fields.request_note" for item in evidence)


def test_unknown_source_plus_forbidden_source_is_never_deferred_as_valid_output(monkeypatch):
    case = scenario()
    case["value"]["evidence"][1]["source_id"] = "c999"
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(case["value"]))))
    call = grounded(case)
    assert not call.succeeded and call.output is None and len(requests) == 4
    issues = call.telemetry["output_validation_retry"]["attempts"][-1]["validation_issues"]
    assert {item["type"] for item in issues} == {"evidence_source_unavailable"}
    assert call.telemetry["evidence_grounding_retry"]["attempt_count"] == 1


def test_direct_executor_stays_strict_without_worker_opt_in(monkeypatch):
    case = scenario()
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(case["value"]))))
    call = direct(case)
    assert not call.succeeded and call.output is None and len(requests) == 4
    assert call.telemetry["output_validation_retry"]["attempts"][-1]["validation_issues"] == [
        {"field": "evidence", "type": "evidence_source_support_forbidden"}]


def test_correction_schema_cannot_rewrite_support_or_interpretation(monkeypatch):
    case = scenario()
    repaired = correction(case)
    repaired["corrections"][0].update(supports="context", interpretation_ko="Changed synthetic meaning")
    requests = responses(monkeypatch, case["value"], repaired)
    call = grounded(case)
    assert not call.succeeded and call.output is None and len(requests) == 2
    issues = call.telemetry["output_validation_retry"]["attempts"][-1]["validation_issues"]
    assert {item["type"] for item in issues} == {"extra_forbidden"}

