from datetime import UTC, datetime

import pytest

from app.models import ModelProfileStatus, ModelTestStatus, VLLMProfile, VLLMTestRun
from app.services.vllm_profiles import profile_fingerprint

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def login_admin(client) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 200


def profile_payload(name: str = "gemma4-test") -> dict:
    return {
        "name": name,
        "base_url": "http://10.0.0.10:8000/v1",
        "model_name": "google/gemma-4-26B-A4B-it",
        "api_key": "profile-secret",
        "timeout_seconds": 120,
        "context_window": 32768,
        "max_output_tokens": 3072,
        "test_concurrency": 3,
        "tls_verify": False,
    }


def mark_full_test_passed(client, profile_id: str) -> None:
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, profile_id)
        test_run = db.query(VLLMTestRun).filter(VLLMTestRun.profile_id == profile_id).one()
        test_run.status = ModelTestStatus.passed.value
        test_run.completed_at = datetime.now(UTC)
        profile.status = ModelProfileStatus.verified.value
        profile.last_verified_at = test_run.completed_at
        assert test_run.profile_fingerprint == profile_fingerprint(profile)
        db.commit()


def test_profile_secret_is_encrypted_and_service_key_is_denied(client, service_headers):
    denied = client.post("/api/v1/model-profiles", headers=service_headers, json=profile_payload())
    assert denied.status_code == 403

    login_admin(client)
    created = client.post("/api/v1/model-profiles", json=profile_payload())
    assert created.status_code == 201
    assert created.json()["has_api_key"] is True
    assert "api_key" not in created.json()
    with client.app.state.session_factory() as db:
        row = db.get(VLLMProfile, created.json()["id"])
        assert row.api_key_ciphertext != "profile-secret"
        assert client.app.state.crypto.decrypt_text(row.api_key_ciphertext) == "profile-secret"


def test_disallowed_target_is_rejected(client):
    login_admin(client)
    payload = profile_payload()
    payload["base_url"] = "http://169.254.169.254:80/v1"
    response = client.post("/api/v1/model-profiles", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "vllm_target_not_allowed"


def test_full_test_is_required_before_production_promotion(client):
    login_admin(client)
    profile = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    denied = client.post(f"/api/v1/model-profiles/{profile['id']}/promote")
    assert denied.status_code == 409

    test_run = client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "full"})
    assert test_run.status_code == 202
    mark_full_test_passed(client, profile["id"])
    promoted = client.post(f"/api/v1/model-profiles/{profile['id']}/promote")
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "production"


def test_profile_update_invalidates_previous_verification(client):
    login_admin(client)
    profile = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "full"})
    mark_full_test_passed(client, profile["id"])
    updated = client.put(
        f"/api/v1/model-profiles/{profile['id']}",
        json={"timeout_seconds": 180},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "draft"
    assert updated.json()["last_verified_at"] is None


def test_promoting_another_profile_demotes_previous_production(client):
    login_admin(client)
    first = client.post("/api/v1/model-profiles", json=profile_payload("gemma4-first")).json()
    second = client.post("/api/v1/model-profiles", json=profile_payload("gemma4-second")).json()
    for profile in (first, second):
        client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "full"})
        mark_full_test_passed(client, profile["id"])
        promoted = client.post(f"/api/v1/model-profiles/{profile['id']}/promote")
        assert promoted.status_code == 200

    profiles = client.get("/api/v1/model-profiles").json()
    production = [profile for profile in profiles if profile["status"] == "production"]
    assert [profile["id"] for profile in production] == [second["id"]]
    assert next(profile for profile in profiles if profile["id"] == first["id"])["status"] == "verified"
