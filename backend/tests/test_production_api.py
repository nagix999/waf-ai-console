import io
import json

import pytest

from app.models import Analysis
from app.schemas import AnalysisInput
from app.services.analysis import enqueue_analysis


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def test_server_assigns_purpose_and_channel(client, event_payload, service_headers):
    production = client.post("/api/v1/analyses", json=event_payload, headers=service_headers)
    assert production.status_code == 202
    assert production.json()["analysis_purpose"] == "production"
    assert production.json()["ingest_channel"] == "service_api"
    assert client.post("/api/v1/test-analyses", json=event_payload, headers=service_headers).status_code == 403
    assert client.post("/api/v1/test-uploads", files={"file": ("events.json", b"[]")}, headers=service_headers).status_code == 403

    login(client)
    lab = client.post("/api/v1/test-analyses", params={"name": "합성 직접 테스트", "idempotency_key": "synthetic-direct-run"}, json=event_payload)
    assert lab.status_code == 202
    assert (lab.json()["analysis_purpose"], lab.json()["ingest_channel"]) == ("test", "test_lab")
    upload_payload = {**event_payload, "event_id": "upload-test"}
    upload = client.post("/api/v1/test-uploads", params={"name": "합성 파일 테스트", "idempotency_key": "synthetic-file-run"}, files={"file": ("events.json", json.dumps([upload_payload]).encode())})
    assert upload.status_code == 202
    detail = client.get(f"/api/v1/analyses/{upload.json()['analysis_ids'][0]}").json()
    assert (detail["analysis_purpose"], detail["ingest_channel"]) == ("test", "file_upload")
    # Named test runs use their own source, so Production receives the same
    # external event ID without colliding with an unrelated test execution.
    ordinary = client.post("/api/v1/analyses", json=event_payload)
    assert ordinary.status_code == 202 and ordinary.json()["id"] != lab.json()["id"]
    legacy_payload = {**event_payload, "event_id": "synthetic-legacy-test"}
    with client.app.state.session_factory() as db:
        enqueue_analysis(db, client.app.state.crypto, "admin-ui", AnalysisInput.model_validate(legacy_payload), analysis_purpose="test", ingest_channel="test_lab")
    collision = client.post("/api/v1/analyses", json=legacy_payload)
    assert collision.status_code == 409
    assert collision.json()["detail"] == "event_purpose_conflict"


@pytest.mark.parametrize("field", ["source_system", "analysis_purpose", "ingest_channel", "verdict", "model_profile", "severity"])
def test_control_fields_are_rejected_without_echoing_values(client, event_payload, service_headers, field):
    marker = "synthetic-secret-control-value"
    response = client.post("/api/v1/analyses", headers=service_headers, json={**event_payload, field: marker})
    assert response.status_code == 422
    assert marker not in response.text
    assert "server_control_fields_not_allowed" in response.text
    response = client.post(f"/api/v1/analyses?{field}={marker}", headers=service_headers, json=event_payload)
    assert response.status_code == 422
    assert marker not in response.text


def test_duplicate_checks_normalized_content_and_preserves_legacy(client, event_payload, service_headers):
    first = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    normalized = client.post("/api/v1/analyses", headers=service_headers, json={**event_payload, "waf_action": "d"})
    assert normalized.status_code == 202
    assert normalized.json()["id"] == first["id"]
    for field, value in (("payload", "synthetic-changed-payload"), ("signature", "changed"), ("future_vendor_field", "changed")):
        conflict = client.post("/api/v1/analyses", headers=service_headers, json={**event_payload, field: value})
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "event_id_conflict"
        assert value not in conflict.text
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, first["id"])
        row.analysis_purpose = row.ingest_channel = "legacy_unknown"
        row.event_fingerprint = None
        row.result_json = {"schema_version": "waf-analysis-v1", "uncertainties": ["synthetic"], "future": {"keep": True}}
        db.commit()
    legacy = client.post("/api/v1/analyses", headers=service_headers, json=event_payload)
    assert legacy.status_code == 202
    assert legacy.json()["analysis_purpose"] == "legacy_unknown"
    assert legacy.json()["result"]["future"] == {"keep": True}
    assert legacy.json()["result"]["uncertainties"] == ["synthetic"]
    assert client.post("/api/v1/analyses", headers=service_headers, json={**event_payload, "payload": "changed"}).status_code == 409


def test_service_can_only_read_and_review_own_source(client, event_payload, service_headers):
    own = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    with client.app.state.session_factory() as db:
        other, _ = enqueue_analysis(db, client.app.state.crypto, "other-parser", AnalysisInput.model_validate(event_payload))
        other_id = other.id
    assert client.get("/api/v1/analyses", headers=service_headers).json()["total"] == 1
    assert client.get("/api/v1/analyses?source_system=other-parser", headers=service_headers).json()["total"] == 0
    assert client.get(f"/api/v1/analyses/{other_id}", headers=service_headers).status_code == 404
    review = {"external_review_id": "cross-review", "event_id": event_payload["event_id"], "decision": "false_positive"}
    assert client.post(f"/api/v1/analyses/{other_id}/reviews", headers=service_headers, json=review).status_code == 404
    for endpoint in ("event", "agent-runs"):
        assert client.get(f"/api/v1/analyses/{own['id']}/{endpoint}", headers=service_headers).status_code == 403
    login(client)
    assert client.get("/api/v1/analyses").json()["total"] == 2
    assert client.get(f"/api/v1/analyses/{other_id}").status_code == 200


def test_utf8_payload_limit_and_upload_row_rejections(client, event_payload, service_headers):
    client.app.state.settings.payload_max_bytes = 6
    exact = {**event_payload, "payload": "한글"}
    first = client.post("/api/v1/analyses", headers=service_headers, json=exact)
    assert first.status_code == 202
    oversized = {**exact, "event_id": "too-large", "payload": "한글x"}
    response = client.post("/api/v1/analyses", headers=service_headers, json=oversized)
    assert response.status_code == 413
    assert response.json()["detail"] == "payload_too_large"
    conflict = {**exact, "payload": "다름"}
    fresh = {**exact, "event_id": "fresh"}
    rows = [exact, conflict, oversized, fresh]
    upload = client.post("/api/v1/uploads", headers=service_headers, files={"file": ("test.json", io.BytesIO(json.dumps(rows).encode()))})
    body = upload.json()
    assert upload.status_code == 202
    assert (body["accepted"], body["duplicates"], body["rejected"]) == (1, 1, 2)
    assert body["errors"] == [{"row": 2, "message": "event_id_conflict"}, {"row": 3, "message": "payload_too_large"}]
    assert "한글" not in upload.text


def test_review_duplicate_cannot_change_content_or_target(client, event_payload, service_headers):
    first = client.post("/api/v1/analyses", json=event_payload, headers=service_headers).json()
    second = client.post("/api/v1/analyses", json={**event_payload, "event_id": "evt-2"}, headers=service_headers).json()
    review = {"external_review_id": "review-1", "event_id": event_payload["event_id"], "decision": "false_positive"}
    endpoint = f"/api/v1/analyses/{first['id']}/reviews"
    assert client.post(endpoint, json=review, headers=service_headers).status_code == 201
    assert client.post(endpoint, json=review, headers=service_headers).status_code == 200
    changed = client.post(endpoint, json={**review, "decision": "true_positive"}, headers=service_headers)
    assert changed.status_code == 409
    changed_target = client.post(f"/api/v1/analyses/{second['id']}/reviews", json={**review, "event_id": "evt-2"}, headers=service_headers)
    assert changed_target.status_code == 409
    assert changed_target.json()["detail"] == "external_review_id_conflict"


def test_openapi_describes_key_result_and_accepted_response(client):
    schema = client.get("/openapi.json").json()
    key = schema["components"]["securitySchemes"]["ServiceAPIKey"]
    assert (key["type"], key["in"], key["name"]) == ("apiKey", "header", "X-API-Key")
    post = schema["paths"]["/api/v1/analyses"]["post"]
    assert {"ServiceAPIKey": []} in post["security"]
    assert post["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith("/AnalysisDetail")
    result = schema["components"]["schemas"]["AnalysisDetail"]["properties"]["result"]
    assert any(branch.get("$ref", "").endswith("/AnalysisResultV2") for branch in result["anyOf"])
