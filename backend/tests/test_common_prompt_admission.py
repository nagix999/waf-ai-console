"""Test and Production share prompt selection; only their LLM roles differ.

Synthetic isolated databases and mocked Agent calls only. Historical snapshots
remain immutable even after the shared active policy or deployed code changes.
"""
import hashlib
import inspect
import json
import uuid

import pytest
from sqlalchemy import func, select

from app import worker
from app.agent.executor import AgentCallResult
from app.agent.prompts import FIXED_INSTRUCTIONS, FIXED_RULES_VERSION, PROMPT_VERSION, build_role_instructions
from app.api.test_runs import upload_test_run
from app.models import (
    Analysis, AnalysisLabel, PromptPolicyState, PromptPolicyVersion,
    TestRun as NamedRun, VLLMProfile, VLLMTestRun, utcnow,
)
from app.schemas import AnalysisInput
from app.services.analysis import AnalysisIngestError, enqueue_analysis
from app.services.prompt_policies import read_policy_text
from app.services.prompt_snapshots import PromptSnapshotError, load_analysis_prompt
from app.services.vllm_profiles import profile_fingerprint
from test_prompt_snapshots import fake_output


POLICIES = "/api/v1/admin/prompt-policies"
TEST_RUNS = "/api/v1/test-runs"
NAME = "합성 공통 프롬프트 회귀"
OVERRIDE_FIELDS = ("prompt_policy_version_id", "fixed_rules_version", "prompt_template")


def login(client):
    assert client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "test-password",
    }).status_code == 200


def save_policy(client, text="SHARED_NEW_SYNTHETIC_POLICY"):
    current = client.get(POLICIES).json()
    response = client.post(POLICIES, json={
        "name": "합성 공통 지침", "policy_text": text,
        "change_note": "운영·테스트 공통 지침 회귀", "parent_version_id": current["active_version_id"],
    })
    assert response.status_code == 201, response.text
    return response.json(), current


def activate(client, version_id):
    current = client.get(POLICIES).json()
    response = client.post(f"{POLICIES}/{version_id}/activate", json={
        "expected_revision": current["revision"], "acknowledge_unverified": True,
    })
    assert response.status_code == 200, response.text


def submit(client, event, *, kind="direct", key=None, expected=False, overrides=None, headers=None):
    metadata = {"name": NAME, "idempotency_key": key or str(uuid.uuid4()), **(overrides or {})}
    row = dict(event)
    if expected:
        row.update(expected_verdict="false_positive", difficulty="synthetic-hard",
                   test_category="SYNTHETIC_REFERENCE_GROUP", case_name="SYNTHETIC_CASE_NAME")
    if kind == "direct":
        return client.post(TEST_RUNS, json={**metadata, "event": row}, headers=headers)
    return client.post(f"{TEST_RUNS}/uploads", data=metadata,
        files={"file": ("synthetic.json", json.dumps([row]).encode())}, headers=headers)


def install_profiles(client):
    with client.app.state.session_factory() as db:
        production = VLLMProfile(name="synthetic-production", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-production-model", status="production")
        test = VLLMProfile(name="synthetic-test", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-test-model", status="verified", is_test=True)
        db.add_all([production, test])
        db.flush()
        db.add(VLLMTestRun(profile_id=test.id, mode="full", status="passed",
            profile_fingerprint=profile_fingerprint(test), completed_at=utcnow()))
        db.commit()
        return production.id, test.id


@pytest.mark.parametrize("kind", ["direct", "upload"])
def test_test_and_production_pin_the_same_active_policy_and_fixed_rules(client, event_payload, kind):
    login(client)
    saved, current = save_policy(client)
    production = client.post("/api/v1/analyses", json=event_payload)
    response = submit(client, event_payload, kind=kind, expected=True)
    assert production.status_code == response.status_code == 202
    run = response.json()
    assert run["accepted"] == 1
    assert run["prompt_policy_version_id"] == production.json()["prompt_policy_version_id"] == current["active_version_id"]
    assert run["prompt_policy_version_id"] != saved["id"]  # Saving is not activation.
    with client.app.state.session_factory() as db:
        stored = db.get(NamedRun, run["id"])
        item = db.get(Analysis, run["items"][0]["analysis_id"])
        first = load_analysis_prompt(db.get(Analysis, production.json()["id"]), client.app.state.crypto)
        second = load_analysis_prompt(item, client.app.state.crypto)
        assert first == second
        assert stored.prompt_snapshot_ciphertext == item.prompt_snapshot_ciphertext
        text = read_policy_text(db.get(PromptPolicyVersion, first.policy_version_id), client.app.state.crypto)
        assert (first.primary_instructions, first.verifier_instructions) == build_role_instructions(text)
        assert first.fixed_rules_version == FIXED_RULES_VERSION
        assert first.fixed_rules_hash == hashlib.sha256(FIXED_INSTRUCTIONS.encode()).hexdigest()
        assert first.prompt_version == f"{PROMPT_VERSION}/policy-{first.policy_version_number}"
        assert not {"expected_verdict", "difficulty", "test_category", "case_name", *OVERRIDE_FIELDS} & set(item.extra_fields)
        assert "SYNTHETIC_REFERENCE_GROUP" not in second.primary_instructions
        assert db.scalar(select(func.count()).select_from(AnalysisLabel)) == 1
    for result in (client.get(TEST_RUNS).json()["items"][0], client.get(f"{TEST_RUNS}/{run['id']}").json()):
        assert result["prompt_policy_version_id"] == current["active_version_id"]
        assert "primary_instructions" not in result


@pytest.mark.parametrize("kind", ["direct", "upload"])
def test_shared_activation_affects_only_new_admission_and_keeps_legacy_request_hash(client, event_payload, kind):
    login(client)
    saved, current = save_policy(client)
    key = str(uuid.uuid4())
    production = client.post("/api/v1/analyses", json=event_payload).json()
    first = submit(client, event_payload, kind=kind, key=key).json()
    previous_document = {"name": NAME, "kind": kind, "rows": [event_payload],
                         "filename": "synthetic.json" if kind == "upload" else None}
    previous_hash = hashlib.sha256(json.dumps(previous_document, sort_keys=True, ensure_ascii=False,
                                             allow_nan=False).encode()).hexdigest()
    with client.app.state.session_factory() as db:
        old = db.get(NamedRun, first["id"])
        frozen = old.prompt_snapshot_ciphertext
        assert old.request_hash == previous_hash
    activate(client, saved["id"])
    replay = submit(client, event_payload, kind=kind, key=key).json()
    assert (replay["id"], replay["prompt_policy_version_id"]) == (first["id"], current["active_version_id"])
    duplicate = client.post("/api/v1/analyses", json=event_payload).json()
    assert (duplicate["id"], duplicate["prompt_policy_version_id"]) == (production["id"], current["active_version_id"])
    newer = submit(client, event_payload, kind=kind).json()
    new_production = client.post("/api/v1/analyses", json={**event_payload, "event_id": "synthetic-new-production"}).json()
    assert newer["prompt_policy_version_id"] == new_production["prompt_policy_version_id"] == saved["id"]
    with client.app.state.session_factory() as db:
        assert db.get(NamedRun, first["id"]).prompt_snapshot_ciphertext == frozen
        new_test_prompt = load_analysis_prompt(db.get(NamedRun, newer["id"]), client.app.state.crypto)
        new_production_prompt = load_analysis_prompt(db.get(Analysis, new_production["id"]), client.app.state.crypto)
        assert new_test_prompt == new_production_prompt


@pytest.mark.parametrize("kind", ["direct", "upload"])
@pytest.mark.parametrize("field", OVERRIDE_FIELDS)
def test_test_prompt_selection_is_rejected_not_silently_ignored(client, event_payload, kind, field):
    login(client)
    response = submit(client, event_payload, kind=kind, overrides={field: "SYNTHETIC_PRIVATE_OVERRIDE"})
    assert response.status_code == 422
    assert "SYNTHETIC_PRIVATE_OVERRIDE" not in response.text
    if kind == "upload":
        assert response.json()["detail"] == "test_prompt_selection_not_supported"
    with client.app.state.session_factory() as db:
        for model in (NamedRun, Analysis, AnalysisLabel):
            assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("kind", ["direct", "upload"])
def test_common_test_admission_remains_admin_only(client, event_payload, service_headers, kind):
    assert submit(client, event_payload, kind=kind).status_code == 401
    assert submit(client, event_payload, kind=kind, headers=service_headers).status_code == 403


@pytest.mark.parametrize("kind", ["direct", "upload"])
def test_corrupt_common_active_policy_fails_both_admission_paths_safely(client, event_payload, kind):
    login(client)
    _, current = save_policy(client)
    with client.app.state.session_factory() as db:
        db.get(PromptPolicyVersion, current["active_version_id"]).policy_ciphertext = "SYNTHETIC_PRIVATE_CORRUPTION"
        db.commit()
    production = client.post("/api/v1/analyses", json=event_payload)
    test = submit(client, event_payload, kind=kind)
    assert production.status_code == test.status_code == 503
    assert production.json()["detail"] == test.json()["detail"] == "prompt_policy_content_unavailable"
    assert "SYNTHETIC_PRIVATE_CORRUPTION" not in production.text + test.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0
        assert db.scalar(select(func.count()).select_from(NamedRun)) == 0


def test_different_llm_roles_execute_identical_frozen_prompts_after_activation(client, registered_vllm_target, event_payload, monkeypatch):
    login(client)
    production_id, test_id = install_profiles(client)
    client.app.state.settings.agent_mode = "moduagent"
    production = client.post("/api/v1/analyses", json=event_payload).json()
    run = submit(client, event_payload, expected=True).json()
    analysis_ids = {production["id"], run["items"][0]["analysis_id"]}
    with client.app.state.session_factory() as db:
        frozen = load_analysis_prompt(db.get(NamedRun, run["id"]), client.app.state.crypto)
    newer, _ = save_policy(client)
    activate(client, newer["id"])
    monkeypatch.setattr("app.agent.prompts.FIXED_INSTRUCTIONS", "LATER_CODE_NOT_FOR_QUEUED_ANALYSES")
    calls = []

    async def execute(**kwargs):
        if kwargs.get("egress_check"):
            kwargs["egress_check"]()
        calls.append(kwargs)
        return AgentCallResult(output=fake_output(), framework_run_id="synthetic-common-prompt",
            agent_fingerprint="synthetic", finish_reason="completed", failure_id=None,
            error=None, telemetry={"framework_version": "0.6.2"})

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        for _ in range(2):
            row = worker.claim_next(db, "synthetic-shared-prompt-worker", 300)
            assert row.id in analysis_ids
            worker.process_moduagent(db, client.app.state.crypto, row, "", 0.75)
            assert row.status == "completed"
            assert row.prompt_policy_version_id == frozen.policy_version_id != newer["id"]
    assert len(calls) == 4
    for call in calls:
        selected_analysis, role = call["session_id"].split(":")
        assert call["profile"].id == (production_id if selected_analysis == production["id"] else test_id)
        assert call["instructions"] == (frozen.primary_instructions if role == "primary" else frozen.verifier_instructions)
        for marker in ("expected_verdict", "fixed_rules_version", "prompt_policy_version_id", "SYNTHETIC_REFERENCE_GROUP",
                       "SYNTHETIC_CASE_NAME", "일차 결과 전용 문구"):
            assert marker not in call["user_input"]


@pytest.mark.parametrize("corruption", ["ciphertext", "missing", "changed_instructions"])
def test_common_snapshot_corruption_stops_before_model_call(client, registered_vllm_target, event_payload, monkeypatch, corruption):
    login(client)
    install_profiles(client)
    client.app.state.settings.agent_mode = "moduagent"
    run = submit(client, event_payload).json()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        raise AssertionError("corrupt snapshots must not invoke a model")

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        if corruption == "changed_instructions":
            altered = load_analysis_prompt(row, client.app.state.crypto).model_copy(
                update={"primary_instructions": "SYNTHETIC_PRIVATE_BAD_INSTRUCTIONS"})
            row.prompt_snapshot_ciphertext = client.app.state.crypto.encrypt_text(altered.model_dump_json())
        else:
            row.prompt_snapshot_ciphertext = None if corruption == "missing" else "SYNTHETIC_PRIVATE_BAD_CIPHER"
        db.commit()
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            worker.process_moduagent(db, client.app.state.crypto, row, "", 0.75)
    assert calls == []


def test_trusted_run_snapshot_is_validated_before_ingestion(client, event_payload):
    login(client)
    run = submit(client, event_payload).json()
    with client.app.state.session_factory() as db:
        snapshot = load_analysis_prompt(db.get(NamedRun, run["id"]), client.app.state.crypto)
        altered = snapshot.model_copy(update={"primary_instructions": "SYNTHETIC_PRIVATE_TAMPERING"})
        with pytest.raises(AnalysisIngestError) as error:
            enqueue_analysis(db, client.app.state.crypto, "synthetic-internal-only",
                AnalysisInput.model_validate(event_payload), prompt_snapshot=altered)
        assert (error.value.status_code, error.value.code) == (503, "prompt_snapshot_invalid")
        assert "SYNTHETIC_PRIVATE_TAMPERING" not in str(error.value)
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1


def test_legacy_upload_wrapper_uses_the_same_common_selection(client, event_payload):
    assert not set(OVERRIDE_FIELDS) & set(inspect.signature(upload_test_run).parameters)
    login(client)
    current = client.get(POLICIES).json()
    params = {"name": NAME, "idempotency_key": "synthetic-common-legacy-upload"}
    content = json.dumps([{**event_payload, "expected_verdict": "false_positive"}]).encode()
    response = client.post("/api/v1/test-uploads", params=params, files={"file": ("legacy.json", content)})
    assert response.status_code == 202, response.text
    assert (response.json()["accepted"], response.json()["label_attached"]) == (1, 1)
    with client.app.state.session_factory() as db:
        snapshot = load_analysis_prompt(db.get(Analysis, response.json()["analysis_ids"][0]), client.app.state.crypto)
        assert snapshot.policy_version_id == current["active_version_id"]
        assert snapshot.fixed_rules_version == FIXED_RULES_VERSION
    for field in OVERRIDE_FIELDS:
        rejected = client.post("/api/v1/test-uploads", params=params, data={field: "synthetic-override"},
                               files={"file": ("legacy.json", content)})
        assert rejected.status_code == 422
