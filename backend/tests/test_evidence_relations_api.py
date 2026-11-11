"""Synthetic admin-only relation reads; no model, source, or configuration writes."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest
from sqlalchemy import select

from app.models import AccessAudit, AgentRun, AgentStep, Analysis, utcnow
from app.result_contracts import validate_new_result
from test_result_contract_v3 import result_fixture


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def relation_fixture():
    return {"version": "evidence-relations-v1", "groups": [{"role": "primary",
            "representative_evidence_id": "e1", "related_evidence_ids": ["e2"]}]}


def saved_analysis(client, event_payload, service_headers, *, mapping=True, run_status="completed"):
    created = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event_payload)
    assert created.status_code == 202
    identifier = created.json()["id"]
    result = result_fixture()
    related = deepcopy(result["evidence"][0])
    related.update(evidence_id="e2", supports="context", field="signature", excerpt="Synthetic Signature",
                   interpretation_ko="탐지 이름은 요청 해석에 참고합니다.", source_spans=[],
                   origins=[{"role": "primary", "index": 1, "source_id": "c2"}])
    result["evidence"].append(related)
    result["primary"]["evidence_ids"].append("e2")
    result = validate_new_result(result)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.result_json = "completed", result
        row.verdict, row.severity = result["verdict"], result["threat_analysis"]["severity"]
        run = AgentRun(analysis_id=identifier, status=run_status)
        db.add(run)
        db.flush()
        output = {**result, **({"evidence_relations": relation_fixture()} if mapping else {})}
        db.add(AgentStep(run_id=run.id, sequence=7, step_type="finalize", name="Synthetic finalization", status="completed",
                         output_ciphertext=client.app.state.crypto.encrypt_text(json.dumps(output))))
        db.commit()
        return identifier, run.id, result


def test_relations_are_admin_only_and_do_not_extend_public_v3(client, event_payload, service_headers):
    identifier, _, expected = saved_analysis(client, event_payload, service_headers)
    path = f"/api/v1/analyses/{identifier}/evidence-relations"
    assert client.get(path, headers=service_headers).status_code == 403
    assert client.get("/api/v1/analyses/nonexistent/evidence-relations", headers=service_headers).status_code == 403
    public_result = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers).json()["result"]
    assert public_result["evidence"] == expected["evidence"]
    assert public_result["schema_version"] == "waf-analysis-v3"
    assert "evidence_relations" not in public_result
    login(client)
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == relation_fixture()
    assert response.headers["cache-control"] == "no-store"
    with client.app.state.session_factory() as db:
        audit = db.scalar(select(AccessAudit).where(AccessAudit.action == "view_evidence_relations"))
        assert audit.resource_id == identifier
        assert db.get(Analysis, identifier).result_json == expected
    assert "/api/v1/analyses/{analysis_id}/evidence-relations" not in client.get("/openapi.json").json()["paths"]


def test_missing_analysis_is_404_without_decryption(client):
    login(client)
    assert client.get("/api/v1/analyses/nonexistent/evidence-relations").status_code == 404


@pytest.mark.parametrize("mapping,run_status", [(False, "completed"), (True, "running"), (True, "failed")])
def test_missing_or_nonfinal_history_is_empty_not_inferred(client, event_payload, service_headers, mapping, run_status):
    identifier, _, _ = saved_analysis(client, event_payload, service_headers, mapping=mapping, run_status=run_status)
    login(client)
    response = client.get(f"/api/v1/analyses/{identifier}/evidence-relations")
    assert response.status_code == 200
    assert response.json() == {"version": "evidence-relations-v1", "groups": []}


@pytest.mark.parametrize("damage", ["ciphertext", "json", "shape", "missing_id", "role", "evidence", "version"])
def test_corrupt_relation_history_fails_safely_and_is_audited(client, event_payload, service_headers, damage):
    identifier, run_id, result = saved_analysis(client, event_payload, service_headers)
    secret = "synthetic-private-source-should-not-escape"
    document = {**result, "evidence_relations": relation_fixture()}
    if damage == "shape": document["evidence_relations"] = {"unexpected": secret}
    if damage == "missing_id": document["evidence_relations"]["groups"][0]["related_evidence_ids"] = [secret]
    if damage == "role": document["evidence_relations"]["groups"][0]["role"] = "verifier"
    if damage == "evidence": document["evidence"] = []
    if damage == "version": document["evidence_relations"]["version"] = secret
    output = secret if damage == "json" else json.dumps(document)
    with client.app.state.session_factory() as db:
        step = db.scalar(select(AgentStep).where(AgentStep.run_id == run_id))
        step.output_ciphertext = secret if damage == "ciphertext" else client.app.state.crypto.encrypt_text(output)
        db.commit()
    login(client)
    response = client.get(f"/api/v1/analyses/{identifier}/evidence-relations")
    assert response.status_code == 500
    assert response.json() == {"detail": "evidence_relations_unavailable"}
    assert secret not in response.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "view_evidence_relations"))


def test_only_latest_run_completed_finalize_is_read(client, event_payload, service_headers):
    identifier, run_id, _ = saved_analysis(client, event_payload, service_headers)
    with client.app.state.session_factory() as db:
        original = db.get(AgentRun, run_id)
        stale = AgentRun(analysis_id=identifier, status="completed", created_at=original.created_at - timedelta(seconds=10))
        db.add(stale)
        db.flush()
        for owner, sequence, kind in [(stale.id, 7, "finalize"), (run_id, 8, "llm_evidence_editor")]:
            db.add(AgentStep(run_id=owner, sequence=sequence, step_type=kind, name="Unread synthetic step", status="completed", output_ciphertext="must-not-be-decrypted"))
        db.commit()
    login(client)
    assert client.get(f"/api/v1/analyses/{identifier}/evidence-relations").json() == relation_fixture()
    with client.app.state.session_factory() as db:
        db.add(AgentRun(analysis_id=identifier, status="running", created_at=utcnow() + timedelta(seconds=10)))
        db.commit()
    assert client.get(f"/api/v1/analyses/{identifier}/evidence-relations").json()["groups"] == []


def test_pending_or_legacy_analysis_does_not_read_relation_ciphertext(client, event_payload, service_headers):
    identifier = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event_payload).json()["id"]
    login(client)
    assert client.get(f"/api/v1/analyses/{identifier}/evidence-relations").json()["groups"] == []
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.result_json = "completed", {"schema_version": "waf-analysis-v2"}
        db.commit()
    assert client.get(f"/api/v1/analyses/{identifier}/evidence-relations").json()["groups"] == []
