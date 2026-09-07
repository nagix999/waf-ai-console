"""Resumable candidate-profile evaluation on the model-test worker only."""
import asyncio
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Analysis, VLLMProfile, VLLMTestRun, utcnow
from .internal_egress import allowed_targets_from_db
from .model_validation import DATASET_SIZE, dataset_source
from .vllm_profiles import TargetNotAllowedError, normalize_and_validate_profile_url, profile_fingerprint, validate_profile_provider_settings
from .vllm_test_runner import run_vllm_test


class ModelTestLeaseLost(RuntimeError):
    def __init__(self):
        super().__init__("model_test_lease_lost")


def owned_conditions(run_id, owner):
    return (
        VLLMTestRun.id == run_id, VLLMTestRun.lease_owner == owner,
        VLLMTestRun.status == "running", VLLMTestRun.lease_expires_at > utcnow(),
    )


def require_owned_test(db: Session, run_id: str, owner: str) -> None:
    """Fence result writes. Caller commits promptly, never holds this over I/O."""
    result = db.execute(update(VLLMTestRun).where(*owned_conditions(run_id, owner))
                        .values(lease_owner=VLLMTestRun.lease_owner)
                        .execution_options(synchronize_session=False, autoflush=False))
    if result.rowcount != 1:
        raise ModelTestLeaseLost()


class TestHeartbeat:
    def __init__(self, engine, run_id: str, owner: str, lease_seconds: int = 900):
        self.engine, self.run_id, self.owner = engine, run_id, owner
        self.lease_seconds = lease_seconds
        self.stop = Event()
        self.lost = Event()

    def renew(self):
        try:
            with self.engine.begin() as connection:
                result = connection.execute(update(VLLMTestRun).where(*owned_conditions(self.run_id, self.owner))
                                            .values(lease_expires_at=utcnow() + timedelta(seconds=self.lease_seconds)))
                if result.rowcount != 1:
                    self.lost.set()
        except Exception:
            self.lost.set()

    def pulse(self):
        while not self.stop.wait(min(30, self.lease_seconds / 3)):
            self.renew()
            if self.lost.is_set():
                return

    def check(self):
        if self.lost.is_set():
            raise ModelTestLeaseLost()
        with self.engine.connect() as connection:
            if connection.scalar(select(VLLMTestRun.id).where(*owned_conditions(self.run_id, self.owner))) is None:
                raise ModelTestLeaseLost()


@contextmanager
def heartbeat(engine, run_id, owner, lease_seconds):
    monitor = TestHeartbeat(engine, run_id, owner, lease_seconds)
    monitor.renew()
    monitor.check()
    thread = Thread(target=monitor.pulse, name="model-test-heartbeat", daemon=True)
    thread.start()
    try:
        yield monitor
    finally:
        monitor.stop.set()
        thread.join()


def profile_request_check(engine, run_id: str, owner: str, monitor):
    def check():
        monitor.check()
        with Session(engine) as latest:
            run = latest.get(VLLMTestRun, run_id)
            profile = latest.get(VLLMProfile, run.profile_id)
            if profile is None or profile.status == "disabled" or profile_fingerprint(profile) != run.profile_fingerprint:
                raise TargetNotAllowedError("model_validation_profile_changed")
            normalized = normalize_and_validate_profile_url(profile, allowed_targets_from_db(latest) if profile.provider == "vllm" else "")
            if normalized != profile.base_url:
                raise TargetNotAllowedError("model_validation_profile_changed")
            validate_profile_provider_settings(profile, has_api_key=bool(profile.api_key_ciphertext))
    return check


def finish_failed(db, run_id, owner, code, *, skipped=False):
    from ..worker import _abandon_running_runs

    require_owned_test(db, run_id, owner)
    run = db.get(VLLMTestRun, run_id, populate_existing=True)
    rows = db.scalars(select(Analysis).where(Analysis.model_test_run_id == run_id, Analysis.status.in_(("pending", "processing")))).all()
    for row in rows:
        _abandon_running_runs(db, row.id)
        row.status = "failed"
        row.error_code = code
        row.error_message = "Model validation was not completed; event data is omitted."
        row.completed_at = utcnow()
        row.lease_owner = row.lease_expires_at = None
    run.status = "failed"
    run.error_code = code
    run.error_message = "Model validation failed; inspect the connection checks and individual synthetic analysis results."
    run.metrics_json = {**(run.metrics_json or {}), "dataset_status": "skipped" if skipped else "failed"}
    run.completed_at = utcnow()
    run.lease_owner = run.lease_expires_at = None
    db.commit()


def process_dataset_test(db, crypto, test_run, *, verifier_confidence_threshold=0.75):
    from ..worker import _abandon_running_runs, mark_failed, process_moduagent

    run_id, owner = test_run.id, test_run.lease_owner
    if test_run.status != "running" or not owner:
        raise ModelTestLeaseLost()
    engine = db.get_bind()
    lease_seconds = db.info.get("model_test_lease_seconds", 900)
    db.info["model_validation_claim"] = (run_id, owner)
    try:
        # Release any read transaction before using a separate heartbeat connection.
        db.commit()
        with heartbeat(engine, run_id, owner, lease_seconds) as monitor:
            check = profile_request_check(engine, run_id, owner, monitor)
            check()
            run = db.get(VLLMTestRun, run_id, populate_existing=True)
            profile = db.get(VLLMProfile, run.profile_id, populate_existing=True)
            cases = db.scalars(select(Analysis).where(Analysis.model_test_run_id == run_id).order_by(Analysis.created_at, Analysis.id)).all()
            if len(cases) != DATASET_SIZE or any(row.analysis_purpose != "test" or row.source_system != dataset_source(run_id) for row in cases):
                raise TargetNotAllowedError("model_validation_dataset_invalid")
            if not (run.metrics_json or {}).get("technical_checks_passed"):
                result = asyncio.run(run_vllm_test(profile, crypto, "full", egress_check=check))
                check()
                require_owned_test(db, run_id, owner)
                run.checks_json = result.checks
                run.metrics_json = {**result.metrics, "technical_checks_passed": result.passed, "dataset_status": "running" if result.passed else "skipped"}
                db.commit()
                if not result.passed:
                    finish_failed(db, run_id, owner, result.error_code or "model_profile_checks_failed", skipped=True)
                    return
            for original in cases:
                check()
                require_owned_test(db, run_id, owner)
                row = db.get(Analysis, original.id, populate_existing=True)
                if row.status in {"completed", "failed"}:
                    db.commit()
                    continue
                _abandon_running_runs(db, row.id)
                row.status = "processing"
                row.started_at = row.started_at or utcnow()
                row.attempt_count += 1
                row.lease_owner = owner
                row.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
                db.commit()
                try:
                    process_moduagent(db, crypto, row, "", verifier_confidence_threshold, request_check=check)
                except ModelTestLeaseLost:
                    raise
                except Exception as exc:
                    db.rollback()
                    require_owned_test(db, run_id, owner)
                    row = db.get(Analysis, original.id, populate_existing=True)
                    mark_failed(db, row, exc)
            check()
            require_owned_test(db, run_id, owner)
            run = db.get(VLLMTestRun, run_id, populate_existing=True)
            profile = db.get(VLLMProfile, run.profile_id, populate_existing=True)
            # Recheck after acquiring the write lock; stale success cannot enable
            # a profile edited or disabled between the network return and commit.
            if profile.status == "disabled" or profile_fingerprint(profile) != run.profile_fingerprint:
                raise TargetNotAllowedError("model_validation_profile_changed")
            normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
            failures = db.scalar(select(Analysis.id).where(Analysis.model_test_run_id == run_id, Analysis.status != "completed").limit(1))
            if failures is not None:
                finish_failed(db, run_id, owner, "model_validation_case_failed")
                return
            run.status = "passed"
            run.metrics_json = {**(run.metrics_json or {}), "dataset_status": "completed"}
            run.completed_at = utcnow()
            run.error_code = run.error_message = None
            run.lease_owner = run.lease_expires_at = None
            profile.last_verified_at = run.completed_at
            if profile.status == "draft":
                profile.status = "verified"
            db.commit()
    except ModelTestLeaseLost:
        # The new owner alone may resume/finish. Never overwrite its status.
        db.rollback()
    except Exception as exc:
        db.rollback()
        code = str(exc) if isinstance(exc, TargetNotAllowedError) else "model_validation_failed"
        try:
            finish_failed(db, run_id, owner, code)
        except ModelTestLeaseLost:
            db.rollback()
    finally:
        db.info.pop("model_validation_claim", None)
