"""Telemetry only: never decrypt or select WAF content or agent input/output."""
from datetime import UTC, timedelta
from math import ceil
from sqlalchemy import func, select, text
from ..models import Analysis, ChangeEvent, ServiceApiKey, WorkerHeartbeat, utcnow
from .analysis import AnalysisIngestError
from .production_configurations import current_configuration


def heartbeat(session_factory, worker_id, kind):
    with session_factory() as db:
        row = db.get(WorkerHeartbeat, worker_id)
        if row is None:
            db.add(WorkerHeartbeat(worker_id=worker_id, worker_kind=kind, observed_at=utcnow()))
        else:
            row.observed_at = utcnow()
        db.commit()


def status_document(db, crypto, settings, window, purpose="all", service_api_key_id=None):
    hours = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}[window]
    now = utcnow()
    start = now - timedelta(hours=hours)
    scope = []
    if purpose != "all":
        scope.append(Analysis.analysis_purpose == purpose)
    if service_api_key_id:
        key = db.get(ServiceApiKey, service_api_key_id)
        if not key or key.deleted_at is not None or (purpose != "all" and key.purpose != purpose):
            raise AnalysisIngestError("service_api_key_not_found", 404)
        scope.append(Analysis.service_api_key_id == key.id)
    condition = (*scope, Analysis.created_at >= start, Analysis.created_at <= now)
    counts = dict(db.execute(select(Analysis.status, func.count()).where(*condition).group_by(Analysis.status)).all())
    queue = dict(db.execute(select(Analysis.status, func.count()).where(*scope, Analysis.status.in_(["pending", "processing"])).group_by(Analysis.status)).all())
    # Only timestamps/status/counters are selected, never result_json or raw input.
    rows = db.execute(select(Analysis.created_at, Analysis.completed_at, Analysis.status, Analysis.attempt_count)
        .where(*condition)).all()
    buckets = [{"from": start + timedelta(hours=hours * i / 12), "completed": 0, "failed": 0, "pending": 0, "processing": 0, "retry": 0} for i in range(12)]
    latencies = []
    retries = 0
    for created, completed, state, attempts in rows:
        created = created.replace(tzinfo=UTC) if created.tzinfo is None else created
        index = min(11, max(0, int((created - start).total_seconds() / (hours * 3600 / 12))))
        if state in buckets[index]:
            buckets[index][state] += 1
        if attempts > 1:
            retries += 1
            buckets[index]["retry"] += 1
        if completed is not None and state in ("completed", "failed"):
            completed = completed.replace(tzinfo=UTC) if completed.tzinfo is None else completed
            latencies.append(max(0, (completed - created).total_seconds() * 1000))
    latencies.sort()
    workers = list(db.scalars(select(WorkerHeartbeat)))
    health = {"api": {"status": "healthy", "observed_at": now, "basis": "request_succeeded"}}
    for name, kinds in (("analysis_worker", ("analysis", "both")), ("model_worker", ("model_test", "both"))):
        seen = [r.observed_at.replace(tzinfo=UTC) if r.observed_at.tzinfo is None else r.observed_at for r in workers if r.worker_kind in kinds]
        recent = max(seen) if seen else None
        health[name] = {"status": "unknown" if recent is None else "healthy" if now - recent <= timedelta(seconds=45) else "stale",
            "observed_at": recent, "basis": "worker_loop_heartbeat", "freshness_seconds": 45}
    health["assigned_models"] = {"status": "unknown", "observed_at": None, "basis": "no_readiness_probe"}
    recent = db.execute(select(Analysis.id, Analysis.status, Analysis.verdict, Analysis.error_code,
        Analysis.analysis_purpose, Analysis.created_at, Analysis.completed_at).where(*condition)
        .order_by(Analysis.created_at.desc()).limit(20)).mappings().all()
    failures = db.execute(select(Analysis.error_code, func.count()).where(*condition, Analysis.status == "failed").group_by(Analysis.error_code)).all()
    return {"window": window, "purpose": purpose, "service_api_key_id": service_api_key_id, "updated_at": now, "production_configuration": current_configuration(db, crypto, settings),
        "health": health, "queue": {k: queue.get(k, 0) for k in ("pending", "processing")},
        "outcome_summary": {k: counts.get(k, 0) for k in ("completed", "failed", "pending", "processing")},
        "request_volume_series": buckets, "request_count": len(rows), "retry_count": retries,
        "retry_basis": "queue_claim_attempts", "failure_types": [{"code": c, "count": n} for c, n in failures],
        "latency_summary": {**{f"p{p}": latencies[max(0, ceil(len(latencies) * p / 100) - 1)] if latencies else None for p in (50, 95, 99)},
            "sample_count": len(latencies), "basis": "enqueue_to_terminal_ms"}, "recent_analyses": [dict(r) for r in recent]}


def deployment_document(db, settings):
    # No infrastructure control, no guessed image/commit/deployment timestamp.
    from .. import __version__
    try:
        revisions = list(db.scalars(text("SELECT version_num FROM alembic_version")))
    except Exception:
        revisions = []
    return {"api_version": __version__, "agent_mode": settings.agent_mode, "db_revisions": revisions,
        "frontend_image": None, "api_image": None, "worker_image": None, "git_commit": None, "deployed_at": None}
