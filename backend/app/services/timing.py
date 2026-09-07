"""Safe elapsed-time projections for API responses; legacy steps stay unmeasured."""

from datetime import UTC, datetime

from ..models import AgentRun, AgentStep, Analysis


LEASE_EXPIRED_FAILURE = "worker_lease_expired"


def _utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes even for DateTime(timezone=True).
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def elapsed_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((_utc(end) - _utc(start)).total_seconds() * 1000))


def analysis_timings(analysis: Analysis, now: datetime | None = None) -> dict[str, int | None]:
    """Processing spans first claim to completion, including lease-recovery gaps."""
    now = now or datetime.now(UTC)
    terminal = analysis.status in {"completed", "failed"}
    end = analysis.completed_at if terminal else now
    queue_end = analysis.started_at or end
    return {
        "total_elapsed_ms": elapsed_ms(analysis.created_at, end),
        "queue_wait_ms": elapsed_ms(analysis.created_at, queue_end),
        "processing_duration_ms": elapsed_ms(analysis.started_at, end),
    }


def step_duration_ms(step: AgentStep, now: datetime | None = None) -> int | None:
    metadata = step.metadata_json or {}
    if metadata.get("timing_measured") is not True or metadata.get("timing_incomplete"):
        return None
    duration = metadata.get("duration_ms")
    if type(duration) is int and duration >= 0:
        return duration
    if step.status == "running":
        return elapsed_ms(step.started_at, now or datetime.now(UTC))
    return None


def run_duration_ms(run: AgentRun, now: datetime | None = None) -> int | None:
    if run.failure_id == LEASE_EXPIRED_FAILURE:
        return None
    if run.status == "running":
        return elapsed_ms(run.started_at, now or datetime.now(UTC))
    return elapsed_ms(run.started_at, run.completed_at)
