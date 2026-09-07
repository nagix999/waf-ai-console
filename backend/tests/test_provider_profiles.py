"""Provider policy tests use synthetic credentials only and never call an LLM."""

import hashlib
import json

import pytest

from app.models import VLLMProfile, VLLMTestRun
from app.services.vllm_profiles import (
    OPENAI_BASE_URL,
    TargetNotAllowedError,
    normalize_and_validate_profile_url,
    profile_fingerprint,
    validate_profile_provider_settings,
)
from test_model_profiles import login_admin, mark_full_test_passed, profile_payload

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def openai_payload(name="synthetic-openai"):
    return {
        **profile_payload(name),
        "provider": "openai",
        "external_data_approved": True,
        "base_url": OPENAI_BASE_URL,
        "model_name": "synthetic-openai-model",
        "api_key": "synthetic-openai-key",
        "tls_verify": True,
    }


def create(client, payload=None):
    response = client.post("/api/v1/model-profiles", json=payload or openai_payload())
    assert response.status_code == 201, response.json()
    return response.json()


def snapshot(client, profile_id):
    with client.app.state.session_factory() as db:
        row = db.get(VLLMProfile, profile_id)
        return {column.name: getattr(row, column.name) for column in VLLMProfile.__table__.columns}


@pytest.mark.parametrize("url", [OPENAI_BASE_URL, OPENAI_BASE_URL + "/", "https://API.OPENAI.COM:443/v1/"])
def test_openai_create_canonicalizes_and_encrypts_key(client, url):
    login_admin(client)
    profile = create(client, {**openai_payload(), "base_url": url})
    assert profile["base_url"] == OPENAI_BASE_URL
    assert profile["provider"] == "openai"
    assert profile["external_data_approved"] is True
    assert profile["thinking_enabled"] is None
    assert profile["has_api_key"] is True
    assert "api_key" not in profile
    row = snapshot(client, profile["id"])
    assert row["api_key_ciphertext"] != "synthetic-openai-key"
    assert client.app.state.crypto.decrypt_text(row["api_key_ciphertext"]) == "synthetic-openai-key"
    listed = client.get("/api/v1/model-profiles")
    assert listed.status_code == 200
    assert "synthetic-openai-key" not in listed.text


@pytest.mark.parametrize("url", [
    "http://api.openai.com/v1", "https://other.invalid/v1", "https://api.openai.com.evil.invalid/v1",
    "https://api.openai.com:444/v1", "https://api.openai.com:invalid/v1", "https://api.openai.com:0443/v1",
    "https://api.openai.com/v1?", "https://api.openai.com/v1?proxy=synthetic-sensitive",
    "https://api.openai.com/v1#", "https://api.openai.com/v1#synthetic-sensitive",
    "https://api.openai.com/", "https://api.openai.com/v1/chat/completions", "https://api.openai.com//v1",
    "https://api.openai.com/v1//", "https://api.openai.com/%76%31", "https://api.openai.com./v1",
    "https://synthetic-sensitive@api.openai.com/v1", "https://@api.openai.com/v1",
    "https://api.openai.com:synthetic-sensitive@other.invalid/v1",
    "https://api.openai.com\t/v1", "https://api.openai.com/v1\n", " https://api.openai.com/v1",
    "https://api.openai.com/v1\x00", "https://api.openai.com\\@other.invalid/v1",
])
def test_openai_rejects_alternate_targets_without_echoing_input(client, url):
    login_admin(client)
    response = client.post("/api/v1/model-profiles", json={**openai_payload(), "base_url": url})
    assert response.status_code == 422
    assert response.json() == {"detail": "openai_base_url_not_allowed"}
    with client.app.state.session_factory() as db:
        assert db.query(VLLMProfile).count() == 0


@pytest.mark.parametrize(("changes", "code"), [
    ({"api_key": None}, "openai_api_key_required"),
    ({"api_key": ""}, "openai_api_key_required"),
    ({"api_key": "   "}, "openai_api_key_required"),
    ({"api_key": "synthetic key"}, "openai_api_key_required"),
    ({"api_key": "synthetic\nkey"}, "openai_api_key_required"),
    ({"api_key": "synthetic\x00key"}, "openai_api_key_required"),
    ({"external_data_approved": False}, "openai_external_data_approval_required"),
    ({"tls_verify": False}, "openai_tls_verification_required"),
])
def test_openai_create_rejects_missing_security_settings(client, changes, code):
    login_admin(client)
    response = client.post("/api/v1/model-profiles", json={**openai_payload(), **changes})
    assert response.status_code == 422
    assert response.json() == {"detail": code}


@pytest.mark.parametrize("field", ["api_key", "external_data_approved", "model_name"])
def test_openai_requires_explicit_registration_fields(client, field):
    login_admin(client)
    payload = openai_payload()
    del payload[field]
    response = client.post("/api/v1/model-profiles", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "api_key": "openai_api_key_required",
        "external_data_approved": "openai_external_data_approval_required",
        "model_name": "openai_model_name_required",
    }[field]


@pytest.mark.parametrize("changes", [
    {"provider": "unknown"}, {"provider": None}, {"external_data_approved": "true"},
    {"external_data_approved": 1}, {"external_data_approved": None}, {"model_name": " \t "},
])
def test_provider_schema_rejects_invalid_values(client, changes):
    login_admin(client)
    assert client.post("/api/v1/model-profiles", json={**openai_payload(), **changes}).status_code == 422


def test_vllm_defaults_and_db_allowlist(client):
    login_admin(client)
    profile = create(client, profile_payload())
    assert profile["provider"] == "vllm"
    assert profile["external_data_approved"] is False
    assert profile["thinking_enabled"] is False
    assert profile["tls_verify"] is False
    rejected = client.post("/api/v1/model-profiles", json={**profile_payload("bad"), "external_data_approved": True})
    assert rejected.json() == {"detail": "external_data_approval_not_applicable"}
    rejected = client.post("/api/v1/model-profiles", json={**profile_payload("bad"), "base_url": OPENAI_BASE_URL})
    assert rejected.json() == {"detail": "vllm_base_url_must_use_ip_address"}


@pytest.mark.parametrize("changes", [
    {"api_key": ""}, {"api_key": "synthetic bad"}, {"external_data_approved": False},
    {"external_data_approved": None}, {"tls_verify": False}, {"base_url": "https://other.invalid/v1"},
    {"model_name": "   "}, {"provider": None},
])
def test_invalid_update_preserves_verified_configuration_and_key(client, changes):
    login_admin(client)
    profile = create(client)
    client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "full"})
    mark_full_test_passed(client, profile["id"])
    before = snapshot(client, profile["id"])
    response = client.put(f"/api/v1/model-profiles/{profile['id']}", json=changes)
    assert response.status_code == 422
    assert snapshot(client, profile["id"]) == before


def test_same_provider_update_retains_omitted_key_but_invalidates_verification(client):
    login_admin(client)
    profile = create(client)
    client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "full"})
    mark_full_test_passed(client, profile["id"])
    before = snapshot(client, profile["id"])
    updated = client.put(f"/api/v1/model-profiles/{profile['id']}", json={"timeout_seconds": 180})
    assert updated.status_code == 200
    assert updated.json()["status"] == "draft"
    assert updated.json()["last_verified_at"] is None
    assert snapshot(client, profile["id"])["api_key_ciphertext"] == before["api_key_ciphertext"]


@pytest.mark.parametrize("omitted", ["api_key", "external_data_approved", "model_name"])
def test_switch_to_openai_requires_new_explicit_settings(client, omitted):
    login_admin(client)
    profile = create(client, profile_payload())
    before = snapshot(client, profile["id"])
    changes = openai_payload()
    del changes[omitted]
    response = client.put(f"/api/v1/model-profiles/{profile['id']}", json=changes)
    assert response.status_code == 422
    assert snapshot(client, profile["id"]) == before


def test_switch_to_openai_stores_new_key_and_approval(client):
    login_admin(client)
    profile = create(client, profile_payload())
    response = client.put(f"/api/v1/model-profiles/{profile['id']}", json=openai_payload())
    assert response.status_code == 200
    assert response.json()["provider"] == "openai"
    assert response.json()["external_data_approved"] is True
    row = snapshot(client, profile["id"])
    assert client.app.state.crypto.decrypt_text(row["api_key_ciphertext"]) == "synthetic-openai-key"


@pytest.mark.parametrize("key_setting", [None, "", "synthetic-vllm-new-key"])
def test_switch_to_vllm_never_reuses_openai_key(client, key_setting):
    login_admin(client)
    profile = create(client)
    changes = {"provider": "vllm", "base_url": "http://10.0.0.10:8000/v1", "model_name": "synthetic-vllm-model"}
    if key_setting is not None:
        changes["api_key"] = key_setting
    response = client.put(f"/api/v1/model-profiles/{profile['id']}", json=changes)
    assert response.status_code == 200
    assert response.json()["external_data_approved"] is False
    row = snapshot(client, profile["id"])
    if key_setting:
        assert client.app.state.crypto.decrypt_text(row["api_key_ciphertext"]) == key_setting
    else:
        assert row["api_key_ciphertext"] is None
        assert row["encryption_key_version"] is None


@pytest.mark.parametrize(("changes", "code"), [
    ({"provider": "unknown"}, "model_provider_not_supported"),
    ({"external_data_approved": False}, "openai_external_data_approval_required"),
    ({"tls_verify": False}, "openai_tls_verification_required"),
    ({"api_key_ciphertext": None}, "openai_api_key_required"),
    ({"api_key_ciphertext": "synthetic-corrupted-ciphertext"}, "model_profile_api_key_unavailable"),
    ({"base_url": "https://other.invalid/v1"}, "openai_base_url_not_allowed"),
    ({"model_name": " "}, "openai_model_name_required"),
])
@pytest.mark.parametrize("action", ["tests", "promote", "assign-test", "enable"])
def test_actions_revalidate_stored_profile_fail_closed(client, changes, code, action):
    login_admin(client)
    profile = create(client)
    with client.app.state.session_factory() as db:
        row = db.get(VLLMProfile, profile["id"])
        row.status = "disabled" if action == "enable" else "verified"
        for key, value in changes.items():
            setattr(row, key, value)
        fingerprint = profile_fingerprint(row)
        db.commit()
    before = snapshot(client, profile["id"])
    # Use each action's valid request contract so this reaches the persisted
    # provider/credential boundary, not unrelated request-shape validation.
    if action == "tests":
        kwargs = {"json": {"mode": "full"}}
    elif action in {"promote", "assign-test"}:
        kwargs = {"json": {"expected_profile_fingerprint": fingerprint}}
    else:
        kwargs = {}
    response = client.post(f"/api/v1/model-profiles/{profile['id']}/{action}", **kwargs)
    assert response.status_code == 422
    assert response.json() == {"detail": code}
    assert snapshot(client, profile["id"]) == before
    with client.app.state.session_factory() as db:
        assert db.query(VLLMTestRun).count() == 0


def test_global_production_gate_and_immutability_across_providers(client):
    login_admin(client)
    first = create(client, profile_payload())
    second = create(client)
    for profile in (first, second):
        path = f"/api/v1/model-profiles/{profile['id']}"
        assert client.post(path + "/promote").status_code == 409
        assert client.post(path + "/tests", json={"mode": "full"}).status_code == 202
        mark_full_test_passed(client, profile["id"])
        assert client.post(path + "/promote").status_code == 200
        assert client.put(path, json={"name": "immutable"}).json() == {"detail": "production_profile_is_immutable"}
    profiles = client.get("/api/v1/model-profiles").json()
    assert [p["id"] for p in profiles if p["status"] == "production"] == [second["id"]]
    assert next(p for p in profiles if p["id"] == first["id"])["status"] == "verified"


def test_legacy_fingerprint_is_unchanged_and_openai_identity_is_separate():
    profile = VLLMProfile(
        base_url="http://10.0.0.10:8000/v1", model_name="synthetic-model", api_key_ciphertext="synthetic-ciphertext",
        timeout_seconds=120, context_window=32768, max_output_tokens=3072, test_concurrency=3, tls_verify=True,
    )
    legacy = {
        "base_url": profile.base_url, "model_name": profile.model_name,
        "api_key_ciphertext_hash": hashlib.sha256(profile.api_key_ciphertext.encode()).hexdigest(),
        "timeout_seconds": 120, "context_window": 32768, "max_output_tokens": 3072,
        "test_concurrency": 3, "tls_verify": True, "thinking_enabled": False,
    }
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert profile_fingerprint(profile) == expected
    profile.provider = "vllm"
    profile.external_data_approved = False
    assert profile_fingerprint(profile) == expected
    profile.provider = "openai"
    profile.external_data_approved = True
    approved = profile_fingerprint(profile)
    assert approved != expected
    profile.external_data_approved = False
    assert profile_fingerprint(profile) not in {expected, approved}


def test_shared_helpers_reject_unknown_provider_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("provider validation must not resolve an unknown/OpenAI target")
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    profile = VLLMProfile(provider="unknown", base_url=OPENAI_BASE_URL)
    with pytest.raises(TargetNotAllowedError, match="^model_provider_not_supported$"):
        normalize_and_validate_profile_url(profile, "")
    with pytest.raises(TargetNotAllowedError, match="^model_provider_not_supported$"):
        validate_profile_provider_settings(profile, has_api_key=True)
    profile.provider = "openai"
    assert normalize_and_validate_profile_url(profile, "") == OPENAI_BASE_URL
