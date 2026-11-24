"""Bounded independent display admission for the v2.32 launcher.

The frozen display processor still owns snapshots, source checks, leases,
egress and shared LLM slots. This module only schedules separate jobs; it never
shares a Session across threads or changes public result-readiness semantics.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
import uuid

from ..models import ConcurrencyConfiguration
from . import evidence_display_jobs_v2 as legacy


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
    # A default limit of one remains one. Raising analysis concurrency alone
    # cannot imply permission to raise server concurrency, or vice versa.
    # This is only a local scheduler ceiling: the processor's shared call_slot
    # enforces each actual server limit across every role and process.
    return min(MAX_DISPLAY_JOBS, purpose_limit, server_limit)


def configured_capacity(session_factory):
    # A read-only, short-lived Session. Do not acquire the global admission
    # writer lock just to learn the current scheduling ceiling.
    with session_factory() as db:
        return _capacity_from_configuration(db.get(ConcurrencyConfiguration, 1))


def serve(session_factory, crypto, settings, worker_id, stop=None):
    """Admit at most the existing configured capacity, then drain on stop.

    Claims are submitted immediately, never placed in an unbounded executor
    queue. Reducing a limit stops new admission until active jobs drain; it
    never cancels a remote call or bypasses the frozen lease checks.
    """
    stop = stop if stop is not None else Event()
    completed = Event()
    jobs, capacity, next_capacity_check = set(), 1, 0.0
    poll_seconds = min(settings.worker_poll_seconds, 5)
    with ThreadPoolExecutor(max_workers=MAX_DISPLAY_JOBS, thread_name_prefix="waf-evidence-display") as pool:
        while not stop.is_set():
            # Clear before checking futures: a completion during scheduling
            # must wake the next iteration instead of adding a poll delay.
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
                # A DB or coordination error must not kill the lane, leak an
                # exception message, or fall back to uncoordinated dispatch.
                logger.error("display lane recovery failed error_type=%s", type(exc).__name__)
            # When full there is no claim/update loop. Configuration is cached
            # briefly, so idle polling does not add an extra writer transaction.
            if jobs:
                completed.wait(poll_seconds)
            else:
                stop.wait(poll_seconds)
