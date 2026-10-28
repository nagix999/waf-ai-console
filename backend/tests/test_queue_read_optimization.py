"""Queue coordination preserves ownership without reading losing large rows."""
from sqlalchemy import event

from app import worker
from test_concurrency import runtime, seed


def test_empty_queue_does_not_take_configuration_write_lock(runtime, monkeypatch):
    from app.services import concurrency
    def forbidden(*_):
        raise AssertionError("empty queue acquired writer lock")
    monkeypatch.setattr(concurrency, "lock_configuration", forbidden)
    with runtime[2]() as db:
        assert worker.claim_next(db, "synthetic-worker", 300, purpose="production", enforce_limits=True) is None


def test_only_successful_candidate_loads_large_columns(runtime):
    identifiers = seed(runtime, 5)
    selects = []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and "analyses.payload_ciphertext" in statement:
            selects.append(statement)
    event.listen(runtime[1], "before_cursor_execute", observe)
    try:
        with runtime[2]() as db:
            claimed = worker.claim_next(db, "synthetic-worker", 300, purpose="production", enforce_limits=True)
            assert claimed.id in identifiers
            assert claimed.lease_owner == "synthetic-worker"
            assert claimed.attempt_count == 1
            assert claimed.payload_ciphertext
    finally:
        event.remove(runtime[1], "before_cursor_execute", observe)
    assert len(selects) == 1
