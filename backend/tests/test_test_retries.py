"""Failed-item batches and current-attempt evaluation; synthetic inputs only."""
import uuid

import pytest
from sqlalchemy import func, select
from app.models import Analysis, AnalysisLabel, TestEvaluation as SavedEvaluation, TestRunItem as RunItem, VLLMProfile, AccessAudit, utcnow
from app.services.analysis_retries import make_execution_snapshot
from app.services.prompt_snapshots import load_analysis_prompt
from test_analysis_retries_keys import login, profile_fixture, retry
from test_test_runs import upload

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def fixture_run(client, event_payload, count=3):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client, is_test=True)
    run = upload(client, [{**event_payload, "event_id": f"fixture-{index}", "expected_verdict": verdict,
                          "difficulty": "hard" if index % 2 else "medium"}
                         for index, verdict in enumerate(["true_positive", "false_positive", "inconclusive"] * count)][:count])
    with client.app.state.session_factory() as db:
        for item in run["items"]:
            row = db.get(Analysis, item["analysis_id"])
            row.status, row.error_code, row.completed_at = "failed", "primary_agent_failed", utcnow()
            snapshot = make_execution_snapshot(row, db.get(VLLMProfile, profile_id), load_analysis_prompt(row, client.app.state.crypto), .75)
            row.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(snapshot.model_dump_json())
        db.commit()
    return run, profile_id


def preview(client, run):
    response = client.get(f"/api/v1/test-runs/{run['id']}/retry-eligibility")
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, run, ids, key=None):
    return client.post(f"/api/v1/test-runs/{run['id']}/retry-failed", json={
        "analysis_ids": ids, "idempotency_key": key or str(uuid.uuid4()), "cost_acknowledged": True})


def finish(client, identifier, verdict="true_positive", *, failed=False):
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status = "failed" if failed else "completed"
        row.completed_at = utcnow()
        if not failed:
            row.verdict = verdict
            row.result_json = {"verdict": verdict, "agent": {"framework": "moduagent"}}
        db.commit()


def detail(client, run, **params):
    response = client.get(f"/api/v1/test-runs/{run['id']}", params={"reference_basis": "latest", **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_batch_recovery_updates_results_metrics_filters_and_detail_without_new_items(client, event_payload):
    run, _profile = fixture_run(client, event_payload)
    ids = [item["analysis_id"] for item in run["items"]]
    info = preview(client, run)
    assert info["failed_count"] == info["eligible_count"] == 3
    response = submit(client, run, info["eligible_ids"])
    assert response.status_code == 202, response.text
    assert response.json()["enqueued"] == 3
    pending = detail(client, run)
    assert pending["pending"] == 3 and pending["failed"] == 0 and pending["total"] == 3
    assert pending["evaluation_summary"]["outcomes"]["pending"] == 3
    latest_ids = [item["analysis_id"] for item in pending["items"]]
    for item, verdict in zip(pending["items"], ["true_positive", "false_positive", "inconclusive"]):
        finish(client, item["analysis_id"], verdict)
    done = detail(client, run)
    assert done["completed"] == done["accepted"] == done["total"] == 3 and done["failed"] == 0
    summary = done["evaluation_summary"]
    assert summary["total"] == summary["evaluable"] == summary["matches"] == 3
    assert summary["metrics"]["accuracy"] == 1 and summary["confusion_matrix"]["expected_hold_match"] == 1
    assert detail(client, run, status="failed")["total_items"] == 0
    assert detail(client, run, difficulty="hard")["completed"] == 1
    for item in done["items"]:
        analysis = client.get(f"/api/v1/analyses/{item['analysis_id']}").json()
        assert analysis["test_run_id"] == run["id"]
        assert analysis["evaluation"]["reference_label"] == item["evaluation"]["reference_label"]
    listing = client.get("/api/v1/analyses", params={"test_run_id": run["id"]}).json()
    assert listing["total"] == 3 and {item["id"] for item in listing["items"]} == set(latest_ids)
    assert client.get("/api/v1/analyses", params={"test_run_id": run["id"], "include_retries": True}).json()["total"] == 6
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(RunItem)) == 3
        assert db.scalar(select(func.count()).select_from(AnalysisLabel)) == 3
        assert all(db.get(Analysis, identifier).status == "failed" for identifier in ids)
        assert all(db.get(Analysis, identifier).retry_of_analysis_id in ids for identifier in latest_ids)


def test_existing_individual_retries_and_retry_chains_are_included(client, event_payload):
    run, _ = fixture_run(client, event_payload, count=1)
    root = run["items"][0]["analysis_id"]
    first = retry(client, root).json()["analysis_id"]
    finish(client, first, failed=True)
    assert preview(client, run)["eligible_ids"] == [first]
    second = submit(client, run, [first]).json()["analysis_ids"][0]
    finish(client, second)
    result = detail(client, run)
    assert result["items"][0]["retry_count"] == 2
    assert result["items"][0]["analysis_id"] == second and result["completed"] == 1
    assert result["evaluation_summary"]["matches"] == 1
    assert result["items"][0]["evaluation"]["reference_label"]["verdict"] == "true_positive"
    assert preview(client, run)["eligible_ids"] == []


def test_batch_replay_after_failure_never_enqueues_grandchildren_and_rejects_changed_targets(client, event_payload):
    run, _ = fixture_run(client, event_payload, count=2)
    ids = preview(client, run)["eligible_ids"]
    key = str(uuid.uuid4())
    first = submit(client, run, ids, key).json()
    for identifier in first["analysis_ids"]:
        finish(client, identifier, failed=True)
    replay = submit(client, run, ids, key).json()
    assert replay["duplicate"] and set(replay["analysis_ids"]) == set(first["analysis_ids"])
    assert submit(client, run, ids[:1], key).status_code == 409
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 4
    newer = submit(client, run, preview(client, run)["eligible_ids"])
    assert newer.status_code == 202 and newer.json()["enqueued"] == 2


def test_saved_evaluations_remain_frozen_while_initial_and_latest_use_recovery(client, event_payload):
    run, _ = fixture_run(client, event_payload, count=1)
    saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    before = detail(client, run, evaluation_id=saved["id"])
    response = submit(client, run, preview(client, run)["eligible_ids"]).json()
    assert client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).status_code == 409
    finish(client, response["analysis_ids"][0])
    frozen = detail(client, run, evaluation_id=saved["id"])
    assert frozen["items"] == before["items"] and frozen["evaluation_summary"] == before["evaluation_summary"]
    assert frozen["failed"] == 1
    assert detail(client, run)["evaluation_summary"]["matches"] == 1
    assert detail(client, run, reference_basis="initial")["evaluation_summary"]["matches"] == 1
    new_saved = client.post(f"/api/v1/test-runs/{run['id']}/evaluations", json={"idempotency_key": str(uuid.uuid4())}).json()
    assert detail(client, run, evaluation_id=new_saved["id"])["evaluation_summary"]["matches"] == 1


def test_retry_reference_can_be_edited_without_changing_initial_or_parent_reference(client, event_payload):
    run, _ = fixture_run(client, event_payload, count=1)
    child = submit(client, run, preview(client, run)["eligible_ids"]).json()["analysis_ids"][0]
    finish(client, child)
    selection = client.post("/api/v1/evaluation-labels/selection", json={"analysis_ids": [child]}).json()
    assert selection["items"][0]["verdict"] == "true_positive" and selection["items"][0]["expected_revision"] == 0
    from test_validation_data import save_reference
    save_reference(client, [child], "false_positive")
    assert detail(client, run)["evaluation_summary"]["confusion_matrix"]["fp"] == 1
    assert detail(client, run, reference_basis="initial")["evaluation_summary"]["matches"] == 1
    root = client.get(f"/api/v1/analyses/{run['items'][0]['analysis_id']}").json()
    assert root["evaluation"]["reference_label"]["verdict"] == "true_positive"


def test_preview_excludes_completed_pending_and_invalid_inputs_and_batch_rechecks(client, event_payload):
    run, profile_id = fixture_run(client, event_payload)
    ids = [item["analysis_id"] for item in run["items"]]
    finish(client, ids[0])
    with client.app.state.session_factory() as db:
        db.get(Analysis, ids[1]).status = "processing"
        db.commit()
    assert preview(client, run)["eligible_ids"] == [ids[2]]
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).status = "disabled"
        db.commit()
    info = preview(client, run)
    assert info["eligible_count"] == 0 and info["blocked_counts"] == {"retry_original_profile_disabled": 1}
    response = submit(client, run, ids).json()
    assert response["enqueued"] == 0 and response["skipped"] == 3
    assert response["blocked_counts"]["retry_requires_failed_analysis"] == 2


def test_batch_targets_are_run_scoped_and_confirmation_admin_required(client, event_payload, service_headers):
    run, _ = fixture_run(client, event_payload, count=1)
    ids = preview(client, run)["eligible_ids"]
    assert submit(client, run, [str(uuid.uuid4())]).status_code == 422
    assert submit(client, run, ids * 2).status_code == 422
    endpoint = f"/api/v1/test-runs/{run['id']}/retry-failed"
    body = {"analysis_ids": ids, "idempotency_key": str(uuid.uuid4())}
    assert client.post(endpoint, json=body).status_code == 422
    client.post("/api/v1/auth/logout")
    body["cost_acknowledged"] = True
    assert client.post(endpoint, json=body).status_code == 401
    assert client.post(endpoint, json=body, headers=service_headers).status_code == 403
    assert client.get(f"/api/v1/test-runs/{run['id']}/retry-eligibility", headers=service_headers).status_code == 403


def test_overlapping_requests_skip_existing_child_without_duplicate_work(client, event_payload):
    run, _ = fixture_run(client, event_payload, count=2)
    ids = preview(client, run)["eligible_ids"]
    retry(client, ids[0])
    response = submit(client, run, ids).json()
    assert response["enqueued"] == response["skipped"] == 1
    assert response["blocked_counts"] == {"retry_already_created": 1}
    assert detail(client, run)["pending"] == 2


def test_legacy_saved_evaluation_does_not_adopt_preexisting_orphan_retry(client, event_payload):
    run, _ = fixture_run(client, event_payload, count=1)
    root = run["items"][0]["analysis_id"]
    child = retry(client, root).json()["analysis_id"]
    finish(client, child)
    with client.app.state.session_factory() as db:
        label = db.scalar(select(AnalysisLabel).where(AnalysisLabel.analysis_id == root))
        record = SavedEvaluation(test_run_id=run["id"], revision=1, label_ids=[label.id],
                                 created_by="fixture", idempotency_key=str(uuid.uuid4()))
        db.add(record)
        db.flush()
        db.add(AccessAudit(actor_kind="admin_session", actor_id="fixture", action="rescore_test",
                           resource_type="test_evaluation", resource_id=record.id))
        db.commit()
        saved_id = record.id
    old = detail(client, run, evaluation_id=saved_id)
    assert old["failed"] == 1 and old["items"][0]["analysis_id"] == root
    assert old["evaluation_summary"]["evaluable"] == 0
    assert detail(client, run)["completed"] == 1


def test_replaying_a_blocked_batch_after_profile_restoration_does_not_call_again(client, event_payload):
    run, profile_id = fixture_run(client, event_payload, count=1)
    ids = preview(client, run)["eligible_ids"]
    key = str(uuid.uuid4())
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).status = "disabled"
        db.commit()
    assert submit(client, run, ids, key).json()["enqueued"] == 0
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).status = "production"
        db.commit()
    replay = submit(client, run, ids, key).json()
    assert replay["duplicate"] and replay["enqueued"] == 0
    assert submit(client, run, ids).json()["enqueued"] == 1


def test_duplicates_and_rejected_rows_do_not_increase_batch_or_quality_denominator(client, event_payload):
    run, _ = fixture_run(client, event_payload, count=1)
    with client.app.state.session_factory() as db:
        root = db.get(RunItem, run["items"][0]["id"])
        db.add(RunItem(test_run_id=run["id"], row_number=2, analysis_id=root.analysis_id,
                       event_id=root.event_id, label_id=root.label_id, ingest_status="duplicate"))
        db.add(RunItem(test_run_id=run["id"], row_number=3, ingest_status="rejected", error_code="invalid_test_event"))
        db.commit()
    assert preview(client, run)["eligible_count"] == 1
    child = submit(client, run, preview(client, run)["eligible_ids"]).json()["analysis_ids"][0]
    finish(client, child)
    result = detail(client, run)
    assert result["total"] == 3 and result["completed"] == result["duplicates"] == result["rejected"] == 1
    assert result["evaluation_summary"]["total"] == result["evaluation_summary"]["matches"] == 1
    assert result["items"][0]["analysis_id"] == result["items"][1]["analysis_id"] == child


def test_test_comparison_uses_recovered_result_once(client, event_payload):
    run, _ = fixture_run(client, event_payload, count=1)
    baseline = upload(client, [{**event_payload, "event_id": "fixture-0", "expected_verdict": "true_positive"}])
    finish(client, baseline["items"][0]["analysis_id"])
    child = submit(client, run, preview(client, run)["eligible_ids"]).json()["analysis_ids"][0]
    finish(client, child)
    response = client.get(f"/api/v1/test-runs/{run['id']}/comparison", params={"baseline_id": baseline["id"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["candidate_evaluation"]["total"] == result["candidate_evaluation"]["matches"] == 1
    assert "retry_results_included" in result["warnings"]
    assert child in response.text


def test_recovered_named_item_worker_preserves_pinned_settings_and_keeps_references_out_of_input(client, event_payload, monkeypatch):
    from app import worker
    from app.agent.executor import AgentCallResult
    from agent_selection_helpers import model_output
    from test_prompt_snapshots import fake_output
    run, profile_id = fixture_run(client, event_payload, count=1)
    child = submit(client, run, preview(client, run)["eligible_ids"]).json()["analysis_ids"][0]
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        return AgentCallResult(model_output(fake_output(), kwargs), "fixture-run", "fixture-fingerprint", "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, child)
        root = db.get(Analysis, row.retry_of_analysis_id)
        assert row.prompt_snapshot_ciphertext == root.prompt_snapshot_ciphertext
        assert row.payload_ciphertext == root.payload_ciphertext
        worker.process_moduagent(db, client.app.state.crypto, row, "", .2)
        assert row.status == "completed" and root.status == "failed"
        assert row.result_json["agent"]["model_profile_id"] == profile_id
        assert row.result_json["policy"]["verifier_confidence_threshold"] == .75
    assert calls and all("expected_verdict" not in call["user_input"] for call in calls)
    assert detail(client, run)["completed"] == 1
