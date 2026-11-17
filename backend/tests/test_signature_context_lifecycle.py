"""Synthetic-only admission, retry and official promotion for reference knowledge."""
import json
import uuid

import pytest

from app.models import Analysis, TestRun as Run, VLLMProfile
from app.services.analysis_retries import make_execution_snapshot, load_execution_snapshot
from app.services.candidate_configurations import verify_configuration
from app.services.prompt_snapshots import load_analysis_prompt
from app.services.signature_explanations import read_snapshot, metadata
from app.worker_v223 import _inference_event
from test_candidate_configurations import candidate
from test_official_evaluations import approved_item, execute, finalize
from test_test_retries import finish
from test_validation_data import create_dataset

pytestmark = pytest.mark.usefixtures("registered_vllm_target")
CATALOG = "/api/v1/admin/signature-explanations"
PRODUCTION = "/api/v1/admin/production-configurations"
DESCRIPTION = "합성 탐지의 입력 처리 방식과 요청 문맥을 비교하는 참고 설명입니다."


def save_catalog(client, event, description=DESCRIPTION, *, parent=None):
    response = client.post(CATALOG, json={"name": "합성 설명집", "change_reason": "오프라인 기능 검증",
        "parent_id": parent, "entries": [{"waf_vendor": event["waf_vendor"], "signature": event["signature"],
        "signature_explanation": description, "reference_source": "합성 참고 자료"}]})
    assert response.status_code == 201, response.text
    return response.json()


def selected(candidate, catalog):
    return {**candidate, "evidence_editor_enabled": False, "evidence_editor_profile_id": None,
            "signature_explanation_version_id": catalog["id"]}


def direct(client, event, configuration, *, key=None):
    return client.post("/api/v1/test-runs", json={"name": "설명 연결 확인", "event": {**event, "vendor_score": 2},
        "idempotency_key": key or str(uuid.uuid4()), "candidate_configuration": configuration})


def qualified(client, event, configuration):
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event, "vendor_score": 2}, "true_positive")
    run = execute(client, dataset, configuration)
    finish(client, run["items"][0]["analysis_id"])
    assert finalize(client, run)
    return run


def promote(client, run):
    before = client.get(PRODUCTION).json()
    response = client.post(PRODUCTION + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 200, response.text
    return response.json()


def test_candidate_pins_only_matched_entry_at_top_level_and_keeps_event_unchanged(client, candidate, event_payload):
    catalog = save_catalog(client, event_payload)
    response = direct(client, event_payload, selected(candidate, catalog))
    assert response.status_code == 202, response.text
    document = response.json()
    assert document["configuration_snapshot"]["schema_version"] == 4
    assert document["configuration_snapshot"]["signature_explanations"]["version_id"] == catalog["id"]
    with client.app.state.session_factory() as db:
        run = db.get(Run, document["id"])
        row = db.get(Analysis, document["items"][0]["analysis_id"])
        run_snapshot, row_snapshot = read_snapshot(client.app.state.crypto, run), read_snapshot(client.app.state.crypto, row)
        assert run_snapshot["scope"] == "catalog" and row_snapshot["scope"] == "event"
        assert len(row_snapshot["entries"]) == 1
        assert metadata(run_snapshot) == metadata(row_snapshot)
        assert "signature_explanation" not in row.extra_fields
        event, safe = _inference_event(row, client.app.state.crypto)
        assert event["signature_explanation"] == DESCRIPTION
        assert "signature_explanation" not in event["extra_fields"]
        assert DESCRIPTION not in json.dumps(safe)
        assert verify_configuration(run, client.app.state.crypto, row) == document["configuration_snapshot"]
    assert client.get(CATALOG).json()["active_version_id"] is None


@pytest.mark.parametrize("field,value", [("waf_vendor", "another-vendor"), ("signature", "another-signature"), ("signature", None)])
def test_unmatched_items_keep_catalog_binding_without_inventing_an_explanation(client, candidate, event_payload, field, value):
    catalog = save_catalog(client, event_payload)
    response = direct(client, {**event_payload, field: value}, selected(candidate, catalog))
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, response.json()["items"][0]["analysis_id"])
        snapshot = read_snapshot(client.app.state.crypto, row)
        assert snapshot["version_id"] == catalog["id"] and snapshot["entries"] == []
        event, safe = _inference_event(row, client.app.state.crypto)
        assert "signature_explanation" not in event and safe["matched"] is False


@pytest.mark.parametrize("extension", [
    {"signature_explanation": "외부에서 지정한 합성 설명"},
    {"extra_fields": {"signature_explanation": "외부에서 지정한 합성 설명"}},
    {"signature_explanation_version_id": "untrusted-version"},
])
def test_external_requests_cannot_override_server_reference(client, candidate, event_payload, extension):
    response = client.post("/api/v1/analyses?wait_seconds=0", json={**event_payload, **extension})
    assert response.status_code == 422, response.text
    assert "외부에서 지정한 합성 설명" not in response.text


def test_retry_preserves_exact_reference_after_a_new_catalog_is_saved(client, candidate, event_payload):
    catalog = save_catalog(client, event_payload)
    configuration = selected(candidate, catalog)
    response = direct(client, event_payload, configuration)
    assert response.status_code == 202, response.text
    identifier = response.json()["items"][0]["analysis_id"]
    finish(client, identifier, failed=True)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        snapshot = make_execution_snapshot(row, db.get(VLLMProfile, configuration["primary_profile_id"]),
            load_analysis_prompt(row, client.app.state.crypto), .75,
            verifier_profile=db.get(VLLMProfile, configuration["verifier_profile_id"]), crypto=client.app.state.crypto)
        row.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(snapshot.model_dump_json())
        db.commit()
    save_catalog(client, event_payload, "새로 저장한 다른 합성 설명입니다.", parent=catalog["id"])
    response = client.post(f"/api/v1/analyses/{identifier}/retry", json={"idempotency_key": str(uuid.uuid4()), "cost_acknowledged": True})
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        retried = db.get(Analysis, response.json()["analysis_id"])
        original = db.get(Analysis, identifier)
        assert read_snapshot(client.app.state.crypto, retried) == read_snapshot(client.app.state.crypto, original)
        assert _inference_event(retried, client.app.state.crypto)[0]["signature_explanation"] == DESCRIPTION
        assert load_execution_snapshot(retried, client.app.state.crypto).schema_version == 6


def test_official_promotion_is_required_and_duplicate_event_does_not_acquire_new_knowledge(client, candidate, event_payload):
    first_catalog = save_catalog(client, event_payload)
    configuration = selected(candidate, first_catalog)
    trial = direct(client, event_payload, configuration).json()
    review = client.get(f"{PRODUCTION}/preflight/{trial['id']}")
    assert review.status_code == 200 and not review.json()["eligible"]
    assert client.get(CATALOG).json()["active_version_id"] is None
    first_run = qualified(client, event_payload, configuration)
    promote(client, first_run)
    assert client.get(CATALOG).json()["active_version_id"] == first_catalog["id"]
    original_event = {**event_payload, "event_id": "synthetic-production-stable", "vendor_score": 2}
    original = client.post("/api/v1/analyses?wait_seconds=0", json=original_event)
    assert original.status_code == 202, original.text
    second_catalog = save_catalog(client, event_payload, "두 번째 합성 참고 설명입니다.", parent=first_catalog["id"])
    second_run = qualified(client, event_payload, selected(candidate, second_catalog))
    assert first_run["configuration_hash"] != second_run["configuration_hash"]
    promote(client, second_run)
    repeated = client.post("/api/v1/analyses?wait_seconds=0", json=original_event)
    assert repeated.status_code == 202 and repeated.json()["id"] == original.json()["id"]
    fresh = client.post("/api/v1/analyses?wait_seconds=0", json={**original_event, "event_id": "synthetic-production-fresh"})
    assert fresh.status_code == 202, fresh.text
    with client.app.state.session_factory() as db:
        old = db.get(Analysis, original.json()["id"])
        new = db.get(Analysis, fresh.json()["id"])
        assert read_snapshot(client.app.state.crypto, old)["version_id"] == first_catalog["id"]
        assert read_snapshot(client.app.state.crypto, new)["version_id"] == second_catalog["id"]
        assert _inference_event(old, client.app.state.crypto)[0]["signature_explanation"] == DESCRIPTION


def test_missing_reference_snapshot_blocks_configuration_verification(client, candidate, event_payload):
    catalog = save_catalog(client, event_payload)
    response = direct(client, event_payload, selected(candidate, catalog)).json()
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, response["items"][0]["analysis_id"])
        run = db.get(Run, response["id"])
        row.signature_explanation_version_id = None
        row.signature_explanation_snapshot_ciphertext = None
        with pytest.raises(ValueError, match="candidate_configuration_invalid"):
            verify_configuration(run, client.app.state.crypto, row)


def test_service_test_sessions_pin_saved_reference_default_without_changing_models(client, candidate, event_payload):
    from test_event_api import service_key, session, submit
    first = save_catalog(client, event_payload)
    response = client.patch("/api/v1/admin/test-configuration-defaults", json={"expected_revision": 0,
        "candidate_configuration": selected(candidate, first)})
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, candidate["primary_profile_id"]).is_test = True
        db.commit()
    headers = service_key(client, purpose="test")
    client.cookies.clear()
    run_id = session(client, headers)
    from test_validation_data import login
    login(client)
    second = save_catalog(client, event_payload, "변경된 테스트 기본 설명입니다.", parent=first["id"])
    response = client.patch("/api/v1/admin/test-configuration-defaults", json={"expected_revision": 1,
        "candidate_configuration": selected(candidate, second)})
    assert response.status_code == 200, response.text
    client.cookies.clear()
    older = submit(client, event_payload, headers, test_run_id=run_id)
    assert older.status_code == 202, older.text
    newer = submit(client, {**event_payload, "event_id": "new-service-test-session"}, headers)
    assert newer.status_code == 202, newer.text
    with client.app.state.session_factory() as db:
        old, new = (db.get(Analysis, item.json()["id"]) for item in (older, newer))
        assert read_snapshot(client.app.state.crypto, old)["version_id"] == first["id"]
        assert read_snapshot(client.app.state.crypto, new)["version_id"] == second["id"]
        assert _inference_event(old, client.app.state.crypto)[0]["signature_explanation"] == DESCRIPTION


def test_unavailable_production_reference_returns_a_safe_error_without_falling_back(client, candidate, event_payload):
    from app.models import SignatureExplanationVersion
    from app.services.signature_explanations import activate_version
    catalog = save_catalog(client, event_payload)
    # Internal corruption fixture only; the public direct-activation API stays denied.
    with client.app.state.session_factory() as db:
        activate_version(db, client.app.state.crypto, catalog["id"])
        db.get(SignatureExplanationVersion, catalog["id"]).definition_ciphertext = "invalid-synthetic-ciphertext"
        db.commit()
    response = client.get(PRODUCTION)
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "signature_explanation_definition_invalid"}
    assert response.headers["cache-control"] == "no-store"
    assert DESCRIPTION not in response.text
