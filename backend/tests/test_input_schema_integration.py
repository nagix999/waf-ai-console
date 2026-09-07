"""Offline admission, documentation and inference-history contract checks."""
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app import worker
from app.agent.executor import AgentCallResult
from app.models import Analysis, AgentStep, TestRun as NamedTestRun, VLLMProfile
from app.services.input_schemas import InputSchemaError, read_schema_snapshot
from test_prompt_snapshots import fake_output

BASE = "/api/v1/admin/input-schemas"


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def new_version(client, extra=None, modify=None):
    current = client.get(BASE).json()
    fields = copy.deepcopy(current["default_fields"])
    if extra:
        fields.append(extra)
    if modify:
        modify(fields)
    response = client.post(BASE, json={"name": "합성 스키마", "change_note": "오프라인 검증",
        "parent_id": current["active_version_id"], "fields": fields})
    assert response.status_code == 201, response.text
    return response.json()


def activate(client, version, event):
    current = client.get(BASE).json()
    validated = client.post(f"{BASE}/{version['id']}/validate", json={"event": event})
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"], validated.text
    response = client.post(f"{BASE}/{version['id']}/activate", json={
        "expected_revision": current["revision"], "validation_token": validated.json()["validation_token"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_live_contract_and_ingestion_change_together(client, event_payload):
    initial = client.get("/openapi.json").json()
    login(client)
    version = new_version(client, {"name": "risk_rank", "description": "고객 정의 위험 순번",
        "type": "integer", "required": True, "minimum": 1, "maximum": 9})
    valid = {**event_payload, "risk_rank": 3}
    activate(client, version, valid)
    rejected = client.post("/api/v1/analyses", json=event_payload)
    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["field"] == "risk_rank"
    bad = client.post("/api/v1/analyses", json={**valid, "risk_rank": "PRIVATE-SYNTHETIC"})
    assert bad.status_code == 422 and "PRIVATE-SYNTHETIC" not in bad.text
    accepted = client.post("/api/v1/analyses", json=valid)
    assert accepted.status_code == 202
    assert accepted.json()["input_schema_metadata"]["content_hash"] == version["content_hash"]
    docs = client.get("/api/v1/production-api")
    live = client.get("/openapi.json")
    assert docs.headers["cache-control"] == live.headers["cache-control"] == "no-store"
    assert initial["info"]["x-input-schema"]["version_id"] != version["id"]
    assert docs.json()["input_schema"] == live.json()["info"]["x-input-schema"]
    assert version["content_hash"] in docs.json()["markdown"]
    shape = live.json()["components"]["schemas"]["AnalysisInput"]
    assert "risk_rank" in shape["required"]
    assert shape["properties"]["risk_rank"]["maximum"] == 9
    assert "risk_rank" in docs.json()["markdown"]
    assert "<!-- INPUT_SCHEMA_START -->" not in docs.json()["markdown"]
    assert "| 필드 | 필수 | 제한과 의미 |" not in docs.json()["markdown"]


def test_duplicate_and_old_test_keep_pinned_schema_after_change_and_rollback(client, event_payload):
    login(client)
    original = client.post("/api/v1/analyses", json=event_payload).json()
    old_version = client.get(BASE).json()["items"][0]
    test = client.post("/api/v1/test-runs", json={"name": "이전 테스트", "idempotency_key": "schema-test-original",
        "event": event_payload}).json()
    version = new_version(client, {"name": "tenant", "type": "string", "required": True})
    activate(client, version, {**event_payload, "tenant": "example"})
    duplicate = client.post("/api/v1/analyses", json=event_payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == original["id"]
    assert duplicate.json()["input_schema_metadata"] == original["input_schema_metadata"]
    bad = client.post("/api/v1/analyses", json={**event_payload, "event_id": "new-required"})
    assert bad.status_code == 422
    with client.app.state.session_factory() as db:
        run = db.get(NamedTestRun, test["id"])
        old = read_schema_snapshot(client.app.state.crypto, run)
        assert old["version_id"] == original["input_schema_metadata"]["version_id"]
        assert "tenant" not in {field["name"] for field in old["fields"]}
    activate(client, old_version, event_payload)
    assert client.post("/api/v1/analyses", json={**event_payload, "event_id": "after-rollback"}).status_code == 202
    assert client.get("/openapi.json").json()["info"]["x-input-schema"]["version_id"] == old_version["id"]


@pytest.mark.parametrize("route", ["/api/v1/analyses", "/api/v1/uploads", "/api/v1/test-runs/uploads"])
def test_required_optional_field_absence_is_not_silently_defaulted(client, event_payload, route):
    login(client)
    def modify(fields):
        next(field for field in fields if field["name"] == "src_port")["required"] = True
    version = new_version(client, modify=modify)
    activate(client, version, event_payload)
    missing = {key: value for key, value in event_payload.items() if key != "src_port"}
    if route.endswith("uploads"):
        params = {"name": "필수 포트", "idempotency_key": "schema-missing-port"} if "test-runs" in route else {}
        response = client.post(route, data=params, files={"file": ("synthetic.json", json.dumps([missing]), "application/json")})
        assert response.status_code == 202, response.text
        assert response.json()["rejected"] == 1
    else:
        assert client.post(route, json=missing).status_code == 422
    # Required does not imply non-null when nullable remains enabled.
    explicit = {**missing, "src_port": None, "event_id": "explicit-null"}
    assert client.post("/api/v1/analyses", json=explicit).status_code == 202


def test_inference_records_encrypted_field_history_without_sending_definitions(client, event_payload, monkeypatch, registered_vllm_target):
    login(client)
    marker = "FIELD-DEFINITION-ONLY-7e9651"
    version = new_version(client, {"name": "zone", "type": "string", "description": marker})
    event = {**event_payload, "zone": "synthetic-zone"}
    activate(client, version, event)
    created = client.post("/api/v1/analyses", json=event).json()
    later = new_version(client, {"name": "required_after_submission", "type": "boolean", "required": True})
    activate(client, later, {**event_payload, "required_after_submission": True})
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="offline-schema", base_url="http://10.0.0.10:8000/v1", model_name="synthetic-model", status="production"))
        db.commit()
    calls = []
    async def execute(**kwargs):
        calls.append(kwargs)
        assert marker not in kwargs["user_input"] and marker not in kwargs["instructions"]
        assert "field_metadata_usage" not in kwargs["user_input"]
        assert "synthetic-zone" in kwargs["user_input"]
        return AgentCallResult(output=fake_output(), framework_run_id="synthetic", agent_fingerprint="synthetic",
            finish_reason="completed", failure_id=None, error=None, telemetry={"framework_version": "0.6.2"})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = worker.claim_next(db, "schema-worker", 60)
        cipher_before = row.input_schema_snapshot_ciphertext
        assert marker not in cipher_before
        worker.process_moduagent(db, client.app.state.crypto, row, "", 0.75)
        assert row.input_schema_snapshot_ciphertext == cipher_before
        step = db.scalar(select(AgentStep).where(AgentStep.step_type == "input"))
        assert marker in client.app.state.crypto.decrypt_text(step.output_ciphertext)
        assert marker not in json.dumps(step.metadata_json)
        assert step.metadata_json["input_schema"]["version_id"] == version["id"]
    assert len(calls) == 2
    result = client.get(f"/api/v1/analyses/{created['id']}").json()
    assert result["result"]["agent"]["input_schema"]["version_id"] == version["id"]
    assert marker not in json.dumps(result)


@pytest.mark.parametrize("corruption", ["cipher", "missing"])
def test_corrupt_schema_fails_before_any_model_call(client, event_payload, monkeypatch, corruption):
    login(client)
    client.post("/api/v1/analyses", json=event_payload)
    async def forbidden(**kwargs):
        pytest.fail("A corrupt schema must not reach an LLM")
    monkeypatch.setattr(worker, "execute_structured_agent", forbidden)
    with client.app.state.session_factory() as db:
        row = worker.claim_next(db, "schema-worker", 60)
        row.input_schema_snapshot_ciphertext = "corrupt" if corruption == "cipher" else None
        db.commit()
        with pytest.raises(InputSchemaError, match="input_schema_snapshot_invalid"):
            worker.process_moduagent(db, client.app.state.crypto, row, "", 0.75)


def test_legacy_unversioned_queue_uses_original_default_not_new_active(client, event_payload):
    login(client)
    created = client.post("/api/v1/analyses", json=event_payload).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, created["id"])
        row.input_schema_version_id = row.input_schema_snapshot_ciphertext = None
        db.commit()
    assert client.get(f"/api/v1/analyses/{created['id']}").json()["input_schema_metadata"] is None
    version = new_version(client, {"name": "new_required", "type": "boolean", "required": True})
    activate(client, version, {**event_payload, "new_required": True})
    with client.app.state.session_factory() as db:
        row = worker.claim_next(db, "legacy-schema-worker", 60)
        worker.process_stub(db, client.app.state.crypto, row)
        snap = read_schema_snapshot(client.app.state.crypto, row)
        assert snap["version_number"] == 1 and snap["selection_origin"] == "legacy_default"
        assert row.result_json["agent"]["input_schema"]["field_metadata_sent_to_model"] is False


def test_packaged_production_template_is_the_documented_source():
    root = Path(__file__).resolve().parents[2]
    if not (root / "docs/Production_API_v0.1.md").exists():
        # The backend-only Docker context has its own deployable copy.
        return
    assert (root / "docs/Production_API_v0.1.md").read_bytes() == (root / "backend/app/data/production_api.md").read_bytes()


def test_live_document_requires_ingest_credentials(client, service_headers):
    assert client.get("/api/v1/production-api").status_code == 401
    assert client.get("/api/v1/production-api", headers=service_headers).status_code == 200
