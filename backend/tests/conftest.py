import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        admin_username="admin",
        admin_password="test-password",
        session_secret="test-session-secret-that-is-long-enough",
        bootstrap_api_key="test-service-api-key",
        bootstrap_source_system="test-parser",
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings, create_schema=True)
    with TestClient(app) as test_client:
        yield test_client


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
def service_headers() -> dict[str, str]:
    return {"x-api-key": "test-service-api-key"}
