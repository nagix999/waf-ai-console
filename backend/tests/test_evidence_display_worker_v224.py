"""Synthetic scheduling and file-backed slots; no real model or event input."""
import asyncio
from collections import deque
from contextlib import nullcontext
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from app.services import evidence_display_worker_v224 as lane
from app.services.concurrency import call_slot, release, server_key, try_reserve
from test_concurrency import configure, runtime


@pytest.mark.parametrize(("production", "test", "limits", "expected"), [
    (1, 1, {}, 1), (1, 1, {"synthetic": 20}, 1),
    (10, 10, {}, 1), (10, 1, {"synthetic": 1}, 1),
    (3, 1, {"synthetic": 8}, 3), (8, 8, {"synthetic": 3}, 3),
    (32, 32, {"synthetic": 64}, 4),
    (True, "20", {"synthetic": True}, 1), (0, None, None, 1),
])
def test_capacity_is_derived_and_defaults_never_implicitly_increase(production, test, limits, expected):
    config = SimpleNamespace(production=production, test=test, server_limits=limits)
    assert lane._capacity_from_configuration(config) == expected
    assert lane._capacity_from_configuration(None) == 1


def test_capacity_read_is_short_lived_and_does_not_write(runtime):
    from sqlalchemy import event
    statements = []
    configure(runtime, production=3, limit=2)
    def observed(_connection, _cursor, statement, *_args):
        statements.append(statement.lstrip().upper())
    event.listen(runtime[1], "before_cursor_execute", observed)
    try:
        assert lane.configured_capacity(runtime[2]) == 2
    finally:
        event.remove(runtime[1], "before_cursor_execute", observed)
    assert len(statements) == 1 and statements[0].startswith("SELECT")


def test_finished_jobs_wake_admission_without_an_extra_poll_delay(monkeypatch):
    stop = Event()
    queued, processed = deque(["first", "second"]), []
    factory = lambda: nullcontext(object())
    monkeypatch.setattr(lane, "configured_capacity", lambda _factory: 1)
    monkeypatch.setattr(lane.legacy, "claim_next", lambda _db, _owner: queued.popleft() if queued else None)
    def process(_factory, _crypto, identifier, _owner, *, stop):
        processed.append(identifier)
        if len(processed) == 2:
            stop.set()
    monkeypatch.setattr(lane.legacy, "process_job", process)
    settings = SimpleNamespace(worker_poll_seconds=60)
    thread = Thread(target=lane.serve, args=(factory, None, settings, "synthetic-worker", stop))
    thread.start()
    try:
        thread.join(2)
        assert not thread.is_alive()
        assert processed == ["first", "second"]
    finally:
        stop.set()
        thread.join(6)


@pytest.mark.parametrize("capacity", [1, 3])
def test_pool_is_bounded_claims_once_and_does_not_share_a_session(runtime, monkeypatch, capacity):
    settings, _, factory, crypto = runtime
    configure(runtime, production=capacity, limit=capacity)
    queued, owners, seen, errors = deque(range(7)), [], [], []
    guard, release_work, wave, stop = Lock(), Event(), Event(), Event()
    active = peak = 0

    def claim(db, owner):
        assert db is not factory
        if not queued:
            return None
        owners.append(owner)
        return str(queued.popleft())

    def process(selected_factory, selected_crypto, identifier, owner, *, stop):
        nonlocal active, peak
        assert selected_factory is factory and selected_crypto is crypto
        with guard:
            active += 1
            peak = max(peak, active)
            if active == capacity:
                wave.set()
        if not release_work.wait(3):
            errors.append("release timeout")
        with selected_factory() as session:
            assert session is not selected_factory
        with guard:
            seen.append(identifier)
            active -= 1
            if len(seen) == 7:
                stop.set()

    monkeypatch.setattr(lane.legacy, "claim_next", claim)
    monkeypatch.setattr(lane.legacy, "process_job", process)
    thread = Thread(target=lane.serve, args=(factory, crypto, settings, "synthetic-worker", stop))
    thread.start()
    try:
        assert wave.wait(3)
        assert len(owners) == capacity  # No preclaimed executor backlog.
    finally:
        release_work.set()
        thread.join(5)
        stop.set()
    assert not thread.is_alive() and not errors
    assert peak == capacity and len(seen) == len(set(seen)) == 7
    assert len(owners) == len(set(owners)) == 7


def test_shared_server_slot_is_still_respected_when_another_role_is_running(runtime, monkeypatch):
    settings, engine, factory, crypto = runtime
    configure(runtime, production=3, limit=2)
    profile = SimpleNamespace(base_url="http://10.0.0.10:8000/v1")
    reserved = try_reserve(engine, server_key(profile), 30)
    assert reserved
    queued, entered, processed = deque(["first", "second"]), [], []
    guard, one_entered, release_work, stop = Lock(), Event(), Event(), Event()
    peak = active = 0

    def claim(_db, _owner):
        return queued.popleft() if queued else None

    def process(_factory, _crypto, identifier, _owner, *, stop):
        async def request():
            nonlocal active, peak
            async with call_slot(engine, profile, timeout_seconds=4):
                with guard:
                    entered.append(identifier)
                    active += 1
                    peak = max(peak, active)
                    one_entered.set()
                while not release_work.is_set():
                    await asyncio.sleep(.005)
                with guard:
                    active -= 1
        try:
            asyncio.run(request())
        finally:
            with guard:
                processed.append(identifier)
                if len(processed) == 2:
                    stop.set()

    monkeypatch.setattr(lane.legacy, "claim_next", claim)
    monkeypatch.setattr(lane.legacy, "process_job", process)
    thread = Thread(target=lane.serve, args=(factory, crypto, settings, "synthetic-worker", stop))
    thread.start()
    try:
        assert one_entered.wait(3)
        assert len(entered) == 1
    finally:
        release_work.set()
        thread.join(6)
        stop.set()
        release(engine, reserved)
    assert not thread.is_alive() and len(processed) == 2 and peak == 1


def test_shutdown_prevents_new_admission_without_cancelling_active_job(monkeypatch):
    stop, entered, release_work, stopped = Event(), Event(), Event(), Event()
    claims, completed = [], []
    factory = lambda: nullcontext(object())
    monkeypatch.setattr(lane, "configured_capacity", lambda _factory: 1)
    monkeypatch.setattr(lane.legacy, "claim_next", lambda _db, owner: claims.append(owner) or "first")
    def process(_factory, _crypto, identifier, _owner, *, stop):
        entered.set()
        assert release_work.wait(3)
        completed.append(identifier)
    monkeypatch.setattr(lane.legacy, "process_job", process)
    settings = SimpleNamespace(worker_poll_seconds=.01)
    def serve():
        lane.serve(factory, None, settings, "synthetic-worker", stop)
        stopped.set()
    thread = Thread(target=serve)
    thread.start()
    try:
        assert entered.wait(3)
        stop.set()
        assert not stopped.wait(.05)
    finally:
        release_work.set()
        thread.join(4)
    assert stopped.is_set() and len(claims) == 1 and completed == ["first"]


def test_lowering_capacity_stops_new_claims_until_active_jobs_drain(monkeypatch):
    stop, wave, lowered, observed_lower = Event(), Event(), Event(), Event()
    releases = {str(index): Event() for index in range(4)}
    finished = {str(index): Event() for index in range(4)}
    queued, claims, guard = deque(releases), [], Lock()
    factory = lambda: nullcontext(object())
    def capacity(_factory):
        if lowered.is_set():
            observed_lower.set()
            return 1
        return 3
    def claim(_db, owner):
        if not queued:
            return None
        identifier = queued.popleft()
        claims.append(identifier)
        return identifier
    def process(_factory, _crypto, identifier, _owner, *, stop):
        with guard:
            if len(claims) == 3:
                wave.set()
        assert releases[identifier].wait(3)
        finished[identifier].set()
        if identifier == "3":
            stop.set()
    monkeypatch.setattr(lane, "configured_capacity", capacity)
    monkeypatch.setattr(lane, "CAPACITY_REFRESH_SECONDS", 0)
    monkeypatch.setattr(lane.legacy, "claim_next", claim)
    monkeypatch.setattr(lane.legacy, "process_job", process)
    thread = Thread(target=lane.serve, args=(factory, None, SimpleNamespace(worker_poll_seconds=.005),
                                          "synthetic-worker", stop))
    thread.start()
    try:
        assert wave.wait(3)
        lowered.set()
        assert observed_lower.wait(3)
        releases["0"].set()
        assert finished["0"].wait(3)
        assert not finished["3"].wait(.05)
        assert claims == ["0", "1", "2"]  # Two active jobs still exceed one.
        releases["1"].set()
        releases["2"].set()
        releases["3"].set()
        thread.join(4)
        assert claims == ["0", "1", "2", "3"]
    finally:
        for release_work in releases.values():
            release_work.set()
        stop.set()
        thread.join(4)
    assert not thread.is_alive() and finished["3"].is_set()


def test_coordination_failures_are_safe_and_lane_recovers(monkeypatch, caplog):
    import logging
    # Alembic fileConfig disables existing loggers in earlier migration tests.
    # Restore this logger for the assertion without changing application logging.
    monkeypatch.setattr(lane.logger, "disabled", False)
    caplog.set_level(logging.ERROR, logger=lane.logger.name)
    stop, calls = Event(), []
    private = "SYNTHETIC_PRIVATE_EXCEPTION_MUST_NOT_LEAVE"
    factory = lambda: nullcontext(object())
    def capacity(_factory):
        calls.append("capacity")
        if len(calls) == 1:
            raise ValueError(private)
        return 1
    def claim(_db, _owner):
        stop.set()
        return None
    monkeypatch.setattr(lane, "configured_capacity", capacity)
    monkeypatch.setattr(lane.legacy, "claim_next", claim)
    lane.serve(factory, None, SimpleNamespace(worker_poll_seconds=.001), "synthetic-worker", stop)
    assert len(calls) == 2 and "error_type=ValueError" in caplog.text and private not in caplog.text
