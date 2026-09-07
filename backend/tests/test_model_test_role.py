"""Independent role assignment, verified Test selection, no real provider I/O."""
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import worker
from app.models import Analysis, VLLMProfile, VLLMTestRun, TestRun as NamedRun, utcnow
from app.schemas import AnalysisInput
from app.services.analysis import enqueue_analysis
from app.services.test_runs import named_test_request_check, selected_test_request_check
from app.services.vllm_profiles import TargetNotAllowedError, profile_fingerprint
from app.services.vllm_test_runner import VLLMTestResult
from test_model_profiles import login_admin, profile_payload

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def create_verified(client, name, *, provider="vllm"):
    payload = profile_payload(name)
    if provider == "openai":
        payload.update(provider="openai", model_name="synthetic-model", base_url="https://api.openai.com/v1",
                       external_data_approved=True, tls_verify=True)
    response = client.post("/api/v1/model-profiles", json=payload)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, identifier)
        profile.status = "verified"
        profile.last_verified_at = utcnow()
        db.add(VLLMTestRun(profile_id=identifier, mode="full", status="passed",
            profile_fingerprint=profile_fingerprint(profile), completed_at=profile.last_verified_at))
        db.commit()
    return get_profile(client, identifier)


def get_profile(client, identifier):
    return next(row for row in client.get("/api/v1/model-profiles").json() if row["id"] == identifier)


def role(client, profile, action):
    return client.post(f"/api/v1/model-profiles/{profile['id']}/{action}",
        json={"expected_profile_fingerprint": profile["profile_fingerprint"]})


def named(client, event):
    response = client.post("/api/v1/test-runs", json={"name": "합성 Test 역할 검증",
        "idempotency_key": str(uuid.uuid4()), "event": event})
    assert response.status_code == 202, response.text
    return response.json()


def test_roles_are_independent_and_same_profile_can_hold_both(client):
    login_admin(client)
    first, second = create_verified(client, "synthetic-first"), create_verified(client, "synthetic-second", provider="openai")
    assert role(client, first, "promote").status_code == 200
    assert role(client, second, "assign-test").status_code == 200
    assert get_profile(client, first["id"])["status"] == "production"
    assert get_profile(client, second["id"])["status"] == "verified"
    both = role(client, first, "assign-test")
    assert both.status_code == 200 and both.json()["is_test"] is True
    assert both.json()["status"] == "production"
    assert role(client, first, "assign-test").json()["is_test"] is True
    assert get_profile(client, second["id"])["is_test"] is False
    assert role(client, second, "promote").status_code == 200
    retained = get_profile(client, first["id"])
    assert retained["is_test"] is True and retained["status"] == "verified"
    assert retained["profile_fingerprint"] == first["profile_fingerprint"]
    assert role(client, first, "unassign-test").json()["is_test"] is False
    assert get_profile(client, second["id"])["status"] == "production"


@pytest.mark.parametrize("state,mode,verdict,matching", [
    ("draft", "full", "passed", True), ("disabled", "full", "passed", True),
    ("verified", "quick", "passed", True), ("verified", "full", "failed", True),
    ("verified", "full", "passed", False),
])
def test_both_roles_require_verified_current_full_pass(client, state, mode, verdict, matching):
    login_admin(client)
    profile = create_verified(client, "synthetic-gate")
    with client.app.state.session_factory() as db:
        row = db.get(VLLMProfile, profile["id"])
        row.status = state
        passed = db.scalar(select(VLLMTestRun).where(VLLMTestRun.profile_id == row.id))
        passed.mode, passed.status = mode, verdict
        if not matching:
            passed.profile_fingerprint = "0" * 64
        db.commit()
    reported = get_profile(client, profile["id"])
    assert reported["can_assign"] is False
    assert role(client, profile, "assign-test").status_code == 409
    assert role(client, profile, "promote").status_code == 409


def test_role_assignment_requires_admin_and_current_fingerprint(client, service_headers):
    login_admin(client)
    profile = create_verified(client, "synthetic-confirm")
    for action in ("assign-test", "unassign-test", "promote"):
        response = client.post(f"/api/v1/model-profiles/{profile['id']}/{action}",
                               json={"expected_profile_fingerprint": "0" * 64})
        assert response.status_code == 409
        assert response.json()["detail"] == "model_profile_changed_reconfirm"
    assert client.post(f"/api/v1/model-profiles/{profile['id']}/assign-test").status_code == 422
    client.post("/api/v1/auth/logout")
    assert client.post(f"/api/v1/model-profiles/{profile['id']}/assign-test", headers=service_headers,
        json={"expected_profile_fingerprint": profile["profile_fingerprint"]}).status_code == 403


def test_assigned_profiles_are_immutable_and_disable_clears_test_atomically(client):
    login_admin(client)
    profile = create_verified(client, "synthetic-immutable")
    assert role(client, profile, "assign-test").status_code == 200
    changed = client.put(f"/api/v1/model-profiles/{profile['id']}", json={"timeout_seconds": 180})
    assert changed.status_code == 409 and changed.json()["detail"] == "test_profile_is_immutable"
    disabled = client.post(f"/api/v1/model-profiles/{profile['id']}/disable")
    assert disabled.json()["is_test"] is False and disabled.json()["status"] == "disabled"
    enabled = client.post(f"/api/v1/model-profiles/{profile['id']}/enable")
    assert enabled.json()["is_test"] is False and enabled.json()["status"] == "draft"
    assert enabled.json()["can_assign"] is False


@pytest.mark.parametrize("invalidate", ["rename", "disable"])
def test_quick_pass_does_not_restore_role_eligibility_after_invalidation(client, monkeypatch, invalidate):
    login_admin(client)
    profile = create_verified(client, "synthetic-revalidate")
    if invalidate == "rename":
        response = client.put(f"/api/v1/model-profiles/{profile['id']}", json={"name": "synthetic-renamed"})
    else:
        client.post(f"/api/v1/model-profiles/{profile['id']}/disable")
        response = client.post(f"/api/v1/model-profiles/{profile['id']}/enable")
    assert response.json()["profile_fingerprint"] == profile["profile_fingerprint"]
    async def fake_test(*args, **kwargs):
        return VLLMTestResult(True, [], {}, None, None)
    monkeypatch.setattr(worker, "run_vllm_test", fake_test)
    assert client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "quick"}).status_code == 202
    with client.app.state.session_factory() as db:
        run = worker.claim_next_vllm_test(db, "synthetic", 300)
        worker.process_vllm_test(db, client.app.state.crypto, run, "")
        assert run.status == "passed"
    latest = get_profile(client, profile["id"])
    assert latest["status"] == "draft" and latest["can_assign"] is False
    assert role(client, latest, "assign-test").status_code == 409


def test_new_tests_require_test_profile_and_pin_it_without_production_fallback(client, event_payload):
    login_admin(client)
    production = create_verified(client, "synthetic-production")
    assert role(client, production, "promote").status_code == 200
    client.app.state.settings.agent_mode = "moduagent"
    request = {"name": "합성 실행", "idempotency_key": str(uuid.uuid4()), "event": event_payload}
    rejected = client.post("/api/v1/test-runs", json=request)
    assert rejected.status_code == 409 and rejected.json()["detail"] == "test_model_profile_required"
    selected = create_verified(client, "synthetic-test")
    assert role(client, selected, "assign-test").status_code == 200
    saved = named(client, event_payload)
    assert saved["profile_metadata"]["model_profile_id"] == selected["id"]
    assert saved["profile_metadata"]["model_profile_id"] != production["id"]
    # New selection affects subsequent tests, not the captured profile.
    assert role(client, production, "assign-test").status_code == 200
    named_test_request_check(client.app.state.engine, saved["id"])()
    next_run = named(client, event_payload)
    assert next_run["profile_metadata"]["model_profile_id"] == production["id"]
    assert get_profile(client, production["id"])["status"] == "production"


def test_pinned_ordinary_test_blocks_unverified_reenable_but_not_role_removal(client, event_payload):
    login_admin(client)
    profile = create_verified(client, "synthetic-pinned")
    role(client, profile, "assign-test")
    client.app.state.settings.agent_mode = "moduagent"
    saved = named(client, event_payload)
    check = named_test_request_check(client.app.state.engine, saved["id"])
    role(client, profile, "unassign-test")
    check()
    client.post(f"/api/v1/model-profiles/{profile['id']}/disable")
    client.post(f"/api/v1/model-profiles/{profile['id']}/enable")
    with pytest.raises(TargetNotAllowedError, match="test_run_profile_not_verified"):
        check()


def test_legacy_pending_test_has_no_production_fallback(client, event_payload):
    login_admin(client)
    production = create_verified(client, "synthetic-legacy-production")
    role(client, production, "promote")
    with client.app.state.session_factory() as db:
        row, _ = enqueue_analysis(db, client.app.state.crypto, "admin-ui", AnalysisInput.model_validate(event_payload),
                                   analysis_purpose="test", ingest_channel="test_lab")
        row = worker.claim_next(db, "synthetic-worker", 300)
        with pytest.raises(worker.WorkerExecutionError, match="test_model_profile_required"):
            worker.process_moduagent(db, client.app.state.crypto, row, "", 0.75)


def test_partial_unique_index_prevents_two_test_profiles(client):
    login_admin(client)
    first, second = create_verified(client, "synthetic-unique-one"), create_verified(client, "synthetic-unique-two")
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, first["id"]).is_test = True
        db.commit()
        db.get(VLLMProfile, second["id"]).is_test = True
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.get(VLLMProfile, first["id"]).is_test is True
        assert db.get(VLLMProfile, second["id"]).is_test is False


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("change", ["unassign", "disable", "reenable", "edit"])
def test_selected_legacy_test_rechecks_configuration_and_approval_not_assignment(client, provider, change):
    login_admin(client)
    profile = create_verified(client, "synthetic-selected", provider=provider)
    role(client, profile, "assign-test")
    check = selected_test_request_check(client.app.state.engine, profile["id"], profile["profile_fingerprint"])
    check()
    role(client, profile, "unassign-test")
    if change == "unassign":
        check()
        return
    if change in {"disable", "reenable"}:
        client.post(f"/api/v1/model-profiles/{profile['id']}/disable")
        if change == "reenable":
            client.post(f"/api/v1/model-profiles/{profile['id']}/enable")
    else:
        assert client.put(f"/api/v1/model-profiles/{profile['id']}", json={"timeout_seconds": 180}).status_code == 200
    expected = "test_run_profile_not_verified" if change == "reenable" else "test_run_profile_changed"
    with pytest.raises(TargetNotAllowedError, match=expected):
        check()


@pytest.mark.parametrize("purpose", ["production", "test", "legacy_test"])
def test_worker_routes_each_purpose_to_its_own_selected_profile(client, event_payload, monkeypatch, purpose):
    from app.agent.executor import AgentCallResult
    from test_prompt_snapshots import fake_output
    login_admin(client)
    production = create_verified(client, "synthetic-worker-production")
    testing = create_verified(client, "synthetic-worker-test", provider="openai")
    role(client, production, "promote")
    role(client, testing, "assign-test")
    client.app.state.settings.agent_mode = "moduagent"
    if purpose == "test":
        saved = named(client, event_payload)
        analysis_id = saved["items"][0]["analysis_id"]
    else:
        with client.app.state.session_factory() as db:
            analysis, _ = enqueue_analysis(db, client.app.state.crypto, "synthetic-source",
                AnalysisInput.model_validate(event_payload),
                analysis_purpose="test" if purpose == "legacy_test" else "production",
                ingest_channel="test_lab" if purpose == "legacy_test" else "api")
            analysis_id = analysis.id
    selected_id = production["id"] if purpose == "production" else testing["id"]
    calls = []
    async def execute(**kwargs):
        assert kwargs["egress_check"] is not None
        kwargs["egress_check"]()
        calls.append(kwargs["profile"].id)
        return AgentCallResult(output=fake_output(), framework_run_id="synthetic", agent_fingerprint="synthetic",
            finish_reason="completed", failure_id=None, error=None, telemetry={"framework_version": "0.6.2"})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, analysis_id)
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", 0.75)
        assert analysis.status == "completed"
        assert analysis.result_json["agent"]["model_profile_id"] == selected_id
    assert calls and all(identifier == selected_id for identifier in calls)


@pytest.mark.parametrize("kind", ["direct", "upload"])
def test_ordinary_submission_works_with_test_only_and_no_production(client, event_payload, kind):
    login_admin(client)
    testing = create_verified(client, "synthetic-only-test")
    role(client, testing, "assign-test")
    client.app.state.settings.agent_mode = "moduagent"
    if kind == "direct":
        saved = named(client, event_payload)
    else:
        response = client.post("/api/v1/test-runs/uploads",
            data={"name": "합성 파일 Test 선택", "idempotency_key": str(uuid.uuid4())},
            files={"file": ("synthetic.json", json.dumps([event_payload]).encode(), "application/json")})
        assert response.status_code == 202, response.text
        saved = response.json()
    assert saved["profile_metadata"]["model_profile_id"] == testing["id"]
    assert saved["accepted"] == 1
    assert all(profile["status"] != "production" for profile in client.get("/api/v1/model-profiles").json())
