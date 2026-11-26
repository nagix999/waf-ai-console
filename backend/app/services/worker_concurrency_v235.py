"""Versioned outer coordinator; frozen claims, fences and old executions remain."""
import logging

from ..models import Analysis
from . import worker_concurrency as legacy
from . import worker_concurrency_v234 as previous
from .prompt_snapshots import PromptSnapshotError
from .worker_runtime_v235 import RULES_VERSION, execution_rules


logger = logging.getLogger("waf-worker")


def run_analysis(session_factory, crypto, settings, identifier, owner, attempt):
    if settings.agent_mode == "stub":
        return previous.run_analysis(session_factory, crypto, settings, identifier, owner, attempt)
    # This preliminary read never writes or dispatches a model. Invalid new
    # provenance is handled inside the normal heartbeat/failure fence below.
    try:
        with session_factory() as db:
            analysis = db.get(Analysis, identifier)
            use_new = analysis is not None and execution_rules(analysis, crypto) == RULES_VERSION
    except PromptSnapshotError:
        use_new = True
    if not use_new:
        return previous.run_analysis(session_factory, crypto, settings, identifier, owner, attempt)
    from .. import worker
    from .worker_runtime_v235 import process_moduagent
    with session_factory() as db:
        db.info["analysis_claim"] = (identifier, owner, attempt)
        try:
            with legacy.analysis_heartbeat(db.get_bind(), identifier, owner, attempt, settings.job_lease_seconds) as monitor:
                analysis = db.get(Analysis, identifier)
                monitor.check()
                process_moduagent(db, crypto, analysis, "", settings.verifier_confidence_threshold,
                                 request_check=monitor.check)
            logger.info("analysis completed id=%s", identifier)
        except Exception as exc:
            logger.error("analysis failed id=%s error_type=%s", identifier, type(exc).__name__)
            db.rollback()
            current = db.get(Analysis, identifier)
            if current is not None:
                worker.mark_failed(db, current, exc)
    # Storage failure must not rewrite an already completed analysis.
    try:
        from .official_evaluations import finalize_for_analysis
        finalize_for_analysis(session_factory, identifier)
    except Exception as exc:
        logger.error("evaluation save failed error_type=%s", type(exc).__name__)


def run_model_test(session_factory, crypto, settings, identifier, owner):
    from ..models import VLLMTestRun
    from .model_validation_worker_v235 import process_dataset_test
    with session_factory() as db:
        run = db.get(VLLMTestRun, identifier)
        if not run or run.lease_owner != owner or run.status != "running":
            return
        if run.include_dataset:
            db.info["verifier_confidence_threshold"] = settings.verifier_confidence_threshold
            db.info["model_test_lease_seconds"] = settings.vllm_test_lease_seconds
            process_dataset_test(db, crypto, run, verifier_confidence_threshold=settings.verifier_confidence_threshold)
            return
    # Technical-only tests retain the exact old lease, check and call path.
    return previous.run_model_test(session_factory, crypto, settings, identifier, owner)
