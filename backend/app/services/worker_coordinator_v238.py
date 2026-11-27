"""v2.38 coordinator preserving queued, retry and administration execution paths."""
import logging
import signal
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

from .concurrency import MAX_ANALYSES_PER_PURPOSE
from .parser_job_dispatch import pending_parser_job, run_parser_job
from .worker_concurrency_v238 import run_analysis, run_model_test

logger = logging.getLogger("waf-worker")


def serve(session_factory, crypto, settings, worker_id, *, stop=None, install_signals=True):
    from .. import worker
    stop = stop if stop is not None else Event()
    previous = {}
    if install_signals:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
    jobs, model_job, turn = set(), None, 0
    administration_turn = 0
    next_evaluation_check, evaluation_cursor = 0, None
    next_runtime_heartbeat = 0
    capacity = MAX_ANALYSES_PER_PURPOSE * 2
    try:
        with ThreadPoolExecutor(max_workers=capacity + 1, thread_name_prefix="waf-analysis") as pool:
            while not stop.is_set():
                if monotonic() >= next_runtime_heartbeat:
                    try:
                        from .runtime_status import heartbeat
                        heartbeat(session_factory, worker_id, settings.worker_role)
                    except Exception as exc:
                        logger.error("runtime heartbeat failed error_type=%s", type(exc).__name__)
                    next_runtime_heartbeat = monotonic() + 15
                if settings.worker_role in {"analysis", "both"} and monotonic() >= next_evaluation_check:
                    try:
                        from .official_evaluations import recover_pending_evaluations
                        evaluation_cursor = recover_pending_evaluations(session_factory, after=evaluation_cursor)
                    except Exception as exc:
                        logger.error("evaluation recovery failed error_type=%s", type(exc).__name__)
                    next_evaluation_check = monotonic() + 15
                for job in list(jobs):
                    if job.done():
                        jobs.remove(job)
                        try:
                            job.result()
                        except Exception as exc:
                            logger.error("worker job failed error_type=%s", type(exc).__name__)
                if model_job and model_job.done():
                    try:
                        model_job.result()
                    except Exception as exc:
                        logger.error("model test job failed error_type=%s", type(exc).__name__)
                    model_job = None
                dispatched = False
                if settings.worker_role in {"analysis", "both"} and len(jobs) < capacity:
                    for purpose in (("production", "test") if turn % 2 == 0 else ("test", "production")):
                        if stop.is_set() or len(jobs) >= capacity:
                            break
                        with session_factory() as db:
                            analysis = worker.claim_next(db, f"{worker_id}:{uuid.uuid4()}", settings.job_lease_seconds,
                                                         purpose=purpose, enforce_limits=True)
                            args = (analysis.id, analysis.lease_owner, analysis.attempt_count) if analysis is not None else None
                        if args:
                            jobs.add(pool.submit(run_analysis, session_factory, crypto, settings, *args))
                            dispatched = True
                    turn += 1
                if settings.worker_role in {"model_test", "both"} and model_job is None and not stop.is_set():
                    parser_ready = pending_parser_job(session_factory)
                    if parser_ready and administration_turn % 2:
                        model_job = pool.submit(run_parser_job, session_factory, crypto, settings, worker_id)
                        administration_turn += 1
                        continue
                    with session_factory() as db:
                        run = worker.claim_next_vllm_test(db, f"{worker_id}:{uuid.uuid4()}", settings.vllm_test_lease_seconds)
                        args = (run.id, run.lease_owner) if run else None
                    if args:
                        model_job = pool.submit(run_model_test, session_factory, crypto, settings, *args)
                        dispatched = True
                        administration_turn += 1
                    elif parser_ready:
                        model_job = pool.submit(run_parser_job, session_factory, crypto, settings, worker_id)
                        dispatched = True
                        administration_turn += 1
                if not dispatched:
                    stop.wait(settings.worker_poll_seconds)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

