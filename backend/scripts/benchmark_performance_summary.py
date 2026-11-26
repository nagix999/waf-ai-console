"""Offline completed-timing benchmark, using disposable synthetic SQLite only.

Run from backend: python scripts/benchmark_performance_summary.py
  --sizes 1000 10000 100000 --repetitions 2
For elapsed time without memory instrumentation, add --wall-time-only.
Add --materialized-scope to retain the production scope CTE materialization.

No service settings, .env, application startup, model, encryption key or network
is loaded. Every database is freshly created in a TemporaryDirectory. Each
measurement uses a separate process and a read-only SQLite connection. The
frozen baseline is the implementation before the streaming aggregate change.
Default elapsed times include tracemalloc overhead; RSS includes imports.
This benchmarks the common timestamp aggregate, not HTTP/filter query latency.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import gc
import json
from math import ceil
from pathlib import Path
import resource
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, event, func, select
from sqlalchemy.orm import Session

from app.models import EvidenceDisplayJob
from app.services import execution_latency as latency
from app.services.performance_timings import completed_timing_summary


def frozen_baseline(db, cases):
    """Frozen 2026-11-26 pre-optimization completed_timing_summary."""
    ranked = select(EvidenceDisplayJob.id, EvidenceDisplayJob.analysis_id,
        func.row_number().over(partition_by=EvidenceDisplayJob.analysis_id,
            order_by=(EvidenceDisplayJob.created_at.desc(), EvidenceDisplayJob.id.desc())).label("position")).join(
                cases, cases.c.analysis_id == EvidenceDisplayJob.analysis_id).where(
                    cases.c.status == "completed").subquery()
    jobs = select(EvidenceDisplayJob.analysis_id, EvidenceDisplayJob.status,
        EvidenceDisplayJob.created_at, EvidenceDisplayJob.started_at, EvidenceDisplayJob.completed_at).join(
            ranked, ranked.c.id == EvidenceDisplayJob.id).where(ranked.c.position == 1)
    displays = {row["analysis_id"]: dict(row) for row in db.execute(jobs).mappings()}
    rows = db.execute(select(cases)).mappings().all()
    completed = [dict(row) | {"display_job": displays.get(row["analysis_id"])}
        for row in rows if row["status"] == "completed"]
    timings = latency.summarize_execution_latency(completed)["timings"]
    for metric in timings.values():
        metric["mean_ms"] = metric["reported_sum_ms"] / metric["recorded"] if metric["recorded"] else None
    return {"cases": len(rows), "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
        "completed_cases": len(completed), "completed_timings": timings}


MARKER = "disposable-completed-timing-synthetic-v1"
METRIC_NAMES = ("decision_elapsed_ms", "analysis_queue_ms", "analysis_processing_ms", "result_ready_elapsed_ms")
metadata = MetaData()
case_table = Table("benchmark_cases", metadata,
    Column("analysis_id", String, primary_key=True), Column("status", String, nullable=False),
    Column("created_at", DateTime), Column("started_at", DateTime), Column("completed_at", DateTime))
# Only the timestamp projection consumed by the service exists in this fixture.
# There is no payload, result JSON, ciphertext or other event field to read.
job_table = Table("evidence_display_jobs", metadata,
    Column("id", String, primary_key=True), Column("analysis_id", String, unique=True, nullable=False),
    Column("status", String, nullable=False), Column("created_at", DateTime),
    Column("started_at", DateTime), Column("completed_at", DateTime))
marker_table = Table("benchmark_marker", metadata, Column("marker", String, primary_key=True))


def synthetic_case(index):
    """Varied status, missing values, subsecond times and unfinished editors."""
    at = datetime(2026, 1, 1) + timedelta(seconds=index)
    status = "pending" if index % 10 == 8 else "failed" if index % 10 == 9 else "completed"
    queue = (index % 97) * 125
    processing = 1000 + (index % 1009) * 250
    start = at + timedelta(milliseconds=queue) if index % 31 else None
    end = at + timedelta(milliseconds=queue + processing) if status != "pending" and index % 43 else None
    row = {"analysis_id": f"synthetic-{index}", "status": status,
        "created_at": at, "started_at": start, "completed_at": end}
    job = None
    if index % 3 == 0:
        state = "running" if index % 17 == 0 else "failed" if index % 19 == 0 else "completed"
        job = {"id": f"display-{index}", "analysis_id": row["analysis_id"], "status": state,
            "created_at": at, "started_at": end,
            "completed_at": end + timedelta(milliseconds=500 + (index % 111) * 125) if end and state != "running" else None}
    return row, job


def expected_summary(count):
    """Independent integer oracle, without calling the service or latency code."""
    statuses = Counter()
    values = {name: [] for name in METRIC_NAMES}
    completed = 0
    for index in range(count):
        # Keep this arithmetic separate from the datetime fixture: this also
        # catches SQL timestamp conversion and millisecond rounding changes.
        status = "pending" if index % 10 == 8 else "failed" if index % 10 == 9 else "completed"
        statuses[status] += 1
        if status != "completed":
            continue
        completed += 1
        queue = (index % 97) * 125
        elapsed = queue + 1000 + (index % 1009) * 250
        if index % 31:
            values["analysis_queue_ms"].append(queue)
        if index % 43:
            values["decision_elapsed_ms"].append(elapsed)
            if index % 31:
                values["analysis_processing_ms"].append(elapsed - queue)
            if index % 3:
                values["result_ready_elapsed_ms"].append(elapsed)
            elif index % 17:
                values["result_ready_elapsed_ms"].append(elapsed + 500 + (index % 111) * 125)
    result = {}
    for name, raw in values.items():
        ordered = sorted(raw)
        total = sum(ordered)
        size = len(ordered)
        result[name] = {"recorded": size, "unrecorded": completed - size, "reported_sum_ms": total,
            "min_ms": ordered[0] if size else None, "p50_ms": median(ordered) if size else None,
            "p95_ms": ordered[ceil(.95 * size) - 1] if size else None,
            "p99_ms": ordered[ceil(.99 * size) - 1] if size else None,
            "max_ms": ordered[-1] if size else None, "mean_ms": total / size if size else None}
    return {"cases": count, "statuses": dict(sorted(statuses.items())),
        "completed_cases": completed, "completed_timings": result}


def seed_database(path, count):
    if path.exists():
        raise ValueError("Refusing to overwrite any existing database")
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(marker_table.insert(), {"marker": MARKER})
        for first in range(0, count, 1000):
            generated = [synthetic_case(index) for index in range(first, min(first + 1000, count))]
            connection.execute(case_table.insert(), [row for row, _ in generated])
            jobs = [job for _, job in generated if job]
            if jobs:
                connection.execute(job_table.insert(), jobs)
    engine.dispose()


def measure_database(path, mode, *, measure_memory=True, materialized_scope=False):
    """Read-only worker. Refuses databases without the synthetic marker."""
    engine = create_engine(f"sqlite+pysqlite:///file:{path}?mode=ro&uri=true")
    queries = []
    with Session(engine) as db:
        db.connection().exec_driver_sql("PRAGMA query_only=ON")
        if db.scalar(select(marker_table.c.marker)) != MARKER:
            raise ValueError("Synthetic benchmark marker required")
        cases = select(case_table).cte("performance_cases")
        if materialized_scope:
            cases = cases.prefix_with("MATERIALIZED", dialect="sqlite")
        def inspect(_connection, cursor, statement, _parameters, _context, _many):
            if not statement.lstrip().upper().startswith(("SELECT", "WITH")):
                raise AssertionError("Aggregate attempted a non-read operation")
            queries.append(1)
            forbidden = {"payload", "payload_ciphertext", "result_json", "metadata_json", "input_ciphertext", "output_ciphertext"}
            if cursor.description and forbidden.intersection(column[0] for column in cursor.description):
                raise AssertionError("Aggregate projected private event content")
        event.listen(engine, "after_cursor_execute", inspect)
        gc.collect()
        if measure_memory:
            tracemalloc.start()
        started = time.perf_counter()
        function = frozen_baseline if mode == "baseline" else completed_timing_summary
        try:
            summary = function(db, cases)
            elapsed_ms = (time.perf_counter() - started) * 1000
            peak = tracemalloc.get_traced_memory()[1] if measure_memory else None
        finally:
            if measure_memory:
                tracemalloc.stop()
            event.remove(engine, "after_cursor_execute", inspect)
        count = summary["cases"]
    engine.dispose()
    expected = expected_summary(count)
    if summary != expected:
        differences = {name: {"observed": summary["completed_timings"][name],
            "expected": expected["completed_timings"][name]} for name in METRIC_NAMES
            if summary["completed_timings"][name] != expected["completed_timings"][name]}
        raise AssertionError("Timing aggregate differs from independent synthetic oracle: " + json.dumps(differences))
    return {"mode": mode, "cases": count, "elapsed_ms": round(elapsed_ms, 3),
        "python_peak_mib": round(peak / 1024**2, 3) if peak is not None else None,
        "process_peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
        "sql_count": len(queries), "oracle_matches": True,
        "measurement": "tracemalloc" if measure_memory else "wall_time_only",
        "materialized_scope": materialized_scope,
        "completed_cases": summary["completed_cases"], "completed_timings": summary["completed_timings"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--modes", choices=("baseline", "current"), nargs="+", default=["baseline", "current"])
    parser.add_argument("--wall-time-only", action="store_true", help="Disable memory tracing when comparing elapsed time")
    parser.add_argument("--materialized-scope", action="store_true", help="Use the production scope CTE materialization")
    parser.add_argument("--_worker", nargs=2, metavar=("SYNTHETIC_DATABASE", "MODE"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._worker:
        path, mode = args._worker
        if mode not in ("baseline", "current"):
            parser.error("invalid measurement mode")
        print(json.dumps(measure_database(Path(path), mode, measure_memory=not args.wall_time_only,
            materialized_scope=args.materialized_scope), sort_keys=True))
        return
    if not all(1 <= size <= 100000 for size in args.sizes) or not 1 <= args.repetitions <= 5:
        parser.error("sizes must be 1..100000; repetitions must be 1..5")
    results = []
    with TemporaryDirectory(prefix="waf-timing-benchmark-") as directory:
        for count in args.sizes:
            path = Path(directory) / f"synthetic-{count}.sqlite"
            seed_database(path, count)
            for mode in args.modes:
                for repetition in range(args.repetitions):
                    command = [sys.executable, str(Path(__file__).resolve()), "--_worker", str(path), mode]
                    if args.wall_time_only:
                        command.append("--wall-time-only")
                    if args.materialized_scope:
                        command.append("--materialized-scope")
                    process = subprocess.run(command, capture_output=True, text=True)
                    if process.returncode:
                        raise RuntimeError("Synthetic measurement failed: " + process.stderr)
                    result = json.loads(process.stdout)
                    result["repetition"] = repetition + 1
                    results.append(result)
                    print(json.dumps(result, sort_keys=True), flush=True)
    print(json.dumps({"benchmark": MARKER, "runs": len(results), "all_oracles_match": True,
        "notes": ["synthetic disposable SQLite only", "no HTTP or filter CTE benchmark",
            "elapsed excludes tracing" if args.wall_time_only else "elapsed includes tracemalloc overhead",
            "RSS includes imports and oracle verification",
            "not an operating-server capacity guarantee"]}, sort_keys=True))


if __name__ == "__main__":
    main()
