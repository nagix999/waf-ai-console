"""Published-revision frozen evaluations; artificial events, no model transport."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.models import Analysis, TestRun as Run, TestEvaluation as Evaluation, VLLMProfile
from app.services.analysis_retries import make_execution_snapshot
from app.services.official_evaluations import finalize_official_evaluation, finalize_for_analysis, recover_pending_evaluations
from app.services.prompt_snapshots import load_analysis_prompt
from test_candidate_configurations import candidate
from test_ground_truth_review import review
from test_test_retries import detail, finish, submit
from test_validation_data import add_item, create_dataset, dataset_run, forbid_model_calls, login, save_reference

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


@pytest.fixture
def settings(settings, tmp_path):
    # Separate connections exercise the same WAL write lock as deployment.
    settings.database_url = f"sqlite+pysqlite:///{tmp_path / 'official.db'}"
    return settings


def approved_item(client, dataset, event, verdict="inconclusive", **metadata):
    # Compatibility name for release-gate fixtures; R3 has no review workflow.
    base = f"/api/v1/validation-datasets/{dataset['id']}"
    draft = client.get(f"{base}/working").json()
    response = client.post(f"{base}/working/items", json={"expected_working_revision": draft["working_revision"],
        "event": event, "reference_verdict": verdict, **metadata})
    assert response.status_code == 201, response.text
    identifier = response.json()["item"]["item_id"]
    result = client.post(f"{base}/publish", json={"expected_working_revision": response.json()["working_revision"], "acknowledge_exclusions": True})
    assert result.status_code == 201, result.text
    dataset["revision"] = result.json()["revision"]
    return next(i for i in client.get(base).json()["items"] if i["item_id"] == identifier)


def execute(client, dataset, candidate, *, mode="ground_truth", key=None, status=202):
    response = client.post(f"/api/v1/validation-datasets/{dataset['id']}/runs", json={
        "name": "official fixture", "expected_revision": dataset["revision"], "idempotency_key": key or str(uuid.uuid4()),
        "candidate_configuration": candidate, "evaluation_mode": mode})
    assert response.status_code == status, response.text
    return response.json()


def finalize(client, run):
    with client.app.state.session_factory() as db:
        record = finalize_official_evaluation(db, run["id"])
        return record.id if record else None


def records(client, run):
    return client.get(f"/api/v1/test-runs/{run['id']}/evaluations").json()["items"]


def test_published_only_frozen_scope_and_no_production_changes(client, event_payload, candidate):
    dataset = create_dataset(client)
    approved = approved_item(client, dataset, {**event_payload, "vendor_score": 2}, difficulty="hard")
    base = f"/api/v1/validation-datasets/{dataset['id']}"
    client.post(f"{base}/working/items", json={"expected_working_revision": 1, "event": {**event_payload, "signature": "draft"}})
    key = str(uuid.uuid4())
    frozen_revision = dataset["revision"]
    run = execute(client, dataset, candidate, key=key)
    assert run["total"] == run["accepted"] == 1
    assert run["evaluation_mode"] == run["reference_basis"] == "ground_truth"
    assert run["ground_truth"]["sample_count"] == 1 and run["ground_truth"]["published"]
    assert run["official_evaluation_pending"]
    assert finalize(client, run) is None and records(client, run) == []
    client.post(f"{base}/working/items", json={"expected_working_revision": 2, "event": {**event_payload, "signature": "another-draft"}})
    replay = execute(client, {**dataset, "revision": frozen_revision}, candidate, key=key)
    assert replay["id"] == run["id"]
    assert execute(client, {**dataset, "revision": frozen_revision}, candidate, key=key, mode="reference", status=409)["detail"] == "test_run_idempotency_conflict"
    current = detail(client, run)
    assert current["ground_truth"] == run["ground_truth"]
    with client.app.state.session_factory() as db:
        saved = db.get(Run, run["id"])
        assert saved.approved_item_version_ids == [approved["id"]]
        event = db.get(Analysis, run["items"][0]["analysis_id"])
        assert not {"review_status", "evaluation_mode", "ground_truth", "expected_verdict"}.intersection(event.extra_fields)


def test_no_published_or_stub_and_schema_invalid_reject_atomically(client, event_payload, candidate):
    dataset = create_dataset(client)
    assert execute(client, dataset, candidate, status=422)["detail"] == "ground_truth_published_revision_required"
    # Published item missing the candidate schema's required vendor_score.
    approved_item(client, dataset, event_payload, "inconclusive")
    assert execute(client, dataset, candidate, status=422)["detail"]["code"] == "dataset_current_schema_invalid"
    client.app.state.settings.agent_mode = "stub"
    assert execute(client, dataset, None, status=409)["detail"] == "official_evaluation_requires_llm"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0


def test_manual_labels_cannot_rewrite_official_scores_and_records(client, event_payload, candidate):
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2}, "true_positive")
    run = execute(client, dataset, candidate)
    identifier = run["items"][0]["analysis_id"]
    finish(client, identifier)
    evaluation = finalize(client, run)
    baseline = detail(client, run, evaluation_id=evaluation)
    assert baseline["evaluation_summary"]["matches"] == 1
    save_reference(client, [identifier], "false_positive")
    assert detail(client, run)["evaluation_summary"]["matches"] == 1
    assert detail(client, run, evaluation_id=evaluation) == baseline
    response = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 409 and response.json()["detail"] == "official_ground_truth_fixed"
    assert finalize(client, run) is None and len(records(client, run)) == 1
    assert records(client, run)[0]["evaluation_kind"] == "ground_truth"


def test_retry_updates_current_and_creates_new_snapshot_without_rewriting_old(client, event_payload, candidate):
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2}, "true_positive")
    run = execute(client, dataset, candidate)
    original = run["items"][0]["analysis_id"]
    finish(client, original, failed=True)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, original)
        snapshot = make_execution_snapshot(row, db.get(VLLMProfile, candidate["primary_profile_id"]),
            load_analysis_prompt(row, client.app.state.crypto), .75, verifier_profile=db.get(VLLMProfile, candidate["verifier_profile_id"]))
        row.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(snapshot.model_dump_json())
        db.commit()
    first = finalize(client, run)
    failed = detail(client, run, evaluation_id=first)
    assert failed["failed"] == 1 and failed["evaluation_summary"]["outcomes"]["failed"] == 1
    response = submit(client, run, [original])
    assert response.status_code == 202, response.text
    assert detail(client, run)["official_evaluation_pending"] and finalize(client, run) is None
    retried = response.json()["analysis_ids"][0]
    finish(client, retried)
    finalize_for_analysis(client.app.state.session_factory, retried)
    current = detail(client, run)
    assert current["completed"] == 1 and current["evaluation_summary"]["matches"] == 1
    assert not current["official_evaluation_pending"]
    assert len(records(client, run)) == 2
    assert detail(client, run, evaluation_id=first) == failed
    newest = detail(client, run, evaluation_id=records(client, run)[0]["id"])
    assert newest["items"][0]["analysis_id"] == retried and newest["items"][0]["retry_count"] == 1


def test_recovery_and_competing_finalizers_are_idempotent(client, event_payload, candidate):
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2})
    run = execute(client, dataset, candidate)
    finish(client, run["items"][0]["analysis_id"], "inconclusive")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: finalize(client, run), range(2)))
    assert len(records(client, run)) == 1
    another = execute(client, dataset, candidate)
    finish(client, another["items"][0]["analysis_id"], "inconclusive")
    recover_pending_evaluations(client.app.state.session_factory)
    assert len(records(client, another)) == 1


def test_comparison_requires_identical_approved_versions_and_pairs_duplicate_event_ids(client, event_payload, candidate):
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2})
    approved_item(client, dataset, {**event_payload, "signature": "different-input", "vendor_score": 2}, "false_positive")
    baseline, current = [execute(client, dataset, candidate) for _ in range(2)]
    for run in (baseline, current):
        for item in run["items"]:
            finish(client, item["analysis_id"], "inconclusive")
    url = f"/api/v1/test-runs/{current['id']}/comparison"
    comparison = client.get(url, params={"baseline_id": baseline["id"]}).json()
    assert comparison["comparable"] and comparison["counts"]["comparable_pairs"] == 2
    assert len({item["pair_id"] for item in comparison["items"]}) == 2
    ordinary = execute(client, dataset, candidate, mode="reference")
    comparison = client.get(url, params={"baseline_id": ordinary["id"]}).json()
    assert not comparison["comparable"] and comparison["items"] == []
    approved_item(client, dataset, {**event_payload, "vendor_score": 2, "signature": "new-published-case"})
    changed = execute(client, dataset, candidate)
    comparison = client.get(url, params={"baseline_id": changed["id"]}).json()
    assert not comparison["comparable"] and comparison["baseline_evaluation"]["metrics"]["accuracy"] is None
    with client.app.state.session_factory() as db:
        db.get(Run, baseline["id"]).metrics_version = "different-metric-definition"
        db.commit()
    assert client.get(url, params={"baseline_id": baseline["id"]}).json()["comparable"] is False


def test_search_is_literal_bounded_and_read_only(client):
    login(client)
    client.post("/api/v1/validation-datasets", json={"name": "fixture_100%"})
    client.post("/api/v1/validation-datasets", json={"name": "fixture-other"})
    result = client.post("/api/v1/validation-datasets/search", json={"query": "_100%", "limit": 1}).json()
    assert result["total"] == 1 and result["items"][0]["name"] == "fixture_100%"
    assert client.post("/api/v1/validation-datasets/search", json={"limit": 500}).status_code == 422
