import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
