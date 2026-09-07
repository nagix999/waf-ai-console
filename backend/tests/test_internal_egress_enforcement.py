"""Synthetic authorization and mocked transport; never contact a real endpoint."""
import asyncio

import httpx
import pytest
from sqlalchemy import delete

from app.models import Analysis, InternalEgressTarget, VLLMProfile, VLLMTestRun
from app.services.vllm_profiles import TargetNotAllowedError, normalize_and_validate_base_url, profile_fingerprint
from app.services.vllm_test_runner import run_vllm_test
from app.worker import process_moduagent, process_vllm_test, vllm_egress_check
from test_agent_openai import completion, execute, install_http, openai_profile
from test_model_profiles import login_admin, profile_payload
from test_provider_test_runner import SyntheticCrypto, profile_for, successful_handler


@pytest.mark.parametrize("url,allowed,expected", [
    ("http://10.0.0.10:8000", "10.0.0.10:8000", "http://10.0.0.10:8000/v1"),
    ("https://192.168.1.2/v1/", "192.168.1.2:443", "https://192.168.1.2:443/v1"),
    ("http://172.16.0.1/", "172.16.0.1:80", "http://172.16.0.1:80/v1"),
    ("http://[FD00:0000::1]:8000/v1", "[fd00::1]:8000", "http://[fd00::1]:8000/v1"),
])
def test_exact_registered_literal_endpoints(url, allowed, expected):
    assert normalize_and_validate_base_url(url, allowed) == expected


@pytest.mark.parametrize("url", [
    "http://10.0.0.10:8001/v1", "http://10.0.0.11:8000/v1", "http://vllm.internal:8000/v1",
    "http://169.254.169.254:8000/v1", "http://127.0.0.1:8000/v1", "http://8.8.8.8:8000/v1",
    "http://10.0.0.10:0/v1", "http://10.0.0.10:65536/v1", "http://10.0.0.10:/v1",
    "http://@10.0.0.10:8000/v1", "http://user:secret@10.0.0.10:8000/v1",
    "http://10.0.0.10:8000/v1?", "http://10.0.0.10:8000/v1#", "http://10.0.0.10:8000/v1//",
    "http://10.0.0.10:8000/other", "http://10.0.0.10:8000/v1\n", " http://10.0.0.10:8000/v1",
    "http://10.0.0.10:8000/\\v1", "http://0x0a00000a:8000/v1", "http://167772170:8000/v1",
    "http://010.000.000.010:8000/v1", "http://[::ffff:10.0.0.10]:8000/v1",
    "http://[fd00::1%25eth0]:8000/v1", "file://10.0.0.10:8000/v1", "http://[broken/v1",
])
def test_endpoint_parser_rejects_bypasses_without_dns(monkeypatch, url):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: pytest.fail("No DNS is allowed"))
    with pytest.raises(TargetNotAllowedError):
        normalize_and_validate_base_url(url, "10.0.0.10:8000,[fd00::1]:8000")


def test_environment_cannot_authorize_a_profile_and_empty_db_stays_empty(client):
    client.app.state.settings.vllm_allowed_targets = "10.0.0.10:8000,10.0.0.0/8:8000,vllm.internal:8000"
    login_admin(client)
    response = client.post("/api/v1/model-profiles", json=profile_payload())
    assert response.status_code == 422
    assert response.json() == {"detail": "vllm_target_not_allowed"}
    assert client.get("/api/v1/admin/internal-egress").json() == []
    assert client.get("/api/v1/model-profiles").json() == []


def test_profile_update_enable_test_and_promote_recheck_db(client, registered_vllm_target):
    login_admin(client)
    created = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    profile_id = created["id"]
    # A UI validation bypass must not modify a previously valid row.
    response = client.put(f"/api/v1/model-profiles/{profile_id}", json={"base_url": "http://10.0.0.11:8000/v1"})
    assert response.status_code == 422
    assert client.get("/api/v1/model-profiles").json()[0]["base_url"] == created["base_url"]
    with client.app.state.session_factory() as db:
        db.execute(delete(InternalEgressTarget))  # Simulate out-of-band revocation.
        db.commit()
    assert client.post(f"/api/v1/model-profiles/{profile_id}/tests", json={"mode": "quick"}).status_code == 422
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).status = "verified"
        db.commit()
    assert client.post(f"/api/v1/model-profiles/{profile_id}/promote").status_code == 422
    assert client.post(f"/api/v1/model-profiles/{profile_id}/disable").status_code == 200
    assert client.post(f"/api/v1/model-profiles/{profile_id}/enable").status_code == 422


def test_analysis_worker_ignores_legacy_allowlist_before_dispatch(client, event_payload, service_headers, monkeypatch):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    monkeypatch.setattr("app.worker.execute_structured_agent", lambda **kw: pytest.fail("Must fail before model call"))
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production"))
        db.commit()
        with pytest.raises(RuntimeError, match="vllm_target_not_allowed"):
            process_moduagent(db, client.app.state.crypto, db.get(Analysis, created["id"]), "10.0.0.10:8000", 0.75)


def test_model_test_worker_rechecks_revoked_target_before_dispatch(client, registered_vllm_target, monkeypatch):
    login_admin(client)
    profile_id = client.post("/api/v1/model-profiles", json=profile_payload()).json()["id"]
    run_id = client.post(f"/api/v1/model-profiles/{profile_id}/tests", json={"mode": "quick"}).json()["id"]
    monkeypatch.setattr("app.worker.run_vllm_test", lambda *a, **kw: pytest.fail("No transport allowed"))
    with client.app.state.session_factory() as db:
        db.execute(delete(InternalEgressTarget))
        db.commit()
        run = db.get(VLLMTestRun, run_id)
        process_vllm_test(db, client.app.state.crypto, run, "10.0.0.10:8000")
        assert run.status == "failed"
        assert run.error_code == "vllm_target_not_allowed"


@pytest.mark.parametrize("change", ["delete_target", "disable_profile", "change_endpoint"])
def test_runtime_callback_does_not_cache_permissions(client, registered_vllm_target, change):
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(name="synthetic", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production")
        db.add(profile)
        db.commit()
        check = vllm_egress_check(db, profile)
        check()
        with client.app.state.session_factory() as other:
            if change == "delete_target":
                other.execute(delete(InternalEgressTarget))
            elif change == "disable_profile":
                other.get(VLLMProfile, profile.id).status = "disabled"
            else:
                other.get(VLLMProfile, profile.id).base_url = "http://10.0.0.11:8000/v1"
            other.commit()
        with pytest.raises(TargetNotAllowedError):
            check()


def test_vllm_agent_transport_no_redirect_or_proxy_and_checks_each_request(monkeypatch):
    requests, options = install_http(monkeypatch, lambda request, count: httpx.Response(302, headers={"location": "http://10.0.0.11:8000/v1/chat/completions"}))
    checks = []
    result = execute(openai_profile(provider="vllm", base_url="http://10.0.0.10:8000/v1"),
                     api_key=None, egress_check=lambda: checks.append(True))
    assert result.succeeded is False
    assert len(requests) == 1
    assert len(checks) == 2  # Entry and actual HTTP dispatch.
    assert options == [{"verify": True, "follow_redirects": False, "trust_env": False}]


def test_revocation_blocks_sdk_retry_before_a_second_http_request(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(503))
    def check():
        if requests:
            raise TargetNotAllowedError("vllm_target_not_allowed")
    result = execute(openai_profile(provider="vllm", base_url="http://10.0.0.10:8000/v1"), api_key=None, egress_check=check)
    assert result.succeeded is False
    assert len(requests) == 1


def test_vllm_test_runner_rechecks_between_checks(monkeypatch):
    profile = profile_for("vllm")
    requests, _ = install_http(monkeypatch, successful_handler(profile))
    def check():
        if requests:
            raise TargetNotAllowedError("vllm_target_not_allowed")
    result = asyncio.run(run_vllm_test(profile, SyntheticCrypto(), "full", egress_check=check))
    assert result.passed is False
    assert result.error_code == "vllm_target_not_allowed"
    assert len(requests) == 1


def test_direct_vllm_entrypoints_require_authorization_callback(monkeypatch):
    requests, _ = install_http(monkeypatch, lambda *a: pytest.fail("No request allowed"))
    with pytest.raises(ValueError, match="vllm_egress_check_required"):
        execute(openai_profile(provider="vllm"), api_key=None)
    with pytest.raises(ValueError, match="vllm_egress_check_required"):
        asyncio.run(run_vllm_test(profile_for("vllm"), SyntheticCrypto(), "quick"))
    assert requests == []


@pytest.mark.parametrize("change", ["disabled", "edited", "revoked"])
def test_completed_model_test_cannot_restore_stale_authorization(client, registered_vllm_target, monkeypatch, change):
    from app.services.vllm_test_runner import VLLMTestResult

    login_admin(client)
    profile_id = client.post("/api/v1/model-profiles", json=profile_payload()).json()["id"]
    run_id = client.post(f"/api/v1/model-profiles/{profile_id}/tests", json={"mode": "full"}).json()["id"]

    async def changed_during_test(*args, **kwargs):
        with client.app.state.session_factory() as other:
            if change == "disabled":
                other.get(VLLMProfile, profile_id).status = "disabled"
            elif change == "edited":
                other.get(VLLMProfile, profile_id).timeout_seconds = 180
            else:
                other.execute(delete(InternalEgressTarget))
            other.commit()
        return VLLMTestResult(True, [], {}, None, None)

    monkeypatch.setattr("app.worker.run_vllm_test", changed_during_test)
    with client.app.state.session_factory() as db:
        run = db.get(VLLMTestRun, run_id)
        process_vllm_test(db, client.app.state.crypto, run, "")
        assert run.status == "failed"
        assert run.error_code == {"disabled": "profile_unavailable", "edited": "profile_changed", "revoked": "vllm_target_not_allowed"}[change]
        profile = db.get(VLLMProfile, profile_id)
        assert profile.status == ("disabled" if change == "disabled" else "draft")
        assert profile.last_verified_at is None
