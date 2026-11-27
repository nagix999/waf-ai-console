"""Bounded independent display admission for the editor-only release.

The versioned processor owns snapshots, source checks, leases, egress and
shared LLM slots. This scheduler does not change verdict execution or public
result readiness, and it never shares a Session across threads.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
import uuid

from ..models import ConcurrencyConfiguration
from . import evidence_display_jobs_v4 as legacy


MAX_DISPLAY_JOBS = 4
CAPACITY_REFRESH_SECONDS = 1.0
logger = logging.getLogger("waf-worker")


def _positive_limit(value):
    return value if type(value) is int and value >= 1 else 1


def _capacity_from_configuration(config):
    if config is None:
        return 1
    purpose_limit = max(_positive_limit(config.production), _positive_limit(config.test))
    saved = config.server_limits if isinstance(config.server_limits, dict) else {}
    server_limit = max([1, *(_positive_limit(value) for value in saved.values())])
    # Neither analysis concurrency nor server concurrency alone authorizes a
    # larger pool. The processor still enforces each actual shared call slot.
    return min(MAX_DISPLAY_JOBS, purpose_limit, server_limit)


def configured_capacity(session_factory):
    with session_factory() as db:
        return _capacity_from_configuration(db.get(ConcurrencyConfiguration, 1))


def serve(session_factory, crypto, settings, worker_id, stop=None):
    """Immediately admit bounded claims, stop admission on shutdown and drain."""
    stop = stop if stop is not None else Event()
    completed = Event()
    jobs, capacity, next_capacity_check = set(), 1, 0.0
    poll_seconds = min(settings.worker_poll_seconds, 5)
    with ThreadPoolExecutor(max_workers=MAX_DISPLAY_JOBS, thread_name_prefix="waf-evidence-display") as pool:
        while not stop.is_set():
            completed.clear()
            for job in list(jobs):
                if not job.done():
                    continue
                jobs.remove(job)
                try:
                    job.result()
                except Exception as exc:
                    logger.error("display job failed error_type=%s", type(exc).__name__)
            try:
                if monotonic() >= next_capacity_check:
                    capacity = configured_capacity(session_factory)
                    next_capacity_check = monotonic() + CAPACITY_REFRESH_SECONDS
                while not stop.is_set() and len(jobs) < capacity:
                    owner = worker_id + ":display:" + str(uuid.uuid4())
                    with session_factory() as db:
                        identifier = legacy.claim_next(db, owner)
                    if identifier is None:
                        break
                    job = pool.submit(legacy.process_job, session_factory, crypto,
                                      identifier, owner, stop=stop)
                    jobs.add(job)
                    job.add_done_callback(lambda _job: completed.set())
            except Exception as exc:
                logger.error("display lane recovery failed error_type=%s", type(exc).__name__)
            if jobs:
                completed.wait(poll_seconds)
            else:
                stop.wait(poll_seconds)

