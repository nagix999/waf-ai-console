"""Additive v2.22 dataset dispatch; old datasets execute the frozen coordinator."""
import asyncio
from datetime import timedelta

from sqlalchemy import select

from ..models import Analysis, VLLMProfile, VLLMTestRun, utcnow
from . import model_validation_worker as legacy
from .internal_egress import allowed_targets_from_db
from .model_validation import DATASET_SIZE, dataset_source
from .prompt_snapshots import PromptSnapshotError
from .vllm_profiles import TargetNotAllowedError, normalize_and_validate_profile_url, profile_fingerprint
from .worker_runtime_v222 import RULES_VERSION, execution_rules, process_moduagent


def _dataset_uses_new_runtime(db, crypto, run_id):
    rows = db.scalars(select(Analysis).where(Analysis.model_test_run_id == run_id)
                      .order_by(Analysis.created_at, Analysis.id).limit(DATASET_SIZE + 1)).all()
    return any(execution_rules(row, crypto) == RULES_VERSION for row in rows)


def process_dataset_test(db, crypto, test_run, *, verifier_confidence_threshold=0.75):
    try:
        use_new = _dataset_uses_new_runtime(db, crypto, test_run.id)
    except PromptSnapshotError:
        use_new = True  # Record a fenced failure; never fall back on bad provenance.
    if not use_new:
        return legacy.process_dataset_test(db, crypto, test_run,
            verifier_confidence_threshold=verifier_confidence_threshold)
    return _process_v222_dataset_test(db, crypto, test_run,
        verifier_confidence_threshold=verifier_confidence_threshold)


def _process_v222_dataset_test(db, crypto, test_run, *, verifier_confidence_threshold):
    from ..worker import _abandon_running_runs, mark_failed
    run_id, owner = test_run.id, test_run.lease_owner
    if test_run.status != "running" or not owner:
        raise legacy.ModelTestLeaseLost()
    engine = db.get_bind()
    lease_seconds = db.info.get("model_test_lease_seconds", 900)
    db.info["model_validation_claim"] = (run_id, owner)
    try:
        db.commit()
        with legacy.heartbeat(engine, run_id, owner, lease_seconds) as monitor:
            check = legacy.profile_request_check(engine, run_id, owner, monitor)
            check()
            run = db.get(VLLMTestRun, run_id, populate_existing=True)
            profile = db.get(VLLMProfile, run.profile_id, populate_existing=True)
            cases = db.scalars(select(Analysis).where(Analysis.model_test_run_id == run_id)
                               .order_by(Analysis.created_at, Analysis.id)).all()
            if (len(cases) != DATASET_SIZE
                    or any(row.analysis_purpose != "test" or row.source_system != dataset_source(run_id) for row in cases)
                    or any(execution_rules(row, crypto) != RULES_VERSION for row in cases)):
                raise TargetNotAllowedError("model_validation_dataset_invalid")
            if not (run.metrics_json or {}).get("technical_checks_passed"):
                result = asyncio.run(legacy.run_vllm_test(profile, crypto, "full", egress_check=check, concurrency_engine=engine))
                check()
                legacy.require_owned_test(db, run_id, owner)
                run.checks_json = result.checks
                run.metrics_json = {**result.metrics, "technical_checks_passed": result.passed,
                                    "dataset_status": "running" if result.passed else "skipped"}
                db.commit()
                if not result.passed:
                    legacy.finish_failed(db, run_id, owner, result.error_code or "model_profile_checks_failed", skipped=True)
                    return
            for original in cases:
                check()
                legacy.require_owned_test(db, run_id, owner)
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
                except legacy.ModelTestLeaseLost:
                    raise
                except Exception as exc:
                    db.rollback()
                    legacy.require_owned_test(db, run_id, owner)
                    row = db.get(Analysis, original.id, populate_existing=True)
                    mark_failed(db, row, exc)
            check()
            legacy.require_owned_test(db, run_id, owner)
            run = db.get(VLLMTestRun, run_id, populate_existing=True)
            profile = db.get(VLLMProfile, run.profile_id, populate_existing=True)
            if profile.status == "disabled" or profile_fingerprint(profile) != run.profile_fingerprint:
                raise TargetNotAllowedError("model_validation_profile_changed")
            normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
            failures = db.scalar(select(Analysis.id).where(Analysis.model_test_run_id == run_id, Analysis.status != "completed").limit(1))
            if failures is not None:
                legacy.finish_failed(db, run_id, owner, "model_validation_case_failed")
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
    except legacy.ModelTestLeaseLost:
        db.rollback()
    except Exception as exc:
        db.rollback()
        code = str(exc) if isinstance(exc, TargetNotAllowedError) else "model_validation_failed"
        try:
            legacy.finish_failed(db, run_id, owner, code)
        except legacy.ModelTestLeaseLost:
            db.rollback()
    finally:
        db.info.pop("model_validation_claim", None)
