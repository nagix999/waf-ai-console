"""Offline service-key lifecycle and least-privilege regressions."""
import hashlib
from datetime import UTC, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api_key_schemas import ServiceApiKeyCreate
from app.models import AccessAudit, Analysis, ServiceApiKey, utcnow
from app.services.service_api_keys import TOKEN_PATTERN, authenticate_key, issue_key


BASE = "/api/v1/admin/service-api-keys"


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def logout(client):
    assert client.post("/api/v1/auth/logout").status_code == 204


def issue(client, name="Synthetic parser", source="parser-alpha", scopes=None):
    response = client.post(BASE, json={"name": name, "source_system": source, "scopes": scopes or ["ingest", "review"]})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    return response.json()


def headers(issued):
    return {"X-API-Key": issued["api_key"]}


def db_key(client, key_id):
    with client.app.state.session_factory() as db:
        return db.get(ServiceApiKey, key_id)


def test_one_time_response_and_hashed_storage(client, caplog):
    login(client)
    issued = issue(client, name="  한글 Parser / Production  ", scopes=["review", "ingest"])
    raw, item = issued["api_key"], issued["item"]
    assert TOKEN_PATTERN.fullmatch(raw) is not None
    assert item["name"] == "한글 Parser / Production"
    assert item["scopes"] == ["ingest", "review"]
    assert item["last_used_at"] is None and item["revoked_at"] is None
    assert item["created_at"].endswith("Z")
    row = db_key(client, item["id"])
    assert row.key_hash == hashlib.sha256(raw.encode("ascii")).hexdigest()
    assert row.key_prefix == "wafsvc_" + item["id"].replace("-", "")
    assert len(raw[len(row.key_prefix) + 1:]) == 43
    assert raw not in repr(row.__dict__)
    listing = client.get(BASE)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "no-store"
    assert listing.headers["pragma"] == "no-cache"
    assert listing.json() == {"items": [item]}
    assert raw not in listing.text and row.key_hash not in listing.text
    audit_response = client.get("/api/v1/audit-logs")
    assert raw not in audit_response.text and row.key_hash not in audit_response.text
    with client.app.state.session_factory() as db:
        records = db.scalars(select(AccessAudit).where(AccessAudit.resource_type == "service_api_key")).all()
        assert [(entry.action, entry.resource_id) for entry in records] == [("issue_service_api_key", item["id"])]
        assert records[0].actor_kind == "admin_session" and records[0].actor_id == "admin"
    assert raw not in caplog.text and row.key_hash not in caplog.text


def test_authentication_scopes_source_and_same_source_idempotency(client, event_payload):
    login(client)
    alpha = issue(client, "Alpha")
    alpha_ingest = issue(client, "Alpha ingest", scopes=["ingest"])
    alpha_review = issue(client, "Alpha review", scopes=["review"])
    beta = issue(client, "Beta", source="parser-beta")
    logout(client)
    identity = client.get("/api/v1/auth/me", headers=headers(alpha)).json()
    assert identity["kind"] == "service_api_key" and identity["source_system"] == "parser-alpha"
    assert identity["scopes"] == ["ingest", "review"]
    created = client.post("/api/v1/analyses", headers=headers(alpha), json=event_payload)
    assert created.status_code == 202
    result = created.json()
    assert result["analysis_purpose"] == "production" and result["ingest_channel"] == "service_api"
    again = client.post("/api/v1/analyses", headers=headers(alpha_ingest), json=event_payload)
    assert again.status_code == 202 and again.json()["id"] == result["id"]
    beta_result = client.post("/api/v1/analyses", headers=headers(beta), json=event_payload)
    assert beta_result.status_code == 202 and beta_result.json()["id"] != result["id"]
    assert client.get("/api/v1/analyses", headers=headers(alpha)).json()["total"] == 1
    assert client.get("/api/v1/analyses?source_system=parser-beta", headers=headers(alpha)).json()["total"] == 0
    assert client.get(f"/api/v1/analyses/{beta_result.json()['id']}", headers=headers(alpha)).status_code == 404
    assert client.get(f"/api/v1/analyses/{result['id']}", headers=headers(alpha_ingest)).status_code == 200
    for key, expected in ((alpha_review, 403), (alpha_ingest, 200)):
        assert client.get("/api/v1/analyses", headers=headers(key)).status_code == expected
    assert client.post("/api/v1/analyses", headers=headers(alpha_review), json=event_payload).status_code == 403
    review = {"external_review_id": "synthetic-review", "event_id": event_payload["event_id"], "decision": "false_positive"}
    review_url = f"/api/v1/analyses/{result['id']}/reviews"
    assert client.post(review_url, headers=headers(alpha_ingest), json=review).status_code == 403
    first_review = client.post(review_url, headers=headers(alpha_review), json=review)
    assert first_review.status_code == 201
    repeat_review = client.post(review_url, headers=headers(alpha), json=review)
    assert repeat_review.status_code == 200 and repeat_review.json()["id"] == first_review.json()["id"]
    assert client.post(review_url, headers=headers(beta), json=review).status_code == 404
    for endpoint in ("event", "agent-runs"):
        assert client.get(f"/api/v1/analyses/{result['id']}/{endpoint}", headers=headers(alpha)).status_code == 403
    assert client.post("/api/v1/test-analyses", headers=headers(alpha), json=event_payload).status_code == 403


@pytest.mark.parametrize("auth", ["anonymous", "fixture_key", "database"])
def test_management_never_accessible_to_non_admin(client, service_headers, auth):
    login(client)
    issued = issue(client)
    logout(client)
    credentials = {} if auth == "anonymous" else service_headers if auth == "fixture_key" else headers(issued)
    expected = 401 if auth == "anonymous" else 403
    key_id = issued["item"]["id"]
    for method, path, payload in (
        ("get", BASE, None),
        ("post", BASE, {"name": "Denied", "source_system": "denied", "scopes": ["ingest"]}),
        ("patch", f"{BASE}/{key_id}", {"name": "Denied"}),
        ("post", f"{BASE}/{key_id}/revoke", None),
    ):
        assert client.request(method, path, json=payload, headers=credentials).status_code == expected
    assert db_key(client, key_id).revoked_at is None


def test_rename_conflicts_and_idempotent_revocation_preserve_history(client, event_payload):
    login(client)
    issued = issue(client, "Before")
    second = issue(client, "Taken")
    key_id = issued["item"]["id"]
    original_hash = db_key(client, key_id).key_hash
    duplicate = client.post(BASE, json={"name": "Before", "source_system": "other", "scopes": ["review"]})
    assert duplicate.status_code == 409 and duplicate.json()["detail"] == "service_api_key_name_exists"
    conflicting = client.patch(f"{BASE}/{key_id}", json={"name": "Taken"})
    assert conflicting.status_code == 409 and conflicting.json()["detail"] == "service_api_key_name_exists"
    assert db_key(client, key_id).name == "Before"
    renamed = client.patch(f"{BASE}/{key_id}", json={"name": " After "})
    assert renamed.status_code == 200 and renamed.json()["name"] == "After"
    assert renamed.headers["cache-control"] == "no-store"
    assert renamed.json()["source_system"] == issued["item"]["source_system"]
    assert renamed.json()["scopes"] == issued["item"]["scopes"]
    assert issued["api_key"] not in renamed.text and original_hash not in renamed.text
    logout(client)
    created = client.post("/api/v1/analyses", headers=headers(issued), json=event_payload).json()
    login(client)
    revoked = client.post(f"{BASE}/{key_id}/revoke")
    assert revoked.status_code == 200 and revoked.json()["revoked_at"].endswith("Z")
    assert revoked.headers["cache-control"] == "no-store" and revoked.headers["pragma"] == "no-cache"
    repeated = client.post(f"{BASE}/{key_id}/revoke")
    assert repeated.status_code == 200 and repeated.json() == revoked.json()
    assert issued["api_key"] not in repeated.text and original_hash not in repeated.text
    denied_rename = client.patch(f"{BASE}/{key_id}", json={"name": "Resurrect"})
    assert denied_rename.status_code == 409 and denied_rename.json()["detail"] == "service_api_key_revoked"
    assert client.delete(f"{BASE}/{key_id}").status_code == 204
    assert [entry["name"] for entry in client.get(BASE).json()["items"]] == ["Taken"]
    with client.app.state.session_factory() as db:
        persisted = db.get(ServiceApiKey, key_id)
        assert persisted.key_hash == original_hash and persisted.revoked_by == "admin"
        assert db.get(Analysis, created["id"]).status == "pending"
        actions = db.scalars(select(AccessAudit.action).where(AccessAudit.resource_id == key_id)).all()
        assert actions == ["issue_service_api_key", "rename_service_api_key", "revoke_service_api_key", "delete_service_api_key"]
    logout(client)
    assert client.get("/api/v1/auth/me", headers=headers(issued)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=headers(second)).status_code == 200


@pytest.mark.parametrize("path,method", [("missing", "patch"), ("missing/revoke", "post")])
def test_unknown_key_has_safe_error(client, path, method):
    login(client)
    response = client.request(method, f"{BASE}/{path}", json={"name": "Unknown"} if method == "patch" else None)
    assert response.status_code == 404 and response.json() == {"detail": "service_api_key_not_found"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("field,value", [
    ("source_system", "admin-ui"), ("source_system", "ADMIN-UI"),
    ("source_system", "waf-internal-test"), ("source_system", "WAF-Internal-Case"),
    ("source_system", "한글"), ("source_system", " parser"), ("source_system", "parser "),
    ("source_system", "a/b"), ("source_system", "a:b"), ("source_system", "a\n"),
    ("source_system", ".dot"), ("source_system", "a" * 121), ("source_system", ""),
    ("scopes", []), ("scopes", ["admin"]), ("scopes", ["other"]),
    ("scopes", ["ingest", "ingest"]), ("scopes", ["ingest", "review", "admin"]),
    ("scopes", "ingest"), ("scopes", [1]),
    ("name", ""), ("name", "   "), ("name", "a" * 121),
    ("name", "line\nbreak"), ("name", "a\t"), ("name", "bidi\u202e"),
    ("name", "zero\u200b"), ("name", "surrogate\ud800"), ("name", 42),
])
def test_invalid_schema_values(field, value):
    with pytest.raises(ValidationError):
        ServiceApiKeyCreate.model_validate({"name": "Valid", "source_system": "valid-source", "scopes": ["ingest"], field: value})


@pytest.mark.parametrize("source", ["a", "A_b.c-9", "x" * 120, "waf-internal", "admin-ui-parser"])
def test_valid_source_identifiers(source):
    result = ServiceApiKeyCreate(name=" Parser ", source_system=source, scopes=["review", "ingest"])
    assert result.source_system == source and result.name == "Parser"
    assert result.scopes == ["ingest", "review"]


@pytest.mark.parametrize("field,value", [("source_system", "new-source"), ("scopes", ["admin"]), ("revoked_at", None), ("api_key", "synthetic-not-accepted")])
def test_immutable_fields_cannot_be_patched(client, field, value):
    login(client)
    issued = issue(client)
    response = client.patch(f"{BASE}/{issued['item']['id']}", json={"name": "New", field: value})
    assert response.status_code == 422
    assert db_key(client, issued["item"]["id"]).name == "Synthetic parser"


def test_invalid_create_does_not_save_or_audit(client):
    login(client)
    response = client.post(BASE, json={"name": "No", "source_system": "admin-ui", "scopes": ["admin"]})
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalars(select(ServiceApiKey)).all() == []
        assert db.scalars(select(AccessAudit).where(AccessAudit.resource_type == "service_api_key")).all() == []


@pytest.mark.parametrize("mutation", ["secret", "public_id", "prefix", "truncated", "long", "unicode"])
def test_invalid_credentials_are_rejected_without_echo_or_timestamp(client, mutation):
    login(client)
    issued = issue(client)
    logout(client)
    raw = issued["api_key"]
    invalid = {
        "secret": raw[:-1] + ("A" if raw[-1] != "A" else "B"),
        "public_id": "wafsvc_" + "0" * 32 + "_" + raw.rsplit("_", 1)[-1],
        "prefix": "other_" + raw,
        "truncated": raw[:-1], "long": "a" * 513, "unicode": "invalid-\xe9",
    }[mutation]
    # Non-ASCII header bytes are legal transport input and must not raise a
    # compare_digest TypeError or leak into an error response.
    supplied = invalid.encode("latin-1")
    response = client.get("/api/v1/auth/me", headers={"X-API-Key": supplied})
    assert response.status_code == 401 and response.json() == {"detail": "authentication_required"}
    assert db_key(client, issued["item"]["id"]).last_used_at is None


def test_last_authentication_is_utc_throttled_and_counts_scope_denials(client):
    login(client)
    issued = issue(client)
    key_id = issued["item"]["id"]
    logout(client)
    assert client.get(BASE, headers=headers(issued)).status_code == 403
    first = db_key(client, key_id).last_used_at
    assert first is not None
    assert client.get("/api/v1/auth/me", headers=headers(issued)).status_code == 200
    assert db_key(client, key_id).last_used_at == first
    old = utcnow() - timedelta(seconds=61)
    with client.app.state.session_factory() as db:
        db.get(ServiceApiKey, key_id).last_used_at = old
        db.commit()
    assert client.get("/api/v1/auth/me", headers=headers(issued)).status_code == 200
    updated = db_key(client, key_id).last_used_at
    assert updated.replace(tzinfo=UTC) > old
    login(client)
    assert client.get(BASE).json()["items"][0]["last_used_at"].endswith("Z")


@pytest.mark.parametrize("enabled", ["true", "false", "invalid-legacy-value"])
@pytest.mark.parametrize("legacy_key", ["synthetic-obsolete-environment-key", "wafsvc_" + "0" * 32 + "_" + "A" * 43], ids=["legacy-format", "managed-format"])
def test_legacy_environment_credentials_are_ignored_and_db_keys_still_work(client, monkeypatch, enabled, legacy_key):
    from app.config import Settings

    monkeypatch.setenv("WAF_BOOTSTRAP_API_KEY", legacy_key)
    monkeypatch.setenv("WAF_BOOTSTRAP_API_KEY_ENABLED", enabled)
    monkeypatch.setenv("WAF_BOOTSTRAP_SOURCE_SYSTEM", "synthetic-obsolete-source")
    # Read the environment anew as a restarted process would; removed settings
    # are ignored even when old .env/Compose values are still present.
    settings = Settings(_env_file=None, **client.app.state.settings.model_dump())
    assert not {"bootstrap_api_key", "bootstrap_api_key_enabled", "bootstrap_source_system"} & set(Settings.model_fields)
    client.app.state.settings = settings
    login(client)
    assert client.get(BASE).json() == {"items": []}
    issued = issue(client)
    logout(client)
    for raw in (legacy_key, "dev-service-key-change-me", "test-service-api-key"):
        response = client.get("/api/v1/auth/me", headers={"X-API-Key": raw})
        assert response.status_code == 401 and response.json() == {"detail": "authentication_required"}
    assert client.get("/api/v1/auth/me", headers=headers(issued)).status_code == 200
    login(client)
    listing = client.get(BASE).json()
    assert set(listing) == {"items"}
    assert len(listing["items"]) == 1
    assert legacy_key not in client.get(BASE).text


def test_authenticated_admin_session_still_takes_precedence(client):
    login(client)
    issued = issue(client, scopes=["ingest"])
    assert client.get(BASE, headers=headers(issued)).status_code == 200
    principal = client.get("/api/v1/auth/me", headers=headers(issued)).json()
    assert principal["kind"] == "admin_session" and "admin" in principal["scopes"]
    assert db_key(client, issued["item"]["id"]).last_used_at is None


@pytest.mark.parametrize("field,value", [
    ("scopes_json", ["admin"]), ("scopes_json", ["ingest", "ingest"]),
    ("scopes_json", {"ingest": True}), ("scopes_json", [{"admin": True}]),
    ("scopes_json", []), ("source_system", "waf-internal-shadow"),
    ("source_system", "admin-ui"), ("key_hash", "invalid-digest"),
])
def test_tampered_database_credentials_fail_closed(client, field, value):
    login(client)
    issued = issue(client)
    with client.app.state.session_factory() as db:
        setattr(db.get(ServiceApiKey, issued["item"]["id"]), field, value)
        db.commit()
    logout(client)
    assert client.get("/api/v1/auth/me", headers=headers(issued)).status_code == 401
    assert db_key(client, issued["item"]["id"]).last_used_at is None


def test_issue_and_auth_helpers_do_not_commit_the_callers_transaction(client):
    payload = ServiceApiKeyCreate(name="Transaction", source_system="transaction", scopes=["ingest"])
    with client.app.state.session_factory() as db:
        key, raw = issue_key(db, payload, "synthetic-admin")
        key_id = key.id
        assert authenticate_key(db, raw) is not None
        db.rollback()
    assert db_key(client, key_id) is None


def test_storage_failure_has_safe_error_and_no_partial_row(client, monkeypatch, caplog):
    login(client)

    def fail_issue(*_args, **_kwargs):
        raise SQLAlchemyError("synthetic-sensitive-internal-error")

    monkeypatch.setattr("app.api.service_api_keys.issue_key", fail_issue)
    response = client.post(BASE, json={"name": "Failure", "source_system": "failure", "scopes": ["ingest"]})
    assert response.status_code == 503 and response.json() == {"detail": "service_api_key_storage_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "synthetic-sensitive-internal-error" not in response.text + caplog.text
    assert client.get(BASE).json()["items"] == []


def test_auth_storage_failure_fails_closed_without_internal_exception(client, monkeypatch, caplog):
    login(client)
    issued = issue(client)
    logout(client)

    def fail_auth(*_args, **_kwargs):
        raise SQLAlchemyError("synthetic-sensitive-auth-error")

    monkeypatch.setattr("app.security.authenticate_key", fail_auth)
    response = client.get("/api/v1/auth/me", headers=headers(issued))
    assert response.status_code == 503 and response.json() == {"detail": "service_api_key_authentication_unavailable"}
    assert "synthetic-sensitive-auth-error" not in response.text + caplog.text


def test_old_constructor_and_dotenv_settings_are_ignored(tmp_path):
    from app.config import Settings

    synthetic_env = tmp_path / "synthetic-obsolete.env"
    synthetic_env.write_text("WAF_BOOTSTRAP_API_KEY=synthetic-obsolete-key\nWAF_BOOTSTRAP_API_KEY_ENABLED=invalid\nWAF_BOOTSTRAP_SOURCE_SYSTEM=synthetic-source\n", encoding="utf-8")
    settings = Settings(_env_file=synthetic_env, bootstrap_api_key="synthetic-obsolete-constructor", bootstrap_api_key_enabled=True, bootstrap_source_system="synthetic-constructor-source")
    assert not {"bootstrap_api_key", "bootstrap_api_key_enabled", "bootstrap_source_system"} & set(settings.model_dump())
