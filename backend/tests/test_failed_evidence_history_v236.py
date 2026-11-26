"""Encrypted worker persistence and audited admin-only reading, synthetic data."""
from copy import deepcopy
import json

import pytest

from app import worker_v236 as worker
from app.agent.contracts_v234 import SecurityAnalysisOutputV234
from app.agent.executor import AgentCallResult
from app.agent.rejected_evidence_v1 import rejected_evidence_snapshot
from app.models import AgentRun, Analysis, VLLMProfile
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from app.services.worker_runtime_v236 import process_moduagent
from test_flat_evidence_v228 import wire




def rejected_record(text):
    snapshot = rejected_evidence_snapshot({"evidence": [
        {"evidence_id": "e1", "source_id": "c1", "supports": "attack_request",
         "interpretation_ko": text, "related_evidence_ids": ["e2"]},
        {"evidence_id": "e2", "source_id": "c1", "supports": "normal_request",
         "interpretation_ko": "합성 반대 해석입니다.", "related_evidence_ids": []}]})
    return {**snapshot, "attempt": 1,
            "validation_issues": [{"field": "evidence", "type": "evidence_support_conflict"}]}


def test_step_serializer_correlates_multiple_calls_without_duplicate_sensitive_records():
    first, second = rejected_record("FIRST_PRIVATE_SYNTHETIC"), rejected_record("SECOND_PRIVATE_SYNTHETIC")
    histories = [{"attempt": 1, "output": None, "output_validation_history": [first]},
                 {"attempt": 2, "output": None, "output_validation_history": [second]},
                 {"attempt": 3, "kind": "decision_review", "output": None, "output_validation_history": []}]
    before = deepcopy(histories)
    call = AgentCallResult(None, "synthetic-run", None, "error", None, "output_validation_failed", {}, histories)
    value = worker._agent_step_output(call)
    assert value["output_validation_history"] == [
        {**first, "grounding_attempt": 1}, {**second, "grounding_attempt": 2}]
    assert not any("output_validation_history" in entry for entry in value["grounding_history"])
    assert value["validated_output"] is None
    assert histories == before and call.telemetry == {}


def test_legacy_or_success_only_output_does_not_invent_failure_records():
    call = AgentCallResult(None, None, None, "error", None, None, {}, [{"attempt": 1, "output": None}])
    assert "output_validation_history" not in worker._agent_step_output(call)


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("failed_role", [None, "primary", "verifier"])
def test_worker_encrypts_failed_attempts_excludes_them_from_results_and_audits_access(
        client, event_payload, service_headers, monkeypatch, caplog, failed_role):
    event = {**event_payload, "waf_action": "A",
             "payload": "POST /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\nsynthetic body"}
    created = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert created.status_code == 202, created.text
    identifier = created.json()["id"]
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-v236", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="production"))
        row = db.get(Analysis, identifier)
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.36"
        row.prompt_version, row.prompt_policy_version_id = prompt.prompt_version, prompt.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(prompt.model_dump_json())
        db.commit()

    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        role = "verifier" if kwargs["agent_name"] == "waf-verifier" else "primary"
        calls.append(role)
        assert "PRIVATE_SYNTHETIC" not in kwargs["user_input"]
        history = [rejected_record(f"PRIVATE_SYNTHETIC_{role}")]
        history[0]["next_retry_context"] = {"target_attempt": 2, "envelope": {
            "version": "evidence-conflict-correction-v1",
            "evidence": [{"interpretation_ko": f"PRIVATE_SYNTHETIC_CORRECTION_{role}"}]}}
        assert kwargs["conflict_repair_char_budget"] > len(kwargs["user_input"])
        if failed_role == role:
            return AgentCallResult(None, f"synthetic-{role}", None, "error", None,
                "output_validation_failed", {"error_summary": {"code": "output_validation_failed"}}, history)
        document = json.loads(kwargs["user_input"])
        source = next(item for item in document["evidence_candidates"]["items"] if item["field"] == "payload")
        value = wire()
        value["evidence"] = [{"evidence_id": "e4", "source_id": source["source_id"],
            "interpretation_ko": "합성 요청의 동작을 실제 원문과 대조해 해석했습니다.",
            "supports": "attack_request", "related_evidence_ids": []}]
        value["signature_assessment"].update(relation="exact", uncertainty_ko=None,
            matched_points=["합성 탐지 설명과 요청을 비교했습니다."])
        value["confidence_score"] = .9
        return AgentCallResult(SecurityAnalysisOutputV234.model_validate(value), f"synthetic-{role}",
            None, "completed", None, None, {}, history)

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        if failed_role == "primary":
            with pytest.raises(worker.WorkerExecutionError, match="primary_agent_failed"):
                process_moduagent(db, crypto, row, "", .75)
            assert row.result_json is None
        else:
            process_moduagent(db, crypto, row, "", .75)
            assert row.status == "completed"
            assert row.verdict == ("inconclusive" if failed_role == "verifier" else "true_positive")
            assert "PRIVATE_SYNTHETIC" not in json.dumps(row.result_json)
        run = db.query(AgentRun).filter_by(analysis_id=identifier).one()
        role_steps = [step for step in run.steps if step.step_type in {"llm_primary", "llm_verifier"}]
        assert len(role_steps) == (1 if failed_role == "primary" else 2)
        for step in role_steps:
            assert "PRIVATE_SYNTHETIC" not in step.output_ciphertext
            assert "PRIVATE_SYNTHETIC" not in json.dumps(step.metadata_json)
            assert "PRIVATE_SYNTHETIC" not in json.dumps(step.tool_calls_json)
            output = json.loads(crypto.decrypt_text(step.output_ciphertext))
            assert len(output["output_validation_history"]) == 1
            diagnostic = output["output_validation_history"][0]
            assert diagnostic["attempt"] == diagnostic["grounding_attempt"] == 1
            assert diagnostic["evidence"][0]["evidence_id"] == "e1"
            assert "PRIVATE_SYNTHETIC" in diagnostic["evidence"][0]["interpretation_ko"]
            assert "PRIVATE_SYNTHETIC_CORRECTION" in json.dumps(diagnostic["next_retry_context"])
            assert "PRIVATE_SYNTHETIC" not in json.dumps(output["validated_output"])
            if step.step_type == f"llm_{failed_role}":
                assert output["validated_output"] is None
        db.commit()

    path = f"/api/v1/analyses/{identifier}/agent-runs"
    denied = client.get(path)
    assert denied.status_code == 401 and denied.headers["cache-control"] == "no-store"
    denied = client.get(path, headers=service_headers)
    assert denied.status_code == 403 and denied.headers["cache-control"] == "no-store"
    public = client.get(f"/api/v1/analyses/{identifier}?result_version=v3", headers=service_headers)
    assert public.status_code == 200 and "PRIVATE_SYNTHETIC" not in public.text
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200
    viewed = client.get(path)
    assert viewed.status_code == 200 and viewed.headers["cache-control"] == "no-store"
    assert "PRIVATE_SYNTHETIC" in viewed.text
    assert any(audit["action"] == "view_agent_runs" and audit["resource_id"] == identifier
               for audit in client.get("/api/v1/audit-logs").json())
    assert "PRIVATE_SYNTHETIC" not in caplog.text


def test_missing_history_is_not_cached(client):
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    response = client.get("/api/v1/analyses/synthetic-missing/agent-runs")
    assert response.status_code == 404 and response.headers["cache-control"] == "no-store"
