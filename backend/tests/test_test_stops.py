"""User stop semantics on separate WAL connections; synthetic data, no paid I/O."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import func, select

from app import worker
from app.models import AccessAudit, AgentRun, AgentStep, Analysis, TestEvaluation as Evaluation, TestRun as Run, VLLMProfile, VLLMTestRun, utcnow
from app.services.analysis_retries import enqueue_retry, eligibility, RetryError
from app.services.official_evaluations import finalize_official_evaluation, recover_pending_evaluations
from app.services.test_runs import named_test_request_check, write_lock, describe_run
from app.services.test_stops import stop_test_run
from app.services.vllm_profiles import TargetNotAllowedError
from test_test_runs import direct, login, upload


@pytest.fixture
def settings(settings, tmp_path):
    settings.database_url = f"sqlite+pysqlite:///{tmp_path / 'stop.db'}"
    return settings


def stop(client, run, status=200):
    response = client.post(f"/api/v1/test-runs/{run['id']}/stop")
    assert response.status_code == status, response.text
    return response.json()


def test_stop_pending_idempotent_audited_and_not_failed(client, event_payload):
    login(client)
    run = upload(client, [{**event_payload, "expected_verdict": "false_positive"},
                         {**event_payload, "event_id": "second"}])
    identifier = run["items"][0]["analysis_id"]
    first = stop(client, run)
    assert (first["status"], first["canceled"], first["failed"], first["pending"], first["can_stop"]) == ("stopped", 2, 0, 0, False)
    assert first["evaluation_summary"]["outcomes"]["canceled"] == 2  # Includes unlabeled stops.
    assert first["evaluation_summary"]["evaluable"] == 0
    assert first["evaluation_summary"]["metrics"]["accuracy"] is None
    assert stop(client, run) == first
    detail = client.get(f"/api/v1/test-runs/{run['id']}?status=canceled").json()
    assert detail["total_items"] == 2
    assert {row["evaluation"]["outcome"] for row in detail["items"]} == {"canceled"}
    assert client.get("/api/v1/test-runs?has_failures=true").json()["total"] == 0
    with client.app.state.session_factory() as db:
        assert worker.claim_next(db, "fixture", 60, purpose="test") is None
        row = db.get(Analysis, identifier)
        assert row.result_json is None and row.verdict is None
        worker.mark_failed(db, row, RuntimeError("synthetic late exception"))
        db.refresh(row)
        assert row.status == "canceled" and row.error_code == "test_run_stopped"
        audits = list(db.scalars(select(AccessAudit).where(AccessAudit.action == "stop_test_run")))
        assert len(audits) == 1 and audits[0].actor_id == "admin" and audits[0].resource_id == run["id"]
        assert eligibility(db, client.app.state.crypto, identifier, "moduagent").blocked_reason == "test_run_stopped"
    assert client.get(f"/api/v1/test-runs/{run['id']}/retry-eligibility").json()["detail"] == "test_run_stopped"
    assert client.post(f"/api/v1/test-runs/{run['id']}/retry-failed", json={"analysis_ids": [identifier],
        "idempotency_key": str(uuid.uuid4()), "cost_acknowledged": True}).status_code == 409
    assert client.post(f"/api/v1/analyses/{identifier}/retry", json={
        "idempotency_key": str(uuid.uuid4()), "cost_acknowledged": True}).json()["detail"] == "test_run_stopped"


@pytest.mark.parametrize("state", ["completed", "failed"])
def test_terminal_test_not_active_but_open_session_can_stop(client, event_payload, state):
    login(client)
    run = direct(client, event_payload)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        row.status, row.completed_at = state, utcnow()
        db.commit()
    assert stop(client, run, 409)["detail"] == "test_run_not_active"
    with client.app.state.session_factory() as db:
        db.get(Run, run["id"]).accepting_items = True
        db.commit()
    result = stop(client, run)
    assert result["canceled"] == 0 and result[state] == 1 and not result["accepting_items"]


def test_admin_only_origin_and_model_validation_exclusion(client, event_payload, service_headers):
    assert client.post("/api/v1/test-runs/missing/stop").status_code == 401
    assert client.post("/api/v1/test-runs/missing/stop", headers=service_headers).status_code == 403
    login(client)
    run = direct(client, event_payload)
    assert client.post(f"/api/v1/test-runs/{run['id']}/stop", headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(name="offline fixture", model_name="fixture", base_url="http://10.0.0.10:8000/v1")
        db.add(profile); db.flush()
        model_run = VLLMTestRun(profile_id=profile.id, mode="full", status="running", profile_fingerprint="a" * 64)
        db.add(model_run); db.flush()
        db.get(Run, run["id"]).model_test_run_id = model_run.id
        db.commit()
    assert stop(client, run, 409)["detail"] == "test_run_stop_not_supported_for_model_validation"
    assert client.get(f"/api/v1/test-runs/{run['id']}").json()["can_stop"] is False


@pytest.mark.parametrize("point", ["during_primary", "after_primary", "before_final_commit"])
@pytest.mark.parametrize("claimed", [False, True])
def test_stop_blocks_late_steps_results_and_failure_updates(client, event_payload, point, claimed):
    login(client)
    run = direct(client, event_payload)
    identifier = run["items"][0]["analysis_id"]
    factory, crypto = client.app.state.session_factory, client.app.state.crypto
    if claimed:
        with factory() as db:
            row = worker.claim_next(db, "synthetic-worker", 300, purpose="test")
            claim = (row.id, row.lease_owner, row.attempt_count)
    reached, resume = Event(), Event()
    dispatched = []
    def pause():
        reached.set()
        assert resume.wait(10)
    def execute():
        with factory() as db:
            row = db.get(Analysis, identifier)
            if claimed:
                db.info["analysis_claim"] = claim
            try:
                with worker.measured_run(db, row) as trace:
                    with worker.measured_step(db, crypto, trace, 1, "primary", "fixture-primary") as step:
                        dispatched.append("primary")
                        if point == "during_primary":
                            pause()
                        step.output_ciphertext = crypto.encrypt_text('"synthetic response"')
                    if point == "after_primary":
                        pause()
                        named_test_request_check(client.app.state.engine, run["id"])()
                        dispatched.append("verifier")
                    if point == "before_final_commit":
                        pause()
                    row.result_json = {"verdict": "true_positive"}
                    row.verdict = "true_positive"
            except (worker.WorkerExecutionError, TargetNotAllowedError) as exc:
                db.rollback()
                worker.mark_failed(db, row, exc)
                return getattr(exc, "code", str(exc))
            raise AssertionError("stale worker committed")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute)
        try:
            assert reached.wait(10)
            stopped = stop(client, run)
            assert stopped["canceled"] == 1
        finally:
            resume.set()
        assert future.result(timeout=10) in {"analysis_lease_lost", "test_run_stopped"}
    assert dispatched == ["primary"]
    with factory() as db:
        row = db.get(Analysis, identifier)
        assert row.status == "canceled" and row.verdict is None and row.result_json is None
        assert row.lease_owner is None and row.lease_expires_at is None
        trace = db.scalar(select(AgentRun).where(AgentRun.analysis_id == identifier))
        assert trace.status == "failed" and trace.failure_id == "test_run_stopped"
        step = db.scalar(select(AgentStep).where(AgentStep.run_id == trace.id))
        if point == "during_primary":
            assert step.status == "failed" and step.output_ciphertext is None
            assert step.metadata_json["error_code"] == "test_run_stopped"
            assert step.metadata_json["timing_incomplete"] and "duration_ms" not in step.metadata_json
        else:
            assert step.status == "completed" and step.output_ciphertext


def test_completed_current_and_retry_history_are_preserved(client, event_payload):
    login(client)
    run = upload(client, [{**event_payload, "event_id": str(i)} for i in range(3)])
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, run["items"][0]["analysis_id"])
        original.status = "failed"
        copy_fields = ("source_system", "event_id", "company_name", "src_ip", "dest_ip", "waf_vendor", "waf_action", "payload_ciphertext", "encryption_key_version")
        retry = Analysis(**{key: getattr(original, key) for key in copy_fields}, analysis_purpose="test", retry_of_analysis_id=original.id, status="pending")
        db.add(retry)
        complete = db.get(Analysis, run["items"][1]["analysis_id"])
        complete.status, complete.verdict, complete.result_json, complete.completed_at = "completed", "true_positive", {"verdict": "true_positive", "agent": {"framework": "moduagent"}}, utcnow()
        fields = {key: getattr(original, key) for key in copy_fields}
        fields["source_system"] = "unrelated-production"
        production = Analysis(**fields, analysis_purpose="production", status="processing")
        db.add(production); db.commit()
        ids = original.id, retry.id, complete.id, production.id
    result = stop(client, run)
    assert (result["completed"], result["failed"], result["canceled"]) == (1, 0, 2)
    with client.app.state.session_factory() as db:
        assert [db.get(Analysis, identifier).status for identifier in ids] == ["failed", "canceled", "completed", "processing"]
        assert db.get(Analysis, ids[2]).result_json["verdict"] == "true_positive"
        assert eligibility(db, client.app.state.crypto, run["items"][2]["analysis_id"], "moduagent").blocked_reason == "test_run_stopped"


def test_finalizer_refreshes_stale_pending_flag_after_stop(client, event_payload):
    login(client)
    run = direct(client, event_payload)
    factory = client.app.state.session_factory
    with factory() as db:
        row = db.get(Run, run["id"])
        row.evaluation_mode, row.official_evaluation_pending = "ground_truth", True
        db.commit()
    with factory() as stale:
        cached = stale.get(Run, run["id"])
        assert cached.official_evaluation_pending
        stale.commit()  # Keep identity map but no read lock.
        with factory() as stopping:
            stop_test_run(stopping, run["id"], "admin")
        assert finalize_official_evaluation(stale, run["id"]) is None
        assert cached.stopped_at is not None and not cached.official_evaluation_pending
    recover_pending_evaluations(factory)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Evaluation)) == 0


def test_filtered_detail_cannot_hide_stop_action(client, event_payload):
    login(client)
    run = upload(client, [{**event_payload, "difficulty": "easy"}, {**event_payload, "event_id": "other", "difficulty": "hard"}])
    with client.app.state.session_factory() as db:
        db.get(Analysis, run["items"][0]["analysis_id"]).status = "completed"
        db.commit()
    filtered = client.get(f"/api/v1/test-runs/{run['id']}?difficulty=easy").json()
    assert filtered["status"] == "completed" and filtered["can_stop"] is True
    stop(client, run)
    assert client.get(f"/api/v1/test-runs/{run['id']}?difficulty=easy").json()["status"] == "stopped"


def test_stop_holds_writer_before_finalizer_and_admission(client, event_payload, monkeypatch):
    from types import SimpleNamespace
    from app.services import test_api, official_evaluations
    from app.services.analysis import AnalysisIngestError
    from app.models import TestRunItem
    login(client)
    run = direct(client, event_payload)
    factory = client.app.state.session_factory
    with factory() as db:
        saved = db.get(Run, run["id"])
        saved.accepting_items, saved.api_source_system = True, "fixture-key-source"
        db.commit()
    entered = Event()
    def waiting_lock(db):
        entered.set()
        write_lock(db)
    monkeypatch.setattr(test_api, "write_lock", waiting_lock)
    principal = SimpleNamespace(kind="service_api_key", purpose="test", source_system="fixture-key-source", service_api_key_id=None)
    def admit():
        with factory() as db:
            try:
                test_api.ingest(db, client.app.state.crypto, client.app.state.settings, principal,
                    [{**event_payload, "event_id": "late-item"}], run_id=run["id"])
            except AnalysisIngestError as exc:
                return exc.code
            return "admitted"
    with ThreadPoolExecutor(max_workers=1) as pool, factory() as stopping:
        write_lock(stopping)
        future = pool.submit(admit)
        assert entered.wait(10)
        stop_test_run(stopping, run["id"], "admin")
        assert future.result(timeout=10) == "test_run_stopped"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(TestRunItem).where(TestRunItem.test_run_id == run["id"])) == 1
        assert not db.get(Run, run["id"]).accepting_items


def test_runtime_counts_separate_stops_and_failure_denominator(client, event_payload):
    login(client)
    run = upload(client, [{**event_payload, "event_id": str(i)} for i in range(3)])
    with client.app.state.session_factory() as db:
        for item, state in zip(run["items"], ["completed", "failed"]):
            row = db.get(Analysis, item["analysis_id"])
            row.status, row.completed_at = state, utcnow()
        db.commit()
    stop(client, run)
    from app.services.runtime_status import status_document
    with client.app.state.session_factory() as db:
        data = status_document(db, client.app.state.crypto, client.app.state.settings, "24h", "test")
    assert data["outcome_summary"] == {"completed": 1, "failed": 1, "pending": 0, "processing": 0, "canceled": 1}
    assert sum(row["canceled"] for row in data["request_volume_series"]) == 1
    assert sum(row["count"] for row in data["failure_types"]) == 1
    assert data["latency_summary"]["sample_count"] == 2
