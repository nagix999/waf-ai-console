"""Explicit failed-item batches; no model I/O, no original observation writes."""
from collections import Counter
import hashlib
import json
import uuid

from sqlalchemy import select
from ..models import AccessAudit, Analysis, TestRun, TestRunItem
from ..retry_schemas import RetryRequest
from .analysis_retries import RetryError, eligibility, enqueue_retry
from .test_attempts import test_attempts
from .test_runs import read_snapshot, write_lock


def get_run(db, run_id):
    run = db.get(TestRun, run_id)
    if run is None:
        raise RetryError("test_run_not_found", 404)
    return run


def preview_test_retries(db, crypto, settings, run_id):
    read_snapshot(db)
    run = get_run(db, run_id)
    _nodes, current = test_attempts(run.id)
    failed = list(db.scalars(select(current.c.analysis_id).join(TestRunItem, TestRunItem.id == current.c.item_id).join(
        Analysis, Analysis.id == current.c.analysis_id).where(
        TestRunItem.ingest_status == "accepted", Analysis.status == "failed").order_by(TestRunItem.row_number)))
    eligible, blocked = [], Counter()
    external = False
    for identifier in failed:
        info = eligibility(db, crypto, identifier, settings.agent_mode)
        if info.allowed:
            eligible.append(identifier)
            external |= "openai" in {info.provider, info.verifier_provider, info.evidence_editor_provider}
        else:
            blocked[info.blocked_reason] += 1
    return {"test_run_id": run.id, "failed_count": len(failed), "eligible_ids": eligible,
            "eligible_count": len(eligible), "blocked_counts": dict(blocked), "external_calls": external}


def enqueue_test_retries(db, crypto, settings, run_id, payload, actor):
    write_lock(db)
    run = get_run(db, run_id)
    identifiers = sorted(set(payload.analysis_ids))
    if len(identifiers) != len(payload.analysis_ids):
        raise RetryError("duplicate_retry_target", 422)
    nodes, current = test_attempts(run_id)
    members = set(db.scalars(select(nodes.c.analysis_id).join(TestRunItem, TestRunItem.id == nodes.c.item_id).where(
        TestRunItem.ingest_status == "accepted", nodes.c.analysis_id.in_(identifiers))))
    if members != set(identifiers):
        raise RetryError("retry_target_not_in_test", 422)
    marker = str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-retry:{actor}:{run_id}:{payload.idempotency_key}"))
    digest = hashlib.sha256(json.dumps(identifiers).encode()).hexdigest()
    binding = f"{run_id}:{digest}"
    previous = db.get(AccessAudit, marker)
    prefix = f"test-retry:{marker}:"
    if previous:
        if previous.resource_id != binding:
            raise RetryError("retry_idempotency_conflict")
        # Never reevaluate failed descendants or previously blocked rows on a
        # lost-response replay. It is the same admitted batch, even days later.
        admitted = list(db.scalars(select(Analysis.id).where(Analysis.retry_idempotency_key.startswith(prefix))))
        return {"test_run_id": run.id, "duplicate": True, "requested": len(identifiers),
                "enqueued": len(admitted), "skipped": len(identifiers) - len(admitted), "analysis_ids": admitted,
                "blocked_counts": {}}
    latest = set(db.scalars(select(current.c.analysis_id)))
    admitted, blocked = [], Counter()
    for identifier in identifiers:
        if identifier not in latest:
            blocked["retry_already_created"] += 1
            continue
        request = RetryRequest(idempotency_key=prefix + identifier, cost_acknowledged=True)
        try:
            result = enqueue_retry(db, crypto, settings, identifier, request, actor, commit=False)
        except RetryError as exc:
            blocked[exc.code] += 1
            continue
        admitted.append(result.analysis_id)
    db.add(AccessAudit(id=marker, actor_kind="admin_session", actor_id=actor,
        action="retry_failed_test_items", resource_type="test_run_retry_batch", resource_id=binding))
    db.commit()
    return {"test_run_id": run.id, "duplicate": False, "requested": len(identifiers),
            "enqueued": len(admitted), "skipped": sum(blocked.values()), "analysis_ids": admitted,
            "blocked_counts": dict(blocked)}
