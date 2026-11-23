"""Synthetic offline normalization preserves exact evidence and graph semantics."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError

from app.agent import executor_v224 as frozen
from app.agent import executor_v225 as executor
from app.agent.contracts_v224 import SecurityAnalysisOutputV224
from app.agent.relation_correction_v225 import recover_relation_graph
from test_agent_openai import completion, install_http, openai_profile
from test_executor_recovery_v224 import wire


def request_input():
    chunks = ["GET /synthetic HTTP/1.1\r\n" + "x" * 220,
              "item=synthetic-input&" + "y" * 220,
              "remaining-synthetic-capture=" + "z" * 220]
    items, start = [], 0
    for index, excerpt in enumerate(chunks):
        items.append({"source_id": f"c{index + 1}", "field": "payload", "excerpt": excerpt,
                      "start": start, "end": start + len(excerpt)})
        start += len(excerpt)
    return json.dumps({"event": {"signature": "synthetic"},
                       "evidence_candidates": {"version": "evidence-selection-v5", "items": items}},
                      ensure_ascii=False)


def self_mixed():
    value = wire()
    value["evidence_relations"] = [{"representative_index": 1, "related_indexes": [1, 0]}]
    return value


def profile(provider):
    return openai_profile(provider=provider, base_url=("http://10.0.0.10:8000/v1"
        if provider == "vllm" else "https://api.openai.com/v1"))


def execute(provider="vllm", **kwargs):
    return asyncio.run(executor.execute_structured_agent(
        profile=profile(provider), api_key="synthetic-key", instructions="Synthetic independent role instructions.",
        user_input=request_input(), session_id="synthetic:verifier", agent_name="waf-verifier",
        egress_check=lambda: None, output_model=SecurityAnalysisOutputV224,
        structured_output_recovery=True, **kwargs))


def nonrelations(value):
    return {key: item for key, item in value.items() if key != "evidence_relations"}


def test_self_reference_among_known_edges_is_removed_without_changing_any_assessment_value():
    value = self_mixed()
    before = deepcopy(value)
    result, codes = recover_relation_graph(value, request_input())
    assert result == wire()
    assert codes == ["relation_self_reference_removed"]
    assert value == before
    assert nonrelations(result) == nonrelations(before)


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": [1]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": [9, 0]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": "1", "related_indexes": [0]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": [True, 0]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": []}]),
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": [1, 0], "other": "private"}]),
    lambda value: value["evidence"][0].update(supports="normal_request"),
    lambda value: value["evidence"][0].update(source_id="c999"),
    lambda value: value.update(confidence_score=2),
    lambda value: value.update(capture_assessment={"status": "unknown", "reason_ko": ""}),
])
def test_ambiguous_or_unrelated_invalid_data_is_not_dropped_to_succeed(mutate):
    value = self_mixed()
    mutate(value)
    before = deepcopy(value)
    result, codes = recover_relation_graph(value, request_input())
    assert result == before and codes == [] and value == before


def test_every_nonrelation_dependency_runs_even_when_nested_relation_validation_failed_first():
    value = self_mixed()
    value["decision_issue"] = {"point_ko": "합성 확인 조건", "evidence_indexes": [0],
                              "missing_condition_ko": "실제 값에 따라 판정이 바뀌는 조건"}
    with pytest.raises(ValidationError) as failure:
        SecurityAnalysisOutputV224.model_validate(value)
    assert all(issue["type"] == "evidence_relation_invalid" for issue in failure.value.errors())
    result, codes = recover_relation_graph(value, request_input())
    assert result == value and codes == []


def test_a_valid_group_cannot_hide_a_later_self_only_group():
    value = wire()
    value["evidence_relations"].append({"representative_index": 0, "related_indexes": [0]})
    result, codes = recover_relation_graph(value, request_input())
    assert result == value and codes == []


@pytest.mark.parametrize("mutate", [
    lambda document: document["evidence_candidates"].update(version="other"),
    lambda document: document["evidence_candidates"]["items"].append(deepcopy(document["evidence_candidates"]["items"][0])),
    lambda document: document["evidence_candidates"]["items"][1].update(allowed_supports=["context"]),
    lambda document: document["evidence_candidates"]["items"][1].update(field="signature", start=None, end=None),
    lambda document: document["evidence_candidates"]["items"][1].update(end=999),
])
def test_catalog_provenance_is_checked_before_graph_normalization(mutate):
    document = json.loads(request_input())
    mutate(document)
    value = self_mixed()
    result, codes = recover_relation_graph(value, json.dumps(document))
    assert result == value and codes == []


@pytest.mark.parametrize("user_input", ["synthetic-unstructured", "{}", '{"evidence_candidates":', '[]'])
def test_missing_or_malformed_catalog_uses_existing_full_retry(user_input):
    value = self_mixed()
    result, codes = recover_relation_graph(value, user_input)
    assert result == value and codes == []


def test_metadata_source_with_omitted_null_offsets_does_not_raise():
    document = json.loads(request_input())
    document["event"]["src_ip"] = "192.0.2.10"
    item = document["evidence_candidates"]["items"][0]
    item.update(field="src_ip", excerpt="192.0.2.10")
    item.pop("start")
    item.pop("end")
    actual, codes = recover_relation_graph(self_mixed(), json.dumps(document))
    assert actual == wire() and codes == ["relation_self_reference_removed"]


def test_same_representative_groups_keep_all_associations_in_first_appearance_order():
    value = wire()
    value["evidence"].append({"source_id": "c3", "supports": "context", "interpretation_ko": "관련된 합성 원문입니다."})
    value["evidence_relations"] = [{"representative_index": 1, "related_indexes": [0]},
                                  {"representative_index": 1, "related_indexes": [2, 1, 0]}]
    before = deepcopy(value)
    result, codes = recover_relation_graph(value, request_input())
    assert result["evidence_relations"] == [{"representative_index": 1, "related_indexes": [0, 2]}]
    assert codes == ["relation_self_reference_removed", "relation_representative_groups_merged",
                     "relation_related_duplicates_removed"]
    assert nonrelations(result) == nonrelations(before) and value == before
    SecurityAnalysisOutputV224.model_validate(result)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_complete_safe_graph_normalizes_in_one_call_with_unchanged_model_options(monkeypatch, provider):
    requests, options = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(self_mixed(), ensure_ascii=False))))
    result = execute(provider)
    assert result.succeeded and result.output.model_dump(mode="json") == wire()
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["model"] == profile(provider).model_name
    assert set(body["response_format"]["json_schema"]["schema"]["properties"]) == set(wire())
    retry = result.telemetry["output_validation_retry"]
    assert retry["version"] == "structured-output-repair-v5"
    assert retry["attempt_count"] == 1 and retry["max_attempts"] == 4
    assert retry["attempts"][0]["json_normalizations"] == ["relation_self_reference_removed"]
    assert not retry["attempted"]
    assert options[0]["verify"] and not options[0]["follow_redirects"] and not options[0]["trust_env"]
    assert body["max_tokens" if provider == "vllm" else "max_completion_tokens"] == 3072


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("damage", ["nonrelation", "source", "opposite", "ambiguous"])
def test_ineligible_outputs_keep_established_full_retry(monkeypatch, provider, damage):
    value = self_mixed()
    if damage == "nonrelation": value.update(confidence_score=2)
    if damage == "source": value["evidence"][1]["source_id"] = "c999"
    if damage == "opposite": value["evidence"][0]["supports"] = "normal_request"
    if damage == "ambiguous": value["evidence_relations"][0]["related_indexes"] = [1]
    requests, _ = install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(value if count == 1 else wire()))))
    result = execute(provider)
    assert result.succeeded and len(requests) == 2
    bodies = [json.loads(request.content) for request in requests]
    assert all(set(body["response_format"]["json_schema"]["schema"]["properties"]) == set(wire()) for body in bodies)
    assert [m for m in bodies[0]["messages"] if m["role"] == "user"] == [
        m for m in bodies[1]["messages"] if m["role"] == "user"]


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_unrecoverable_graph_stops_at_the_existing_four_attempt_cap(monkeypatch, provider):
    value = wire()
    value["evidence_relations"] = [{"representative_index": 1, "related_indexes": [1]}]
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(value))))
    result = execute(provider)
    retry = result.telemetry["output_validation_retry"]
    assert not result.succeeded and result.output is None and len(requests) == 4
    assert retry["attempt_count"] == 4 and retry["stop_reason"] == "attempt_limit"


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("reason", ["length", "content_filter"])
def test_incomplete_and_filtered_outputs_never_enter_graph_recovery(monkeypatch, provider, reason):
    monkeypatch.setattr(executor, "recover_relation_graph", lambda *_: pytest.fail("No graph recovery for incomplete output"))
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(self_mixed()), finish_reason=reason)))
    result = execute(provider)
    assert not result.succeeded and len(requests) == (4 if reason == "length" else 1)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_refusal_and_invalid_json_never_enter_graph_recovery_or_leak_content(monkeypatch, provider):
    secret = "synthetic-private-refusal-or-output"
    monkeypatch.setattr(executor, "recover_relation_graph", lambda *_: pytest.fail("No graph recovery expected"))
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(self_mixed()), refusal=secret)))
    result = execute(provider)
    assert not result.succeeded and len(requests) == 1
    assert secret not in str(result.error) + json.dumps(result.telemetry)
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion('{"' + secret + '":')))
    result = execute(provider)
    assert not result.succeeded and len(requests) == 4
    assert secret not in str(result.error) + json.dumps(result.telemetry)


def test_even_broken_graph_helper_cannot_bypass_same_full_domain_codec(monkeypatch):
    def broken(value, _user_input):
        value["confidence_score"] = 2
        value["evidence_relations"] = []
        return value, ["relation_self_reference_removed"]
    monkeypatch.setattr(executor, "recover_relation_graph", broken)
    codec = executor._TrackingOutputCodec(PydanticOutputCodec(SecurityAnalysisOutputV224),
        output_model=SecurityAnalysisOutputV224, structured_output_recovery=True, user_input=request_input())
    with pytest.raises(ValidationError):
        codec.decode(json.dumps(self_mixed()))
    assert "less_than_equal" in {issue["type"] for issue in codec.validation_issues}
    assert codec.json_normalizations == []


def test_a_nonrelation_failure_beyond_the_telemetry_sample_blocks_graph_recovery():
    class Failure(Exception):
        def errors(self, **_kwargs):
            return [{"type": "evidence_relation_invalid"}] * 12 + [{"type": "missing"}]
    assert not executor._only_relation_invalid(Failure())


def test_frozen_executor_keeps_rejecting_newly_supported_graph_shape():
    codec = frozen._TrackingOutputCodec(PydanticOutputCodec(SecurityAnalysisOutputV224),
        output_model=SecurityAnalysisOutputV224, structured_output_recovery=True)
    with pytest.raises(ValidationError):
        codec.decode(json.dumps(self_mixed()))
