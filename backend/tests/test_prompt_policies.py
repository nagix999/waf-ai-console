"""Synthetic/offline policy storage, authorization and concurrency checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.agent.prompts import DEFAULT_POLICY_TEXT, FIXED_INSTRUCTIONS, FIXED_RULES_VERSION
from app.database import Base, build_engine, build_session_factory
from app.models import AccessAudit, PromptPolicyState, PromptPolicyVersion, VLLMProfile
from app.prompt_schemas import PromptPolicyCreate
from app.services.crypto import CryptoService
from app.services.prompt_policies import (
    PromptPolicyError, activate_policy_version, content_hash, create_policy_version,
    get_active_policy, get_policy_state, read_policy_text,
)


ROOT = "/api/v1/admin/prompt-policies"


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def policy_payload(**changes):
    return {"name": "합성 정책", "policy_text": "확인된 원문과 판정 이유를 구체적으로 설명한다.", "change_note": "합성 문체 검토", **changes}


@pytest.mark.parametrize("method,path,body", [
    ("get", ROOT, None),
    ("get", ROOT + "/missing", None),
    ("post", ROOT, policy_payload()),
    ("post", ROOT + "/missing/activate", {"expected_revision": 1, "acknowledge_unverified": True}),
])
def test_policy_endpoints_are_admin_only(client, service_headers, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    assert getattr(client, method)(path, headers=service_headers, **kwargs).status_code == 403
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(PromptPolicyVersion)) == 0


def test_default_bootstrap_list_detail_and_read_audit(client, monkeypatch):
    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("Settings must not call a model")

    monkeypatch.setattr("app.agent.executor.execute_structured_agent", unexpected_model_call)
    login(client)
    first = client.get(ROOT).json()
    assert first["revision"] == 1
    assert first["fixed_instructions"] == FIXED_INSTRUCTIONS
    assert first["fixed_rules_version"] == FIXED_RULES_VERSION
    assert first["max_policy_chars"] == 4000
    assert len(first["items"]) == 1
    item = first["items"][0]
    assert item["id"] == first["active_version_id"]
    assert item["version_number"] == 1
    assert item["quality_status"] == "not_evaluated"
    assert "policy_text" not in item
    assert "policy_ciphertext" not in item
    detail = client.get(f"{ROOT}/{item['id']}").json()
    assert detail["policy_text"] == DEFAULT_POLICY_TEXT
    assert detail["content_hash"] == content_hash(DEFAULT_POLICY_TEXT)
    assert client.get(ROOT).json() == first
    with client.app.state.session_factory() as db:
        stored = db.get(PromptPolicyVersion, item["id"])
        assert DEFAULT_POLICY_TEXT not in stored.policy_ciphertext
        assert db.scalar(select(func.count()).select_from(PromptPolicyVersion)) == 1
        actions = db.scalars(select(AccessAudit.action)).all()
        assert actions.count("list_prompt_policies") == 2
        assert actions.count("view_prompt_policy") == 1
        assert actions.count("initialize_prompt_policy") == 1


def test_save_clone_activate_compare_and_restore_preserve_versions(client):
    login(client)
    initial = client.get(ROOT).json()
    previous = client.get(f"{ROOT}/{initial['active_version_id']}").json()
    payload = policy_payload(name="  새 지침  ", policy_text="  첫 줄\n\t둘째 줄 {{ event.payload }} ${HOME}  ", parent_version_id=previous["id"])
    response = client.post(ROOT, json=payload)
    assert response.status_code == 201
    saved = response.json()
    assert saved["name"] == "새 지침"
    assert saved["policy_text"] == "첫 줄\n\t둘째 줄 {{ event.payload }} ${HOME}"
    assert saved["parent_version_id"] == previous["id"]
    assert saved["version_number"] == 2
    assert saved["quality_status"] == "not_evaluated"
    assert saved["created_by"] == "admin"
    assert client.get(ROOT).json()["active_version_id"] == previous["id"]
    assert client.put(f"{ROOT}/{saved['id']}", json=payload).status_code == 405
    assert client.delete(f"{ROOT}/{saved['id']}").status_code == 405
    activated = client.post(f"{ROOT}/{saved['id']}/activate", json={"expected_revision": 1, "acknowledge_unverified": True})
    assert activated.status_code == 200
    assert activated.json() == {"active_version_id": saved["id"], "revision": 2}
    stale = client.post(f"{ROOT}/{previous['id']}/activate", json={"expected_revision": 1, "acknowledge_unverified": True})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "prompt_policy_changed_concurrently"
    restored = client.post(f"{ROOT}/{previous['id']}/activate", json={"expected_revision": 2, "acknowledge_unverified": True})
    assert restored.json() == {"active_version_id": previous["id"], "revision": 3}
    assert client.get(f"{ROOT}/{previous['id']}").json() == previous
    assert client.get(f"{ROOT}/{saved['id']}").json() == saved
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(PromptPolicyVersion)) == 2
        actions = db.scalars(select(AccessAudit.action)).all()
        assert actions.count("activate_prompt_policy") == 2


@pytest.mark.parametrize("field,value", [
    ("policy_text", ""), ("policy_text", " \n\t "), ("policy_text", "가" * 4001),
    ("policy_text", "x\x00y"), ("policy_text", "x\x7fy"), ("policy_text", "x\u202ey"),
    ("policy_text", "x\ud800y"), ("policy_text", "x\r\ny"),
    ("name", " "), ("name", "a" * 121), ("name", "x\ny"),
    ("change_note", ""), ("change_note", "a" * 1001), ("change_note", "x\ty"),
    ("policy_text", 123), ("unknown", "unaccepted"),
])
def test_invalid_policy_content_is_rejected_without_echo(client, field, value):
    login(client)
    response = client.post(ROOT, content=json.dumps(policy_payload(**{field: value}), ensure_ascii=True), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert all("input" not in error and "ctx" not in error for error in errors)
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(PromptPolicyVersion)) == 0


@pytest.mark.parametrize("ack", [False, "true", 1, None])
def test_activation_requires_explicit_boolean_acknowledgement(client, ack):
    login(client)
    initial = client.get(ROOT).json()
    response = client.post(f"{ROOT}/{initial['active_version_id']}/activate", json={"expected_revision": 1, "acknowledge_unverified": ack})
    assert response.status_code == 422
    assert client.get(ROOT).json()["revision"] == 1


def test_unknown_versions_and_missing_ack_do_not_change_active(client):
    login(client)
    initial = client.get(ROOT).json()
    assert client.get(ROOT + "/missing").status_code == 404
    assert client.post(ROOT, json=policy_payload(parent_version_id="missing")).status_code == 404
    assert client.post(ROOT + "/missing/activate", json={"expected_revision": 1, "acknowledge_unverified": True}).status_code == 404
    assert client.post(f"{ROOT}/{initial['active_version_id']}/activate", json={"expected_revision": 1}).status_code == 422
    assert client.get(ROOT).json() == initial


@pytest.mark.parametrize("corruption,code", [
    ("ciphertext", "prompt_policy_content_unavailable"),
    ("hash", "prompt_policy_integrity_failed"),
])
def test_corrupt_policy_is_not_returned_or_activated(client, corruption, code):
    login(client)
    initial = client.get(ROOT).json()
    version_id = initial["active_version_id"]
    with client.app.state.session_factory() as db:
        row = db.get(PromptPolicyVersion, version_id)
        if corruption == "ciphertext":
            row.policy_ciphertext = "synthetic-invalid-ciphertext"
        else:
            row.content_hash = "변경된 해시"
        db.commit()
    response = client.get(f"{ROOT}/{version_id}")
    assert response.status_code == 503
    assert response.json()["detail"] == code
    activation = client.post(f"{ROOT}/{version_id}/activate", json={"expected_revision": 1, "acknowledge_unverified": True})
    assert activation.status_code == 503
    with client.app.state.session_factory() as db:
        assert db.get(PromptPolicyState, 1).revision == 1


def test_bootstrap_helper_does_not_commit_callers_transaction(client):
    with client.app.state.session_factory() as db:
        version = get_active_policy(db, client.app.state.crypto)
        assert version.version_number == 1
        db.rollback()
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(PromptPolicyVersion)) == 0
        assert db.get(PromptPolicyState, 1) is None


def test_concurrent_bootstrap_creates_only_one_default(tmp_path, settings):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'concurrent-default.db'}")
    Base.metadata.create_all(engine)
    sessions = build_session_factory(engine)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    barrier = Barrier(2)

    def initialize():
        with sessions() as db:
            barrier.wait()
            version = get_active_policy(db, crypto)
            db.commit()
            return version.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: initialize(), range(2)))
    assert ids[0] == ids[1]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PromptPolicyVersion)) == 1
        assert db.get(PromptPolicyState, 1).revision == 1
    engine.dispose()


def test_concurrent_activation_is_compare_and_swap(tmp_path, settings):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'concurrent-activation.db'}")
    Base.metadata.create_all(engine)
    sessions = build_session_factory(engine)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with sessions() as db:
        previous = get_active_policy(db, crypto)
        saved = create_policy_version(db, crypto, PromptPolicyCreate(**policy_payload()), "synthetic-admin")
        ids = [previous.id, saved.id]
        db.commit()
    barrier = Barrier(2)

    def activate(version_id):
        with sessions() as db:
            barrier.wait()
            try:
                changed = activate_policy_version(db, crypto, version_id, 1)
                db.commit()
                return changed.active_version_id
            except PromptPolicyError as exc:
                db.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(activate, ids))
    assert results.count("prompt_policy_changed_concurrently") == 1
    with sessions() as db:
        state = db.get(PromptPolicyState, 1)
        assert state.revision == 2
        assert state.active_version_id in results
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()


def test_database_restricts_active_version_deletion_and_multiple_state(client):
    with client.app.state.session_factory() as db:
        version = get_active_policy(db, client.app.state.crypto)
        db.commit()
        db.delete(version)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(PromptPolicyState(id=2, active_version_id=version.id, revision=1))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_creation_and_activation_helpers_leave_transaction_to_caller(client):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        previous = get_active_policy(db, crypto)
        original_id = previous.id
        db.commit()
        saved = create_policy_version(db, crypto, PromptPolicyCreate(**policy_payload()), "synthetic-admin")
        activate_policy_version(db, crypto, saved.id, 1)
        db.rollback()
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(PromptPolicyVersion)) == 1
        state = db.get(PromptPolicyState, 1)
        assert state.active_version_id == original_id
        assert state.revision == 1


def test_impossible_policy_can_be_saved_but_not_activated_for_current_profile(client, monkeypatch):
    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("Policy activation must not call a model")

    monkeypatch.setattr("app.agent.executor.execute_structured_agent", unexpected_model_call)
    login(client)
    initial = client.get(ROOT).json()
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(
            name="synthetic-small-context", base_url="http://vllm.internal:8000/v1", model_name="synthetic",
            context_window=8192, max_output_tokens=3072, status="production",
        ))
        db.commit()
    saved_response = client.post(ROOT, json=policy_payload(policy_text="가" * 4000))
    assert saved_response.status_code == 201
    saved = saved_response.json()
    rejected = client.post(f"{ROOT}/{saved['id']}/activate", json={"expected_revision": 1, "acknowledge_unverified": True})
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "prompt_policy_context_budget_too_small"
    current = client.get(ROOT).json()
    assert current["active_version_id"] == initial["active_version_id"]
    assert current["revision"] == 1
    assert client.get(f"{ROOT}/{saved['id']}").json() == saved
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AccessAudit).where(AccessAudit.action == "activate_prompt_policy")) == 0


@pytest.mark.parametrize("remaining_tokens,accepted", [(341, False), (342, True)])
def test_activation_uses_worker_minimum_input_budget_boundary(client, remaining_tokens, accepted):
    from app.agent.prompts import policy_reserved_tokens

    login(client)
    client.get(ROOT)
    saved = client.post(ROOT, json=policy_payload(policy_text="합성 지침")).json()
    output_tokens = 1024
    context_window = policy_reserved_tokens(saved["policy_text"]) + output_tokens + remaining_tokens
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(
            name="synthetic-boundary", base_url="http://vllm.internal:8000/v1", model_name="synthetic",
            context_window=context_window, max_output_tokens=output_tokens, status="production",
        ))
        db.commit()
    response = client.post(f"{ROOT}/{saved['id']}/activate", json={"expected_revision": 1, "acknowledge_unverified": True})
    assert response.status_code == (200 if accepted else 422)
    assert client.get(ROOT).json()["revision"] == (2 if accepted else 1)


def test_activation_without_production_profile_does_not_validate_draft_profile_budget(client):
    login(client)
    client.get(ROOT)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(
            name="synthetic-draft", base_url="http://vllm.internal:8000/v1", model_name="synthetic",
            context_window=4096, max_output_tokens=3072, status="draft",
        ))
        db.commit()
    saved = client.post(ROOT, json=policy_payload(policy_text="가" * 4000)).json()
    response = client.post(f"{ROOT}/{saved['id']}/activate", json={"expected_revision": 1, "acknowledge_unverified": True})
    assert response.status_code == 200
    assert response.json()["active_version_id"] == saved["id"]
