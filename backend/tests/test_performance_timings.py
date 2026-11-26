"""Streaming aggregate parity against the established timestamp semantics."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import column, table

from app.services.execution_latency import summarize_execution_latency
from app.services.performance_timings import completed_timing_summary


CASES = table("synthetic_cases", *(column(name) for name in (
    "analysis_id", "status", "created_at", "started_at", "completed_at")))
NOW = datetime(2026, 11, 26, tzinfo=UTC)


class StreamingResult:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def mappings(self):
        # Deliberately only an iterator: bulk .all()/.fetchall() is unavailable.
        yield from self.rows

    def close(self):
        self.closed = True


class StreamingDb:
    def __init__(self, records):
        self.statements = []
        rows = ({**{key: record.get(key) for key in ("status", "created_at", "started_at", "completed_at")},
                 "display_job_id": f"job-{index}" if record.get("display_job") is not None else None,
                 "display_status": (record.get("display_job") or {}).get("status"),
                 "display_completed_at": (record.get("display_job") or {}).get("completed_at")}
                for index, record in enumerate(records))
        self.result = StreamingResult(rows)

    def execute(self, statement):
        self.statements.append(statement)
        return self.result


def expected(records):
    completed = [record for record in records if record["status"] == "completed"]
    metrics = summarize_execution_latency(completed)["timings"]
    for metric in metrics.values():
        metric["mean_ms"] = metric["reported_sum_ms"] / metric["recorded"] if metric["recorded"] else None
    return metrics


def test_streams_one_projection_and_keeps_exact_legacy_boundaries():
    records = []
    for index in range(137):
        record = {"status": "completed", "created_at": NOW,
                  "started_at": NOW + timedelta(microseconds=index * 34567),
                  "completed_at": NOW + timedelta(microseconds=index * 312345)}
        if index % 11 == 0:
            record["started_at"] = None
        if index % 13 == 0:
            record["completed_at"] = None
        if index % 17 == 0:
            record["created_at"] = NOW + timedelta(days=1)  # Reversed timestamps are missing.
        if index % 19 == 0:
            record["completed_at"] = NOW + timedelta(days=8)  # Out of the allowed duration range.
        if index % 3 == 0:
            record["display_job"] = {"status": ("completed", "failed", "pending", "running", "skipped", "canceled")[index % 6],
                                     "completed_at": NOW + timedelta(seconds=index + 12)}
        if index % 5 == 0:
            record["status"] = ("failed", "pending", "processing", "canceled")[index % 4]
        records.append(record)
    before = deepcopy(records)
    db = StreamingDb(records)
    summary = completed_timing_summary(db, CASES)
    assert summary["completed_timings"] == expected(records)
    assert summary["cases"] == len(records)
    assert sum(summary["statuses"].values()) == len(records)
    assert summary["completed_cases"] == sum(record["status"] == "completed" for record in records)
    assert records == before
    assert len(db.statements) == 1
    statement = db.statements[0]
    assert statement.get_execution_options()["yield_per"] == 1024
    assert {item.key for item in statement.selected_columns} == {
        "status", "created_at", "started_at", "completed_at", "display_job_id", "display_status", "display_completed_at"}
    assert db.result.closed


@pytest.mark.parametrize("records", [[], [{"status": "pending"}], [{"status": "completed"}], [
    {"status": "completed", "created_at": NOW, "started_at": NOW, "completed_at": NOW}]])
def test_empty_missing_and_real_zero_samples_remain_distinct(records):
    summary = completed_timing_summary(StreamingDb(records), CASES)
    assert summary["completed_timings"] == expected(records)


def test_stream_is_closed_even_when_reading_fails():
    db = StreamingDb([])

    def fail():
        raise RuntimeError("synthetic read interruption")
        yield  # pragma: no cover

    db.result.rows = fail()
    with pytest.raises(RuntimeError, match="synthetic read interruption"):
        completed_timing_summary(db, CASES)
    assert db.result.closed
