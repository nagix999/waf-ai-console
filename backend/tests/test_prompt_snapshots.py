from datetime import UTC, datetime, timedelta

import pytest

from app import worker
from app.agent.contracts import WAFAnalysisOutput
from app.agent.executor import AgentCallResult
from app.agent.input_builder import build_agent_input
from app.agent.prompts import DEFAULT_POLICY_TEXT, FIXED_INSTRUCTIONS, build_role_instructions
from app.models import Analysis, PromptPolicyVersion, VLLMProfile
from app.services.http_parser import parse_http_payload
from app.services.prompt_snapshots import PromptSnapshotError, load_analysis_prompt

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


BASE = "/api/v1/admin/prompt-policies"


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def create_and_activate(client, text="요청의 처리 조건을 구체적으로 설명한다."):
    current = client.get(BASE).json()
    version = client.post(BASE, json={
        "name": "새 작성 지침", "policy_text": text, "change_note": "합성 테스트용 변경",
        "parent_version_id": current["active_version_id"],
    }).json()
    assert client.post(f"{BASE}/{version['id']}/activate", json={
        "expected_revision": current["revision"], "acknowledge_unverified": True,
    }).status_code == 200
    return version


def test_queue_pins_encrypted_instructions_and_duplicate_retains_original(client, event_payload):
    login(client)
    # Ordinary ingestion tests prompt pinning independently of named test-run
    # profile snapshots, which have their own focused regression coverage.
    original = client.post("/api/v1/analyses", json=event_payload).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, original["id"])
        cipher_before = row.prompt_snapshot_ciphertext
        snapshot = load_analysis_prompt(row, client.app.state.crypto)
        assert DEFAULT_POLICY_TEXT not in cipher_before
        assert snapshot.selection_origin == "enqueue"
        assert FIXED_INSTRUCTIONS in snapshot.primary_instructions
        assert snapshot.reserved_tokens == 4096 + len(DEFAULT_POLICY_TEXT.encode("utf-8"))
    newer = create_and_activate(client)
    duplicate = client.post("/api/v1/analyses", json=event_payload).json()
    assert duplicate["id"] == original["id"]
    assert duplicate["prompt_version"] == original["prompt_version"]
    assert duplicate["prompt_policy_version_id"] != newer["id"]
    created = client.post("/api/v1/analyses", json={**event_payload, "event_id": "second-event"}).json()
    assert created["prompt_policy_version_id"] == newer["id"]
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, original["id"]).prompt_snapshot_ciphertext == cipher_before


def fake_output():
    return WAFAnalysisOutput.model_validate({
        "verdict": "inconclusive", "confidence_score": 0.5,
        "summary_ko": "일차 결과 전용 문구 — 처리 조건을 확인하지 못해 판정을 보류합니다.",
        "threat_analysis": {"severity": "UNKNOWN", "category": "unknown", "target": "payload.query", "technique_ko": "검색 요청입니다.", "potential_impact_ko": "처리 방식이 확인되지 않았습니다.", "obfuscations": []},
        "signature_assessment": {"relation": "unknown", "explanation_ko": "탐지 조건이 제공되지 않았습니다."},
        "evidence": [{"field": "payload.query", "excerpt": "q=test", "interpretation_ko": "검색 값은 있지만 실제 처리 조건을 확인하지 못했습니다."}],
        "recommended_checks": [], "analyst_checks": [], "conflicting_evidence": [],
        "tuning_recommendation": {"recommended": False}, "input_truncated": False,
    })


def test_worker_and_verifier_use_queued_snapshot_after_activation_and_code_change(client, event_payload, monkeypatch):
    login(client)
    created = client.post("/api/v1/analyses", json=event_payload).json()
    with client.app.state.session_factory() as db:
        original = load_analysis_prompt(db.get(Analysis, created["id"]), client.app.state.crypto)
        db.add(VLLMProfile(name="synthetic-profile", base_url="http://10.0.0.10:8000/v1", model_name="synthetic-model", status="production"))
        db.commit()
    newer = create_and_activate(client, "새 버전에서만 사용하는 별도 작성 지침.")
    # Even a different deployed fixed-rule implementation cannot alter the
    # complete instructions captured with this already-accepted event.
    monkeypatch.setattr("app.agent.prompts.FIXED_INSTRUCTIONS", "changed-after-enqueue")
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        return AgentCallResult(
            output=fake_output(), framework_run_id="mock-run", agent_fingerprint="mock-fingerprint",
            finish_reason="completed", failure_id=None, error=None, telemetry={"framework_version": "0.6.2"},
        )

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = worker.claim_next(db, "worker-one", 60)
        cipher_before = row.prompt_snapshot_ciphertext
        # A reclaimed lease keeps the same encrypted policy bundle.
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        reclaimed = worker.claim_next(db, "worker-two", 60)
        assert reclaimed.prompt_snapshot_ciphertext == cipher_before
        worker.process_moduagent(db, client.app.state.crypto, reclaimed, "10.0.0.10:8000", 0.75)
    assert len(calls) == 2
    assert calls[0]["instructions"] == original.primary_instructions
    assert calls[1]["instructions"] == original.verifier_instructions
    assert calls[0]["user_input"] == calls[1]["user_input"]
    assert "일차 결과 전용 문구" not in calls[1]["user_input"]
    assert "새 버전에서만" not in calls[0]["instructions"]
    detail = client.get(f"/api/v1/analyses/{created['id']}").json()
    assert detail["prompt_policy_version_id"] != newer["id"]
    assert detail["result"]["policy"]["instructions_hash"] == original.instructions_hash
    assert detail["result"]["policy"]["fixed_rules_version"] == original.fixed_rules_version
    assert "primary_instructions" not in detail["result"]["policy"]
    assert detail["prompt_version"] == original.prompt_version


@pytest.mark.parametrize("corruption", ["invalid_cipher", "changed_role", "missing_snapshot", "missing_both"])
def test_snapshot_corruption_fails_before_model_call(client, event_payload, monkeypatch, corruption):
    login(client)
    created = client.post("/api/v1/analyses", json=event_payload).json()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        raise AssertionError("model must not be called")

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, created["id"])
        if corruption == "changed_role":
            snapshot = load_analysis_prompt(row, client.app.state.crypto)
            altered = snapshot.model_copy(update={"primary_instructions": "private-invalid-policy"})
            row.prompt_snapshot_ciphertext = client.app.state.crypto.encrypt_text(altered.model_dump_json())
        else:
            row.prompt_snapshot_ciphertext = "private-invalid-policy" if corruption == "invalid_cipher" else None
            if corruption == "missing_both":
                row.prompt_policy_version_id = None
        db.commit()
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            worker.process_moduagent(db, client.app.state.crypto, row, "10.0.0.10:8000", 0.75)
    assert calls == []


def test_legacy_unpinned_event_is_marked_as_selected_at_execution(client, event_payload, monkeypatch):
    login(client)
    created = client.post("/api/v1/analyses", json=event_payload).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, created["id"])
        row.prompt_snapshot_ciphertext = None
        row.prompt_policy_version_id = None
        row.prompt_version = None
        db.commit()
    selected = create_and_activate(client)
    from app.services.prompt_snapshots import pin_analysis_prompt
    with client.app.state.session_factory() as db:
        snapshot = pin_analysis_prompt(db, client.app.state.crypto, db.get(Analysis, created["id"]), origin="legacy_execution")
        assert snapshot.policy_version_id == selected["id"]
        assert snapshot.selection_origin == "legacy_execution"


def test_policy_is_literal_text_and_roles_remain_separate():
    text = "{{ event.payload }} ${ENV_SECRET} を実行しない"
    primary, verifier = build_role_instructions(text)
    assert text in primary and text in verifier
    assert "다른 Agent의 판정이나 근거는 제공되지 않으며" in verifier
    assert primary != verifier


def test_small_context_fails_before_llm_and_preserves_pinned_policy(client, event_payload, monkeypatch):
    login(client)
    created = client.post("/api/v1/analyses", json=event_payload).json()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        raise AssertionError("insufficient context must not call a model")

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="small-synthetic", base_url="http://10.0.0.10:8000/v1", model_name="synthetic-model", context_window=8192, max_output_tokens=3072, status="production"))
        db.commit()
        row = db.get(Analysis, created["id"])
        cipher_before = row.prompt_snapshot_ciphertext
        with pytest.raises(worker.WorkerExecutionError, match="agent_context_budget_too_small"):
            worker.process_moduagent(db, client.app.state.crypto, row, "10.0.0.10:8000", 0.75)
        assert row.prompt_snapshot_ciphertext == cipher_before
    assert calls == []


def test_editable_policy_reserve_reduces_input_budget_without_modifying_payload():
    raw = "GET / HTTP/1.1\r\nHost: example.internal\r\n\r\n" + "a" * 18000
    parsed = parse_http_payload(raw)
    ordinary = build_agent_input({}, raw, parsed, 8192, 1024)
    reserved = build_agent_input({}, raw, parsed, 8192, 1024, prompt_reserved_tokens=5096)
    assert len(reserved.text) < len(ordinary.text)
    assert reserved.input_truncated is True
    with pytest.raises(ValueError, match="agent_context_budget_too_small"):
        build_agent_input({}, raw, parsed, 8192, 1024, prompt_reserved_tokens=8192)


@pytest.mark.parametrize("name", ["prompt_policy_version_id", "prompt_snapshot_ciphertext", "policy_version_id", "policy_text", "fixed_rules_version", "primary_instructions", "verifier_instructions"])
def test_ingest_cannot_select_or_override_policy(client, event_payload, service_headers, name):
    response = client.post("/api/v1/analyses", headers=service_headers, json={**event_payload, name: "private-override"})
    assert response.status_code == 422
    assert "private-override" not in response.text
