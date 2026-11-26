"""Real ModuAgent with mocked HTTP; no live LLM or production data."""
import asyncio
import json

import httpx
import pytest

from app.agent import executor_v235 as executor
from app.agent.contracts import EvidenceSelectionCorrectionOutput
from app.agent.contracts_v234 import SecurityAnalysisOutputV234
from test_agent_openai import completion, install_http, openai_profile
from test_contracts_v234 import conflict
from test_evidence_interpretations_v227 import request_input
from test_executor_v234 import install_sequence, instructions
from test_flat_evidence_v228 import wire


def execute(provider="vllm", **changes):
    arguments = dict(profile=openai_profile(provider=provider,
        base_url="http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1"),
        api_key="synthetic-key", instructions="Synthetic independent analysis.",
        user_input=request_input(), session_id="synthetic:primary", agent_name="waf-primary",
        egress_check=lambda: None, output_model=SecurityAnalysisOutputV234,
        structured_output_recovery=True)
    return asyncio.run(executor.execute_structured_agent(**{**arguments, **changes}))


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_failed_then_success_preserves_only_failed_domain_snapshot_outside_telemetry(monkeypatch, caplog, provider):
    value = conflict()
    value["evidence"][0]["interpretation_ko"] = "PRIVATE_SYNTHETIC_INTERPRETATION"
    requests = install_sequence(monkeypatch, [(value, "stop"), (wire(), "stop")])
    result = execute(provider)
    assert result.succeeded and len(requests) == 2
    assert result.output.model_dump(mode="json") == wire()
    assert len(result.grounding_history) == 1
    record = result.grounding_history[0]
    assert record["attempt"] == 1
    assert record["validation_issues"][0]["type"] == "evidence_support_conflict"
    assert record["evidence"][0] == {"position": 0, **value["evidence"][0]}
    assert record["evidence"][1] == {"position": 1, **value["evidence"][1]}
    assert "PRIVATE_SYNTHETIC" not in json.dumps(result.telemetry)
    assert "PRIVATE_SYNTHETIC" not in instructions(requests[1])
    assert "PRIVATE_SYNTHETIC" not in caplog.text
    assert result.telemetry["raw_provider_body_stored"] is False


def test_four_failures_keep_distinct_attempts_without_affecting_retry_limits(monkeypatch):
    first, second = conflict(), conflict(changed_ids=True)
    requests = install_sequence(monkeypatch, [(first, "stop"), (second, "stop")])
    result = execute()
    assert not result.succeeded and result.output is None and len(requests) == 4
    assert [row["attempt"] for row in result.grounding_history] == [1, 2, 3, 4]
    assert result.grounding_history[0]["evidence"][0]["evidence_id"] == "e2"
    assert result.grounding_history[1]["evidence"][0]["evidence_id"] == "e1"
    assert result.telemetry["output_validation_retry"]["stop_reason"] == "attempt_limit"
    assert all(row["max_output_tokens"] == 3072 for row in result.telemetry["output_validation_retry"]["attempts"])


@pytest.mark.parametrize("content, reason", [
    ('{"evidence":[', "stop"), ('{"evidence":[],"evidence":[]}', "stop"),
    ('{"evidence":NaN}', "stop"), (conflict(), "length"), (conflict(), "max_tokens"),
    ('{"evidence":[', "length"),
])
def test_incomplete_ambiguous_and_nonfinite_documents_are_never_saved(monkeypatch, content, reason):
    requests = install_sequence(monkeypatch, [(content, reason), (wire(), "stop")])
    result = execute()
    assert result.succeeded and len(requests) == 2
    assert result.grounding_history == []


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_provider_private_reasoning_and_extra_fields_never_enter_snapshots(monkeypatch, provider):
    value = conflict()
    value["private_reasoning"] = "PRIVATE_ROOT_REASONING"
    value["evidence"][0]["unknown_field"] = "PRIVATE_NESTED_FIELD"
    def response(_request, count):
        body = completion(json.dumps(value if count == 1 else wire()))
        body["choices"][0]["message"]["reasoning_content"] = "PRIVATE_PROVIDER_REASONING"
        return httpx.Response(200, json=body)
    install_http(monkeypatch, response)
    result = execute(provider)
    assert result.succeeded and len(result.grounding_history) == 1
    assert "PRIVATE_" not in json.dumps(result.grounding_history)
    assert "PRIVATE_" not in json.dumps(result.telemetry)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_refused_complete_content_is_not_collected(monkeypatch, provider):
    body = completion(json.dumps(conflict()))
    body["choices"][0]["message"]["refusal"] = "PRIVATE_REFUSAL"
    requests, _ = install_http(monkeypatch, lambda *_args: httpx.Response(200, json=body))
    result = execute(provider)
    assert not result.succeeded and len(requests) == 1
    assert result.grounding_history == []
    assert "PRIVATE_" not in json.dumps(result.telemetry)


def test_other_output_contracts_cannot_capture_unexpected_evidence(monkeypatch):
    value = {"corrections": [], "requires_reanalysis": False, "evidence": conflict()["evidence"]}
    install_sequence(monkeypatch, [(value, "stop")])
    result = execute(output_model=EvidenceSelectionCorrectionOutput, output_validation_max_attempts=1)
    assert not result.succeeded and result.grounding_history == []


def test_success_without_retries_adds_no_failed_output(monkeypatch):
    install_sequence(monkeypatch, [(wire(), "stop")])
    result = execute()
    assert result.succeeded and result.grounding_history == []
