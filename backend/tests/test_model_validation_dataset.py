"""Offline candidate evaluation, with real orchestration and synthetic outputs."""
import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app import worker
from app.agent.executor import AgentCallResult
from app.models import Analysis, AnalysisLabel, VLLMProfile, VLLMTestRun, utcnow
from app.services.evaluation import attach_evaluations
from app.services.model_validation import DATASET_SIZE, dataset_evaluation, dataset_source, load_dataset
from app.services.model_validation_worker import ModelTestLeaseLost, TestHeartbeat as LeaseHeartbeat, require_owned_test
from app.services.vllm_test_runner import VLLMTestResult
from test_agent_openai import synthetic_output
from app.agent.contracts import WAFAnalysisOutput
from test_model_profiles import login_admin, profile_payload


def enqueue(client, *, include=True):
    login_admin(client)
    profile = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    response = client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "full", "include_dataset": include, "name": "합성 150건 검증", "idempotency_key": "synthetic-model-validation"})
    assert response.status_code == 202, response.text
    return profile, response.json()


def install_calls(monkeypatch, *, checks_pass=True, fail_case=False):
    calls = []
    async def technical(profile, crypto, mode, *, egress_check):
        egress_check()
        assert mode == "full"
        return VLLMTestResult(checks_pass, [], {"provider": profile.provider}, None if checks_pass else "synthetic_check_failed", None)
    async def analyze(**kwargs):
        calls.append(kwargs)
        kwargs["request_check"]() if "request_check" in kwargs else kwargs["egress_check"]()
        document = json.loads(kwargs["user_input"])
        assert "expected_verdict" not in document.get("event", {})
        assert "expected_verdict" not in document.get("event", {}).get("extra_fields", {})
        if fail_case and len(calls) == 1:
            raise RuntimeError("synthetic_failure")
        return AgentCallResult(
            output=WAFAnalysisOutput.model_validate(synthetic_output()), framework_run_id="synthetic-run",
            agent_fingerprint="synthetic-agent", finish_reason="completed", failure_id=None, error=None,
            telemetry={"framework": "moduagent", "framework_version": "0.6.2"},
        )
    monkeypatch.setattr("app.services.model_validation_worker.run_vllm_test", technical)
    monkeypatch.setattr("app.worker.execute_structured_agent", analyze)
    return calls


def run_claimed(client):
    with client.app.state.session_factory() as db:
        claimed = worker.claim_next_vllm_test(db, "synthetic-model-tester", 900)
        worker.process_vllm_test(db, client.app.state.crypto, claimed, "ignored.example:8000")
        return claimed.id


def test_packaged_dataset_matches_current_samples_and_excludes_answers_from_inputs():
    events, digest = load_dataset()
    assert len(events) == DATASET_SIZE == 150 and len(digest) == 64
    assert {verdict: sum(expected == verdict for _, expected in events) for verdict in ("true_positive", "false_positive", "inconclusive")} == {"true_positive": 60, "false_positive": 60, "inconclusive": 30}
    assert all("expected_verdict" not in event.model_dump() for event, _ in events)
    sample = Path(__file__).resolve().parents[2] / "samples/waf-dummy-v1/all_150.json"
    if sample.exists():
        expected = json.loads(sample.read_text())
        assert [{**event.model_dump(exclude_none=True), "expected_verdict": verdict} for event, verdict in events] == [{key: value for key, value in row.items() if value is not None and key not in {"difficulty", "test_category", "case_name"}} for row in expected]


def test_optional_dataset_is_atomic_separate_and_initially_unscored(client, registered_vllm_target):
    profile, run = enqueue(client)
    assert run["include_dataset"] is True
    report = run["dataset_evaluation"]
    assert report["total"] == report["pending"] == 150
    assert report["summary"]["evaluable"] == 0
    with client.app.state.session_factory() as db:
        assert worker.claim_next(db, "ordinary-analysis-worker", 300) is None
        cases = db.scalars(select(Analysis)).all()
        assert len(cases) == 150
        assert {row.analysis_purpose for row in cases} == {"test"}
        assert {row.ingest_channel for row in cases} == {"model_validation"}
        assert {row.source_system for row in cases} == {dataset_source(run["id"])}
        assert len({row.prompt_policy_version_id for row in cases}) == 1
        assert len({client.app.state.crypto.decrypt_text(row.prompt_snapshot_ciphertext) for row in cases}) == 1
        assert all("expected_verdict" not in row.extra_fields for row in cases)
        labels = db.scalars(select(AnalysisLabel)).all()
        assert len(labels) == 150 and {label.attachment_id for label in labels} == {run["id"]}
        assert {label.source_ref for label in labels} == {"waf-dummy-v1"}
        assert db.get(VLLMProfile, profile["id"]).status == "draft"


def test_functional_only_does_not_enqueue_dataset(client, registered_vllm_target):
    _, run = enqueue(client, include=False)
    assert run["dataset_evaluation"] is None
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0


def test_modal_must_reconfirm_if_candidate_settings_changed(client, registered_vllm_target):
    login_admin(client)
    profile = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    assert len(profile["profile_fingerprint"]) == 64
    changed = client.put(f"/api/v1/model-profiles/{profile['id']}", json={"model_name": "synthetic-changed-model"})
    assert changed.status_code == 200
    response = client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={
        "mode": "full", "include_dataset": True, "name": "변경 확인 테스트", "idempotency_key": "synthetic-changed-candidate",
        "expected_profile_fingerprint": profile["profile_fingerprint"],
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "model_profile_changed_reconfirm"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(VLLMTestRun)) == 0
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0
    assert client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={
        "mode": "full", "include_dataset": False,
        "expected_profile_fingerprint": changed.json()["profile_fingerprint"],
    }).status_code == 202


def test_dataset_ingest_error_is_safe_and_atomic(client, registered_vllm_target, monkeypatch):
    from app.services.analysis import AnalysisIngestError
    from app.services.test_runs import enqueue_test_upload_row
    count = 0
    def unavailable_third(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 3:
            raise AnalysisIngestError("prompt_policy_unavailable", 503)
        return enqueue_test_upload_row(*args, **kwargs)
    monkeypatch.setattr("app.services.test_runs.enqueue_test_upload_row", unavailable_third)
    login_admin(client)
    profile = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    response = client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "full", "include_dataset": True, "name": "원자적 접수 검증", "idempotency_key": "synthetic-atomic-dataset"})
    assert response.status_code == 503
    assert response.json()["detail"] == "prompt_policy_unavailable"
    with client.app.state.session_factory() as db:
        for model in (Analysis, AnalysisLabel, VLLMTestRun):
            assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("payload", [{"mode": "quick", "include_dataset": True}, {"mode": "full", "include_dataset": "true"}, {"mode": "full", "include_dataset": 1}, {"mode": "full", "include_dataset": True, "dataset_path": "/etc/passwd"}])
def test_dataset_selection_is_explicit_and_cannot_select_an_arbitrary_file(client, registered_vllm_target, payload):
    login_admin(client)
    profile = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    assert client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json=payload).status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0


def test_dataset_failure_rolls_back_run_and_all_cases(client, registered_vllm_target, monkeypatch):
    from app.services.model_validation import DatasetError
    from app.services.test_runs import enqueue_test_upload_row
    count = 0
    def fail_third(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 3:
            raise DatasetError()
        return enqueue_test_upload_row(*args, **kwargs)
    monkeypatch.setattr("app.services.test_runs.enqueue_test_upload_row", fail_third)
    login_admin(client)
    profile = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    assert client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json={"mode": "full", "include_dataset": True, "name": "접수 롤백 검증", "idempotency_key": "synthetic-dataset-rollback"}).status_code == 422
    with client.app.state.session_factory() as db:
        for model in (Analysis, AnalysisLabel, VLLMTestRun):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_all_150_use_candidate_not_production_and_report_reference_comparison(client, registered_vllm_target, monkeypatch):
    profile, run = enqueue(client)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="different-production", model_name="other-model", base_url="http://10.0.0.99:8000/v1", status="production"))
        db.commit()
    calls = install_calls(monkeypatch)
    run_claimed(client)
    response = client.get(f"/api/v1/model-profiles/{profile['id']}/tests/{run['id']}").json()
    assert response["status"] == "passed", response
    report = response["dataset_evaluation"]
    assert report["completed"] == report["total"] == 150 and report["failed"] == 0
    assert report["summary"]["evaluable"] == 150
    assert report["summary"]["outcomes"]["expected_abstention_match"] == 30
    assert report["summary"]["outcomes"]["abstained"] == 120
    assert len(calls) == 300  # Primary and independent Verifier for each held judgment.
    assert {call["profile"].id for call in calls} == {profile["id"]}
    for primary, verifier in zip(calls[::2], calls[1::2]):
        assert primary["user_input"] == verifier["user_input"]
        assert primary["instructions"] != verifier["instructions"]
    with client.app.state.session_factory() as db:
        assert db.get(VLLMProfile, profile["id"]).status == "verified"
        assert db.scalar(select(VLLMProfile).where(VLLMProfile.status == "production")).name == "different-production"


def test_technical_failure_skips_all_150_without_agent_calls(client, registered_vllm_target, monkeypatch):
    profile, run = enqueue(client)
    calls = install_calls(monkeypatch, checks_pass=False)
    run_claimed(client)
    report = client.get(f"/api/v1/model-profiles/{profile['id']}/tests/{run['id']}").json()
    assert report["status"] == "failed"
    assert report["dataset_evaluation"]["status"] == "skipped"
    assert report["dataset_evaluation"]["summary"]["evaluable"] == 0
    assert report["dataset_evaluation"]["failed"] == 150
    assert calls == []


def test_resume_keeps_completed_cases_and_original_references(client, registered_vllm_target, monkeypatch):
    profile, run = enqueue(client)
    with client.app.state.session_factory() as db:
        claimed = worker.claim_next_vllm_test(db, "interrupted", 900)
        rows = db.scalars(select(Analysis).where(Analysis.model_test_run_id == run["id"])).all()
        for row in rows[:-1]:
            row.status = "completed"
            row.completed_at = utcnow()
            row.verdict = "inconclusive"
            row.result_json = {"verdict": "inconclusive", "agent": {"framework": "moduagent"}}
        # A later correction affects normal views but must not rewrite benchmark grading.
        original = db.scalar(select(AnalysisLabel).where(AnalysisLabel.analysis_id == rows[0].id))
        replacement = "false_positive" if original.verdict != "false_positive" else "true_positive"
        db.add(AnalysisLabel(analysis_id=rows[0].id, revision=2, verdict=replacement, source_kind="reference", source_ref="synthetic-correction", ai_visible=True, created_by="admin", attachment_id="separate-correction", token_digest="a" * 64))
        claimed.metrics_json = {"technical_checks_passed": True, "dataset_status": "running"}
        claimed.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        before = dataset_evaluation(db, claimed).summary
        attach_evaluations(db, [rows[0]])
        assert rows[0]._evaluation.reference_label.revision == 2
    calls = install_calls(monkeypatch)
    run_claimed(client)
    assert len(calls) == 2
    report = client.get(f"/api/v1/model-profiles/{profile['id']}/tests/{run['id']}").json()
    assert report["status"] == "passed"
    assert report["dataset_evaluation"]["summary"]["outcomes"]["expected_abstention_match"] == 30


def test_stale_owner_cannot_write_or_renew_new_claim(client, registered_vllm_target):
    _, run = enqueue(client)
    with client.app.state.session_factory() as db:
        claim = worker.claim_next_vllm_test(db, "same-process", 900)
        old_owner = claim.lease_owner
        claim.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        fresh = worker.claim_next_vllm_test(db, "same-process", 900)
        assert fresh.lease_owner != old_owner
        with pytest.raises(ModelTestLeaseLost):
            require_owned_test(db, run["id"], old_owner)
        db.rollback()
        stale = LeaseHeartbeat(db.get_bind(), run["id"], old_owner)
        stale.renew()
        assert stale.lost.is_set()
        with pytest.raises(ModelTestLeaseLost):
            stale.check()


@pytest.mark.parametrize("change", ["disabled", "model_changed"])
def test_review_candidate_change_before_execution_makes_no_provider_calls(
    client, registered_vllm_target, monkeypatch, change,
):
    profile, run = enqueue(client)
    calls = []

    async def unexpected_provider_call(*args, **kwargs):
        calls.append("provider")
        raise AssertionError("synthetic provider must not be contacted")

    monkeypatch.setattr("app.services.model_validation_worker.run_vllm_test", unexpected_provider_call)
    monkeypatch.setattr("app.worker.execute_structured_agent", unexpected_provider_call)
    with client.app.state.session_factory() as db:
        candidate = db.get(VLLMProfile, profile["id"])
        if change == "disabled":
            candidate.status = "disabled"
        else:
            candidate.model_name = "synthetic-changed-after-queue"
        db.commit()
    run_claimed(client)
    assert calls == []
    with client.app.state.session_factory() as db:
        saved = db.get(VLLMTestRun, run["id"])
        assert saved.status == "failed"
        assert saved.error_code == "model_validation_profile_changed"
        assert db.scalar(select(func.count()).select_from(Analysis).where(Analysis.status == "failed")) == 150
        assert db.get(VLLMProfile, profile["id"]).status == ("disabled" if change == "disabled" else "draft")


def _review_leave_one_pending(client, run_id):
    with client.app.state.session_factory() as db:
        rows = db.scalars(select(Analysis).where(Analysis.model_test_run_id == run_id).order_by(Analysis.id)).all()
        for row in rows[:-1]:
            row.status = "completed"
            row.completed_at = utcnow()
            row.verdict = "inconclusive"
            row.result_json = {"verdict": "inconclusive", "agent": {"framework": "moduagent"}}
        db.get(VLLMTestRun, run_id).metrics_json = {"technical_checks_passed": True, "dataset_status": "running"}
        last_id = rows[-1].id
        db.commit()
        return last_id


def test_review_one_remaining_case_failure_fails_run_without_changing_production(
    client, registered_vllm_target, monkeypatch,
):
    profile, run = enqueue(client)
    last_id = _review_leave_one_pending(client, run["id"])
    with client.app.state.session_factory() as db:
        production = VLLMProfile(
            name="synthetic-preserved-production", model_name="synthetic-production-model",
            base_url="http://10.0.0.99:8000/v1", status="production",
        )
        db.add(production)
        db.commit()
        db.refresh(production)
        production_id = production.id
        before = {column.name: getattr(production, column.name) for column in VLLMProfile.__table__.columns}
    calls = install_calls(monkeypatch, fail_case=True)
    run_claimed(client)
    assert len(calls) == 1
    assert calls[0]["profile"].id == profile["id"]
    with client.app.state.session_factory() as db:
        saved = db.get(VLLMTestRun, run["id"])
        assert (saved.status, saved.error_code) == ("failed", "model_validation_case_failed")
        report = dataset_evaluation(db, saved)
        assert (report.completed, report.failed, report.pending, report.processing) == (149, 1, 0, 0)
        assert db.get(Analysis, last_id).error_code == "primary_agent_failed"
        assert db.get(VLLMProfile, profile["id"]).status == "draft"
        production = db.get(VLLMProfile, production_id)
        assert {column.name: getattr(production, column.name) for column in VLLMProfile.__table__.columns} == before


def test_review_lease_loss_during_actual_agent_step_discards_stale_output(
    client, registered_vllm_target, monkeypatch,
):
    from app.models import AgentRun, AgentStep
    from app.services.timing import LEASE_EXPIRED_FAILURE

    profile, run = enqueue(client)
    last_id = _review_leave_one_pending(client, run["id"])
    install_calls(monkeypatch)
    owners = {}
    calls = []

    async def steal_lease_while_model_returns(**kwargs):
        calls.append(kwargs)
        kwargs["egress_check"]()
        with client.app.state.session_factory() as newer:
            old = newer.get(VLLMTestRun, run["id"])
            owners["old"] = old.lease_owner
            old.lease_expires_at = utcnow() - timedelta(seconds=1)
            newer.commit()
            replacement = worker.claim_next_vllm_test(newer, "synthetic-replacement", 900)
            owners["new"] = replacement.lease_owner
        return AgentCallResult(
            output=WAFAnalysisOutput.model_validate(synthetic_output()),
            framework_run_id="synthetic-stale-provider-response", agent_fingerprint="synthetic-stale-fingerprint",
            finish_reason="completed", failure_id=None, error=None,
            telemetry={"framework": "moduagent", "framework_version": "0.6.2"},
        )

    monkeypatch.setattr("app.worker.execute_structured_agent", steal_lease_while_model_returns)
    run_claimed(client)
    assert len(calls) == 1 and owners["old"] != owners["new"]
    with client.app.state.session_factory() as db:
        saved = db.get(VLLMTestRun, run["id"])
        assert saved.status == "running" and saved.lease_owner == owners["new"]
        assert saved.completed_at is None and saved.error_code is None
        last = db.get(Analysis, last_id)
        assert last.status == "processing" and last.result_json is None and last.verdict is None
        old_run = db.scalar(select(AgentRun).where(AgentRun.analysis_id == last_id))
        old_run_id = old_run.id
        assert old_run.status == "running"
        assert old_run.framework_run_id is None
        step = db.scalar(select(AgentStep).where(AgentStep.run_id == old_run.id, AgentStep.step_type == "llm_primary"))
        assert step.status == "running" and step.output_ciphertext is None and step.completed_at is None
        assert db.get(VLLMProfile, profile["id"]).status == "draft"
    # The replacement owner alone may abandon the unfinished step and finish.
    fresh_calls = install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        worker.process_vllm_test(db, client.app.state.crypto, db.get(VLLMTestRun, run["id"]), "ignored.example:8000")
    assert len(fresh_calls) == 2
    with client.app.state.session_factory() as db:
        assert db.get(VLLMTestRun, run["id"]).status == "passed"
        old_run = db.get(AgentRun, old_run_id)
        assert old_run.status == "failed" and old_run.failure_id == LEASE_EXPIRED_FAILURE
        assert db.get(Analysis, last_id).status == "completed"
