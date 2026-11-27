"""Single-case Test copies on synthetic in-memory data. No model/network calls."""
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select

from app.models import AccessAudit, Analysis, AnalysisLabel, TestRun as NamedRun, TestRunItem as NamedItem
from app.schemas import AnalysisInput
from app.services.analysis import AnalysisIngestError
from app.services.single_test_templates import require_internal_copy_profiles
from test_test_runs import direct, login


def template_url(identifier):
    return f"/api/v1/analyses/{identifier}/single-test-template"


def create_copy(client, source_id, event, **metadata):
    return client.post("/api/v1/test-runs", json={"name": "Synthetic single copy",
        "idempotency_key": str(uuid.uuid4()), "event": event, "source_analysis_id": source_id, **metadata})


def test_template_preserves_all_original_inputs_and_explicit_metadata(client, event_payload):
    login(client)
    event = {**event_payload, "src_port": None, "signature": None, "event_name": None,
        "nested_custom": {"enabled": False, "count": 0, "values": [None, 1]},
        "extra_fields": {"count": 0, "enabled": False, "empty": "", "list": [1, None]}}
    initial = {"initial_verdict": "true_positive", "initial_probability": 0.0, "initial_model_version": "offline-v1"}
    run = direct(client, event, expected_verdict="false_positive", difficulty="hard",
        test_category="Synthetic multipart", case_name="Synthetic case", **initial)
    identifier = run["items"][0]["analysis_id"]
    response = client.get(template_url(identifier))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    value = response.json()
    assert value["event"] == event
    assert value["initial_assessment"] == initial
    assert value["source_analysis_id"] == identifier
    assert value["source_analysis_purpose"] == "test" and value["internal_only"] is False
    assert value["expected_verdict"] == "false_positive"
    assert value["name"] == run["name"] and value["source_test_run_id"] == run["id"]
    assert (value["difficulty"], value["test_category"], value["case_name"]) == ("hard", "Synthetic multipart", "Synthetic case")
    assert not {"verdict", "result", "status", "summary_ko", "configuration_snapshot"}.intersection(value)
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AccessAudit).where(
            AccessAudit.action == "view_single_test_template", AccessAudit.resource_id == identifier)) == 1


def test_final_verdict_is_never_a_reference_answer(client, event_payload):
    login(client)
    run = direct(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.verdict = "completed", "true_positive"
        row.result_json = {"verdict": "true_positive", "summary_ko": "Not a reference answer"}
        db.commit()
    value = client.get(template_url(identifier)).json()
    assert value["expected_verdict"] is None
    assert "Not a reference answer" not in json.dumps(value)


def test_retry_template_uses_original_case_membership_and_reference(client, event_payload):
    from app.services.analysis import enqueue_analysis
    login(client)
    original = direct(client, event_payload, expected_verdict="false_positive", difficulty="medium",
        test_category="Synthetic retry category", case_name="Synthetic original case")
    original_id = original["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        child, _ = enqueue_analysis(db, client.app.state.crypto, "synthetic-retry-template",
            AnalysisInput.model_validate(event_payload), analysis_purpose="test")
        child.retry_of_analysis_id = original_id
        db.commit()
        child_id = child.id
    value = client.get(template_url(child_id)).json()
    assert value["source_analysis_id"] == child_id and value["source_test_run_id"] == original["id"]
    assert value["name"] == original["name"] and value["case_name"] == "Synthetic original case"
    assert value["difficulty"] == "medium" and value["test_category"] == "Synthetic retry category"
    assert value["expected_verdict"] == "false_positive"


def test_template_is_administrator_only_and_errors_are_not_cached(client, event_payload, service_headers):
    source = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload, headers=service_headers).json()["id"]
    for headers, status in (({}, 401), (service_headers, 403)):
        response = client.get(template_url(source), headers=headers)
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
    login(client)
    response = client.get(template_url(str(uuid.uuid4())))
    assert response.status_code == 404 and response.headers["cache-control"] == "no-store"


def test_raw_access_is_audited_before_decryption_failure(client, event_payload):
    login(client)
    run = direct(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        db.get(Analysis, identifier).payload_ciphertext = "invalid-synthetic-ciphertext"
        db.commit()
    response = client.get(template_url(identifier))
    assert response.status_code == 503
    assert response.json() == {"detail": "single_test_source_unavailable"}
    with client.app.state.session_factory() as db:
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "view_single_test_template",
            AccessAudit.resource_id == identifier)) is not None


@pytest.mark.parametrize("production,restricted", [(True, False), (False, True), (False, False)])
def test_copy_is_new_development_run_and_preserves_internal_restriction(client, event_payload, production, restricted):
    login(client)
    if production:
        response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
        assert response.status_code == 202, response.text
        identifier = response.json()["id"]
    else:
        identifier = direct(client, event_payload)["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, identifier)
        original.internal_only = restricted
        db.commit()
        original_status = original.status
    copied = create_copy(client, identifier, {**event_payload, "payload": "GET /edited-synthetic HTTP/1.1\r\n\r\n"},
        expected_verdict="false_positive", initial_verdict="true_positive", initial_probability=0.0)
    assert copied.status_code == 202, copied.text
    detail = copied.json()
    child_id = detail["items"][0]["analysis_id"]
    assert child_id != identifier and detail["test_purpose"] == "development"
    assert detail["evaluation_mode"] == "reference"
    with client.app.state.session_factory() as db:
        child = db.get(Analysis, child_id)
        assert child.analysis_purpose == "test" and child.internal_only == (production or restricted)
        assert child.retry_of_analysis_id is None
        assert child.initial_probability == 0.0
        assert child.event_id == event_payload["event_id"]
        assert "source_analysis_id" not in child.extra_fields
        assert "source_analysis_id" not in client.app.state.crypto.decrypt_text(child.prompt_snapshot_ciphertext)
        assert db.get(Analysis, identifier).status == original_status
        assert db.scalar(select(AnalysisLabel.verdict).where(AnalysisLabel.analysis_id == child_id)) == "false_positive"
        assert db.scalar(select(AccessAudit.id).where(AccessAudit.action == "create_single_test_from_analysis",
            AccessAudit.resource_id == f"{identifier}:{detail['id']}"))


def test_copy_idempotency_binds_source_but_replay_survives_source_deletion(client, event_payload):
    login(client)
    source = direct(client, event_payload)["items"][0]["analysis_id"]
    other = direct(client, event_payload)["items"][0]["analysis_id"]
    request = {"name": "Synthetic copy replay", "idempotency_key": str(uuid.uuid4()),
        "source_analysis_id": source, "event": event_payload}
    response = client.post("/api/v1/test-runs", json=request)
    assert response.status_code == 202, response.text
    assert client.post("/api/v1/test-runs", json=request).json()["id"] == response.json()["id"]
    changed = client.post("/api/v1/test-runs", json={**request, "source_analysis_id": other})
    assert changed.status_code == 409 and changed.json()["detail"] == "test_run_idempotency_conflict"
    with client.app.state.session_factory() as db:
        # Membership may outlive the source, as normal Production deletion does.
        item = db.scalar(select(NamedItem).where(NamedItem.analysis_id == source))
        item.analysis_id = None
        db.delete(db.get(Analysis, source))
        db.commit()
    assert client.post("/api/v1/test-runs", json=request).json()["id"] == response.json()["id"]


@pytest.mark.parametrize("source", ["invalid", "", 512, True, [], {}])
def test_invalid_source_identifiers_are_rejected(client, event_payload, source):
    login(client)
    assert create_copy(client, source, event_payload).status_code == 422


def test_missing_source_rolls_back_the_new_run(client, event_payload):
    login(client)
    response = create_copy(client, str(uuid.uuid4()), event_payload)
    assert response.status_code == 404 and response.json()["detail"] == "analysis_not_found"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(NamedRun)) == 0


@pytest.mark.parametrize("restricted", [True, False])
def test_external_test_profile_is_blocked_only_for_internal_copies(client, event_payload, restricted):
    from test_model_test_role import create_verified, role
    login(client)
    original = direct(client, event_payload)
    source_id = original["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        db.get(Analysis, source_id).internal_only = restricted
        db.commit()
    external = create_verified(client, "synthetic-approved-external-test", provider="openai")
    assigned = role(client, external, "assign-test")
    assert assigned.status_code == 200, assigned.text
    client.app.state.settings.agent_mode = "moduagent"
    response = create_copy(client, source_id, event_payload)
    if restricted:
        assert response.status_code == 409 and response.json()["detail"] == "single_test_internal_models_required"
        with client.app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(NamedRun)) == 1
            assert db.scalar(select(func.count()).select_from(Analysis)) == 1
    else:
        assert response.status_code == 202, response.text
        assert response.json()["accepted"] == 1


def test_copy_metadata_is_forbidden_inside_event(client, event_payload):
    login(client)
    response = create_copy(client, str(uuid.uuid4()), {**event_payload, "source_analysis_id": str(uuid.uuid4())})
    assert response.status_code == 404  # Invalid provenance is checked first.
    with pytest.raises(ValueError):
        AnalysisInput.model_validate({**event_payload, "source_analysis_id": str(uuid.uuid4())})


@pytest.mark.parametrize("external", ["primary", "verifier", "legacy_editor", "display_editor"])
def test_internal_copy_checks_every_llm_role_without_model_calls(monkeypatch, external):
    from app.services import evidence_display_jobs_v4
    identifiers = {role: str(uuid.uuid4()) for role in ("primary", "verifier", "legacy_editor", "display_editor")}
    profiles = {identifier: SimpleNamespace(provider="openai" if role == external else "vllm")
        for role, identifier in identifiers.items()}
    run = SimpleNamespace(execution_mode="moduagent", profile_id=identifiers["primary"],
        profile_metadata={"verifier_profile": {"model_profile_id": identifiers["verifier"]}},
        evidence_editor_snapshot_ciphertext="legacy", display_editor_snapshot_ciphertext="display")
    db = SimpleNamespace(get=lambda _model, identifier: profiles.get(identifier))
    crypto = SimpleNamespace(decrypt_text=lambda value: json.dumps({"profile_id": identifiers["legacy_editor"]}))
    monkeypatch.setattr(evidence_display_jobs_v4, "load_snapshot", lambda *_: SimpleNamespace(profile_id=identifiers["display_editor"]))
    with pytest.raises(AnalysisIngestError, match="single_test_internal_models_required"):
        require_internal_copy_profiles(db, crypto, run)
