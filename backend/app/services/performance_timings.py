"""Completed-only timing aggregates from scoped timestamps, without event reads.

The case CTE is supplied by the caller so paging never changes the population.
Only display-job status and its completion timestamp are needed for result
readiness; neither job metadata nor encrypted content is selected or decrypted.
Rows are streamed so only the numeric samples needed for exact percentiles are
retained, not every case and a second dictionary of presentation jobs.
"""
from collections import Counter

from sqlalchemy import and_, select

from ..models import EvidenceDisplayJob
from . import execution_latency as latency


def completed_timing_summary(db, cases):
    # The DB enforces one presentation job per analysis (unique analysis_id).
    # A failed analysis or retry has its own analysis id, never another row in
    # this join. Keep the whole filter scope in one read snapshot and avoid a
    # window sort plus a second materialization of the same population.
    query = select(cases.c.status, cases.c.created_at, cases.c.started_at, cases.c.completed_at,
        EvidenceDisplayJob.id.label("display_job_id"), EvidenceDisplayJob.status.label("display_status"),
        EvidenceDisplayJob.completed_at.label("display_completed_at")).select_from(cases.outerjoin(
            EvidenceDisplayJob, and_(cases.c.status == "completed",
                EvidenceDisplayJob.analysis_id == cases.c.analysis_id)))
    metrics = {name: latency._Metric() for name in (
        "decision_elapsed_ms", "analysis_queue_ms", "analysis_processing_ms", "result_ready_elapsed_ms")}
    statuses, count, completed = Counter(), 0, 0
    result = db.execute(query.execution_options(yield_per=1024))
    try:
        for row in result.mappings():
            count += 1
            statuses[row["status"]] += 1
            if row["status"] != "completed":
                continue
            completed += 1
            end = row["completed_at"]
            metrics["decision_elapsed_ms"].add(latency._elapsed(row["created_at"], end))
            metrics["analysis_queue_ms"].add(latency._elapsed(row["created_at"], row["started_at"]))
            metrics["analysis_processing_ms"].add(latency._elapsed(row["started_at"], end))
            ready_end = end
            if row["display_job_id"] is not None:
                job_end = latency._time(row["display_completed_at"]) if row["display_status"] in latency._TERMINAL else None
                analysis_end = latency._time(end)
                ready_end = max(analysis_end, job_end) if analysis_end is not None and job_end is not None else None
            metrics["result_ready_elapsed_ms"].add(latency._elapsed(row["created_at"], ready_end))
    finally:
        result.close()
    timings = {name: metric.present() for name, metric in metrics.items()}
    for metric in timings.values():
        metric["mean_ms"] = metric["reported_sum_ms"] / metric["recorded"] if metric["recorded"] else None
    return {"cases": count, "statuses": dict(sorted(statuses.items())),
        "completed_cases": completed, "completed_timings": timings}
