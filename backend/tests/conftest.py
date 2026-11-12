import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def legacy_evidence_editor_admissions(monkeypatch, request):
    """Opt-in replay of archived Editor/qualification contracts only.

    New display-job tests deliberately omit this marker and exercise the real
    asynchronous qualification gate. Old worker fixtures must keep their old
    ID-only, same-source editor instead of silently adopting a new contract.
    """
    historical = {"legacy_v220_admissions", "legacy_v221_admissions", "legacy_evidence_editor_admissions"}
    if not any(historical.intersection(marker.args) for marker in request.node.iter_markers("usefixtures")):
        return
    from app.services import evidence_display_jobs
    from app.services.evidence_editor import capture_editor_profile
    monkeypatch.setattr(evidence_display_jobs, "capture_display_profile", capture_editor_profile)


@pytest.fixture(autouse=True)
def legacy_v220_admissions(monkeypatch, request):
    """Replay archived worker contracts without replacing their runtime code.

    v2.21 has its own execution, policy, recovery and mixed-runtime suites.
    Tests that call the frozen worker directly must admit its frozen rules,
    rather than asking an archived engine to execute the latest candidate.
    """
    # Autouse ordering makes the opt-in take effect before a client captures
    # the initial Production baseline. Unmarked current-version suites stay on
    # the actual latest candidate.
    if not any("legacy_v220_admissions" in marker.args
               for marker in request.node.iter_markers("usefixtures")):
        return
    import app.agent
    from app.agent import legacy_prompts_v220
    monkeypatch.setattr(app.agent, "prompts", legacy_prompts_v220)


@pytest.fixture(autouse=True)
def legacy_v221_admissions(monkeypatch, request):
    """Replay archived v2.21 admissions without changing its frozen engine.

    Only explicitly marked historical suites bind the final saved v2.21
    reasoning revision. Unmarked suites keep the real current candidate, and
    local monkeypatch contexts may still exercise older v2.21 text revisions.
    """
    if not any("legacy_v221_admissions" in marker.args
               for marker in request.node.iter_markers("usefixtures")):
        return
    import app.agent
    from app.agent import legacy_prompts_v221_reasoning
    monkeypatch.setattr(app.agent, "prompts", legacy_prompts_v221_reasoning)


@pytest.fixture(autouse=True)
def legacy_v222_admissions(monkeypatch, request):
    """Replay pre-semantics v2.22 text with the unchanged v2.22 execution.

    This opt-in only pins the saved instructions; current unmarked tests and
    the independent display-job qualification gate are not replaced.
    """
    if not any("legacy_v222_admissions" in marker.args
               for marker in request.node.iter_markers("usefixtures")):
        return
    import app.agent
    from app.agent import legacy_prompts_v222
    monkeypatch.setattr(app.agent, "prompts", legacy_prompts_v222)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        admin_username="admin",
        admin_password="test-password",
        session_secret="test-session-secret-that-is-long-enough",
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings, create_schema=True)
    # Simulate the same-origin header browsers send on state-changing requests.
    # Security regressions use separate raw clients to exercise missing headers.
    with TestClient(app, headers={"Origin": "http://testserver"}) as test_client:
        yield test_client


@pytest.fixture
def registered_vllm_target(client):
    """Explicit synthetic authorization; the default client remains fail-closed."""
    from app.models import InternalEgressTarget

    with client.app.state.session_factory() as db:
        target = InternalEgressTarget(ip_address="10.0.0.10", port=8000, description="Synthetic offline fixture")
        db.add(target)
        db.commit()
        return target.id


@pytest.fixture
def event_payload() -> dict:
    return {
        "event_id": "evt-001",
        "company_name": "Example Corp",
        "src_ip": "192.0.2.10",
        "dest_ip": "198.51.100.20",
        "src_port": 43122,
        "dest_port": 443,
        "payload": "GET /search?q=test HTTP/1.1\r\nHost: example.internal\r\nCookie: session=fixture\r\n\r\n",
        "signature": "Synthetic Signature",
        "event_name": "fixture-event",
        "waf_vendor": "generic",
        "waf_action": "D",
        "future_vendor_field": "preserved",
    }


@pytest.fixture
def service_headers(client) -> dict[str, str]:
    """Opt-in synthetic DB credential, never an environment authentication path."""
    from app.api_key_schemas import ServiceApiKeyCreate
    from app.services.service_api_keys import issue_key

    with client.app.state.session_factory() as db:
        _key, raw = issue_key(db, ServiceApiKeyCreate(
            name="Synthetic test parser", source_system="test-parser", scopes=["ingest", "review"],
        ), "synthetic-fixture")
        db.commit()
    return {"x-api-key": raw}
