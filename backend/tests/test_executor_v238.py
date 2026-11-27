"""Real ModuAgent with synthetic HTTP; no live providers or runtime settings."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.agent import executor_v238 as executor
from app.agent.contracts_v238 import SecurityAnalysisOutputV238
from test_agent_openai import openai_profile
from test_contracts_v238 import mixed
from test_evidence_interpretations_v227 import request_input
from test_executor_v234 import install_sequence


def execute(provider="vllm", **changes):
    kwargs = dict(profile=openai_profile(provider=provider,
        base_url="http://10.0.0.10:8000/v1" if provider == "vllm" else "https://api.openai.com/v1"),
        api_key="synthetic-key", instructions="Synthetic independent security analysis.",
        user_input=request_input(), session_id="synthetic:primary", agent_name="waf-primary",
        egress_check=lambda: None, output_model=SecurityAnalysisOutputV238,
        structured_output_recovery=True)
    return asyncio.run(executor.execute_structured_agent(**{**kwargs, **changes}))


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("indirect", [False, True])
def test_mixed_dependencies_finish_first_time_without_relabeling_or_extra_call(monkeypatch, provider, indirect):
    value = mixed(indirect=indirect)
    before = deepcopy(value)
    requests = install_sequence(monkeypatch, [(value, "stop")])
    call = execute(provider)
    assert call.succeeded and len(requests) == 1
    assert call.output.model_dump(mode="json") == value == before
    assert call.output.interpretation_conflict is False
    retry = call.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == 1 and retry["attempts"][0]["validation_issues"] == []
    body = json.loads(requests[0].content)
    schema = body["response_format"]["json_schema"]["schema"]
    fields = schema["$defs"]["FlatEvidenceInterpretation"]["properties"]
    assert list(fields) == ["evidence_id", "source_id", "interpretation_ko", "supports", "related_evidence_ids"]
    assert "contributes_to_evidence_ids" not in str(schema)
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 3072


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_other_bad_reference_still_uses_existing_retry_then_accepts_mixture(monkeypatch, provider):
    invalid, valid = mixed(), mixed()
    invalid["evidence"][0]["related_evidence_ids"] = ["e1"]
    requests = install_sequence(monkeypatch, [(invalid, "stop"), (valid, "stop")])
    call = execute(provider)
    assert call.succeeded and len(requests) == 2
    assert call.output.model_dump(mode="json") == valid
    assert call.telemetry["output_validation_retry"]["attempts"][0]["validation_issues"] == [
        {"field": "evidence.related_evidence_ids", "type": "evidence_link_unknown"}]
    assert not call.output.interpretation_conflict


@pytest.mark.parametrize("reason", ["length", "max_tokens"])
def test_provider_truncation_is_not_accepted_even_if_json_itself_is_complete(monkeypatch, reason):
    value = mixed()
    requests = install_sequence(monkeypatch, [(value, reason), (value, "stop")])
    call = execute()
    assert call.succeeded and len(requests) == 2
    assert call.telemetry["output_validation_retry"]["attempts"][0]["provider_finish_reason"] == reason
    assert call.output.model_dump(mode="json") == value


def test_reference_source_restriction_still_fails_closed_in_direct_executor(monkeypatch, caplog):
    value = mixed()
    value["evidence"][0]["source_id"] = "c3"
    requests = install_sequence(monkeypatch, [(value, "stop")])
    call = execute()
    assert not call.succeeded and len(requests) == 4
    assert call.telemetry["error_summary"]["code"] == "output_validation_failed"
    assert all(row["validation_issues"] == [{"field": "evidence", "type": "evidence_source_support_forbidden"}]
               for row in call.telemetry["output_validation_retry"]["attempts"])
    assert value["evidence"][0]["interpretation_ko"] not in caplog.text


def test_known_duplicate_refs_are_losslessly_normalized_without_dropping_opposing_dependency(monkeypatch):
    value = mixed()
    value["evidence"][0]["related_evidence_ids"] = ["e5", "e5"]
    before = deepcopy(value)
    requests = install_sequence(monkeypatch, [(value, "stop")])
    call = execute()
    assert call.succeeded and len(requests) == 1
    assert call.output.evidence[0].related_evidence_ids == ["e5"]
    assert call.output.evidence[1].supports.value == "normal_request"
    assert value == before
