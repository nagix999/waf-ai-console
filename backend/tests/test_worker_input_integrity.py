"""Exercise the real worker/DB path with synthetic, mocked model responses."""
import json

import pytest
from sqlalchemy import select

from agent_selection_helpers import model_output
from app import worker
from app.agent.executor import AgentCallResult
from app.models import AgentStep, Analysis, VLLMProfile
from test_request_integrity import output, request


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("primary_verdict,verifier_verdict,expected,calls_expected,limited", [
    ("false_positive", "false_positive", "inconclusive", 2, True),
    ("true_positive", "true_positive", "true_positive", 1, False),
    ("false_positive", "true_positive", "inconclusive", 2, False),
    ("inconclusive", "inconclusive", "inconclusive", 2, False),
])
def test_integrity_hints_and_guard_preserve_independent_roles_and_history(
    client, event_payload, service_headers, monkeypatch,
    primary_verdict, verifier_verdict, expected, calls_expected, limited,
):
    raw = request(headers="Content-Length: 80\r\nCookie: SENSITIVE_CANARY\r\n")
    event_payload.update(payload=raw, waf_action="D" if primary_verdict == "true_positive" else "A")
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        value = output(primary_verdict if kwargs["agent_name"] == "waf-primary" else verifier_verdict)
        return AgentCallResult(model_output(value, kwargs), "synthetic-integrity", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-integrity", base_url="http://10.0.0.10:8000/v1",
                          model_name="synthetic", status="production"))
        db.commit()
        row = db.get(Analysis, created["id"])
        before = (row.payload_ciphertext, row.event_fingerprint, row.prompt_snapshot_ciphertext)
        worker.process_moduagent(db, crypto, row, "", .75)
        assert (row.payload_ciphertext, row.event_fingerprint, row.prompt_snapshot_ciphertext) == before
        assert row.status == "completed" and row.verdict == expected
        assert row.result_json["primary"]["verdict"] == primary_verdict
        if calls_expected == 2:
            assert row.result_json["verifier"]["output"]["verdict"] == verifier_verdict
        metadata = row.result_json["diagnostics"]["request_integrity"]
        assert metadata["downgraded_to_inconclusive"] is limited
        assert metadata["issue_codes"] == ["body_shorter_than_content_length"]
        assert "SENSITIVE_CANARY" not in json.dumps(row.result_json)
        assert "input_integrity_observed" in row.result_json["diagnostics"]["input_signals"]
        if limited:
            assert row.result_json["verifier"]["agreement"] is True  # Model agreement isn't final authorization.
            assert "input_integrity_requires_review" in row.result_json["verifier"]["reasons"]
            assert row.result_json["analyst_guidance"]["checks"][0]["source_ko"] == "수집 설정과 같은 요청의 수집 기록"
            assert "input_integrity_limited" in row.result_json["diagnostics"]["inconclusive_reasons"]
        steps = db.scalars(select(AgentStep)).all()
        for step in steps:
            assert "SENSITIVE_CANARY" not in json.dumps(step.metadata_json)
        parser_step = next(s for s in steps if s.step_type == "parser")
        parsed_history = json.loads(crypto.decrypt_text(parser_step.output_ciphertext))
        assert parsed_history["parse_status"] == "success"
        assert parsed_history["request_integrity"]["issues"][0]["code"] == "body_shorter_than_content_length"
        agent_input = next(s for s in steps if s.step_type == "agent_input")
        stored_input = json.loads(crypto.decrypt_text(agent_input.output_ciphertext))["user_input"]
        assert stored_input == calls[0]["user_input"]
    assert len(calls) == calls_expected
    for call in calls:
        document = json.loads(call["user_input"])
        assert document["event"]["payload"] == raw
        assert document["request_integrity"]["issues"]
        assert "expected_verdict" not in document["event"] and "primary" not in document
        assert "request_integrity" in call["instructions"]
    if calls_expected == 2:
        assert calls[0]["user_input"] == calls[1]["user_input"]
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    counts = client.get("/api/v1/admin/agent-settings/diagnostics").json()["counts"]
    assert counts["input_integrity_observed"] == 1
    assert counts["input_integrity_limited"] == int(limited)


@pytest.mark.usefixtures("registered_vllm_target")
def test_unrelated_normal_query_does_not_force_verifier_or_hold(client, event_payload, service_headers, monkeypatch):
    event_payload.update(payload=request(headers="Content-Length: 80\r\n"), waf_action="A")
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        return AgentCallResult(model_output(output("false_positive", "payload.query", "q=hello"), kwargs),
                               "synthetic", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-query", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production"))
        db.commit()
        row = db.get(Analysis, created["id"])
        worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        assert row.verdict == "false_positive" and len(calls) == 1
        assert not row.result_json["diagnostics"]["request_integrity"]["downgraded_to_inconclusive"]
