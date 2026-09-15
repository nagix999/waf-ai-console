"""Artificial records only; never connect to live data or a model."""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api_key_schemas import ServiceApiKeyCreate
from app.models import AccessAudit, Analysis, AnalysisLabel, AgentRun, AgentStep, Review, ServiceApiKey, ValidationDatasetItem
from app.services.service_api_keys import ServiceApiKeyError
from app.schemas import AnalysisInput
from app.services.analysis import AnalysisIngestError, enqueue_analysis
from app.services.service_api_keys import issue_key
from test_validation_data import login, create_dataset, forbid_model_calls


def setup(client, event_payload, purpose="production", status="completed"):
    with client.app.state.session_factory() as db:
        key, raw = issue_key(db, ServiceApiKeyCreate(name="삭제 확인용 키", source_system="fixture-source", scopes=["ingest"], purpose=purpose), "fixture")
        db.commit(); key_id = key.id
    result = client.post("/api/v1/analyses", headers={"x-api-key": raw}, json={**event_payload, "expected_verdict": "true_positive"})
    assert result.status_code == 202
    identifier = result.json()["id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier); row.status = status
        run = AgentRun(analysis_id=identifier, status="completed")
        db.add(run); db.flush()
        db.add(AgentStep(run_id=run.id, sequence=1, step_type="fixture", name="fixture", status="completed", input_ciphertext=client.app.state.crypto.encrypt_text("fixture input"), encryption_key_version="v1"))
        db.add(Review(analysis_id=identifier, source_system="fixture-source", external_review_id=str(uuid4()), event_id=event_payload["event_id"], decision="true_positive"))
        db.commit()
    login(client)
    return key_id, identifier, raw


def preview(client, identifier):
    response = client.get(f"/api/v1/admin/service-api-keys/{identifier}/deletion-preview")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def remove(client, identifier, info=None, **changes):
    body = {"confirm_name": "삭제 확인용 키", "delete_analyses": bool(info), **({"expected_scope": info["scope"]} if info else {}), **changes}
    return client.request("DELETE", f"/api/v1/admin/service-api-keys/{identifier}", json=body)


@pytest.mark.parametrize("name", [None, "", "틀린 이름", "삭제 확인용 키 "])
def test_production_requires_exact_name_and_does_not_mutate(client, event_payload, name):
    key_id, analysis_id, _ = setup(client, event_payload)
    body = {} if name is None else {"confirm_name": name}
    response = client.request("DELETE", f"/api/v1/admin/service-api-keys/{key_id}", json=body)
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, analysis_id) and db.get(ServiceApiKey, key_id).deleted_at is None


def test_preserve_is_default_and_deleted_key_cannot_purge_later(client, event_payload):
    key_id, analysis_id, raw = setup(client, event_payload, status="processing")
    before = preview(client, key_id)
    assert before["analyses"] == before["active_analyses"] == 1
    assert remove(client, key_id).status_code == 204
    assert remove(client, key_id, before).status_code == 404
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, analysis_id) and db.query(AnalysisLabel).count() == 1
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/analyses", headers={"x-api-key": raw}, json=event_payload).status_code == 401


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_active_analysis_blocks_purge_atomically(client, event_payload, status):
    key_id, analysis_id, _ = setup(client, event_payload, status=status)
    response = remove(client, key_id, preview(client, key_id))
    assert response.status_code == 409 and response.json()["detail"] == "service_api_key_analyses_active"
    with client.app.state.session_factory() as db:
        assert db.get(ServiceApiKey, key_id).deleted_at is None and db.get(Analysis, analysis_id)


def test_changed_target_set_or_name_requires_new_confirmation(client, event_payload):
    key_id, identifier, _ = setup(client, event_payload)
    old = preview(client, key_id)
    with client.app.state.session_factory() as db:
        db.get(Analysis, identifier).status = "failed"; db.commit()
    assert remove(client, key_id, old).json()["detail"] == "service_api_key_deletion_changed"
    assert remove(client, key_id, preview(client, key_id)).status_code == 204
    assert client.get(f"/api/v1/analyses/{identifier}").status_code == 404


def test_purge_preserves_other_keys_copies_versions_and_audits(client, event_payload):
    key_id, identifier, _ = setup(client, event_payload)
    # Create a separate analysis under the same source; source alone is never a
    # deletion criterion. Include a retry under the selected key.
    with client.app.state.session_factory() as db:
        second, _ = issue_key(db, ServiceApiKeyCreate(name="다른 키", source_system="fixture-source", scopes=["ingest"]), "fixture")
        db.flush()
        other, _ = enqueue_analysis(db, client.app.state.crypto, "fixture-source", AnalysisInput.model_validate({**event_payload, "event_id": "other"}), service_api_key_id=second.id)
        other_id = other.id
        parent = db.get(Analysis, identifier)
        child = Analysis(source_system="fixture-source", event_id=parent.event_id, company_name="fixture", src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="fixture", waf_action="D", payload_ciphertext=parent.payload_ciphertext, encryption_key_version="v1", retry_of_analysis_id=identifier, service_api_key_id=key_id, analysis_purpose="production", status="failed")
        db.add(child)
        db.add(AccessAudit(actor_kind="fixture", actor_id="fixture", action="previous_audit", resource_type="analysis", resource_id=identifier))
        db.commit()
    dataset = create_dataset(client)
    imported = client.post(f"/api/v1/validation-datasets/{dataset['id']}/imports", json={"expected_revision": dataset["revision"], "analysis_ids": [identifier]})
    assert imported.status_code == 200, imported.text
    with client.app.state.session_factory() as db:
        item = db.scalar(select(ValidationDatasetItem))
        saved = (item.event_ciphertext, item.schema_snapshot_ciphertext, item.reference_verdict, item.comment_ciphertext, item.internal_only, item.id)
    info = preview(client, key_id)
    assert info["analyses"] == 2 and info["dataset_copies"] == 1
    assert remove(client, key_id, info).status_code == 204
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier) is None and db.get(Analysis, other_id)
        assert db.query(AnalysisLabel).count() == 0
        assert db.query(AgentRun).count() == db.query(AgentStep).count() == db.query(Review).count() == 0
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "previous_audit"))
        item = db.get(ValidationDatasetItem, saved[-1])
        assert saved == (item.event_ciphertext, item.schema_snapshot_ciphertext, item.reference_verdict, item.comment_ciphertext, item.internal_only, item.id)
        assert item.original_analysis_id is None and item.original_analysis_deleted is True
    assert client.get(f"/api/v1/validation-datasets/{dataset['id']}").json()["items"][0]["original_analysis_deleted"] is True


def test_test_key_never_purges_and_service_principal_cannot_preview(client, event_payload):
    key_id, identifier, raw = setup(client, event_payload, purpose="test")
    client.post("/api/v1/auth/logout")
    path = f"/api/v1/admin/service-api-keys/{key_id}/deletion-preview"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"x-api-key": raw}).status_code == 403
    login(client)
    assert remove(client, key_id, preview(client, key_id)).status_code == 422
    assert client.delete(f"/api/v1/admin/service-api-keys/{key_id}").status_code == 204
    assert client.get(f"/api/v1/analyses/{identifier}").status_code == 200


def test_admission_rechecks_deleted_key_after_prior_authentication(client, event_payload):
    key_id, _, _ = setup(client, event_payload)
    assert remove(client, key_id).status_code == 204
    with client.app.state.session_factory() as db:
        with pytest.raises(AnalysisIngestError, match="service_api_key_unavailable"):
            enqueue_analysis(db, client.app.state.crypto, "fixture-source", AnalysisInput.model_validate(event_payload), service_api_key_id=key_id)


def test_failure_after_purge_rolls_back_copies_audits_and_key(client, event_payload, monkeypatch):
    key_id, identifier, _ = setup(client, event_payload)
    dataset = create_dataset(client)
    assert client.post(f"/api/v1/validation-datasets/{dataset['id']}/imports", json={"expected_revision": dataset["revision"], "analysis_ids": [identifier]}).status_code == 200
    def fail(*args):
        raise ServiceApiKeyError("service_api_key_storage_unavailable", 503)
    monkeypatch.setattr("app.api.service_api_keys.delete_key", fail)
    assert remove(client, key_id, preview(client, key_id)).status_code == 503
    with client.app.state.session_factory() as db:
        assert db.get(ServiceApiKey, key_id).deleted_at is None
        assert db.get(Analysis, identifier) and db.query(AgentStep).count() == 1
        item = db.scalar(select(ValidationDatasetItem))
        assert item.original_analysis_id == identifier and not item.original_analysis_deleted
        assert not db.scalar(select(AccessAudit.id).where(AccessAudit.action.in_(["delete_analysis_with_service_key", "detach_deleted_analysis_source"])))


def test_other_keys_retry_blocks_deletion_without_scope_expansion(client, event_payload):
    key_id, identifier, _ = setup(client, event_payload)
    with client.app.state.session_factory() as db:
        parent = db.get(Analysis, identifier)
        child = Analysis(source_system="fixture-other", event_id="fixture-child", company_name="fixture", src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="fixture", waf_action="D", payload_ciphertext=parent.payload_ciphertext, encryption_key_version="v1", retry_of_analysis_id=identifier, analysis_purpose="production", status="failed")
        db.add(child); db.commit()
    info = preview(client, key_id)
    assert info["blocked_references"] and info["analyses"] == 1
    assert remove(client, key_id, info).json()["detail"] == "service_api_key_analyses_referenced"
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == 2 and db.get(ServiceApiKey, key_id).deleted_at is None
