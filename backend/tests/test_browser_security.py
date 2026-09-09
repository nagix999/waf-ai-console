"""Independent browser-origin tests: raw clients, synthetic data, no worker.

These clients deliberately do not use conftest.client or inject a default
Origin header. Service credentials exist only in each in-memory fixture DB.
"""
import asyncio
import json
from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api_key_schemas import ServiceApiKeyCreate
from app.browser_security import normalize_origin
from app.config import Settings
from app.database import Base
from app.main import create_app
from app.security import Principal, require_scope
from app.services.service_api_keys import issue_key

PUBLIC_ORIGIN = "https://waf.cyberailabs.team"
ATTACKER_ORIGIN = "https://attacker.invalid"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
SYNTHETIC_WRITE = "/api/v1/_synthetic-browser-write"


def browser_settings(**overrides):
    options = {
        "database_url": "sqlite+pysqlite:///:memory:",
        "admin_username": "synthetic-browser-admin",
        "admin_password": "synthetic-browser-password",
        "session_secret": "synthetic-browser-session-secret-not-a-deployment-secret",
        "data_encryption_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "environment": "production", "public_origin": PUBLIC_ORIGIN,
        "session_https_only": True, "agent_mode": "stub",
    }
    return Settings(_env_file=None, **(options | overrides))


@pytest.fixture
def browser_client():
    app = create_app(browser_settings(), create_schema=True)
    app.state.synthetic_write_calls = 0

    @app.api_route(SYNTHETIC_WRITE, methods=["POST", "PUT", "PATCH", "DELETE"])
    def synthetic_write(_principal: Annotated[Principal, Depends(require_scope("admin"))]):
        app.state.synthetic_write_calls += 1
        return {"accepted": True}

    with TestClient(app, base_url=PUBLIC_ORIGIN, follow_redirects=False) as client:
        assert "origin" not in client.headers
        yield client


@pytest.fixture
def browser_event():
    return {
        "event_id": "synthetic-browser-event", "company_name": "Synthetic Browser Test",
        "src_ip": "192.0.2.44", "dest_ip": "198.51.100.55", "src_port": 44000, "dest_port": 443,
        "payload": "GET /synthetic HTTP/1.1\r\nHost: example.test\r\nCookie: synthetic=fixture\r\n\r\n",
        "signature": "Synthetic browser test", "event_name": "Synthetic event",
        "waf_vendor": "synthetic", "waf_action": "D",
    }


@pytest.fixture
def browser_service_headers(browser_client):
    with browser_client.app.state.session_factory() as db:
        _, raw = issue_key(db, ServiceApiKeyCreate(name="Synthetic browser service",
            source_system="synthetic-browser-source", scopes=["ingest", "review"]), "synthetic-fixture")
        db.commit()
    return {"X-API-Key": raw}


def login_payload(client):
    settings = client.app.state.settings
    return {"username": settings.admin_username, "password": settings.admin_password}


def login(client, headers=None):
    response = client.post(LOGIN, json=login_payload(client),
        headers={"Origin": PUBLIC_ORIGIN} if headers is None else headers)
    assert response.status_code == 200
    return response


def stored_rows(client):
    with client.app.state.session_factory() as db:
        return {table.name: sorted((tuple(row) for row in db.execute(select(table))), key=repr)
                for table in Base.metadata.sorted_tables}


def assert_csrf(response, code):
    assert response.status_code == 403
    assert response.json() == {"detail": code}


@pytest.mark.parametrize("headers,code", [
    ({}, "csrf_origin_required"),
    ({"Origin": "null"}, "csrf_origin_invalid"),
    ({"Origin": ""}, "csrf_origin_invalid"),
    ({"Origin": ATTACKER_ORIGIN}, "csrf_origin_invalid"),
    ({"Origin": PUBLIC_ORIGIN + ".attacker.invalid"}, "csrf_origin_invalid"),
    ({"Origin": "http://waf.cyberailabs.team"}, "csrf_origin_invalid"),
    ({"Origin": PUBLIC_ORIGIN + ":444"}, "csrf_origin_invalid"),
    ({"Origin": PUBLIC_ORIGIN + "/"}, "csrf_origin_invalid"),
    ({"Origin": PUBLIC_ORIGIN + "/path"}, "csrf_origin_invalid"),
    ({"Origin": PUBLIC_ORIGIN + "?query=synthetic"}, "csrf_origin_invalid"),
    ({"Origin": PUBLIC_ORIGIN + "#fragment"}, "csrf_origin_invalid"),
    ({"Origin": "https://user@waf.cyberailabs.team"}, "csrf_origin_invalid"),
    ({"Origin": PUBLIC_ORIGIN + ", " + PUBLIC_ORIGIN}, "csrf_origin_invalid"),
    ({"Origin": ATTACKER_ORIGIN, "Referer": PUBLIC_ORIGIN + "/settings"}, "csrf_origin_invalid"),
    ({"Origin": "null", "Referer": PUBLIC_ORIGIN + "/settings"}, "csrf_origin_invalid"),
], ids=["missing", "null", "empty", "foreign", "suffix", "scheme", "port", "slash", "path",
        "query", "fragment", "userinfo", "comma", "origin-wins", "null-origin-wins"])
def test_login_rejects_bad_origins_without_session_or_database_change(browser_client, headers, code):
    before = stored_rows(browser_client)
    response = browser_client.post(LOGIN, json=login_payload(browser_client), headers=headers)
    assert_csrf(response, code)
    assert not browser_client.cookies
    assert "set-cookie" not in response.headers
    assert stored_rows(browser_client) == before


@pytest.mark.parametrize("headers", [
    [("Origin", PUBLIC_ORIGIN), ("Origin", PUBLIC_ORIGIN)],
    [("Origin", PUBLIC_ORIGIN), ("Origin", ATTACKER_ORIGIN)],
    [("Origin", ATTACKER_ORIGIN), ("Origin", PUBLIC_ORIGIN)],
    [("Origin", PUBLIC_ORIGIN), ("origin", PUBLIC_ORIGIN)],
], ids=["same", "foreign-last", "foreign-first", "mixed-case-header"])
def test_duplicate_origin_is_never_accepted(browser_client, headers):
    assert_csrf(browser_client.post(LOGIN, json=login_payload(browser_client), headers=headers), "csrf_origin_invalid")
    assert not browser_client.cookies


def test_csrf_is_rejected_before_reading_the_request_body(browser_client):
    def forbidden_body():
        raise AssertionError("CSRF rejection must not read the synthetic request body")
        yield b"unreachable"
    response = browser_client.post(LOGIN, content=forbidden_body(),
        headers={"Origin": ATTACKER_ORIGIN, "Content-Type": "application/json"})
    assert_csrf(response, "csrf_origin_invalid")


def test_referer_exact_origin_fallback_and_origin_precedence(browser_client):
    login(browser_client, {"Referer": PUBLIC_ORIGIN + "/settings?tab=synthetic"})
    assert browser_client.post(SYNTHETIC_WRITE, headers={"Referer": PUBLIC_ORIGIN + "/tests"}).status_code == 200
    # A present valid Origin is authoritative; fallback is only for its absence.
    assert browser_client.post(SYNTHETIC_WRITE,
        headers={"Origin": PUBLIC_ORIGIN, "Referer": ATTACKER_ORIGIN}).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Referer": ATTACKER_ORIGIN + "/"}, {"Referer": "null"}, {"Referer": "/relative"},
    {"Referer": "https://user@waf.cyberailabs.team/path"},
    [("Referer", PUBLIC_ORIGIN + "/one"), ("Referer", PUBLIC_ORIGIN + "/two")],
], ids=["foreign", "null", "relative", "userinfo", "duplicate"])
def test_bad_referer_fallback_is_not_accepted(browser_client, headers):
    assert_csrf(browser_client.post(LOGIN, json=login_payload(browser_client), headers=headers), "csrf_origin_invalid")


@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site", "unexpected"])
def test_fetch_metadata_rejects_cross_site_same_site_and_unknown(browser_client, fetch_site):
    assert_csrf(browser_client.post(LOGIN, json=login_payload(browser_client),
        headers={"Origin": PUBLIC_ORIGIN, "Sec-Fetch-Site": fetch_site}), "csrf_origin_invalid")


@pytest.mark.parametrize("fetch_site", ["same-origin", "none"])
def test_allowed_fetch_metadata_still_requires_valid_origin(browser_client, fetch_site):
    assert_csrf(browser_client.post(LOGIN, json=login_payload(browser_client),
        headers={"Sec-Fetch-Site": fetch_site}), "csrf_origin_required")
    login(browser_client, {"Origin": PUBLIC_ORIGIN, "Sec-Fetch-Site": fetch_site})


def test_duplicate_fetch_metadata_is_invalid(browser_client):
    assert_csrf(browser_client.post(LOGIN, json=login_payload(browser_client), headers=[
        ("Origin", PUBLIC_ORIGIN), ("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "cross-site"),
    ]), "csrf_origin_invalid")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_admin_unsafe_method_requires_origin(browser_client, method):
    login(browser_client)
    before = stored_rows(browser_client)
    assert_csrf(browser_client.request(method, SYNTHETIC_WRITE), "csrf_origin_required")
    assert_csrf(browser_client.request(method, SYNTHETIC_WRITE,
        headers={"Origin": ATTACKER_ORIGIN}), "csrf_origin_invalid")
    assert browser_client.app.state.synthetic_write_calls == 0
    assert stored_rows(browser_client) == before
    assert browser_client.request(method, SYNTHETIC_WRITE, headers={"Origin": PUBLIC_ORIGIN}).status_code == 200
    assert browser_client.app.state.synthetic_write_calls == 1


@pytest.mark.parametrize("mode", ["direct", "upload", "legacy-direct", "legacy-upload"])
def test_csrf_test_submission_does_not_initialize_schema_or_enqueue_rows(browser_client, browser_event, mode):
    login(browser_client)
    before = stored_rows(browser_client)
    if mode == "direct":
        response = browser_client.post("/api/v1/test-runs", json={"name": "Synthetic browser test",
            "idempotency_key": "synthetic-browser-run", "event": browser_event})
    elif mode == "upload":
        response = browser_client.post("/api/v1/test-runs/uploads",
            data={"name": "Synthetic browser batch", "idempotency_key": "synthetic-browser-upload"},
            files={"file": ("synthetic.json", json.dumps([browser_event]).encode(), "application/json")})
    elif mode == "legacy-direct":
        response = browser_client.post("/api/v1/test-analyses",
            params={"name": "Synthetic legacy direct", "idempotency_key": "synthetic-legacy-direct"}, json=browser_event)
    else:
        response = browser_client.post("/api/v1/test-uploads",
            params={"name": "Synthetic legacy upload", "idempotency_key": "synthetic-legacy-upload"},
            files={"file": ("synthetic.json", json.dumps([browser_event]).encode(), "application/json")})
    assert_csrf(response, "csrf_origin_required")
    assert stored_rows(browser_client) == before


@pytest.mark.parametrize("mode", ["direct", "upload"])
def test_same_origin_ui_test_submission_is_still_usable(browser_client, browser_event, mode):
    login(browser_client)
    if mode == "direct":
        response = browser_client.post("/api/v1/test-runs", headers={"Origin": PUBLIC_ORIGIN},
            json={"name": "Synthetic same-origin direct", "idempotency_key": "synthetic-direct-ok", "event": browser_event})
    else:
        response = browser_client.post("/api/v1/test-runs/uploads", headers={"Origin": PUBLIC_ORIGIN},
            data={"name": "Synthetic same-origin upload", "idempotency_key": "synthetic-upload-ok"},
            files={"file": ("synthetic.json", json.dumps([browser_event]).encode(), "application/json")})
    assert response.status_code == 202
    assert response.json()["accepted"] == 1 and response.json()["execution_mode"] == "stub"


def test_cookie_free_service_ingest_upload_and_review_need_no_origin(browser_client, browser_service_headers, browser_event):
    assert not browser_client.cookies and "origin" not in browser_client.headers
    created = browser_client.post("/api/v1/analyses", json=browser_event, headers=browser_service_headers)
    assert created.status_code == 202
    assert created.json()["source_system"] == "synthetic-browser-source"
    assert created.json()["analysis_purpose"] == "production"
    batch = browser_client.post("/api/v1/uploads", headers=browser_service_headers,
        files={"file": ("synthetic.json", json.dumps([{**browser_event, "event_id": "synthetic-service-batch"}]).encode(), "application/json")})
    assert batch.status_code == 202 and batch.json()["accepted"] == 1
    review = browser_client.post(f"/api/v1/analyses/{created.json()['id']}/reviews", headers=browser_service_headers,
        json={"external_review_id": "synthetic-browser-review", "event_id": browser_event["event_id"],
              "decision": "deferred", "analyst_id": "synthetic-analyst", "comment": None, "ai_visible": False})
    assert review.status_code == 201 and review.json()["source_system"] == "synthetic-browser-source"
    assert not browser_client.cookies


def test_cookie_and_api_key_keep_session_priority_and_do_not_update_key_on_csrf(browser_client, browser_service_headers, browser_event):
    login(browser_client)
    before = stored_rows(browser_client)
    response = browser_client.post("/api/v1/analyses", json=browser_event, headers=browser_service_headers)
    assert_csrf(response, "csrf_origin_required")
    assert stored_rows(browser_client) == before
    response = browser_client.post("/api/v1/analyses", json=browser_event,
        headers={**browser_service_headers, "Origin": PUBLIC_ORIGIN})
    assert response.status_code == 202
    assert response.json()["source_system"] == "admin-ui"
    assert response.json()["service_api_key_id"] is None
    assert stored_rows(browser_client)["service_api_keys"] == before["service_api_keys"]


def test_session_reads_do_not_require_origin(browser_client):
    login(browser_client)
    assert browser_client.get("/api/v1/auth/me").json()["kind"] == "admin_session"
    assert browser_client.get("/api/v1/test-runs").status_code == 200


@pytest.mark.parametrize("logged_in", [False, True])
def test_logout_always_requires_origin_and_rejection_preserves_login(browser_client, logged_in):
    if logged_in:
        login(browser_client)
    before = stored_rows(browser_client)
    assert_csrf(browser_client.post(LOGOUT), "csrf_origin_required")
    assert_csrf(browser_client.post(LOGOUT, headers={"Origin": ATTACKER_ORIGIN}), "csrf_origin_invalid")
    assert stored_rows(browser_client) == before
    assert browser_client.get("/api/v1/auth/me").status_code == (200 if logged_in else 401)
    assert browser_client.post(LOGOUT, headers={"Origin": PUBLIC_ORIGIN}).status_code == 204
    assert browser_client.get("/api/v1/auth/me").status_code == 401


def test_secure_session_uses_host_prefix_and_logout_clears_same_cookie(browser_client):
    response = login(browser_client)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-waf_session=")
    assert "secure" in cookie.lower() and "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower() and "path=/" in cookie.lower()
    assert "domain=" not in cookie.lower()
    assert "session" not in browser_client.cookies
    response = browser_client.post(LOGOUT, headers={"Origin": PUBLIC_ORIGIN})
    assert response.status_code == 204
    assert response.headers["set-cookie"].startswith("__Host-waf_session=")
    assert "secure" in response.headers["set-cookie"].lower()
    assert not browser_client.cookies


def test_legacy_cookie_name_is_not_an_admin_credential_in_https_mode(browser_client):
    login(browser_client)
    signed = browser_client.cookies.get("__Host-waf_session")
    browser_client.cookies.clear()
    response = browser_client.get("/api/v1/auth/me", headers={"Cookie": "session=" + signed})
    assert response.status_code == 401


@pytest.mark.parametrize("host", ["attacker.invalid", "waf.cyberailabs.team.attacker.invalid", "waf.cyberailabs.team:444"])
def test_public_origin_rejects_other_hosts_before_login(browser_client, host):
    before = stored_rows(browser_client)
    response = browser_client.post(LOGIN, headers={"Host": host, "Origin": PUBLIC_ORIGIN}, json=login_payload(browser_client))
    assert response.status_code == 421 and response.json() == {"detail": "untrusted_host"}
    assert stored_rows(browser_client) == before and not browser_client.cookies


def test_forwarded_headers_do_not_override_fixed_public_origin_or_host(browser_client):
    spoofed = {"X-Forwarded-Host": "attacker.invalid", "X-Forwarded-Proto": "http",
               "Forwarded": "host=attacker.invalid;proto=http"}
    assert_csrf(browser_client.post(LOGIN, json=login_payload(browser_client),
        headers={**spoofed, "Origin": ATTACKER_ORIGIN}), "csrf_origin_invalid")
    login(browser_client, {**spoofed, "Origin": PUBLIC_ORIGIN})
    response = browser_client.get("/api/v1/auth/me",
        headers={"Host": "attacker.invalid", "X-Forwarded-Host": "waf.cyberailabs.team", "X-Forwarded-Proto": "https"})
    assert response.status_code == 421


def test_duplicate_host_cannot_ambivalently_select_the_public_authority(browser_client):
    response = browser_client.post(LOGIN, json=login_payload(browser_client), headers=[
        ("Host", "waf.cyberailabs.team"), ("Host", "attacker.invalid"), ("Origin", PUBLIC_ORIGIN),
    ])
    assert response.status_code == 421 and response.json() == {"detail": "untrusted_host"}


def test_fixed_https_public_origin_supports_proxy_internal_http_without_trusting_forwarded(browser_client):
    response = browser_client.post("http://waf.cyberailabs.team" + LOGIN,
        json=login_payload(browser_client), headers={"Origin": PUBLIC_ORIGIN, "X-Forwarded-Proto": "attacker"})
    assert response.status_code == 200
    assert response.headers["set-cookie"].startswith("__Host-waf_session=")


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_get_allows_direct_internal_host_but_no_general_host_bypass(browser_client, path):
    response = browser_client.get("http://127.0.0.1:8000" + path)
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert browser_client.post("http://127.0.0.1:8000" + path).status_code == 421
    assert browser_client.get("http://127.0.0.1:8000/api/v1/auth/me").status_code == 421


def test_development_uses_actual_request_origin_and_legacy_cookie_name():
    app = create_app(browser_settings(environment="development", public_origin="", session_https_only=False), create_schema=True)
    with TestClient(app, base_url="http://testserver") as client:
        spoofed = {"X-Forwarded-Host": "attacker.invalid", "X-Forwarded-Proto": "https",
                   "Forwarded": "host=attacker.invalid;proto=https"}
        assert_csrf(client.post(LOGIN, json=login_payload(client),
            headers={**spoofed, "Origin": ATTACKER_ORIGIN}), "csrf_origin_invalid")
        response = client.post(LOGIN, json=login_payload(client), headers={**spoofed, "Origin": "http://testserver"})
        assert response.status_code == 200 and response.headers["set-cookie"].startswith("session=")
        assert "secure" not in response.headers["set-cookie"].lower()
        assert client.post(LOGOUT, headers={"Origin": "https://testserver"}).status_code == 403
        assert client.post(LOGOUT, headers={"Origin": "http://testserver"}).status_code == 204


def test_configured_public_origin_enforces_host_in_development_too():
    app = create_app(browser_settings(environment="development"), create_schema=True)
    with TestClient(app, base_url=PUBLIC_ORIGIN) as client:
        response = client.post(LOGIN, json=login_payload(client),
            headers={"Host": "attacker.invalid", "Origin": PUBLIC_ORIGIN})
        assert response.status_code == 421 and response.json() == {"detail": "untrusted_host"}


@pytest.mark.parametrize("options", [
    {"public_origin": ""}, {"public_origin": "http://waf.cyberailabs.team"},
    {"public_origin": "https://waf.cyberailabs.team/extra"},
    {"public_origin": "https://user@waf.cyberailabs.team"},
    {"public_origin": "https://waf.cyberailabs.team?query=synthetic"},
    {"public_origin": "https://waf.cyberailabs.team#fragment"},
    {"session_https_only": False},
], ids=["missing-origin", "insecure-origin", "origin-path", "origin-userinfo", "origin-query", "origin-fragment", "insecure-cookie"])
def test_production_insecure_or_malformed_browser_settings_fail_closed(options):
    with pytest.raises(ValueError):
        create_app(browser_settings(**options), create_schema=True)


@pytest.mark.parametrize("value", [
    "https://[::1]trailing", "https://[::1]:443trailing", "https://[::1]:", "https://[::1]:0",
    "https://[::1]:65536", "https://[fe80::1%25fixture]", "https://[v1.example]", "https://[::1",
    PUBLIC_ORIGIN + ":0", PUBLIC_ORIGIN + ":65536", PUBLIC_ORIGIN + ":-1", PUBLIC_ORIGIN + ":abc",
    PUBLIC_ORIGIN + ":", PUBLIC_ORIGIN + "\n", "\t" + PUBLIC_ORIGIN, PUBLIC_ORIGIN + "\x00",
    PUBLIC_ORIGIN + "\x7f", "https://waƒ.cyberailabs.team", PUBLIC_ORIGIN + "\\@attacker.invalid",
    "https://-invalid.test", "https://invalid-.test", "https://invalid..test", "https://invalid_test",
    "https://" + "a" * 64 + ".test", "https://" + "a" * 2050,
], ids=["ipv6-junk", "ipv6-port-junk", "ipv6-empty-port", "ipv6-zero-port", "ipv6-port-range",
        "ipv6-zone", "ipvfuture", "ipv6-unclosed", "zero-port", "port-range", "negative-port", "nonnumeric-port",
        "empty-port", "newline", "leading-control", "nul", "del", "nonascii", "backslash",
        "leading-hyphen", "trailing-hyphen", "empty-label", "underscore", "long-label", "long-origin"])
def test_origin_parser_rejects_ambiguous_authorities_and_controls(value):
    with pytest.raises(ValueError) as caught:
        normalize_origin(value)
    assert str(caught.value) == "invalid origin"


@pytest.mark.parametrize("value,expected", [
    ("HTTPS://WAF.CYBERAILABS.TEAM:443", PUBLIC_ORIGIN),
    ("http://example.test:80", "http://example.test"),
    ("https://example.test:8443", "https://example.test:8443"),
    ("https://[2001:0db8:0000:0000:0000:0000:0000:0001]:443", "https://[2001:db8::1]"),
    ("http://[::1]:80", "http://[::1]"),
    ("https://192.0.2.10:443", "https://192.0.2.10"),
])
def test_origin_parser_canonicalizes_case_default_ports_and_ip_literals(value, expected):
    assert normalize_origin(value) == expected


@pytest.mark.parametrize("path", [LOGIN + "/", LOGOUT + "/"])
def test_auth_trailing_slash_cannot_skip_origin_before_route_redirect(browser_client, path):
    assert_csrf(browser_client.post(path, json=login_payload(browser_client)), "csrf_origin_required")
    assert not browser_client.cookies


@pytest.mark.parametrize("configuration", ["malformed-origin", "production-invariant"])
def test_configuration_errors_do_not_echo_origin_or_other_supplied_secrets(configuration):
    marker = "SYNTHETIC-CONFIG-PRIVATE-MARKER"
    options = {"admin_password": marker + "-password", "session_secret": marker + "-session"}
    if configuration == "malformed-origin":
        options["public_origin"] = "https://" + marker + "@waf.cyberailabs.team"
    else:
        options["session_https_only"] = False
    with pytest.raises(ValueError) as caught:
        browser_settings(**options)
    assert marker not in str(caught.value)
    assert marker not in repr(caught.value)


def through_real_uvicorn_proxy_middleware(peer):
    """Exercise installed Uvicorn, without binding a socket or starting an API."""
    captured = {}
    headers = [(b"host", b"waf.cyberailabs.team"), (b"x-forwarded-for", b"203.0.113.55"),
               (b"x-forwarded-proto", b"https"), (b"x-forwarded-host", b"attacker.invalid")]

    async def application(scope, receive, send):
        captured.update(scheme=scope["scheme"], client=scope["client"], headers=scope["headers"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"synthetic"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        assert message["type"] in {"http.response.start", "http.response.body"}

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": "/synthetic", "raw_path": b"/synthetic",
             "root_path": "", "query_string": b"", "headers": headers,
             "client": (peer, 43210), "server": ("0.0.0.0", 8000)}
    middleware = ProxyHeadersMiddleware(application, trusted_hosts="172.30.251.10")
    asyncio.run(middleware(scope, receive, send))
    assert captured["headers"] == headers  # Host is never sourced from XFH.
    return captured


def test_real_uvicorn_restores_client_and_https_only_for_exact_frontend_peer():
    result = through_real_uvicorn_proxy_middleware("172.30.251.10")
    assert result["scheme"] == "https"
    assert result["client"] == ("203.0.113.55", 0)
    assert dict(result["headers"])[b"host"] == b"waf.cyberailabs.team"


@pytest.mark.parametrize("peer", ["172.30.251.11", "198.51.100.80"], ids=["other-worker", "external-peer"])
def test_real_uvicorn_ignores_forged_forwarding_from_other_peers(peer):
    result = through_real_uvicorn_proxy_middleware(peer)
    assert result["scheme"] == "http"
    assert result["client"] == (peer, 43210)
    assert dict(result["headers"])[b"host"] == b"waf.cyberailabs.team"
