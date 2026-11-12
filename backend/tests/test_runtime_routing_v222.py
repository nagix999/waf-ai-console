"""Synthetic version routing and real SQLite lease fences; no model/network."""
from contextlib import contextmanager
from datetime import timedelta
import sys
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

import app
from app import worker, worker_entrypoint_v222
from app.database import Base, build_engine, build_session_factory
from app.models import Analysis, VLLMProfile, VLLMTestRun, utcnow
from app.services import model_validation_worker as old_validation
from app.services import model_validation_worker_v222 as validation
from app.services import worker_concurrency as old_pool
from app.services import worker_concurrency_v222 as pool
from app.services import worker_runtime as old_runtime
from app.services import worker_runtime_v222 as runtime
from app.services.prompt_snapshots import PromptSnapshotError
from app.services.vllm_profiles import profile_fingerprint


OLD, PREVIOUS, NEW = "waf-system-v2.20", "waf-system-v2.21", "waf-system-v2.22"


def row_for(rules=NEW, **changes):
    suffix = rules.removeprefix("waf-system-") if rules else None
    return SimpleNamespace(prompt_snapshot_ciphertext="synthetic-encrypted-snapshot" if rules else None,
        prompt_version=f"waf-judgment-{suffix}/policy-1" if rules else None, **changes)


@pytest.fixture
def snapshot_reader(monkeypatch):
    calls = []
    def load(row, crypto):
        calls.append((row, crypto))
        if row.prompt_snapshot_ciphertext == "synthetic-invalid":
            raise PromptSnapshotError()
        return SimpleNamespace(fixed_rules_version=row.prompt_version.split("/", 1)[0].replace("waf-judgment-", "waf-system-"))
    monkeypatch.setattr(runtime, "load_analysis_prompt", load)
    return calls


@pytest.fixture
def synthetic_runtime(tmp_path, snapshot_reader):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'routing.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = build_session_factory(engine)
    settings = SimpleNamespace(agent_mode="moduagent", job_lease_seconds=60,
        vllm_test_lease_seconds=120, verifier_confidence_threshold=.71,
        worker_role="analysis", worker_poll_seconds=.01)
    yield engine, sessions, settings, object()
    engine.dispose()


def seed_analysis(sessions, *, version=NEW, **changes):
    prompt = row_for(version)
    row = Analysis(source_system="synthetic-routing", event_id="synthetic-case", company_name="Fixture",
        analysis_purpose="test", src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic", waf_action="D",
        payload_ciphertext="synthetic-encrypted-payload", encryption_key_version="synthetic-v1",
        prompt_version=prompt.prompt_version, prompt_snapshot_ciphertext=prompt.prompt_snapshot_ciphertext,
        status="processing", lease_owner="synthetic-owner", attempt_count=1,
        lease_expires_at=utcnow() + timedelta(seconds=60))
    for key, value in changes.items():
        setattr(row, key, value)
    with sessions() as db:
        db.add(row)
        db.commit()
        return row.id


def install_new_worker(monkeypatch, process):
    module = SimpleNamespace(process_moduagent=process)
    monkeypatch.setitem(sys.modules, "app.worker_v222", module)
    monkeypatch.setattr(app, "worker_v222", module, raising=False)


def test_dispatch_new_snapshot_to_new_worker_only_and_pass_checks_unchanged(monkeypatch, snapshot_reader):
    calls, check = [], object()
    install_new_worker(monkeypatch, lambda *args, **kwargs: calls.append((args, kwargs)) or "new")
    monkeypatch.setattr(old_runtime, "process_moduagent", lambda *_a, **_k: pytest.fail("legacy dispatch"))
    row, crypto = row_for(), object()
    assert runtime.process_moduagent(None, crypto, row, "", .71, request_check=check) == "new"
    assert calls == [((None, crypto, row, "", .71), {"request_check": check})]
    assert snapshot_reader == [(row, crypto)]


@pytest.mark.parametrize("version", [None, OLD, PREVIOUS])
def test_previous_executions_delegate_frozen_dispatcher(monkeypatch, snapshot_reader, version):
    calls = []
    monkeypatch.setattr(old_runtime, "process_moduagent", lambda *args, **kwargs: calls.append((args, kwargs)) or "old")
    install_new_worker(monkeypatch, lambda *_a, **_k: pytest.fail("new worker"))
    row, crypto, check = row_for(version), object(), object()
    assert runtime.process_moduagent(None, crypto, row, "", .8, request_check=check) == "old"
    assert calls == [((None, crypto, row, "", .8), {"request_check": check})]


@pytest.mark.parametrize("version", ["v2.21", "v2.22", "v2.23"])
def test_missing_new_provenance_never_reconstructs_as_legacy(monkeypatch, version):
    monkeypatch.setattr(old_runtime, "process_moduagent", lambda *_a, **_k: pytest.fail("legacy fallback"))
    row = SimpleNamespace(prompt_snapshot_ciphertext=None, prompt_version=f"waf-judgment-{version}/policy-1")
    with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
        runtime.process_moduagent(None, object(), row, "", .7)


def test_bad_or_unknown_new_snapshot_does_not_fall_back(monkeypatch, snapshot_reader):
    monkeypatch.setattr(old_runtime, "process_moduagent", lambda *_a, **_k: pytest.fail("legacy fallback"))
    for row in (SimpleNamespace(prompt_snapshot_ciphertext="synthetic-invalid", prompt_version="waf-judgment-v2.22/policy-1"),
                row_for("waf-system-v2.23")):
        with pytest.raises(PromptSnapshotError):
            runtime.process_moduagent(None, object(), row, "", .7)


@pytest.mark.parametrize("version", [OLD, PREVIOUS])
def test_outer_pool_uses_actual_frozen_job_runner_for_old_jobs(monkeypatch, synthetic_runtime, version):
    _, sessions, settings, crypto = synthetic_runtime
    identifier = seed_analysis(sessions, version=version)
    calls = []
    monkeypatch.setattr(old_pool, "run_analysis", lambda *args: calls.append(args))
    pool.run_analysis(sessions, crypto, settings, identifier, "synthetic-owner", 1)
    assert calls == [(sessions, crypto, settings, identifier, "synthetic-owner", 1)]


def test_stub_is_exact_legacy_runner_without_new_snapshot_reads(monkeypatch):
    calls = []
    sessions = lambda: pytest.fail("unnecessary provenance read")
    settings = SimpleNamespace(agent_mode="stub")
    monkeypatch.setattr(old_pool, "run_analysis", lambda *args: calls.append(args))
    pool.run_analysis(sessions, None, settings, "synthetic-case", "synthetic-owner", 1)
    assert len(calls) == 1


def test_new_job_keeps_real_heartbeat_claim_fence_and_finalization(monkeypatch, synthetic_runtime):
    _, sessions, settings, crypto = synthetic_runtime
    identifier = seed_analysis(sessions)
    calls, finalized = [], []
    def process(db, key, row, targets, threshold, *, request_check):
        request_check()
        worker._ensure_benchmark_owner(db)
        assert db.info["analysis_claim"] == (identifier, "synthetic-owner", 1)
        calls.append((key, targets, threshold))
        row.status, row.completed_at = "completed", utcnow()
        row.lease_owner = row.lease_expires_at = None
        db.commit()
    install_new_worker(monkeypatch, process)
    monkeypatch.setattr(old_pool, "run_analysis", lambda *_a: pytest.fail("old job runner"))
    monkeypatch.setattr("app.services.official_evaluations.finalize_for_analysis", lambda *args: finalized.append(args))
    pool.run_analysis(sessions, crypto, settings, identifier, "synthetic-owner", 1)
    assert calls == [(crypto, "", .71)]
    assert finalized == [(sessions, identifier)]
    with sessions() as db:
        assert db.get(Analysis, identifier).status == "completed"


def test_corrupt_new_provenance_is_failed_with_normal_lease_fence(monkeypatch, synthetic_runtime):
    _, sessions, settings, crypto = synthetic_runtime
    identifier = seed_analysis(sessions, prompt_snapshot_ciphertext=None)
    install_new_worker(monkeypatch, lambda *_a, **_k: pytest.fail("new model"))
    monkeypatch.setattr(old_pool, "run_analysis", lambda *_a: pytest.fail("legacy fallback"))
    monkeypatch.setattr("app.services.official_evaluations.finalize_for_analysis", lambda *_a: None)
    pool.run_analysis(sessions, crypto, settings, identifier, "synthetic-owner", 1)
    with sessions() as db:
        row = db.get(Analysis, identifier)
        assert row.status == "failed" and row.error_code == "prompt_snapshot_invalid"
        assert row.lease_owner is None


def test_stale_new_job_cannot_run_or_fail_another_owners_analysis(monkeypatch, synthetic_runtime):
    _, sessions, settings, crypto = synthetic_runtime
    identifier = seed_analysis(sessions, lease_owner="new-owner")
    install_new_worker(monkeypatch, lambda *_a, **_k: pytest.fail("stale model call"))
    monkeypatch.setattr("app.services.official_evaluations.finalize_for_analysis", lambda *_a: None)
    pool.run_analysis(sessions, crypto, settings, identifier, "old-owner", 1)
    with sessions() as db:
        row = db.get(Analysis, identifier)
        assert row.status == "processing" and row.lease_owner == "new-owner" and row.error_code is None


def seed_dataset(sessions, *, version=NEW, count=150, mixed=False, include=True):
    with sessions() as db:
        profile = VLLMProfile(name="Synthetic", model_name="fixture", base_url="http://192.0.2.10:8000/v1", status="draft")
        db.add(profile)
        db.flush()
        run = VLLMTestRun(profile_id=profile.id, mode="full", status="running", include_dataset=include,
            profile_fingerprint=profile_fingerprint(profile), metrics_json={"technical_checks_passed": True},
            lease_owner="synthetic-owner", lease_expires_at=utcnow() + timedelta(seconds=120))
        db.add(run)
        db.flush()
        for index in range(count):
            prompt = row_for(PREVIOUS if mixed and index == count - 1 else version)
            db.add(Analysis(source_system=validation.dataset_source(run.id), event_id=f"fixture-{index}", company_name="Fixture",
                analysis_purpose="test", src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="generic", waf_action="D",
                payload_ciphertext="synthetic-encrypted-payload", encryption_key_version="synthetic-v1", model_test_run_id=run.id,
                prompt_version=prompt.prompt_version, prompt_snapshot_ciphertext=prompt.prompt_snapshot_ciphertext,
                status="pending" if index == 0 else "completed"))
        db.commit()
        return run.id


def test_previous_dataset_calls_actual_frozen_coordinator(monkeypatch, synthetic_runtime):
    _, sessions, _, crypto = synthetic_runtime
    identifier = seed_dataset(sessions, version=PREVIOUS)
    calls = []
    monkeypatch.setattr(old_validation, "process_dataset_test", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(validation, "_process_v222_dataset_test", lambda *_a, **_k: pytest.fail("new coordinator"))
    with sessions() as db:
        run = db.get(VLLMTestRun, identifier)
        validation.process_dataset_test(db, crypto, run, verifier_confidence_threshold=.81)
        assert calls == [((db, crypto, run), {"verifier_confidence_threshold": .81})]


def prepare_dataset_checks(monkeypatch):
    monkeypatch.setattr(old_validation, "profile_request_check", lambda _engine, _run, _owner, monitor: monitor.check)
    monkeypatch.setattr(validation, "normalize_and_validate_profile_url", lambda profile, _targets: profile.base_url)
    monkeypatch.setattr(old_validation, "run_vllm_test", lambda *_a, **_k: pytest.fail("unexpected technical/model call"))


def test_new_dataset_resumes_150_cases_through_new_dispatch_and_old_lease_fences(monkeypatch, synthetic_runtime):
    _, sessions, settings, crypto = synthetic_runtime
    identifier = seed_dataset(sessions)
    prepare_dataset_checks(monkeypatch)
    calls = []
    def process(db, _crypto, row, _targets, threshold, *, request_check):
        request_check()
        worker._ensure_benchmark_owner(db)
        assert db.info["model_validation_claim"] == (identifier, "synthetic-owner")
        calls.append(threshold)
        row.status, row.completed_at = "completed", utcnow()
        db.commit()
    install_new_worker(monkeypatch, process)
    monkeypatch.setattr(old_validation, "process_dataset_test", lambda *_a, **_k: pytest.fail("old coordinator"))
    pool.run_model_test(sessions, crypto, settings, identifier, "synthetic-owner")
    assert calls == [.71]  # 149 already completed synthetic rows are not rerun.
    with sessions() as db:
        run = db.get(VLLMTestRun, identifier)
        assert run.status == "passed" and run.lease_owner is None
        assert db.get(VLLMProfile, run.profile_id).status == "verified"
        assert all(row.status == "completed" for row in db.scalars(select(Analysis)))


@pytest.mark.parametrize("changes", [{"count": 149}, {"mixed": True}])
def test_new_dataset_invalid_membership_fails_before_any_model_call(monkeypatch, synthetic_runtime, changes):
    _, sessions, settings, crypto = synthetic_runtime
    identifier = seed_dataset(sessions, **changes)
    prepare_dataset_checks(monkeypatch)
    install_new_worker(monkeypatch, lambda *_a, **_k: pytest.fail("invalid dataset model call"))
    pool.run_model_test(sessions, crypto, settings, identifier, "synthetic-owner")
    with sessions() as db:
        run = db.get(VLLMTestRun, identifier)
        assert run.status == "failed" and run.error_code == "model_validation_dataset_invalid"
        assert db.get(VLLMProfile, run.profile_id).status == "draft"


def test_technical_only_model_test_retains_exact_frozen_runner(monkeypatch, synthetic_runtime):
    _, sessions, settings, crypto = synthetic_runtime
    identifier = seed_dataset(sessions, count=0, include=False)
    calls = []
    monkeypatch.setattr(old_pool, "run_model_test", lambda *args: calls.append(args))
    monkeypatch.setattr(validation, "process_dataset_test", lambda *_a, **_k: pytest.fail("dataset call"))
    pool.run_model_test(sessions, crypto, settings, identifier, "synthetic-owner")
    assert calls == [(sessions, crypto, settings, identifier, "synthetic-owner")]


def test_model_test_wrong_owner_does_not_dispatch(monkeypatch, synthetic_runtime):
    _, sessions, settings, crypto = synthetic_runtime
    identifier = seed_dataset(sessions, count=0)
    monkeypatch.setattr(validation, "process_dataset_test", lambda *_a, **_k: pytest.fail("unowned dataset"))
    monkeypatch.setattr(old_pool, "run_model_test", lambda *_a: pytest.fail("unowned technical test"))
    pool.run_model_test(sessions, crypto, settings, identifier, "stale-owner")


def test_outer_pool_preserves_separate_purpose_claims_and_shared_stop(monkeypatch):
    calls, stop = [], Event()
    @contextmanager
    def sessions():
        yield object()
    class Future:
        def done(self): return True
        def result(self): return None
    class Pool:
        def __init__(self, **kwargs): self.options = kwargs
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def submit(self, function, *_args):
            assert function is pool.run_analysis
            if len(calls) == 2:
                stop.set()
            return Future()
    def claim(_db, _owner, seconds, **kwargs):
        calls.append((seconds, kwargs))
        return SimpleNamespace(id="fixture", lease_owner="fixture-owner", attempt_count=1)
    monkeypatch.setattr(pool, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(worker, "claim_next", claim)
    monkeypatch.setattr("app.services.runtime_status.heartbeat", lambda *_a: None)
    monkeypatch.setattr("app.services.official_evaluations.recover_pending_evaluations", lambda *_a, **_k: None)
    settings = SimpleNamespace(worker_role="analysis", job_lease_seconds=60, worker_poll_seconds=.01)
    pool.serve(sessions, None, settings, "fixture", stop=stop, install_signals=False)
    assert calls == [(60, {"purpose": "production", "enforce_limits": True}),
                     (60, {"purpose": "test", "enforce_limits": True})]


@pytest.mark.parametrize("role", ["analysis", "model_test", "both"])
def test_entrypoint_preserves_display_lane_and_shutdown_without_global_patches(monkeypatch, role):
    entry, calls, signals = worker_entrypoint_v222, [], []
    settings = SimpleNamespace(worker_role=role, database_url="synthetic", data_encryption_key="synthetic", encryption_key_version="synthetic")
    old_display = entry.serve_display
    class Thread:
        def __init__(self, *, target, args, name):
            assert target is old_display and name == "waf-evidence-display"
            self.stop = args[-1]
            calls.append(("thread", self.stop))
        def start(self): calls.append(("start", self.stop))
        def join(self):
            assert self.stop.is_set()
            calls.append(("join", self.stop))
    def signal(signum, handler):
        signals.append((signum, handler))
        return f"old-{signum}"
    def serve(_sessions, _crypto, _settings, _worker_id, *, stop, install_signals):
        assert not install_signals and not stop.is_set()
        calls.append(("serve", stop))
    monkeypatch.setattr(entry, "get_settings", lambda: settings)
    monkeypatch.setattr(entry, "build_engine", lambda _url: object())
    monkeypatch.setattr(entry, "build_session_factory", lambda _engine: object())
    monkeypatch.setattr(entry, "CryptoService", lambda *_a: object())
    monkeypatch.setattr(entry, "Thread", Thread)
    monkeypatch.setattr(entry.signal, "signal", signal)
    monkeypatch.setattr(entry, "serve_analyses", serve)
    entry.main()
    assert [name for name, _ in calls] == (["thread", "start", "serve", "join"] if role in {"analysis", "both"} else ["serve"])
    assert len({id(stop) for _, stop in calls}) == 1
    assert calls[0][1].is_set()
    assert len(signals) == 4
    assert signals[-2][1] == f"old-{signals[-2][0]}" and signals[-1][1] == f"old-{signals[-1][0]}"
