"""Offline wire requests and trusted-schema feedback preserve domain checks."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest
from moduagent.output import PydanticOutputCodec
from pydantic import ValidationError

from app.agent import executor_v225 as frozen
from app.agent import executor_v226 as executor
from app.agent.contracts import WAFAnalysisOutput
from app.agent.contracts_v224 import SecurityAnalysisOutputV224
from app.agent.output_schema_v226 import compact_strict_schema, constraint_hints
from app.services.provider_options import strict_json_schema
from test_agent_openai import assert_strict_schema, completion, install_http, openai_profile
from test_executor_recovery_v224 import duplicate_wire, wire
from test_executor_relation_correction_v225 import request_input, self_mixed


def profile(provider):
    return openai_profile(provider=provider, base_url=("http://10.0.0.10:8000/v1"
        if provider == "vllm" else "https://api.openai.com/v1"))


def execute(provider="vllm", **kwargs):
    return asyncio.run(executor.execute_structured_agent(
        profile=profile(provider), api_key="synthetic-key", instructions="Synthetic independent analysis.",
        user_input=request_input(), session_id="synthetic:primary", agent_name="waf-primary",
        egress_check=lambda: None, output_model=SecurityAnalysisOutputV224,
        structured_output_recovery=True, **kwargs))


def codec(*, strict=False, enabled=True, model=SecurityAnalysisOutputV224):
    return executor._TrackingOutputCodec(PydanticOutputCodec(model), strict=strict,
        output_model=model, structured_output_recovery=enabled, user_input=request_input())


def schema_facts(value, path=()):
    """Collect constraints, names and descriptions while ignoring annotations."""
    facts = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if key != "title":
                facts[path + (key,)] = child if not isinstance(child, (list, dict)) else type(child).__name__
            if key in {"properties", "$defs", "definitions", "patternProperties"} and isinstance(child, dict):
                facts[path + (key, "names")] = tuple(child)
                for name, schema in child.items():
                    facts.update(schema_facts(schema, path + (key, name)))
            elif key != "title":
                facts.update(schema_facts(child, path + (key,)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            facts[path + (index,)] = child if not isinstance(child, (list, dict)) else type(child).__name__
            facts.update(schema_facts(child, path + (index,)))
    return facts


def test_compact_schema_preserves_all_constraints_refs_order_and_descriptions():
    original = SecurityAnalysisOutputV224.model_json_schema()
    before = deepcopy(original)
    expected = strict_json_schema(original)
    compact = compact_strict_schema(original)
    assert original == before
    assert schema_facts(compact) == schema_facts(expected)
    assert compact["title"] == original["title"]
    assert_strict_schema(compact)
    assert len(json.dumps(compact, ensure_ascii=False)) < len(json.dumps(expected, ensure_ascii=False))
    assert "title" not in compact["properties"]["summary_ko"]
    assert compact["properties"]["summary_ko"]["maxLength"] == 3000
    assert compact["$defs"]["CaptureAssessment"]["properties"]["reason_ko"]["maxLength"] == 240


def test_title_and_default_property_names_and_description_are_not_annotations():
    original = {"type": "object", "title": "SyntheticOutput", "properties": {
        "title": {"type": "string", "title": "Display title", "description": "Keep this description", "minLength": 2},
        "default": {"type": ["integer", "null"], "default": None, "minimum": 0},
    }}
    assert compact_strict_schema(original) == {"type": "object", "title": "SyntheticOutput", "properties": {
        "title": {"type": "string", "description": "Keep this description", "minLength": 2},
        "default": {"type": ["integer", "null"], "minimum": 0},
    }, "required": ["title", "default"], "additionalProperties": False}
    assert original["properties"]["default"]["default"] is None


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_actual_provider_request_uses_compact_strict_schema_without_extra_calls(monkeypatch, provider):
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion(json.dumps(wire(), ensure_ascii=False))))
    result = execute(provider)
    assert result.succeeded and len(requests) == 1
    body = json.loads(requests[0].content)
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema" and response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "SecurityAnalysisOutputV224"
    actual = response_format["json_schema"]["schema"]
    assert actual == compact_strict_schema(SecurityAnalysisOutputV224.model_json_schema())
    assert_strict_schema(actual)
    assert result.output == SecurityAnalysisOutputV224.model_validate(wire())
    assert result.telemetry["output_validation_retry"]["attempt_count"] == 1
    assert result.telemetry["output_validation_retry"]["max_attempts"] == 4
    assert result.telemetry["structured_output"]["schema_preparation"] == "schema-guided-output-v1"
    if provider == "vllm":
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["temperature"] == 0 and body["max_tokens"] == 3072
    else:
        assert "chat_template_kwargs" not in body and "temperature" not in body


@pytest.mark.parametrize("strict", [False, True])
def test_other_models_and_non_opted_in_schema_remain_frozen(strict):
    for model, enabled in [(WAFAnalysisOutput, True), (SecurityAnalysisOutputV224, False)]:
        actual = codec(strict=strict, enabled=enabled, model=model).schema()
        previous = frozen._TrackingOutputCodec(PydanticOutputCodec(model), strict=strict,
            output_model=model, structured_output_recovery=enabled).schema()
        assert actual == previous


@pytest.mark.parametrize("path,code,expected", [
    ("verdict", "enum", ['"true_positive"', '"false_positive"', '"inconclusive"']),
    ("signature_assessment.version", "literal_error", ['"signature-assessment-v2"']),
    ("evidence.[].supports", "enum", ['"attack_request"', '"normal_request"', '"context"']),
    ("confidence_score", "less_than_equal", ["최솟값 0", "최댓값 1", "타입 number"]),
    ("summary_ko", "string_too_long", ["최소 글자 수 1", "최대 글자 수 3000"]),
    ("evidence", "too_long", ["최대 항목 수 5", "타입 array"]),
    ("evidence_relations.[].related_indexes", "too_short", ["최소 항목 수 1", "최대 항목 수 4"]),
    ("capture_assessment.reason_ko", "string_too_long", ["최대 글자 수 240"]),
    ("decision_issue.missing_condition_ko", "string_type", ["타입 string/null"]),
])
def test_hints_follow_actual_nested_ref_nullable_and_array_schema(path, code, expected):
    hints = constraint_hints(SecurityAnalysisOutputV224.model_json_schema(), [{"field": path, "type": code}])
    assert len(hints) == 1 and path in hints[0]
    for item in expected:
        assert item in hints[0]


def test_hints_never_echo_unknown_paths_messages_or_values_and_are_bounded():
    schema = SecurityAnalysisOutputV224.model_json_schema()
    issues = [
        {"field": "synthetic-private-path", "type": "enum", "input": "synthetic-private-value"},
        {"field": "evidence.[].<unknown>", "type": "string_type", "msg": "synthetic-private-message"},
        {"field": "verdict", "type": "synthetic-private-type"},
        {"field": "verdict", "type": "enum", "msg": "synthetic-private-message", "input": "synthetic-private-value"},
    ]
    hints = constraint_hints(schema, issues * 20)
    assert len(hints) == 1 and "synthetic-private" not in str(hints)
    text = executor._structured_repair_instructions(issues, SecurityAnalysisOutputV224)
    assert "synthetic-private" not in text
    assert "허용값" in text and "tuning_recommendation" not in text and "tuning scope" not in text


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("mutate,expected", [
    (lambda value: value.update(confidence_score=2), ["최댓값 1", "confidence_score"]),
    (lambda value: value.update(verdict="invalid"), ['"true_positive"', '"false_positive"', '"inconclusive"']),
    (lambda value: value["capture_assessment"].update(reason_ko="가" * 241), ["최대 글자 수 240", "capture_assessment.reason_ko"]),
])
def test_actual_corrective_request_uses_exact_schema_constraints(monkeypatch, provider, mutate, expected):
    invalid = wire()
    mutate(invalid)
    requests, _ = install_http(monkeypatch, lambda _request, count: httpx.Response(200,
        json=completion(json.dumps(invalid if count == 1 else wire(), ensure_ascii=False))))
    result = execute(provider)
    assert result.succeeded and len(requests) == 2
    body = json.loads(requests[-1].content)
    instructions = "\n".join(message["content"] for message in body["messages"] if message["role"] == "system")
    for item in expected:
        assert item in instructions
    assert "tuning_recommendation" not in instructions and "tuning scope" not in instructions
    assert result.telemetry["output_validation_retry"]["version"] == "structured-output-repair-v6"
    assert result.output == SecurityAnalysisOutputV224.model_validate(wire())


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(confidence_score=2),
    lambda value: value.update(summary_ko="정탐입니다."),
    lambda value: (value["evidence"][0].update(supports="normal_request"),
                   value.update(evidence_relations=[{"representative_index": 1, "related_indexes": [0]}])),
    lambda value: value.update(threat_analysis={**value["threat_analysis"], "severity": "NONE"}),
])
def test_schema_preparation_does_not_relax_existing_domain_checks(mutate):
    value = wire()
    mutate(value)
    with pytest.raises(ValidationError):
        codec().decode(json.dumps(value, ensure_ascii=False))


@pytest.mark.parametrize("factory", [duplicate_wire, self_mixed])
def test_existing_lossless_relation_recovery_preserves_exact_assessment(factory):
    value = factory()
    actual = codec().decode(json.dumps(value, ensure_ascii=False))
    assert actual == SecurityAnalysisOutputV224.model_validate(wire())
    expected = {key: item for key, item in value.items() if key != "evidence_relations"}
    assert actual.model_dump(mode="json", exclude={"evidence_relations"}) == expected
