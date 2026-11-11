"""Isolated synthetic display jobs; model and external transport are mocked."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest
from sqlalchemy import event, func, select, update

from app.agent.evidence_editor import EvidenceEditorOutput
from app.agent.executor import AgentCallResult
from app.models import AgentConfiguration, Analysis, EvidenceDisplayJob, InternalEgressTarget, TestRun as NamedRun, VLLMProfile, utcnow
from app.result_contracts import validate_new_result
from app.services import evidence_display_jobs as jobs
from app.services.analysis_retries import make_execution_snapshot
from app.services.candidate_configurations import verify_configuration
from app.services.evidence_editor import capture_editor
from app.services.prompt_snapshots import load_analysis_prompt
from test_analysis_retries_keys import profile_fixture, retry
from test_candidate_configurations import candidate, direct
from test_result_contract_v3 import result_fixture
from test_validation_data import login

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def canonical(*, single=False):
    result = result_fixture()
    if not single:
        second = deepcopy(result["evidence"][0])
        second.update(evidence_id="e2", excerpt="q=second", interpretation_ko="동일 검색 기능에 전달된 다른 합성 값입니다.",
            origins=[{"role": "primary", "index": 1, "source_id": "c2"}], source_spans=[{"start": 15, "end": 23}])
        result["evidence"].append(second)
        result["primary"]["evidence_ids"].append("e2")
    return validate_new_result(result)


def enqueue_job(client, event_payload, *, completed=True, single=False):
    login(client)
    profile_id = profile_fixture(client)
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, response.json()["id"])
        selected = jobs.capture_display_profile(db.get(VLLMProfile, profile_id))
        job = jobs.pin_job(db, client.app.state.crypto, analysis, selected)
        if completed:
            analysis.status, analysis.result_json = "completed", canonical(single=single)
            analysis.verdict, analysis.completed_at = "false_positive", utcnow()
        db.commit()
        return analysis.id, job.id, profile_id


def install_model(monkeypatch, *, mode="valid", callback=None):
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        assert kwargs["output_model"] is EvidenceEditorOutput
        assert kwargs["output_validation_max_attempts"] == 1 and kwargs["structured_output_recovery"] is True
        assert kwargs["agent_name"] == "waf-evidence-display-editor"
        kwargs["egress_check"]()
        if callback:
            callback()
        if mode == "timeout":
            raise TimeoutError("SYNTHETIC_PRIVATE_PROVIDER_ERROR")
        document = json.loads(kwargs["user_input"])
        ids = [item["evidence_id"] for item in document["evidence"]]
        if mode == "invalid":
            ids = ids[:1]
        value = EvidenceEditorOutput(groups=[{"member_ids": ids, "representative_id": ids[-1]}])
        return AgentCallResult(value, "synthetic-display", None, "completed", None, None, {})

    monkeypatch.setattr("app.agent.executor_v221.execute_structured_agent", execute)
    return calls


def run_job(client, identifier, *, owner="synthetic-display-owner"):
    with client.app.state.session_factory() as db:
        assert jobs.claim_next(db, owner) == identifier
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, identifier, owner)


def test_job_waits_for_analysis_and_single_claim_owns_lease(client, event_payload):
    analysis_id, identifier, _ = enqueue_job(client, event_payload, completed=False)
    with client.app.state.session_factory() as db:
        assert jobs.claim_next(db, "first") is None
        analysis = db.get(Analysis, analysis_id)
        analysis.status, analysis.result_json = "completed", canonical()
        db.commit()
        assert jobs.claim_next(db, "first") == identifier
        assert jobs.claim_next(db, "second") is None
        job = db.get(EvidenceDisplayJob, identifier)
        assert job.lease_owner == "first" and job.attempt_count == 1


def test_success_is_encrypted_source_bound_and_never_changes_canonical_result(client, event_payload, monkeypatch):
    analysis_id, identifier, _ = enqueue_job(client, event_payload)
    calls = install_model(monkeypatch)
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, analysis_id)
        job = db.get(EvidenceDisplayJob, identifier)
        assert analysis.status == "completed" and analysis.result_json == canonical()
        assert job.status == "completed" and job.attempt_count == 1 and job.lease_owner is None
        assert "q=sample" not in job.input_ciphertext and "q=sample" not in job.output_ciphertext
        assert "q=sample" in client.app.state.crypto.decrypt_text(job.input_ciphertext)
        assert "q=sample" not in json.dumps(job.metadata_json)
        view = jobs.get_display_view(db, client.app.state.crypto, analysis)
        assert view["status"] == "completed" and len(view["plan"]["groups"]) == 1
        assert jobs.get_saved_display_plan(db, client.app.state.crypto, analysis) == view["plan"]
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, identifier, "synthetic-display-owner")
    assert len(calls) == 1


@pytest.mark.parametrize("mode,reason", [("invalid", "display_groups_invalid"), ("timeout", "display_timeout")])
def test_optional_failure_keeps_original_all_visible_and_exposes_only_safe_reason(
    client, event_payload, monkeypatch, mode, reason,
):
    analysis_id, identifier, _ = enqueue_job(client, event_payload)
    calls = install_model(monkeypatch, mode=mode)
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        job, analysis = db.get(EvidenceDisplayJob, identifier), db.get(Analysis, analysis_id)
        assert job.status == "failed" and job.error_code == reason
        assert "SYNTHETIC_PRIVATE_PROVIDER_ERROR" not in json.dumps(job.metadata_json)
        assert analysis.result_json == canonical() and analysis.status == "completed"
        view = jobs.get_display_view(db, client.app.state.crypto, analysis)
        assert view["status"] == "fallback" and len(view["plan"]["groups"]) == 2
        assert view["metadata"]["reason"] == reason
        assert all(group["default_visible"] for group in view["plan"]["groups"])
    assert len(calls) == 1


def test_no_semantic_group_candidate_skips_model_with_complete_saved_plan(client, event_payload, monkeypatch):
    analysis_id, identifier, _ = enqueue_job(client, event_payload, single=True)
    calls = install_model(monkeypatch)
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, analysis_id)
        assert db.get(EvidenceDisplayJob, identifier).status == "skipped"
        assert jobs.get_display_view(db, client.app.state.crypto, analysis)["status"] == "skipped"
    assert calls == []


@pytest.mark.parametrize("damage", ["snapshot", "profile", "source_changed"])
def test_snapshot_profile_and_source_binding_fail_closed(client, event_payload, monkeypatch, damage):
    analysis_id, identifier, profile_id = enqueue_job(client, event_payload)
    if damage != "source_changed":
        with client.app.state.session_factory() as db:
            if damage == "snapshot":
                db.get(EvidenceDisplayJob, identifier).snapshot_ciphertext = "synthetic-invalid-ciphertext"
            else:
                db.get(VLLMProfile, profile_id).max_output_tokens += 1
            db.commit()

    def change_source():
        with client.app.state.session_factory() as db:
            analysis = db.get(Analysis, analysis_id)
            value = deepcopy(analysis.result_json)
            value["evidence"][0]["excerpt"] = "synthetic-source-changed"
            analysis.result_json = value
            db.commit()

    calls = install_model(monkeypatch, callback=change_source if damage == "source_changed" else None)
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        job = db.get(EvidenceDisplayJob, identifier)
        assert job.status == "failed"
        assert job.error_code == {"snapshot": "display_editor_snapshot_invalid", "profile": "display_profile_unavailable",
            "source_changed": "display_source_changed"}[damage]
        assert jobs.get_display_view(db, client.app.state.crypto, db.get(Analysis, analysis_id))["status"] == "fallback"
    assert len(calls) == (1 if damage == "source_changed" else 0)


def test_expired_unknown_model_completion_is_not_automatically_sent_again(client, event_payload, monkeypatch):
    _analysis_id, identifier, _ = enqueue_job(client, event_payload)
    calls = install_model(monkeypatch)
    with client.app.state.session_factory() as db:
        assert jobs.claim_next(db, "dead-process") == identifier
        db.get(EvidenceDisplayJob, identifier).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        assert jobs.claim_next(db, "recovery") is None
        job = db.get(EvidenceDisplayJob, identifier)
        assert job.status == "failed" and job.error_code == "display_lease_expired" and job.attempt_count == 1
    assert calls == []


def test_completion_cannot_overwrite_a_recovered_lease(client, event_payload, monkeypatch):
    analysis_id, identifier, _ = enqueue_job(client, event_payload)
    def recover_while_model_returns():
        with client.app.state.session_factory() as db:
            db.execute(update(EvidenceDisplayJob).where(EvidenceDisplayJob.id == identifier).values(
                status="failed", error_code="display_lease_expired", lease_owner=None, lease_expires_at=None,
                completed_at=utcnow()))
            db.commit()
    calls = install_model(monkeypatch, callback=recover_while_model_returns)
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        job = db.get(EvidenceDisplayJob, identifier)
        assert job.status == "failed" and job.error_code == "display_lease_expired"
        assert job.output_ciphertext is None and job.attempt_count == 1
        assert db.get(Analysis, analysis_id).result_json == canonical()
    assert len(calls) == 1


def test_revoked_egress_prevents_any_editor_dispatch(client, event_payload, monkeypatch):
    _analysis_id, identifier, _ = enqueue_job(client, event_payload)
    calls = install_model(monkeypatch)
    with client.app.state.session_factory() as db:
        for target in db.scalars(select(InternalEgressTarget)):
            db.delete(target)
        db.commit()
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        assert db.get(EvidenceDisplayJob, identifier).status == "failed"
    assert calls == []


@pytest.mark.parametrize("block", ["internal_only", "consent_revoked"])
def test_external_editor_requires_current_consent_and_respects_internal_only(
    client, event_payload, monkeypatch, block,
):
    from test_model_test_role import create_verified
    analysis_id, identifier, _ = enqueue_job(client, event_payload)
    external = create_verified(client, "synthetic-external-display", provider="openai")
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, external["id"])
        selected = jobs.capture_display_profile(profile)
        db.get(EvidenceDisplayJob, identifier).snapshot_ciphertext = client.app.state.crypto.encrypt_text(selected.model_dump_json())
        if block == "internal_only":
            db.get(Analysis, analysis_id).internal_only = True
        else:
            profile.external_data_approved = False
        db.commit()
    accepted = []
    async def transport(**kwargs):
        kwargs["egress_check"]()
        accepted.append(kwargs)
        raise AssertionError("An external request must not be accepted")
    monkeypatch.setattr("app.agent.executor_v221.execute_structured_agent", transport)
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        job = db.get(EvidenceDisplayJob, identifier)
        assert job.status == "failed" and job.error_code in {"display_egress_blocked", "display_profile_unavailable"}
        assert db.get(Analysis, analysis_id).result_json == canonical()
    assert accepted == []


def test_stopped_test_cannot_start_display_call(client, event_payload, candidate, monkeypatch):
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert response.status_code == 202, response.text
    run = response.json()
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        analysis.status, analysis.result_json = "completed", canonical()
        db.get(NamedRun, run["id"]).stopped_at = utcnow()
        job = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == analysis.id))
        identifier = job.id
        db.commit()
    calls = install_model(monkeypatch)
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        assert db.get(EvidenceDisplayJob, identifier).error_code == "display_test_stopped"
    assert calls == []


def test_lane_recovers_coordination_failure_without_logging_exception_text(monkeypatch, caplog):
    from contextlib import nullcontext
    from threading import Event
    from types import SimpleNamespace
    stop = Event()
    count = 0
    def claim(*_args):
        nonlocal count
        count += 1
        if count == 1:
            raise ValueError("SYNTHETIC_SECRET_MUST_NOT_BE_LOGGED")
        stop.set()
    monkeypatch.setattr(jobs, "claim_next", claim)
    jobs.serve(lambda: nullcontext(None), None, SimpleNamespace(worker_poll_seconds=0), "synthetic", stop)
    assert count == 2 and "SYNTHETIC_SECRET" not in caplog.text
    assert "display lane recovery failed error_type=ValueError" in caplog.text


def test_worker_shutdown_cancels_before_dispatch_without_dropping_accepted_result(client, event_payload, monkeypatch):
    from threading import Event
    analysis_id, identifier, _ = enqueue_job(client, event_payload)
    stop = Event()
    stop.set()
    calls = install_model(monkeypatch)
    with client.app.state.session_factory() as db:
        assert jobs.claim_next(db, "stopping") == identifier
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, identifier, "stopping", stop=stop)
    with client.app.state.session_factory() as db:
        job = db.get(EvidenceDisplayJob, identifier)
        assert job.status == "failed" and job.error_code == "display_worker_stopping"
        assert db.get(Analysis, analysis_id).status == "completed"
    assert calls == []


def test_read_view_is_batched_and_never_enqueues_or_calls_a_model(client, event_payload, monkeypatch):
    analysis_id, _identifier, _ = enqueue_job(client, event_payload)
    calls = install_model(monkeypatch)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, analysis_id)
        statements = []
        def trace(_connection, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(db.get_bind(), "before_cursor_execute", trace)
        try:
            views = jobs.get_display_views(db, client.app.state.crypto, [analysis], {analysis_id: None})
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", trace)
        assert len(statements) == 1 and "evidence_display_jobs" in statements[0]
        assert views[analysis_id]["status"] == "pending"
        assert db.scalar(select(func.count()).select_from(EvidenceDisplayJob)) == 1
    assert calls == []


def test_new_candidate_separates_core_editor_and_blocks_official_gate_until_display_finishes(
    client, event_payload, candidate, monkeypatch,
):
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert response.status_code == 202, response.text
    run = response.json()
    calls = install_model(monkeypatch)
    with client.app.state.session_factory() as db:
        saved = db.get(NamedRun, run["id"])
        assert saved.evidence_editor_snapshot_ciphertext is None and saved.display_editor_snapshot_ciphertext
        assert run["configuration_snapshot"]["evidence_editor"]["version"] == jobs.VERSION
        assert run["configuration_snapshot"]["evidence_editor"]["implementation_hash"] == jobs.implementation_artifact()["sha256"]
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        assert verify_configuration(saved, client.app.state.crypto, analysis) == run["configuration_snapshot"]
        assert jobs.qualification(db, saved, client.app.state.crypto)["pending"]
        analysis.status, analysis.result_json = "completed", canonical()
        job = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == analysis.id))
        identifier = job.id
        db.commit()
        assert not jobs.qualification(db, saved, client.app.state.crypto)["ready"]
    run_job(client, identifier)
    with client.app.state.session_factory() as db:
        assert jobs.qualification(db, db.get(NamedRun, run["id"]), client.app.state.crypto) == {
            "required": True, "ready": True, "pending": False, "failed": 0}
    assert len(calls) == 1


def test_retry_copies_original_display_snapshot_without_using_current_settings(client, event_payload, monkeypatch):
    client.app.state.settings.agent_mode = "moduagent"
    analysis_id, identifier, profile_id = enqueue_job(client, event_payload, completed=False)
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, analysis_id)
        selected = load_analysis_prompt(original, client.app.state.crypto)
        execution = make_execution_snapshot(original, db.get(VLLMProfile, profile_id), selected, .73,
            crypto=client.app.state.crypto)
        original.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(execution.model_dump_json())
        original.status, original.completed_at, original.error_code = "failed", utcnow(), "synthetic-primary-failure"
        before = db.get(EvidenceDisplayJob, identifier).snapshot_ciphertext
        db.commit()
    response = retry(client, analysis_id, key="synthetic-display-retry")
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        child = db.get(Analysis, response.json()["analysis_id"])
        display = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == child.id))
        assert display.snapshot_ciphertext == before and display.status == "pending"
        assert db.get(Analysis, analysis_id).status == "failed"


@pytest.mark.parametrize("failure", [False, True])
def test_official_evaluation_and_production_promotion_require_successful_new_display_job(
    client, event_payload, candidate, monkeypatch, failure,
):
    from test_official_evaluations import approved_item, execute
    from test_validation_data import create_dataset
    from app.services.official_evaluations import finalize_official_evaluation
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2}, "false_positive")
    run = execute(client, dataset, candidate)
    identifier = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, identifier)
        analysis.status, analysis.result_json, analysis.verdict = "completed", canonical(), "false_positive"
        analysis.completed_at = utcnow()
        db.commit()
        assert finalize_official_evaluation(db, run["id"]) is None
        assert db.get(NamedRun, run["id"]).official_evaluation_pending
        job = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == identifier))
        job_id = job.id
    calls = install_model(monkeypatch, mode="invalid" if failure else "valid")
    run_job(client, job_id)
    base = "/api/v1/admin/production-configurations"
    review = client.get(f"{base}/preflight/{run['id']}")
    assert review.status_code == 200, review.text
    checked = {item["code"]: item["passed"] for item in review.json()["checks"]}
    assert checked["display_editor_completed"] == (not failure)
    if not failure:
        assert review.json()["eligible"], review.json()["checks"]
    before = client.get(base).json()
    promoted = client.post(base + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert promoted.status_code == (409 if failure else 200), promoted.text
    if not failure:
        created = client.post("/api/v1/analyses?wait_seconds=0", json={**event_payload,
            "event_id": "synthetic-approved-display-production", "vendor_score": 2})
        assert created.status_code == 202, created.text
        with client.app.state.session_factory() as db:
            analysis = db.get(Analysis, created.json()["id"])
            display = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == analysis.id))
            assert jobs.load_snapshot(client.app.state.crypto, display.snapshot_ciphertext).version == jobs.VERSION
            primary = db.get(VLLMProfile, candidate["primary_profile_id"])
            assert capture_editor(db, "production", primary) is None
            assert analysis.result_json is None
    assert len(calls) == 1


def test_fresh_and_existing_upgrade_preserve_prior_analysis_and_add_nullable_snapshot(tmp_path, monkeypatch, settings):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text
    backend = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{tmp_path / 'display-migration.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: settings.model_copy(update={"database_url": url}))
    configuration = Config(str(backend / "alembic.ini"))
    configuration.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(configuration, "0029_parser_management")
    engine = create_engine(url)
    with engine.begin() as connection:
        # Current metadata is intentionally used by the historical initial
        # migration. Remove only these new empty structures to reconstruct the
        # populated, immediately preceding production schema faithfully.
        connection.exec_driver_sql("DROP TABLE evidence_display_jobs")
        connection.exec_driver_sql("ALTER TABLE test_runs DROP COLUMN display_editor_snapshot_ciphertext")
        connection.execute(Analysis.__table__.insert().values(id="synthetic-existing", source_system="fixture",
            event_id="existing", company_name="Synthetic", src_ip="192.0.2.1", dest_ip="198.51.100.1",
            waf_vendor="generic", waf_action="D", payload_ciphertext="synthetic-unchanged-encrypted-blob",
            encryption_key_version="fixture", result_json={"legacy": "preserve"}))
        before = connection.execute(text("SELECT id,payload_ciphertext,result_json FROM analyses")).all()
    command.upgrade(configuration, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0030_evidence_display_jobs"
        assert inspect(connection).has_table("evidence_display_jobs")
        column = next(c for c in inspect(connection).get_columns("test_runs") if c["name"] == "display_editor_snapshot_ciphertext")
        assert column["nullable"]
        assert connection.execute(text("SELECT id,payload_ciphertext,result_json FROM analyses")).all() == before
    engine.dispose()
    fresh_url = f"sqlite+pysqlite:///{tmp_path / 'fresh-display.db'}"
    monkeypatch.setattr("app.config.get_settings", lambda: settings.model_copy(update={"database_url": fresh_url}))
    command.upgrade(configuration, "head")
    fresh = create_engine(fresh_url)
    assert inspect(fresh).has_table("evidence_display_jobs")
    fresh.dispose()
