import io
import json

from app.models import Analysis
from app.services.crypto import CryptoService
from app.worker import process_stub


def test_health_is_public(client):
    assert client.get("/health/live").json() == {"status": "ok"}


def test_analysis_enqueue_is_idempotent_and_payload_is_encrypted(client, event_payload, service_headers):
    first = client.post("/api/v1/analyses", headers=service_headers, json=event_payload)
    assert first.status_code == 202
    second = client.post("/api/v1/analyses", headers=service_headers, json=event_payload)
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]

    with client.app.state.session_factory() as db:
        row = db.get(Analysis, first.json()["id"])
        assert event_payload["payload"] not in row.payload_ciphertext
        assert row.extra_fields == {"future_vendor_field": "preserved"}


def test_raw_payload_requires_admin_session(client, event_payload, service_headers):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    denied = client.get(f"/api/v1/analyses/{created['id']}/event", headers=service_headers)
    assert denied.status_code == 403

    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    assert login.status_code == 200
    raw = client.get(f"/api/v1/analyses/{created['id']}/event")
    assert raw.status_code == 200
    assert raw.json()["payload"] == event_payload["payload"]
    audits = client.get("/api/v1/audit-logs").json()
    assert audits[0]["action"] == "view_raw_event"
    assert audits[0]["resource_id"] == created["id"]


def test_review_is_append_only_and_idempotent(client, event_payload, service_headers):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    review = {
        "external_review_id": "review-001",
        "event_id": event_payload["event_id"],
        "decision": "false_positive",
        "analyst_id": "analyst-7",
        "comment": "fixture review",
        "ai_visible": False,
    }
    first = client.post(f"/api/v1/analyses/{created['id']}/reviews", headers=service_headers, json=review)
    second = client.post(f"/api/v1/analyses/{created['id']}/reviews", headers=service_headers, json=review)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_csv_upload_accepts_multiple_rows(client, event_payload, service_headers):
    csv_content = (
        "event_id,company_name,src_ip,dest_ip,src_port,dest_port,payload,signature,event_name,waf_vendor,waf_action\n"
        'csv-1,Example,192.0.2.1,198.51.100.1,1234,443,"GET / HTTP/1.1",sig,event,generic,D\n'
        'csv-2,Example,192.0.2.2,198.51.100.2,1235,443,"POST /login HTTP/1.1",sig,event,generic,A\n'
    )
    response = client.post(
        "/api/v1/uploads",
        headers=service_headers,
        files={"file": ("events.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert response.status_code == 202
    assert response.json()["accepted"] == 2
    assert response.json()["rejected"] == 0


def test_stub_worker_creates_visible_agent_history(client, event_payload, service_headers, settings):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with client.app.state.session_factory() as db:
        process_stub(db, crypto, db.get(Analysis, created["id"]))

    client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    result = client.get(f"/api/v1/analyses/{created['id']}").json()
    runs = client.get(f"/api/v1/analyses/{created['id']}/agent-runs").json()
    assert result["verdict"] == "inconclusive"
    assert len(runs) == 1
    assert [step["step_type"] for step in runs[0]["steps"]] == ["input", "parser", "agent_stub"]
    step_input = json.loads(runs[0]["steps"][0]["input"])
    assert step_input["payload"] == event_payload["payload"]
