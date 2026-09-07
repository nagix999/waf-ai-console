import json

import pytest
from sqlalchemy import select

from app import worker
from app.agent.executor import AgentCallResult
from app.models import AgentStep, Analysis, VLLMProfile
from app.services.crypto import CryptoService
from app.services.http_parser import HTTP_PARSER_VERSION
from test_moduagent_worker import primary_output

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


@pytest.mark.parametrize("mode", ["stub", "moduagent"])
@pytest.mark.parametrize("raw,parse_status", [
    ("GET /synthetic HTTP/1.1\r\nHost: synthetic.test\r\n\r\n", "success"),
    ("GET /synthetic\r\nHost: synthetic.test\r\n\r\n", "partial"),
    ("GET http://[synthetic-invalid/ HTTP/1.1\r\n\r\n", "partial"),
    (r"GET /synthetic HTTP/1.1\r\nHost: synthetic.test\r\n\r\n", "partial"),
    ("synthetic non-HTTP payload", "failed"),
])
def test_parser_status_is_recorded_without_claiming_an_extractor_call(
    client, event_payload, service_headers, settings, monkeypatch, mode, raw, parse_status,
):
    event_payload["payload"] = raw
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        return AgentCallResult(
            output=primary_output(excerpt="synthetic", field="payload"),
            framework_run_id=f"synthetic-{len(calls)}",
            agent_fingerprint="synthetic-fingerprint",
            finish_reason="completed",
            failure_id=None,
            error=None,
            telemetry={"framework": "moduagent", "framework_version": "0.6.2", "tool_trace": []},
        )

    monkeypatch.setattr(worker, "execute_structured_agent", fake_execute)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as db:
        if mode == "moduagent":
            db.add(VLLMProfile(
                name="synthetic-parser-profile",
                base_url="http://10.0.0.10:8000/v1",
                model_name="synthetic-model",
                status="production",
            ))
            db.commit()
        analysis = db.get(Analysis, created["id"])
        original_ciphertext = analysis.payload_ciphertext
        original_fingerprint = analysis.event_fingerprint
        if mode == "moduagent":
            worker.process_moduagent(db, crypto, analysis, "10.0.0.10:8000", 0.75)
        else:
            worker.process_stub(db, crypto, analysis)

        parser_step = db.scalar(select(AgentStep).where(AgentStep.step_type == "parser"))
        assert parser_step.status == "completed"
        assert parser_step.metadata_json["parser_version"] == HTTP_PARSER_VERSION
        assert parser_step.metadata_json["parse_status"] == parse_status
        assert parser_step.metadata_json["fallback_llm_used"] is False
        assert json.loads(crypto.decrypt_text(parser_step.input_ciphertext)) == {"payload": raw}
        assert json.loads(crypto.decrypt_text(parser_step.output_ciphertext))["parse_status"] == parse_status
        assert analysis.payload_ciphertext == original_ciphertext
        assert analysis.event_fingerprint == original_fingerprint
        assert crypto.decrypt_text(analysis.payload_ciphertext) == raw
        assert analysis.status == "completed"
        if mode == "stub":
            assert calls == []
        else:
            assert [call["agent_name"] for call in calls] == (
                ["waf-primary"] if parse_status == "success" else ["waf-primary", "waf-verifier"]
            )
            for call in calls:
                document = json.loads(call["user_input"])
                assert document["event"]["payload"] == raw
                assert document["generic_http_parser_hints"]["parse_status"] == parse_status
            if parse_status != "success":
                assert "parser_incomplete" in analysis.result_json["verifier"]["reasons"]
