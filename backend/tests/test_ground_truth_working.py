"""R3 solo lifecycle: synthetic inputs only, no network/model transport."""
import uuid

from sqlalchemy import func, select

from app.models import ValidationDatasetItem, ValidationDatasetVersion, ValidationDatasetWorkingItem, TestRun as Run
from test_validation_data import create_dataset, login, add_item
from test_candidate_configurations import candidate
from test_test_retries import finish
from app.services.official_evaluations import finalize_official_evaluation
import pytest

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def draft(client, dataset):
    response = client.get(f"/api/v1/validation-datasets/{dataset['id']}/working")
    assert response.status_code == 200, response.text
    return response.json()


def save(client, dataset, event, *, verdict="true_positive", item=None, revision=None, **metadata):
    payload = {"expected_working_revision": draft(client, dataset)["working_revision"] if revision is None else revision,
        "event": event, "reference_verdict": verdict, **metadata}
    base = f"/api/v1/validation-datasets/{dataset['id']}/working/items"
    return client.put(f"{base}/{item}", json=payload) if item else client.post(base, json=payload)


def publish(client, dataset, ack=False):
    return client.post(f"/api/v1/validation-datasets/{dataset['id']}/publish", json={
        "expected_working_revision": draft(client, dataset)["working_revision"], "acknowledge_exclusions": ack})


def test_draft_edits_do_not_allocate_immutable_versions(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    saved = save(client, dataset, event_payload)
    assert saved.status_code == 201, saved.text
    identifier = saved.json()["item"]["item_id"]
    assert save(client, dataset, {**event_payload, "signature": "edited"}, item=identifier).status_code == 200
    data = draft(client, dataset)
    assert data["working_revision"] == 2 and data["counts"] == {"ready": 1, "needs_attention": 0, "excluded": 0}
    assert data["changes"]["added"] == 1
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ValidationDatasetItem)) == 0
        assert db.scalar(select(func.count()).select_from(ValidationDatasetVersion)) == 1
        item = db.scalar(select(ValidationDatasetWorkingItem))
        assert event_payload["payload"] not in item.event_ciphertext
    assert save(client, dataset, event_payload, item=identifier, revision=0).status_code == 409


def test_working_import_survives_source_key_deletion(client, event_payload):
    from test_key_analysis_deletion import setup, preview, remove
    key_id, analysis_id, _ = setup(client, event_payload)
    dataset = create_dataset(client)
    result = client.post(f"/api/v1/validation-datasets/{dataset['id']}/working/imports", json={
        "analysis_ids": [analysis_id], "expected_working_revision": 0})
    assert result.status_code == 200, result.text
    first = draft(client, dataset)["items"][0]
    assert first["internal_only"] is True
    assert remove(client, key_id, preview(client, key_id)).status_code == 204
    retained = client.get(f"/api/v1/validation-datasets/{dataset['id']}/working/items/{first['item_id']}").json()
    assert retained["original_analysis_id"] is None and retained["original_analysis_deleted"] is True
    assert retained["event"]["payload"] == event_payload["payload"]
    assert retained["reference_verdict"] == "true_positive" and retained["internal_only"] is True


def test_overview_does_not_guess_a_ground_truth_context(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    save(client, dataset, event_payload)
    publish(client, dataset)
    response = client.get("/api/v1/admin/production-configurations/overview")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ground_truth_working_draft"] is None
    assert data["comparison_key"] is None and data["trend"] == [] and data["recent_comparable_tests"] == []


def test_publish_exclusion_ack_fixed_membership_and_changes(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    first = save(client, dataset, event_payload).json()["item"]["item_id"]
    save(client, dataset, {**event_payload, "signature": "no-answer"}, verdict=None)
    save(client, dataset, {**event_payload, "signature": "excluded"}, excluded=True)
    save(client, dataset, {"payload": "synthetic-invalid-event"})
    before = draft(client, dataset)
    assert before["counts"] == {"ready": 1, "needs_attention": 2, "excluded": 1}
    assert publish(client, dataset).json()["detail"] == "ground_truth_exclusion_ack_required"
    response = publish(client, dataset, True)
    assert response.status_code == 201, response.text
    version = response.json()
    assert version["metadata"]["included_ready_count"] == 1
    assert version["metadata"]["inclusion_rate"] == .25
    assert publish(client, dataset, True).json()["id"] == version["id"]
    save(client, dataset, {**event_payload, "signature": "changed"}, item=first)
    data = draft(client, dataset)
    assert data["changes"]["changed"] == 1 and data["changes"]["added"] == 3
    with client.app.state.session_factory() as db:
        snapshot = db.get(ValidationDatasetVersion, version["id"])
        assert len(snapshot.item_version_ids) == 1
        item = db.get(ValidationDatasetItem, snapshot.item_version_ids[0])
        assert '"signature": "Synthetic Signature"' in client.app.state.crypto.decrypt_text(item.event_ciphertext)
    restore = client.post(f"/api/v1/validation-datasets/{dataset['id']}/working/items/{first}/revert",
        json={"expected_working_revision": data["working_revision"]})
    assert restore.status_code == 200, restore.text
    assert draft(client, dataset)["changes"]["unchanged"] == 1


def test_bulk_remove_revert_discard_and_zero_ready(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    assert publish(client, dataset).status_code == 422
    case = save(client, dataset, event_payload, tags=["regression"]).json()["item"]["item_id"]
    publish(client, dataset)
    base = f"/api/v1/validation-datasets/{dataset['id']}/working"
    response = client.post(f"{base}/bulk", json={"expected_working_revision": 1, "case_ids": [case], "action": "delete"})
    assert response.status_code == 200, response.text
    assert draft(client, dataset)["changes"]["removed"] == 1
    removed = client.get(f"{base}/items/{case}").json()
    assert removed["change"] == "removed" and removed["tags"] == ["regression"]
    assert client.post(f"{base}/discard", json={"expected_working_revision": 1}).status_code == 409
    assert client.post(f"{base}/discard", json={"expected_working_revision": 2}).status_code == 200
    assert draft(client, dataset)["working_changes_count"] == 0
    assert client.post(f"{base}/items/{uuid.uuid4()}/revert", json={"expected_working_revision": 3}).status_code == 404


def test_legacy_records_are_not_auto_published(client, event_payload, candidate):
    dataset = create_dataset(client)
    add_item(client, dataset, {**event_payload, "vendor_score": 2}, "true_positive")
    data = draft(client, dataset)
    assert data["total"] == 1 and data["latest_published_revision_id"] is None
    rejected = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json={
        "expected_revision": dataset["revision"], "evaluation_mode": "ground_truth", "candidate_configuration": candidate,
        "idempotency_key": str(uuid.uuid4())})
    assert rejected.status_code == 422 and rejected.json()["detail"] == "ground_truth_published_revision_required"


def test_official_uses_exact_published_membership_and_scope(client, event_payload, candidate):
    dataset = create_dataset(client)
    for index, verdict in enumerate(("true_positive", "false_positive", "inconclusive")):
        assert save(client, dataset, {**event_payload, "vendor_score": 2, "signature": f"case-{index}"}, verdict=verdict).status_code == 201
    version = publish(client, dataset).json()
    save(client, dataset, {**event_payload, "vendor_score": 2, "signature": "not-published"})
    body = {"expected_revision": version["revision"], "dataset_revision_id": version["id"],
        "evaluation_mode": "ground_truth", "candidate_configuration": candidate, "idempotency_key": str(uuid.uuid4())}
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json=body)
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["accepted"] == 3 and run["ground_truth"]["published"] is True
    assert run["ground_truth"]["sample_count"] == 3
    assert run["evaluation_summary"]["comparison_key"] == run["ground_truth"]["comparison_key"]
    for case in run["items"]:
        finish(client, case["analysis_id"])
    with client.app.state.session_factory() as db:
        record = finalize_official_evaluation(db, run["id"])
        assert record.summary_json["ground_truth"] == run["ground_truth"]
        assert len(db.get(Run, run["id"]).approved_item_version_ids) == 3
    preflight = client.get(f"/api/v1/admin/production-configurations/preflight/{run['id']}").json()
    assert next(c for c in preflight["checks"] if c["code"] == "published_membership_valid")["passed"]


def test_working_requires_admin_and_does_not_put_search_in_url(client):
    assert client.get(f"/api/v1/validation-datasets/{uuid.uuid4()}/working").status_code == 401
    login(client)
    dataset = create_dataset(client)
    result = client.post(f"/api/v1/validation-datasets/{dataset['id']}/working/search", json={"query": "not-a-case"})
    assert result.status_code == 200 and result.json()["filtered_total"] == 0


def test_working_draft_enforces_storage_limits_and_keeps_answers_outside_input(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    client.app.state.settings.payload_max_bytes = 100
    response = save(client, dataset, {**event_payload, "payload": "x" * 101})
    assert response.status_code == 413
    assert draft(client, dataset)["working_revision"] == 0
    response = save(client, dataset, {**event_payload, "payload": "GET / HTTP/1.1", "expected_verdict": "true_positive"})
    assert response.status_code == 201
    assert draft(client, dataset)["counts"]["needs_attention"] == 1
    assert publish(client, dataset).status_code == 422


def test_conflicting_import_needs_attention_until_explicit_save(client, event_payload):
    from test_key_analysis_deletion import setup
    from test_official_evaluations import save_reference
    _, analysis_id, _ = setup(client, event_payload)
    dataset = create_dataset(client)
    path = f"/api/v1/validation-datasets/{dataset['id']}/working/imports"
    def import_again():
        return client.post(path, json={"analysis_ids": [analysis_id],
            "expected_working_revision": draft(client, dataset)["working_revision"]})
    assert import_again().json()["added"] == 1
    case = draft(client, dataset)["items"][0]["item_id"]
    save_reference(client, [analysis_id], "false_positive")
    result = import_again()
    assert result.status_code == 200 and result.json()["conflicts"] == [analysis_id]
    assert draft(client, dataset)["counts"]["needs_attention"] == 1
    assert publish(client, dataset).status_code == 422
    # Saving is explicit confirmation; no answer is chosen by the importer.
    assert save(client, dataset, event_payload, item=case, verdict="false_positive").status_code == 200
    assert draft(client, dataset)["counts"]["ready"] == 1


def test_overview_compares_only_exact_published_context(client, event_payload, candidate):
    from test_official_evaluations import approved_item, execute, finalize
    from test_production_promotion import BASE
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2})

    def run_and_promote(configuration):
        run = execute(client, dataset, configuration)
        for item in run["items"]:
            finish(client, item["analysis_id"])
        finalize(client, run)
        current = client.get(BASE).json()
        response = client.post(BASE + "/promote", json={"candidate_test_run_id": run["id"],
            "expected_production_configuration_hash": current["configuration_hash"], "acknowledge_schema_change": True})
        assert response.status_code == 200, response.text
        return run

    first = run_and_promote(candidate)
    second = run_and_promote({**candidate, "verifier_profile_id": candidate["primary_profile_id"]})
    overview = client.get(BASE + "/overview").json()
    assert len(overview["trend"]) == 2
    assert {row["test_run_id"] for row in overview["recent_comparable_tests"]} == {first["id"], second["id"]}
    assert overview["ground_truth_working_draft"]["id"] == dataset["id"]
    # Same model configuration, new published revision: never join the old
    # curve or calculate a delta as though the denominator were unchanged.
    approved_item(client, dataset, {**event_payload, "vendor_score": 2, "signature": "new-scope"})
    third = execute(client, dataset, {**candidate, "verifier_profile_id": candidate["primary_profile_id"]})
    for item in third["items"]:
        finish(client, item["analysis_id"])
    finalize(client, third)
    changed = client.get(BASE + "/overview").json()
    assert changed["comparison_key"] != overview["comparison_key"]
    assert len(changed["trend"]) == 1
    assert [row["test_run_id"] for row in changed["recent_comparable_tests"]] == [third["id"]]
