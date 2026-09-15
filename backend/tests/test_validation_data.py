"""Only artificial inputs and isolated databases; no model or external calls."""
import json
import uuid
import pytest
from sqlalchemy import func, select
from app.models import Analysis, AnalysisLabel, TestRun as Run, TestRunItem as RunItem, ValidationDatasetItem, ValidationDatasetVersion, VLLMProfile
from app.api_key_schemas import ServiceApiKeyCreate
from app.services.service_api_keys import issue_key
from app.services.agent_configuration import role_request_check
from app.services.validation_datasets import require_internal_profiles
from app.services.vllm_profiles import TargetNotAllowedError
from app.services.analysis import AnalysisIngestError


@pytest.fixture(autouse=True)
def forbid_model_calls(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Validation data tests must not invoke a model")
    monkeypatch.setattr("app.worker.execute_structured_agent", forbidden)
    monkeypatch.setattr("app.services.model_validation_worker.run_vllm_test", forbidden)


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def key(client, purpose="test", source="fixture-source"):
    with client.app.state.session_factory() as db:
        _, raw = issue_key(db, ServiceApiKeyCreate(name=str(uuid.uuid4()), source_system=source,
                            scopes=["ingest", "review"], purpose=purpose), "fixture")
        db.commit()
    return {"x-api-key": raw}


def create_dataset(client):
    response = client.post("/api/v1/validation-datasets", json={"name": "검증 fixture"})
    assert response.status_code == 201, response.text
    return response.json()


def add_item(client, dataset, event, answer=None, **values):
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/items", json={
        "expected_revision": dataset["revision"], "event": event, "reference_verdict": answer, **values})
    assert response.status_code == 201, response.text
    dataset["revision"] = response.json()["revision"]
    return response.json()["item"]


def dataset_run(client, dataset, key_value=None):
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json={
        "expected_revision": dataset["revision"], "idempotency_key": key_value or str(uuid.uuid4())})
    assert response.status_code == 202, response.text
    return response.json()


def save_reference(client, ids, verdict="inconclusive", comment="fixture-private-reference"):
    selection = client.post("/api/v1/evaluation-labels/selection", json={"analysis_ids": ids}).json()["items"]
    payload = {"targets": [{"analysis_id": item["analysis_id"], "expected_revision": item["expected_revision"]} for item in selection],
        "verdict": verdict, "comment": comment, "idempotency_key": str(uuid.uuid4())}
    response = client.post("/api/v1/evaluation-labels/bulk", json=payload)
    assert response.status_code == 200, response.text
    return payload


def test_dataset_versions_dedup_edit_delete_and_original_run(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    item = add_item(client, dataset, event_payload, "inconclusive", comment="private-fixture")
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/items", json={
        "expected_revision": dataset["revision"], "event": {**event_payload, "event_id": "different-id"}})
    assert response.status_code == 409 and response.json()["detail"] == "dataset_duplicate_input"
    # Same event ID with another actual input is a separate case.
    add_item(client, dataset, {**event_payload, "signature": "different-fixture"})
    run = dataset_run(client, dataset)
    assert run["accepted"] == 2 and run["evaluation_summary"]["labeled"] == 1
    assert run["dataset_version_id"] and run["items"][0]["event_id"] == event_payload["event_id"]
    assert run["items"][1]["evaluation"]["outcome"] == "unlabeled"
    first = client.get(f"/api/v1/validation-datasets/{dataset['id']}/items/{item['item_id']}").json()
    assert first["event"] == event_payload and first["comment"] == "private-fixture"
    assert first["field_metadata"]["field_metadata_usage"] == "history_only"
    response = client.put(f"/api/v1/validation-datasets/{dataset['id']}/items/{item['item_id']}", json={
        "expected_revision": dataset["revision"], "event": {**event_payload, "payload": "GET /changed HTTP/1.1\r\n\r\n"},
        "reference_verdict": "false_positive", "comment": "new-comment"})
    assert response.status_code == 200
    revision = response.json()["revision"]
    old = client.get(f"/api/v1/validation-datasets/{dataset['id']}/items/{item['item_id']}?version_id={item['id']}").json()
    assert old["event"] == event_payload and old["comment"] == "private-fixture"
    assert len(old["history"]) == 2
    deletion = client.request("DELETE", f"/api/v1/validation-datasets/{dataset['id']}", json={"expected_revision": revision})
    assert deletion.status_code == 200
    assert client.get("/api/v1/validation-datasets").json()["total"] == 0
    assert client.get(f"/api/v1/test-runs/{run['id']}").json()["accepted"] == 2
    with client.app.state.session_factory() as db:
        copies = list(db.scalars(select(ValidationDatasetItem)))
        assert len(copies) == 3
        for copy in copies:
            assert "fixture" not in copy.event_ciphertext and "private-fixture" not in (copy.comment_ciphertext or "")


def test_manual_reference_append_only_and_fixed_regrade(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    add_item(client, dataset, event_payload, "false_positive")
    run = dataset_run(client, dataset)
    identifier = run["items"][0]["analysis_id"]
    payload = save_reference(client, [identifier])
    replay = client.post("/api/v1/evaluation-labels/bulk", json=payload)
    assert replay.json()["duplicate"] is True
    stale = {**payload, "idempotency_key": str(uuid.uuid4())}
    assert client.post("/api/v1/evaluation-labels/bulk", json=stale).status_code == 409
    assert client.post("/api/v1/evaluation-labels/bulk", json={**payload, "comment": "changed"}).status_code == 409
    history = client.get(f"/api/v1/analyses/{identifier}/evaluation-labels").json()["items"]
    assert [row["verdict"] for row in history] == ["inconclusive", "false_positive"]
    assert history[0]["comment"] == "fixture-private-reference"
    initial = client.get(f"/api/v1/test-runs/{run['id']}").json()
    assert initial["items"][0]["evaluation"]["reference_label"]["verdict"] == "false_positive"
    regrade_key = {"idempotency_key": str(uuid.uuid4())}
    assert client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json=regrade_key).status_code == 409
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        assert "fixture-private-reference" not in json.dumps(row.extra_fields)
        assert row.result_json is None
        row.status = "failed"
        db.commit()
    response = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json=regrade_key)
    assert response.status_code == 201
    evaluation = response.json()
    assert client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json=regrade_key).json()["id"] == evaluation["id"]
    rescored = client.get(f"/api/v1/test-runs/{run['id']}?evaluation_id={evaluation['id']}").json()
    assert rescored["items"][0]["evaluation"]["reference_label"]["verdict"] == "inconclusive"
    save_reference(client, [identifier], "true_positive")
    frozen = client.get(f"/api/v1/test-runs/{run['id']}?evaluation_id={evaluation['id']}").json()
    assert frozen["items"][0]["evaluation"]["reference_label"]["verdict"] == "inconclusive"
    assert client.get(f"/api/v1/test-runs/{run['id']}?evaluation_id={uuid.uuid4()}").status_code == 404


def test_bulk_stale_target_is_all_or_nothing(client, event_payload):
    login(client)
    ids = [client.post("/api/v1/analyses", json={**event_payload, "event_id": str(uuid.uuid4())}).json()["id"] for _ in range(2)]
    save_reference(client, [ids[0]])
    response = client.post("/api/v1/evaluation-labels/bulk", json={"targets": [
        {"analysis_id": identifier, "expected_revision": 0} for identifier in ids],
        "verdict": "true_positive", "comment": "do-not-save", "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 409
    assert client.get(f"/api/v1/analyses/{ids[1]}/evaluation-labels").json()["items"] == []


def test_copy_production_sticky_restriction_and_conflicting_answers(client, event_payload):
    headers = key(client, "production")
    production = client.post("/api/v1/analyses", json=event_payload, headers=headers).json()
    login(client)
    save_reference(client, [production["id"]], "inconclusive")
    dataset = create_dataset(client)
    payload = {"expected_revision": dataset["revision"], "analysis_ids": [production["id"]]}
    result = client.post(f"/api/v1/validation-datasets/{dataset['id']}/imports", json=payload).json()
    assert result["added"] == 1
    dataset["revision"] = result["revision"]
    detail = client.get(f"/api/v1/validation-datasets/{dataset['id']}").json()
    item = detail["items"][0]
    assert item["internal_only"] and detail["internal_only"]
    payload["expected_revision"] = dataset["revision"]
    assert client.post(f"/api/v1/validation-datasets/{dataset['id']}/imports", json=payload).json()["duplicates"] == 1
    save_reference(client, [production["id"]], "true_positive")
    assert client.post(f"/api/v1/validation-datasets/{dataset['id']}/imports", json=payload).json()["conflicts"] == [production["id"]]
    response = client.put(f"/api/v1/validation-datasets/{dataset['id']}/items/{item['item_id']}", json={
        "expected_revision": dataset["revision"], "event": {**event_payload, "src_ip": "192.0.2.42"}})
    assert response.json()["item"]["internal_only"] is True
    dataset["revision"] = response.json()["revision"]
    run = dataset_run(client, dataset)
    with client.app.state.session_factory() as db:
        copy = db.get(Analysis, run["items"][0]["analysis_id"])
        assert copy.internal_only and copy.analysis_purpose == "test"
        assert db.get(Analysis, production["id"]).src_ip == event_payload["src_ip"]


@pytest.mark.parametrize("role", ["primary", "verifier", "editor"])
def test_internal_dataset_blocks_each_external_role_before_enqueue(client, role):
    with client.app.state.session_factory() as db:
        internal = VLLMProfile(name="internal-fixture", provider="vllm", base_url="http://10.0.0.10:8000/v1", model_name="fixture")
        external = VLLMProfile(name="external-fixture", provider="openai", base_url="https://api.openai.com/v1", model_name="fixture")
        db.add_all([internal, external]); db.flush()
        run = Run(execution_mode="moduagent", profile_id=external.id if role == "primary" else internal.id,
            profile_metadata={"verifier_profile": {"model_profile_id": external.id if role == "verifier" else internal.id}})
        if role == "editor":
            run.evidence_editor_snapshot_ciphertext = client.app.state.crypto.encrypt_text(json.dumps({"profile_id": external.id}))
        with pytest.raises(AnalysisIngestError, match="dataset_internal_models_required"):
            require_internal_profiles(db, client.app.state.crypto, run)


def test_worker_request_guard_blocks_external_even_without_prior_check(client):
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(name="external-fixture", provider="openai", base_url="https://api.openai.com/v1", model_name="fixture")
        db.add(profile); db.commit()
        check = role_request_check(db.get_bind(), profile, internal_only=True, require_verified=False)
    with pytest.raises(TargetNotAllowedError, match="dataset_internal_models_required"):
        check()


def test_test_key_isolation_automatic_runs_and_grouped_replay(client, event_payload):
    test_key, production_key, other_key = key(client), key(client, "production"), key(client, source="other-fixture")
    production = client.post("/api/v1/analyses", json=event_payload, headers=production_key).json()
    response = client.post("/api/v1/analyses", json=event_payload, headers=test_key)
    assert response.status_code == 202, response.text
    first = response.json()
    assert first["analysis_purpose"] == "test" and first["test_run_id"]
    assert client.post("/api/v1/analyses", json=event_payload, headers=test_key).json()["id"] == first["id"]
    assert client.post("/api/v1/analyses", json={**event_payload, "signature": "changed"}, headers=test_key).status_code == 409
    for headers, identifier in [(test_key, production["id"]), (production_key, first["id"]), (other_key, first["id"])]:
        assert client.get(f"/api/v1/analyses/{identifier}", headers=headers).status_code == 404
    assert client.get("/api/v1/analyses", headers=test_key).json()["total"] == 1
    assert client.get("/api/v1/analyses", headers=production_key).json()["total"] == 1
    payload = {"name": "fixture-test-session", "idempotency_key": str(uuid.uuid4())}
    run = client.post("/api/v1/test-sessions", json=payload, headers=test_key).json()
    assert client.post("/api/v1/test-sessions", json=payload, headers=test_key).json()["id"] == run["id"]
    assert client.get(f"/api/v1/test-sessions/{run['id']}", headers=other_key).status_code == 404
    assert client.post(f"/api/v1/analyses?test_run_id={run['id']}", json=event_payload, headers=production_key).status_code == 422
    grouped = client.post(f"/api/v1/analyses?test_run_id={run['id']}", json=event_payload, headers=test_key).json()
    assert grouped["id"] != first["id"]
    assert client.post(f"/api/v1/test-sessions/{run['id']}/close", headers=test_key).status_code == 200
    assert client.post(f"/api/v1/analyses?test_run_id={run['id']}", json=event_payload, headers=test_key).json()["id"] == grouped["id"]
    assert client.post(f"/api/v1/analyses?test_run_id={run['id']}", json={**event_payload, "event_id": "later"}, headers=test_key).status_code == 409
    assert client.get(f"/api/v1/test-sessions/{run['id']}", headers=test_key).json()["total"] == 1


def test_test_upload_answers_do_not_enter_model_input(client, event_payload):
    headers = key(client)
    rows = [{**event_payload, "expected_verdict": "inconclusive"}, {**event_payload, "event_id": "second"}]
    response = client.post("/api/v1/uploads", headers=headers, files={"file": ("fixture.json", json.dumps(rows).encode())})
    assert response.status_code == 202, response.text
    data = response.json()
    assert data["accepted"] == 2 and data["label_attached"] == 1 and data["test_run_id"]
    repeated = client.post("/api/v1/uploads", headers=headers, files={"file": ("fixture.json", json.dumps(rows).encode())}).json()
    assert repeated["duplicates"] == 2
    with client.app.state.session_factory() as db:
        for identifier in data["analysis_ids"]:
            analysis = db.get(Analysis, identifier)
            assert "expected_verdict" not in analysis.extra_fields
            assert analysis.service_api_key_id and analysis.analysis_purpose == "test"
        assert db.scalar(select(func.count()).select_from(RunItem)) == 2


@pytest.mark.parametrize("path,method,body", [
    ("/validation-datasets", "GET", None), ("/validation-datasets", "POST", {"name": "fixture"}),
    ("/evaluation-labels/selection", "POST", {"analysis_ids": [str(uuid.uuid4())]}),
])
def test_dataset_and_references_admin_only(client, path, method, body):
    response = client.request(method, "/api/v1" + path, headers=key(client), json=body)
    assert response.status_code == 403


def test_api_key_purpose_is_not_client_overridable(client, event_payload):
    headers = key(client)
    for name in ("analysis_purpose", "purpose", "internal_only", "reference_verdict"):
        assert client.post("/api/v1/analyses", headers=headers, json={**event_payload, name: "production"}).status_code == 422


def test_manual_hold_enters_three_way_matrix_and_test_keys_are_not_production_dashboards(client, event_payload):
    headers = key(client)
    analysis = client.post("/api/v1/analyses", headers=headers, json=event_payload).json()
    login(client)
    save_reference(client, [analysis["id"]], "inconclusive")
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis["id"])
        from app.models import utcnow
        row.status, row.verdict, row.completed_at = "completed", "true_positive", utcnow()
        row.result_json = {"verdict": "true_positive", "agent": {"framework": "moduagent"}}
        identifier = row.service_api_key_id
        db.commit()
    result = client.get("/api/v1/analyses?analysis_purpose=test").json()["evaluation_summary"]
    assert result["confusion_matrix"]["expected_hold_positive"] == 1
    assert result["metrics"]["expected_hold_decided_rate"] == 1
    assert result["metrics"]["accuracy"] is None
    assert client.get(f"/api/v1/dashboard/summary?service_api_key_id={identifier}").status_code == 404


def test_test_api_pins_verified_test_model_and_rejects_production_fallback(client, registered_vllm_target, event_payload):
    from test_model_test_role import create_verified, role
    headers = key(client)
    login(client)
    production = create_verified(client, "production-fixture")
    assert role(client, production, "promote").status_code == 200
    client.app.state.settings.agent_mode = "moduagent"
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/analyses", json=event_payload, headers=headers).status_code == 409
    login(client)
    test_profile = create_verified(client, "test-fixture")
    assert role(client, test_profile, "assign-test").status_code == 200
    client.post("/api/v1/auth/logout")
    response = client.post("/api/v1/analyses", json=event_payload, headers=headers)
    assert response.status_code == 202, response.text
    assert client.get("/api/v1/auth/me", headers=headers).json()["purpose"] == "test"
    with client.app.state.session_factory() as db:
        run = db.get(Run, response.json()["test_run_id"])
        assert run.profile_id == test_profile["id"] != production["id"]
        assert run.profile_metadata["verifier_profile"]["model_profile_id"] == test_profile["id"]


def test_real_copy_with_external_test_model_rejects_whole_run(client, registered_vllm_target, event_payload):
    from test_model_test_role import create_verified, role
    login(client)
    original = client.post("/api/v1/analyses", json=event_payload).json()
    dataset = create_dataset(client)
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/imports", json={
        "analysis_ids": [original["id"]], "expected_revision": dataset["revision"]})
    dataset["revision"] = response.json()["revision"]
    external = create_verified(client, "external-fixture", provider="openai")
    assert role(client, external, "assign-test").status_code == 200
    client.app.state.settings.agent_mode = "moduagent"
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json={
        "expected_revision": dataset["revision"], "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 409 and response.json()["detail"] == "dataset_internal_models_required"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1
