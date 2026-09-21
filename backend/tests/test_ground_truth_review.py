"""Synthetic, offline review workflow tests; no actual models or production DB."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import AccessAudit, AnalysisLabel, ValidationDatasetItem, ValidationDatasetVersion
from test_validation_data import add_item, create_dataset, dataset_run, forbid_model_calls, key, login, save_reference


def review(client, dataset, item, status, expected=200):
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/items/{item['item_id']}/reviews",
        json={"expected_revision": dataset["revision"], "review_status": status})
    assert response.status_code == expected, response.text
    if expected == 200:
        dataset["revision"] = response.json()["revision"]
        return response.json()["item"]
    return response


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
def test_review_versions_are_immutable_and_preserve_original_ciphertext(client, event_payload, verdict):
    login(client)
    dataset = create_dataset(client)
    draft = add_item(client, dataset, event_payload, verdict, comment="private-fixture")
    assert draft["review_status"] == "draft"
    baseline = dataset_run(client, dataset)
    reviewed = review(client, dataset, draft, "reviewed")
    approved = review(client, dataset, reviewed, "approved")
    assert [draft["revision"], reviewed["revision"], approved["revision"]] == [1, 2, 3]
    assert len({draft["id"], reviewed["id"], approved["id"]}) == 3
    with client.app.state.session_factory() as db:
        copies = [db.get(ValidationDatasetItem, item["id"]) for item in (draft, reviewed, approved)]
        for field in ("event_ciphertext", "schema_snapshot_ciphertext", "comment_ciphertext",
                      "input_hash", "encryption_key_version", "reference_verdict"):
            assert len({getattr(item, field) for item in copies}) == 1
        assert [item.review_status for item in copies] == ["draft", "reviewed", "approved"]
        assert "private-fixture" not in copies[0].comment_ciphertext
        actions = list(db.scalars(select(AccessAudit.action).where(AccessAudit.resource_id == approved["id"])))
        assert actions == ["review_dataset_item_approved"]
    original = client.get(f"/api/v1/test-runs/{baseline['id']}").json()
    assert original["dataset_version_id"] == baseline["dataset_version_id"]
    assert original["items"][0]["evaluation"]["reference_label"]["verdict"] == verdict
    previous = client.get(f"/api/v1/validation-datasets/{dataset['id']}?revision=2").json()
    assert previous["items"][0]["review_status"] == "draft"
    assert previous["review_counts"] == {"draft": 1, "reviewed": 0, "approved": 0}
    history = client.get(f"/api/v1/validation-datasets/{dataset['id']}/items/{approved['item_id']}").json()["history"]
    assert [item["review_status"] for item in history] == ["approved", "reviewed", "draft"]
    assert all(item["created_by"] == "admin" for item in history)


def test_review_requires_verdict_and_separate_review_step(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    draft = add_item(client, dataset, event_payload)
    assert review(client, dataset, draft, "reviewed", 422).json()["detail"] == "dataset_review_verdict_required"
    assert review(client, dataset, draft, "approved", 409).json()["detail"] == "dataset_review_transition_invalid"
    assert review(client, dataset, draft, "invented", 422).status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ValidationDatasetItem)) == 1
        assert db.scalar(select(func.count()).select_from(ValidationDatasetVersion)) == 2


def test_stale_approval_is_rejected_and_noop_does_not_add_revision(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    draft = add_item(client, dataset, event_payload, "false_positive")
    assert review(client, dataset, draft, "draft")["id"] == draft["id"]
    stale = dict(dataset)
    reviewed = review(client, dataset, draft, "reviewed")
    assert review(client, stale, reviewed, "approved", 409).json()["detail"] == "dataset_changed_reload"
    assert client.get(f"/api/v1/validation-datasets/{dataset['id']}").json()["items"][0]["review_status"] == "reviewed"


def test_approved_edit_creates_draft_and_old_approval_survives(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    draft = add_item(client, dataset, event_payload, "false_positive")
    reviewed = review(client, dataset, draft, "reviewed")
    approved = review(client, dataset, reviewed, "approved")
    response = client.put(f"/api/v1/validation-datasets/{dataset['id']}/items/{draft['item_id']}", json={
        "expected_revision": dataset["revision"], "event": event_payload, "reference_verdict": "inconclusive"})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["review_status"] == "draft"
    assert response.json()["item"]["revision"] == 4
    old = client.get(f"/api/v1/validation-datasets/{dataset['id']}/items/{draft['item_id']}?version_id={approved['id']}").json()
    assert old["review_status"] == "approved" and old["reference_verdict"] == "false_positive"


def test_return_to_draft_and_review_filter_have_exact_counts(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    items = [add_item(client, dataset, {**event_payload, "signature": f"fixture-{i}"}, "inconclusive") for i in range(3)]
    reviewed = review(client, dataset, items[1], "reviewed")
    approved = review(client, dataset, items[2], "reviewed")
    approved = review(client, dataset, approved, "approved")
    filtered = client.get(f"/api/v1/validation-datasets/{dataset['id']}?review_status=approved&limit=1").json()
    assert filtered["total"] == 3 and filtered["filtered_total"] == 1
    assert filtered["review_counts"] == {"draft": 1, "reviewed": 1, "approved": 1}
    assert filtered["items"][0]["id"] == approved["id"]
    assert client.get(f"/api/v1/validation-datasets/{dataset['id']}?review_status=approved&offset=1").json()["items"] == []
    assert client.get(f"/api/v1/validation-datasets/{dataset['id']}?review_status=arbitrary").status_code == 422
    assert review(client, dataset, reviewed, "draft")["review_status"] == "draft"
    assert review(client, dataset, approved, "reviewed", 409).status_code == 409
    assert review(client, dataset, approved, "draft")["review_status"] == "draft"


def test_import_keeps_reference_provenance_across_review_and_edit(client, event_payload):
    production = client.post("/api/v1/analyses", json=event_payload, headers=key(client, "production")).json()
    login(client)
    save_reference(client, [production["id"]], "inconclusive")
    with client.app.state.session_factory() as db:
        label = db.scalar(select(AnalysisLabel).where(AnalysisLabel.analysis_id == production["id"]))
        label.ai_visible = False
        db.commit()
        provenance = {"source_kind": label.source_kind, "source_ref": label.source_ref,
                      "source_label_id": label.id, "source_created_by": label.created_by, "ai_visible": False}
    dataset = create_dataset(client)
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/imports", json={
        "expected_revision": dataset["revision"], "analysis_ids": [production["id"]]})
    dataset["revision"] = response.json()["revision"]
    draft = client.get(f"/api/v1/validation-datasets/{dataset['id']}").json()["items"][0]
    assert draft["review_status"] == "draft"
    reviewed = review(client, dataset, draft, "reviewed")
    approved = review(client, dataset, reviewed, "approved")
    duplicate = client.post(f"/api/v1/validation-datasets/{dataset['id']}/imports", json={
        "expected_revision": dataset["revision"], "analysis_ids": [production["id"]]}).json()
    assert duplicate["revision"] == dataset["revision"] and duplicate["duplicates"] == 1
    response = client.put(f"/api/v1/validation-datasets/{dataset['id']}/items/{draft['item_id']}", json={
        "expected_revision": dataset["revision"], "event": event_payload, "reference_verdict": "false_positive"})
    edited = response.json()["item"]
    for item in (draft, reviewed, approved, edited):
        assert all(item[field] == value for field, value in provenance.items())
        assert item["original_analysis_id"] == production["id"] and item["internal_only"] is True
    assert edited["review_status"] == "draft"


def test_review_is_admin_only_and_write_cannot_smuggle_status(client, event_payload):
    login(client)
    dataset = create_dataset(client)
    draft = add_item(client, dataset, event_payload, "true_positive")
    assert client.post(f"/api/v1/validation-datasets/{dataset['id']}/items", json={
        "expected_revision": dataset["revision"], "event": event_payload, "review_status": "approved"}).status_code == 422
    test_headers = key(client)
    client.cookies.clear()
    path = f"/api/v1/validation-datasets/{dataset['id']}/items/{draft['item_id']}/reviews"
    payload = {"expected_revision": dataset["revision"], "review_status": "reviewed"}
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, json=payload, headers=test_headers).status_code == 403


def test_review_transaction_rolls_back_on_audit_failure(client, event_payload, monkeypatch):
    login(client)
    dataset = create_dataset(client)
    draft = add_item(client, dataset, event_payload, "true_positive")
    from app.services.validation_datasets import review_item
    from app.validation_data_schemas import DatasetItemReview

    def fail(*args):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("app.services.validation_datasets.audit", fail)
    with client.app.state.session_factory() as db:
        with pytest.raises(RuntimeError, match="synthetic audit"):
            review_item(db, dataset["id"], draft["item_id"],
                DatasetItemReview(expected_revision=dataset["revision"], review_status="reviewed"), "fixture")
        db.rollback()
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ValidationDatasetItem)) == 1
        assert db.scalar(select(func.count()).select_from(ValidationDatasetVersion)) == 2


@pytest.mark.parametrize("status,verdict", [("bad", "true_positive"), ("reviewed", None), ("approved", None)])
def test_database_enforces_review_constraints(client, event_payload, status, verdict):
    login(client)
    dataset = create_dataset(client)
    item = add_item(client, dataset, event_payload)
    with client.app.state.session_factory() as db:
        row = db.get(ValidationDatasetItem, item["id"])
        row.review_status, row.reference_verdict = status, verdict
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
