"""Synthetic offline v2.22 codec and real ModuAgent transport regression."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from moduagent.messages import Message
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError

from app.agent.contracts import SecurityAnalysisOutputV220
from app.agent import executor_v221 as frozen
from app.agent import executor_v222 as executor
from test_agent_openai import completion, install_http, openai_profile
from test_evidence_relations import wire


def duplicate_wire(kind="both"):
    value = wire()
    group = {"representative_index": 1, "related_indexes": [0, 0] if kind != "groups" else [0]}
    value["evidence_relations"] = [group, deepcopy(group)] if kind != "related" else [group]
    return value


def codec(*, enabled=True, delegate=None):
    return executor._TrackingOutputCodec(delegate or PydanticOutputCodec(SecurityAnalysisOutputV220),
        output_model=SecurityAnalysisOutputV220, structured_output_recovery=enabled)


def profile(provider):
    return openai_profile(provider=provider, base_url=(
        "http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1"))


def execute(provider, **kwargs):
    return asyncio.run(executor.execute_structured_agent(profile=profile(provider), api_key="synthetic-key",
        instructions="Synthetic independent WAF analysis.", user_input="synthetic-input-only",
        session_id="synthetic-analysis:primary", agent_name="waf-primary",
        egress_check=lambda: None, output_model=SecurityAnalysisOutputV220,
        structured_output_recovery=True, **kwargs))


@pytest.mark.parametrize("kind,codes", [
    ("related", ["relation_related_duplicates_removed"]),
    ("groups", ["relation_group_duplicates_removed"]),
    ("both", ["relation_related_duplicates_removed", "relation_group_duplicates_removed"]),
])
@pytest.mark.parametrize("shape", [lambda text: text, lambda text: text.encode(), lambda text: bytearray(text.encode()),
    lambda text: {"message": {"content": text}}, lambda text: Message.assistant(text), lambda text: SimpleNamespace(message=Message.assistant(text))])
def test_complete_json_redundant_relations_only_are_normalized(kind, codes, shape):
    value = duplicate_wire(kind)
    text = json.dumps(value, ensure_ascii=False)
    output_codec = codec()
    actual = output_codec.decode(shape("\ufeff```json\n" + text + "\n```"))
    assert actual == SecurityAnalysisOutputV220.model_validate(wire())
    assert output_codec.validation_issues == []
    assert output_codec.json_normalizations == ["utf8_bom", "markdown_json_fence", *codes]
    assert value == duplicate_wire(kind)


def test_recovered_object_passes_the_same_full_domain_codec_again():
    class Delegate:
        def __init__(self):
            self.values = []
            self.codec = PydanticOutputCodec(SecurityAnalysisOutputV220)

        def schema(self):
            return self.codec.schema()

        def decode(self, value):
            self.values.append(value)
            return self.codec.decode(value)

    delegate = Delegate()
    output_codec = codec(delegate=delegate)
    result = output_codec.decode(json.dumps(duplicate_wire()))
    assert len(delegate.values) == 2
    assert result == SecurityAnalysisOutputV220.model_validate(wire())
    assert json.loads(delegate.values[1]) == wire()


def test_even_a_broken_recovery_helper_cannot_skip_final_domain_validation(monkeypatch):
    def broken(value):
        value["evidence_relations"] = []
        value["confidence_score"] = 2
        return value, ["relation_related_duplicates_removed"]

    monkeypatch.setattr(executor, "recover_relation_duplicates", broken)
    output_codec = codec()
    with pytest.raises(ValidationError):
        output_codec.decode(json.dumps(duplicate_wire()))
    assert "less_than_equal" in {item["type"] for item in output_codec.validation_issues}
    assert output_codec.json_normalizations == []


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": [1]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": 4, "related_indexes": [0]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": [2]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": True, "related_indexes": [0]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": "1", "related_indexes": [0]}]),
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": []}]),
    lambda value: value.update(evidence_relations=[{"representative_index": 1, "related_indexes": [0], "unknown": "synthetic-private-extra"}]),
    lambda value: value["evidence"][0].update(supports="normal_request"),
    lambda value: value.update(confidence_score=2),
    lambda value: value.update(summary_ko="정탐입니다."),
])
def test_unsafe_or_other_invalid_content_is_never_accepted_by_deduplication(mutate):
    value = duplicate_wire()
    mutate(value)
    before = deepcopy(value)
    output_codec = codec()
    with pytest.raises(ValidationError):
        output_codec.decode(json.dumps(value))
    assert value == before
    assert not output_codec.json_normalizations


def test_changed_related_lists_are_not_inferred_or_merged():
    value = wire()
    value["evidence"].append({"source_id": "c3", "supports": "context", "interpretation_ko": "다른 합성 입력 위치입니다."})
    value["evidence_relations"] = [{"representative_index": 1, "related_indexes": [0]},
        {"representative_index": 1, "related_indexes": [2]}]
    with pytest.raises(ValidationError):
        codec().decode(json.dumps(value))


def test_a_nonrelation_failure_beyond_the_telemetry_sample_blocks_local_repair():
    class Failure(Exception):
        def errors(self, **kwargs):
            return [{"type": "evidence_relation_invalid"}] * 12 + [{"type": "missing"}]

    assert executor._only_relation_invalid(Failure()) is False
    assert executor._only_relation_invalid(ValueError("synthetic-private-error")) is False


def test_frozen_v221_and_non_opted_in_v222_keep_rejecting_duplicates():
    text = json.dumps(duplicate_wire())
    old = frozen._TrackingOutputCodec(PydanticOutputCodec(SecurityAnalysisOutputV220),
        output_model=SecurityAnalysisOutputV220, structured_output_recovery=True)
    for output_codec in [old, codec(enabled=False)]:
        with pytest.raises(ValidationError):
            output_codec.decode(text)
        assert output_codec.json_normalizations == []


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("kind", ["related", "groups", "both"])
def test_local_relation_recovery_succeeds_without_an_extra_model_request(monkeypatch, provider, kind):
    requests, options = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(duplicate_wire(kind), ensure_ascii=False))))
    result = execute(provider)
    assert result.succeeded and result.output == SecurityAnalysisOutputV220.model_validate(wire())
    assert len(requests) == 1
    retry = result.telemetry["output_validation_retry"]
    assert retry["version"] == "structured-output-repair-v4"
    assert retry["attempt_count"] == 1 and not retry["attempted"]
    assert retry["attempts"][0]["validation_issues"] == []
    assert retry["attempts"][0]["json_normalizations"]
    assert result.telemetry["raw_provider_body_stored"] is False
    assert options[0]["verify"] and not options[0]["follow_redirects"] and not options[0]["trust_env"]


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_unsafe_relations_use_existing_four_full_json_attempts_and_fixed_feedback(monkeypatch, provider):
    value = wire()
    value["evidence_relations"] = [{"representative_index": 1, "related_indexes": [1]}]
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(value))))
    result = execute(provider)
    assert not result.succeeded and result.output is None and len(requests) == 4
    retry = result.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == retry["max_attempts"] == 4
    assert retry["stop_reason"] == "attempt_limit"
    assert result.error == "output_validation_failed"
    bodies = [json.loads(request.content) for request in requests]
    limit = "max_tokens" if provider == "vllm" else "max_completion_tokens"
    assert all(body[limit] == 3072 and body["model"] == profile(provider).model_name for body in bodies)
    users = [[message for message in body["messages"] if message["role"] == "user"] for body in bodies]
    assert all(messages == users[0] for messages in users)
    for body in bodies[1:]:
        feedback = json.dumps(body, ensure_ascii=False)
        for expected in ["0부터 시작", "자기 자신", "중복하지", "필요하지 않을 때만", "필요한 관련 원문", "supports"]:
            assert expected in feedback
        assert "핵심 1문장" not in feedback and "핵심 1~2문장" not in feedback


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_relation_error_can_be_corrected_by_the_existing_full_json_retry(monkeypatch, provider):
    bad = wire()
    bad["evidence_relations"][0]["related_indexes"] = [1]
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion(json.dumps(bad if count == 1 else wire(), ensure_ascii=False))))
    result = execute(provider)
    assert result.succeeded and len(requests) == 2
    assert result.telemetry["output_validation_retry"]["recovered"]


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("reason", ["length", "max_tokens", "timeout", "content_filter"])
def test_incomplete_or_filtered_provider_envelopes_are_not_locally_repaired(monkeypatch, provider, reason):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(duplicate_wire()), finish_reason=reason)))
    monkeypatch.setattr(executor, "recover_relation_duplicates", lambda _: pytest.fail("No relation recovery expected"))
    result = execute(provider)
    retryable = reason != "content_filter" and (provider == "vllm" or reason == "length")
    assert not result.succeeded and result.output is None and len(requests) == (4 if retryable else 1)
    assert all(not attempt["json_normalizations"] for attempt in result.telemetry["output_validation_retry"]["attempts"])


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_refusal_and_broken_json_cannot_enter_relation_recovery(monkeypatch, provider):
    monkeypatch.setattr(executor, "recover_relation_duplicates", lambda _: pytest.fail("No relation recovery expected"))
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(duplicate_wire()), refusal="synthetic-private-refusal")))
    result = execute(provider)
    assert not result.succeeded and len(requests) == 1
    assert "synthetic-private" not in str(result.error) + json.dumps(result.telemetry)
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion('{"synthetic-private-output":')))
    result = execute(provider)
    assert not result.succeeded and len(requests) == 4
    assert "synthetic-private" not in str(result.error) + json.dumps(result.telemetry)


def test_only_pure_relation_retries_avoid_unrelated_sentence_shrinking():
    relation = {"retry_kind": "schema_correction", "validation_issues": [{"field": "evidence_relations", "type": "evidence_relation_invalid"}], "provider_finish_reason": None}
    other = {**relation, "validation_issues": [{"field": "summary_ko", "type": "string_too_long"}]}
    for level in [1, 2, 3]:
        text = executor._retry_instructions([relation] * level, 3072, SecurityAnalysisOutputV220, structured_output_recovery=True)
        assert "핵심 1문장" not in text and "핵심 1~2문장" not in text
        assert "evidence_relations" in text
    assert "핵심 1문장" in executor._retry_instructions([other] * 2, 3072, SecurityAnalysisOutputV220, structured_output_recovery=True)
    length = {"retry_kind": "compact_output", "validation_issues": [], "provider_finish_reason": "length", "error_code": "vllm_output_incomplete"}
    assert "핵심 1문장" in executor._retry_instructions([length, relation], 3072, SecurityAnalysisOutputV220, structured_output_recovery=True)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind,reason,code", [("schema_correction", None, "output_validation_failed"),
    ("compact_output", "length", "vllm_output_incomplete"), ("timeout_recovery", "timeout", "vllm_output_incomplete")])
@pytest.mark.parametrize("level", [1, 2, 3])
def test_nonrelation_feedback_matches_frozen_v221_exactly(enabled, kind, reason, code, level):
    attempt = {"retry_kind": kind, "validation_issues": [{"field": "summary_ko", "type": "string_too_long"}],
        "provider_finish_reason": reason, "error_code": code}
    args = ([attempt] * level, 3072, SecurityAnalysisOutputV220)
    assert executor._retry_instructions(*args, structured_output_recovery=enabled) == frozen._retry_instructions(*args, structured_output_recovery=enabled)


def test_fixed_relation_feedback_never_includes_arbitrary_provider_paths_or_values():
    text = executor._structured_repair_instructions([
        {"field": "synthetic-private-value", "type": "synthetic-private-error"},
        {"field": "evidence_relations", "type": "evidence_relation_invalid"},
    ], SecurityAnalysisOutputV220)
    assert "synthetic-private" not in text
    assert "evidence_relation_invalid" in text and "전체 JSON" in text
