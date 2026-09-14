"""Bounded job pool for synchronous SQLAlchemy pipelines and async model I/O.

Each job owns its Session and lease. No Session/ORM object crosses threads.
"""
import logging
import signal
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread

from sqlalchemy import select, update

from ..models import Analysis, utcnow
from .concurrency import MAX_ANALYSES_PER_PURPOSE

logger = logging.getLogger("waf-worker")


def owned_conditions(identifier, owner, attempt):
    return (Analysis.id == identifier, Analysis.lease_owner == owner, Analysis.attempt_count == attempt,
            Analysis.status == "processing", Analysis.lease_expires_at > utcnow())


class AnalysisHeartbeat:
    def __init__(self, engine, identifier, owner, attempt, lease_seconds):
        self.engine, self.claim, self.lease_seconds = engine, (identifier, owner, attempt), lease_seconds
        self.stop, self.lost = Event(), Event()

    def renew(self):
        try:
            with self.engine.begin() as connection:
                result = connection.execute(update(Analysis).where(*owned_conditions(*self.claim)).values(
                    lease_expires_at=utcnow() + timedelta(seconds=self.lease_seconds), updated_at=Analysis.updated_at))
                if result.rowcount != 1:
                    self.lost.set()
        except Exception:
            self.lost.set()

    def check(self):
        from ..worker import WorkerExecutionError
        if self.lost.is_set():
            raise WorkerExecutionError("analysis_lease_lost")
        with self.engine.connect() as connection:
            if connection.scalar(select(Analysis.id).where(*owned_conditions(*self.claim))) is None:
                raise WorkerExecutionError("analysis_lease_lost")

    def pulse(self):
        while not self.stop.wait(min(10, self.lease_seconds / 3)):
            self.renew()
            if self.lost.is_set():
                return


@contextmanager
def analysis_heartbeat(engine, identifier, owner, attempt, lease_seconds):
    monitor = AnalysisHeartbeat(engine, identifier, owner, attempt, lease_seconds)
    monitor.renew()
    monitor.check()
    thread = Thread(target=monitor.pulse, name="analysis-heartbeat", daemon=True)
    thread.start()
    try:
        yield monitor
    finally:
        monitor.stop.set()
        thread.join()


def run_analysis(session_factory, crypto, settings, identifier, owner, attempt):
    from .. import worker
    with session_factory() as db:
        db.info["analysis_claim"] = (identifier, owner, attempt)
        try:
            with analysis_heartbeat(db.get_bind(), identifier, owner, attempt, settings.job_lease_seconds) as monitor:
                analysis = db.get(Analysis, identifier)
                monitor.check()
                if settings.agent_mode == "stub":
                    worker.process_stub(db, crypto, analysis)
                else:
                    worker.process_moduagent(db, crypto, analysis, "", settings.verifier_confidence_threshold,
                                             request_check=monitor.check)
            logger.info("analysis completed id=%s", identifier)
        except Exception as exc:
            logger.error("analysis failed id=%s error_type=%s", identifier, type(exc).__name__)
            db.rollback()
            current = db.get(Analysis, identifier)
            if current is not None:
                worker.mark_failed(db, current, exc)


def run_model_test(session_factory, crypto, settings, identifier, owner):
    from .. import worker
    from ..models import VLLMTestRun
    from .model_validation_worker import heartbeat
    with session_factory() as db:
        db.info["verifier_confidence_threshold"] = settings.verifier_confidence_threshold
        db.info["model_test_lease_seconds"] = settings.vllm_test_lease_seconds
        run = db.get(VLLMTestRun, identifier)
        if not run or run.lease_owner != owner or run.status != "running":
            return
        if run.include_dataset:  # Its existing coordinator owns a heartbeat.
            worker.process_vllm_test(db, crypto, run, "")
            return
        db.commit()
        with heartbeat(db.get_bind(), identifier, owner, settings.vllm_test_lease_seconds) as monitor:
            db.info["model_test_request_check"] = monitor.check
            db.info["model_validation_claim"] = (identifier, owner)
            monitor.check()
            worker.process_vllm_test(db, crypto, run, "")


def serve(session_factory, crypto, settings, worker_id, *, stop=None, install_signals=True):
    from .. import worker
    stop = stop if stop is not None else Event()
    previous = {}
    if install_signals:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
    jobs, model_job, turn = set(), None, 0
    capacity = MAX_ANALYSES_PER_PURPOSE * 2
    try:
        # The extra slot isolates candidate qualification from ordinary tests.
        with ThreadPoolExecutor(max_workers=capacity + 1, thread_name_prefix="waf-analysis") as pool:
            while not stop.is_set():
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
                    # Alternate lanes; a large test upload cannot occupy the
                    # production analysis allowance (server capacity is shared).
                    for purpose in (("production", "test") if turn % 2 == 0 else ("test", "production")):
                        if stop.is_set() or len(jobs) >= capacity:
                            break
                        with session_factory() as db:
                            analysis = worker.claim_next(db, f"{worker_id}:{uuid.uuid4()}", settings.job_lease_seconds,
                                                         purpose=purpose, enforce_limits=True)
                            if analysis is not None:
                                args = (analysis.id, analysis.lease_owner, analysis.attempt_count)
                            else:
                                args = None
                        if args:
                            jobs.add(pool.submit(run_analysis, session_factory, crypto, settings, *args))
                            dispatched = True
                    turn += 1
                if settings.worker_role in {"model_test", "both"} and model_job is None and not stop.is_set():
                    with session_factory() as db:
                        run = worker.claim_next_vllm_test(db, f"{worker_id}:{uuid.uuid4()}", settings.vllm_test_lease_seconds)
                        args = (run.id, run.lease_owner) if run else None
                    if args:
                        model_job = pool.submit(run_model_test, session_factory, crypto, settings, *args)
                        dispatched = True
                if not dispatched:
                    stop.wait(settings.worker_poll_seconds)
        # SIGTERM stops admission, then drains jobs until the runtime kills us.
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
