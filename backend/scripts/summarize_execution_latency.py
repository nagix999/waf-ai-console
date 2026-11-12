"""Summarize saved numeric execution telemetry from an approved SQLite backup.

Read-only: no application startup, environment, decryption, model or network.
Only aggregate JSON is written to stdout. Identifiers and paths are never
included in diagnostics. Use a consistent approved backup, not copied live
DB/WAL files. No timing gaps or absent historical records are counted as zero.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.dialects import sqlite

from app.services.test_attempts import test_attempts
from app.models import TestRunItem

CHUNK_SIZE = 100
MAX_CASES = 10_000
STATUSES = ("pending", "processing", "completed", "failed", "canceled")
STEP_TYPES = ("input", "parser", "decoder", "agent_input", "llm_primary", "policy", "llm_verifier",
    "finalize", "llm_evidence_editor", "agent_stub")
RETRY_KINDS = ("schema_correction", "compact_output", "timeout_recovery")
SCHEMA = {
    "test_runs": {"id", "execution_mode"},
    "test_run_items": {"id", "test_run_id", "analysis_id", "ingest_status"},
    "analyses": {"id", "status", "analysis_purpose", "retry_of_analysis_id", "created_at", "started_at", "completed_at"},
    "agent_runs": {"id", "analysis_id", "status", "created_at"},
    "agent_steps": {"id", "run_id", "sequence", "step_type", "status", "metadata_json"},
}


class DiagnosticError(ValueError):
    """Fixed diagnostics only, never a source exception or supplied argument."""


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        raise DiagnosticError("invalid_arguments")


def _max_cases(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise DiagnosticError("invalid_arguments") from None
    if not 1 <= number <= MAX_CASES:
        raise DiagnosticError("invalid_arguments")
    return number


def _compile(statement):
    compiled = statement.compile(dialect=sqlite.dialect(paramstyle="named"))
    return str(compiled), compiled.params


def _enum(column, values):
    allowed = ",".join("'" + value + "'" for value in values)
    return f"CASE WHEN {column} IN ({allowed}) THEN {column} END"


def _document(column):
    return f"CASE WHEN json_valid({column}) THEN {column} ELSE '{{}}' END"


def _number(document, path):
    return (f"CASE WHEN json_type({document},'{path}') IN ('integer','real') "
            f"THEN json_extract({document},'{path}') END")


def _boolean(document, path):
    return f"CASE json_type({document},'{path}') WHEN 'true' THEN 1 WHEN 'false' THEN 0 END"


def _bounded_count(document, path, maximum):
    # A corrupted/future array must not become apparently complete after the
    # bounded scan drops its tail. Preserve unknown rather than a partial sum.
    return (f"CASE WHEN json_type({document},'{path}.attempts')='array' "
            f"AND json_array_length({document},'{path}.attempts')<={maximum} "
            f"THEN {_number(document, path + '.attempt_count')} END")


def _as_bool(value):
    return None if value is None else bool(value)


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _placeholders(identifiers):
    params = {f"a{index}": identifier for index, identifier in enumerate(identifiers)}
    return ",".join(":" + key for key in params), params


def _check_schema(connection):
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in SCHEMA.items():
        if table not in names:
            raise DiagnosticError("database_schema_unsupported")
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not columns <= actual:
            raise DiagnosticError("database_schema_unsupported")
    if "evidence_display_jobs" not in names:
        return False  # A pre-display database has unknown, not zero, display work.
    required = {"analysis_id", "status", "created_at", "started_at", "completed_at", "metadata_json"}
    actual = {row[1] for row in connection.execute("PRAGMA table_info(evidence_display_jobs)")}
    if not required <= actual:
        raise DiagnosticError("database_schema_unsupported")
    return True


def _step_cte(placeholders):
    return f"""WITH ranked_runs AS (
        SELECT id,analysis_id,row_number() OVER (
            PARTITION BY analysis_id ORDER BY created_at DESC,id DESC) AS position
        FROM agent_runs WHERE analysis_id IN ({placeholders})
    ), ranked_steps AS (
        SELECT s.id,r.analysis_id,s.step_type,s.status,s.metadata_json,
            row_number() OVER (PARTITION BY r.analysis_id,s.step_type ORDER BY s.sequence DESC,s.id DESC) AS position
        FROM agent_steps s JOIN ranked_runs r ON s.run_id=r.id AND r.position=1
    ), chosen AS (SELECT * FROM ranked_steps WHERE position=1) """


def _attempt_values(document):
    return ",".join([
        _number(document, "$.attempt") + " AS attempt",
        _number(document, "$.duration_ms") + " AS duration_ms",
        _enum(f"json_extract({document},'$.retry_kind')", RETRY_KINDS) + " AS retry_kind",
    ])


def _attempt_record(row):
    return {"attempt": row["attempt"], "duration_ms": row["duration_ms"], "retry_kind": row["retry_kind"]}


def _load_steps(connection, identifiers):
    if not identifiers:
        return {}
    slots, params = _placeholders(identifiers)
    cte = _step_cte(slots)
    meta = _document("s.metadata_json")
    columns = ["s.id", "s.analysis_id", _enum("s.step_type", STEP_TYPES) + " AS step_type",
        _enum("s.status", ("pending", "running", "completed", "failed", "skipped", "canceled")) + " AS status",
        f"CASE WHEN json_type({meta},'$.evidence_grounding_retry') IS NOT NULL THEN 1 ELSE 0 END AS grounding_present"]
    numeric = {"duration_ms": "$.duration_ms", "wait_ms": "$.concurrency.wait_ms"}
    columns += [_number(meta, path) + " AS " + name for name, path in numeric.items()]
    columns += [_bounded_count(meta, "$.output_validation_retry", 4) + " AS validation_count",
        _bounded_count(meta, "$.evidence_grounding_retry", 2) + " AS grounding_count"]
    columns += [_boolean(meta, "$.timing_measured") + " AS timing_measured",
        f"CASE WHEN json_type({meta},'$.timing_incomplete') IS NULL "
        f"OR json_type({meta},'$.timing_incomplete')='false' THEN 0 ELSE 1 END AS timing_incomplete"]
    steps, by_analysis = {}, {}
    for row in connection.execute(cte + "SELECT " + ",".join(columns) + " FROM chosen s", params):
        value = {"step_type": row["step_type"], "status": row["status"], "metadata": {
            "timing_measured": _as_bool(row["timing_measured"]), "timing_incomplete": _as_bool(row["timing_incomplete"]),
            "duration_ms": row["duration_ms"], "concurrency": {"wait_ms": row["wait_ms"]},
            "output_validation_retry": {"attempt_count": row["validation_count"], "attempts": []},
            "evidence_grounding_retry": {"attempt_count": row["grounding_count"], "attempts": []}}}
        if not row["grounding_present"]:
            value["metadata"].pop("evidence_grounding_retry")
        steps[row["id"]] = value
        by_analysis.setdefault(row["analysis_id"], []).append(value)
    # Only scalar telemetry is returned by these JSON table-valued scans.
    # The limits match the frozen runtime's 4 schema and 2 citation attempts;
    # higher future counts remain visible as incomplete telemetry, not totals.
    item = "CASE WHEN a.type='object' THEN a.value ELSE '{}' END"
    top_array = f"CASE WHEN json_type({meta},'$.output_validation_retry.attempts')='array' THEN json_extract({meta},'$.output_validation_retry.attempts') ELSE '[]' END"
    sql = cte + f"SELECT s.id,a.key AS idx,{_attempt_values(item)} FROM chosen s,json_each({top_array}) a WHERE CAST(a.key AS INTEGER)<4 ORDER BY s.id,CAST(a.key AS INTEGER)"
    for row in connection.execute(sql, params):
        steps[row["id"]]["metadata"]["output_validation_retry"]["attempts"].append(_attempt_record(row))
    grounding_array = f"CASE WHEN json_type({meta},'$.evidence_grounding_retry.attempts')='array' THEN json_extract({meta},'$.evidence_grounding_retry.attempts') ELSE '[]' END"
    ground = "CASE WHEN g.type='object' THEN g.value ELSE '{}' END"
    sql = cte + f"SELECT s.id,g.key AS idx,{_attempt_values(ground)}," + _bounded_count(ground, "$.output_validation_retry", 4) + f" AS validation_count FROM chosen s,json_each({grounding_array}) g WHERE CAST(g.key AS INTEGER)<2 ORDER BY s.id,CAST(g.key AS INTEGER)"
    grounding = {}
    for row in connection.execute(sql, params):
        value = _attempt_record(row)
        value["output_validation_retry"] = {"attempt_count": row["validation_count"], "attempts": []}
        steps[row["id"]]["metadata"]["evidence_grounding_retry"]["attempts"].append(value)
        grounding[row["id"], row["idx"]] = value
    nested = f"CASE WHEN json_type({ground},'$.output_validation_retry.attempts')='array' THEN json_extract({ground},'$.output_validation_retry.attempts') ELSE '[]' END"
    sql = cte + f"SELECT s.id,g.key AS ground_idx,a.key AS idx,{_attempt_values(item)} FROM chosen s,json_each({grounding_array}) g,json_each({nested}) a WHERE CAST(g.key AS INTEGER)<2 AND CAST(a.key AS INTEGER)<4 ORDER BY s.id,CAST(g.key AS INTEGER),CAST(a.key AS INTEGER)"
    for row in connection.execute(sql, params):
        grounding[row["id"], row["ground_idx"]]["output_validation_retry"]["attempts"].append(_attempt_record(row))
    return by_analysis


def _load_chunk(connection, identifiers, execution_mode, display_available):
    actual = [identifier for identifier in identifiers if identifier is not None]
    slots, params = _placeholders(actual)
    rows, displays = {}, {}
    if actual:
        query = "SELECT id," + _enum("status", STATUSES) + f" AS status,created_at,started_at,completed_at FROM analyses WHERE id IN ({slots})"
        rows = {row["id"]: row for row in connection.execute(query, params)}
        if display_available:
            meta = _document("metadata_json")
            query = "SELECT analysis_id," + _enum("status", ("pending", "running", "completed", "skipped", "failed", "canceled")) + " AS status,created_at,started_at,completed_at,"
            query += _number(meta, "$.duration_ms") + " AS duration_ms," + _boolean(meta, "$.model_called") + f" AS model_called FROM evidence_display_jobs WHERE analysis_id IN ({slots})"
            displays = {row["analysis_id"]: row for row in connection.execute(query, params)}
    steps = _load_steps(connection, actual)
    for identifier in identifiers:
        row, display = rows.get(identifier), displays.get(identifier)
        yield {"status": row["status"] if row else None, "execution_mode": execution_mode,
            **{key: _time(row[key]) if row else None for key in ("created_at", "started_at", "completed_at")},
            "steps": steps.get(identifier, []), "display_job": ({"status": None} if not display_available else None) if display is None else {
                "status": display["status"],
                **{key: _time(display[key]) for key in ("created_at", "started_at", "completed_at")},
                "metadata": {"duration_ms": display["duration_ms"], "model_called": _as_bool(display["model_called"])}}}


def read_records(connection, run_id, max_cases):
    """A bounded consistent read of current retry attempts, never ORM rows."""
    display_available = _check_schema(connection)
    run = connection.execute("SELECT " + _enum("execution_mode", ("stub", "moduagent")) + " AS execution_mode FROM test_runs WHERE id=:identifier",
        {"identifier": run_id}).fetchone()
    if run is None:
        raise DiagnosticError("test_run_unavailable")
    _, current = test_attempts(run_id)
    membership = select(current.c.analysis_id, current.c.item_id).join(TestRunItem,
        TestRunItem.id == current.c.item_id).where(TestRunItem.ingest_status == "accepted").subquery()
    sql, params = _compile(select(func.count()).select_from(membership))
    count = connection.execute(sql, params).fetchone()[0]
    if count > max_cases:
        raise DiagnosticError("case_limit_exceeded")
    sql, params = _compile(select(membership.c.analysis_id).order_by(membership.c.item_id))
    identifiers = [row[0] for row in connection.execute(sql, params)]
    for offset in range(0, len(identifiers), CHUNK_SIZE):
        yield from _load_chunk(connection, identifiers[offset:offset + CHUNK_SIZE], run["execution_mode"], display_available)


def summarize_file(database_file, run_id, max_cases=5000):
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", run_id):
        raise DiagnosticError("invalid_arguments")
    if type(max_cases) is not int or not 1 <= max_cases <= MAX_CASES:
        raise DiagnosticError("invalid_arguments")
    try:
        path = Path(database_file).resolve(strict=True)
        if not path.is_file():
            raise OSError()
        uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            remaining = [100_000]
            def bounded():
                remaining[0] -= 1
                return int(remaining[0] <= 0)
            connection.set_progress_handler(bounded, 1000)
            connection.execute("BEGIN")
            try:
                from app.services.execution_latency import summarize_execution_latency
                return summarize_execution_latency(read_records(connection, run_id, max_cases))
            finally:
                connection.rollback()
    except DiagnosticError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        raise DiagnosticError("latency_diagnostics_unavailable") from None


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--database-file", required=True)
    parser.add_argument("--test-run-id", required=True)
    parser.add_argument("--max-cases", type=_max_cases, default=5000)
    try:
        args = parser.parse_args(argv)
        result = summarize_file(args.database_file, args.test_run_id, args.max_cases)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    except DiagnosticError as exc:
        print(json.dumps({"error": str(exc)}, separators=(",", ":")), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
