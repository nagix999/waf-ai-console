"""Synthetic SQLite-only diagnostics. No environment, crypto, model or network."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "summarize_execution_latency.py"
spec = importlib.util.spec_from_file_location("execution_latency_cli", SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
MARKER = "SYNTHETIC_PRIVATE_LOG_KEY_AND_PROVIDER_TEXT"


def fixture_db(path, count=1, *, display=True):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE test_runs (id TEXT PRIMARY KEY,execution_mode TEXT);
        CREATE TABLE test_run_items (id TEXT PRIMARY KEY,test_run_id TEXT,analysis_id TEXT,
            ingest_status TEXT DEFAULT 'accepted');
        CREATE TABLE analyses (id TEXT PRIMARY KEY,status TEXT,analysis_purpose TEXT,retry_of_analysis_id TEXT,
            created_at TEXT,started_at TEXT,completed_at TEXT,payload_ciphertext TEXT,result_json TEXT,extra_fields TEXT);
        CREATE TABLE agent_runs (id TEXT PRIMARY KEY,analysis_id TEXT,status TEXT,created_at TEXT);
        CREATE TABLE agent_steps (id TEXT PRIMARY KEY,run_id TEXT,sequence INTEGER,step_type TEXT,status TEXT,
            metadata_json TEXT,input_ciphertext TEXT,output_ciphertext TEXT,tool_calls_json TEXT);
    """)
    if display:
        connection.execute("""CREATE TABLE evidence_display_jobs (analysis_id TEXT,status TEXT,
            created_at TEXT,started_at TEXT,completed_at TEXT,metadata_json TEXT,
            snapshot_ciphertext TEXT,input_ciphertext TEXT,output_ciphertext TEXT)""")
    connection.execute("INSERT INTO test_runs VALUES (?,?)", ("synthetic-run", "moduagent"))
    metadata = {"timing_measured": True, "duration_ms": 10,
        "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        "concurrency": {"wait_ms": 3},
        "output_validation_retry": {"attempt_count": 1, "attempts": [{"attempt": 1, "duration_ms": 5,
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            "framework_run_id": MARKER, "validation_issues": [{"field": MARKER, "type": MARKER}]}]},
        "tool_trace": MARKER, "error_summary": {"code": MARKER}, "provider": MARKER}
    for index in range(count):
        identifier = f"analysis-{index:05d}"
        connection.execute("INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?)", (
            identifier, "completed", "test", None, "2026-01-01 00:00:00", "2026-01-01 00:00:01",
            "2026-01-01 00:00:03", MARKER, MARKER, MARKER))
        connection.execute("INSERT INTO test_run_items (id,test_run_id,analysis_id) VALUES (?,?,?)", (f"item-{index:05d}", "synthetic-run", identifier))
        connection.execute("INSERT INTO agent_runs VALUES (?,?,?,?)", (f"run-{index}", identifier, "completed", "2026-01-01 00:00:02"))
        connection.execute("INSERT INTO agent_steps VALUES (?,?,?,?,?,?,?,?,?)", (
            f"step-{index}", f"run-{index}", 5, "llm_primary", "completed", json.dumps(metadata), MARKER, MARKER, MARKER))
    connection.commit()
    connection.close()
    return path


def read_fixture(path):
    connection = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return list(cli.read_records(connection, "synthetic-run", 5000))
    finally:
        connection.close()


def test_read_only_aggregate_and_no_identifiers_or_protected_content(tmp_path, capsys):
    path = fixture_db(tmp_path / "private database.db")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert cli.main(["--database-file", str(path), "--test-run-id", "synthetic-run"]) == 0
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["version"] == "execution-latency-v1" and report["cases"] == 1
    assert report["statuses"] == {"completed": 1}
    assert not output.err
    assert all(value not in output.out for value in (MARKER, str(path), "synthetic-run", "analysis-00000", "run-0", "step-0"))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_only_current_retry_latest_run_and_latest_same_type_step_are_selected(tmp_path):
    path = fixture_db(tmp_path / "retry.db")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE analyses SET status='failed' WHERE id='analysis-00000'")
        db.execute("INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?)", (
            "selected-retry", "completed", "test", "analysis-00000", "2026-01-01 00:00:04",
            "2026-01-01 00:00:05", "2026-01-01 00:00:07", MARKER, MARKER, MARKER))
        for identifier, created, duration in (("old", "2026-01-01 00:00:04", 999), ("new", "2026-01-01 00:00:05", 8)):
            db.execute("INSERT INTO agent_runs VALUES (?,?,?,?)", (identifier, "selected-retry", "completed", created))
            db.execute("INSERT INTO agent_steps VALUES (?,?,?,?,?,?,?,?,?)", (identifier, identifier, 5,
                "llm_primary", "completed", json.dumps({"timing_measured": True, "duration_ms": duration}), MARKER, MARKER, MARKER))
        db.execute("INSERT INTO agent_steps VALUES (?,?,?,?,?,?,?,?,?)", ("newer-step", "new", 6, "llm_primary", "completed",
            json.dumps({"timing_measured": True, "duration_ms": 4}), MARKER, MARKER, MARKER))
    records = read_fixture(path)
    assert len(records) == 1 and records[0]["status"] == "completed"
    assert records[0]["started_at"].second == 5
    assert len(records[0]["steps"]) == 1 and records[0]["steps"][0]["metadata"]["duration_ms"] == 4


def test_nested_retry_duration_counts_are_allowlisted_and_old_top_level_is_not_masked(tmp_path):
    path = fixture_db(tmp_path / "nested.db")
    record = read_fixture(path)[0]
    meta = record["steps"][0]["metadata"]
    assert "evidence_grounding_retry" not in meta
    assert meta["output_validation_retry"]["attempts"][0]["duration_ms"] == 5
    assert meta["output_validation_retry"]["attempt_count"] == 1
    assert "usage" not in json.dumps(meta)
    nested = {"attempt_count": 2, "attempts": [
        {"attempt": 1, "output_validation_retry": {"attempt_count": 2, "attempts": [
            {"attempt": 1, "duration_ms": 5, "retry_kind": "schema_correction", "private": MARKER},
            {"attempt": 2, "duration_ms": 7}]}},
        {"attempt": 2, "issues": [MARKER], "output_validation_retry": {"attempt_count": 1,
            "attempts": [{"attempt": 1, "duration_ms": 9}]}}]}
    with sqlite3.connect(path) as db:
        meta["evidence_grounding_retry"] = nested
        db.execute("UPDATE agent_steps SET metadata_json=?", (json.dumps(meta),))
    loaded = read_fixture(path)[0]["steps"][0]["metadata"]
    assert loaded["evidence_grounding_retry"]["attempt_count"] == 2
    assert loaded["evidence_grounding_retry"]["attempts"][0]["output_validation_retry"]["attempts"][1]["duration_ms"] == 7
    assert MARKER not in json.dumps(loaded)


@pytest.mark.parametrize("level", ["schema", "grounding", "nested_schema"])
def test_oversized_attempt_arrays_do_not_look_complete_after_bounded_scan(tmp_path, level):
    path = fixture_db(tmp_path / "oversized-attempts.db")
    retry = {"attempt_count": 4, "attempts": [{"attempt": index + 1, "duration_ms": 5} for index in range(5)]}
    metadata = {"timing_measured": True, "duration_ms": 50, "output_validation_retry": retry}
    if level == "nested_schema":
        metadata["evidence_grounding_retry"] = {"attempt_count": 1, "attempts": [{"output_validation_retry": retry}]}
    elif level == "grounding":
        complete = {"attempt_count": 1, "attempts": [{"attempt": 1, "duration_ms": 5}]}
        metadata["evidence_grounding_retry"] = {"attempt_count": 2,
            "attempts": [{"output_validation_retry": complete} for _ in range(3)]}
    with sqlite3.connect(path) as db:
        db.execute("UPDATE agent_steps SET metadata_json=?", (json.dumps(metadata),))
    loaded = read_fixture(path)[0]["steps"][0]["metadata"]
    if level == "schema":
        assert loaded["output_validation_retry"]["attempt_count"] is None
        assert len(loaded["output_validation_retry"]["attempts"]) == 4
    report = cli.summarize_file(path, "synthetic-run")
    assert report["roles"]["primary"]["accounting"]["recorded_cases"] == 0
    assert report["roles"]["primary"]["accounting"]["unrecorded_cases"] == 1


@pytest.mark.parametrize("broken", [None, [], MARKER])
def test_present_but_invalid_grounding_record_does_not_use_last_call_as_full_history(tmp_path, broken):
    path = fixture_db(tmp_path / "broken-grounding.db")
    with sqlite3.connect(path) as db:
        metadata = json.loads(db.execute("SELECT metadata_json FROM agent_steps").fetchone()[0])
        metadata["evidence_grounding_retry"] = broken
        db.execute("UPDATE agent_steps SET metadata_json=?", (json.dumps(metadata),))
    loaded = read_fixture(path)[0]["steps"][0]["metadata"]
    assert "evidence_grounding_retry" in loaded
    assert loaded["evidence_grounding_retry"]["attempt_count"] is None
    report = cli.summarize_file(path, "synthetic-run")
    assert report["roles"]["primary"]["accounting"]["recorded_cases"] == 0
    assert report["roles"]["primary"]["accounting"]["unrecorded_cases"] == 1


@pytest.mark.parametrize("flag", [None, 0, "false", [], {}])
def test_malformed_incomplete_flag_is_not_a_complete_measurement(tmp_path, flag):
    path = fixture_db(tmp_path / "invalid-timing.db")
    with sqlite3.connect(path) as db:
        metadata = json.loads(db.execute("SELECT metadata_json FROM agent_steps").fetchone()[0])
        metadata["timing_incomplete"] = flag
        db.execute("UPDATE agent_steps SET metadata_json=?", (json.dumps(metadata),))
    report = cli.summarize_file(path, "synthetic-run")
    assert report["phases"]["llm_primary"]["duration"]["recorded"] == 0
    assert report["phases"]["llm_primary"]["duration"]["unrecorded"] == 1


def test_only_accepted_members_count_against_limit_or_summary(tmp_path):
    path = fixture_db(tmp_path / "accepted.db")
    with sqlite3.connect(path) as db:
        for index, state in enumerate(("rejected", "duplicate")):
            db.execute("INSERT INTO test_run_items VALUES (?,?,?,?)", (
                f"not-executed-{index}", "synthetic-run", "analysis-00000", state))
    report = cli.summarize_file(path, "synthetic-run", max_cases=1)
    assert report["cases"] == 1


def test_missing_historical_metadata_and_display_table_stay_unknown(tmp_path):
    path = fixture_db(tmp_path / "legacy.db", display=False)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE agent_steps SET metadata_json='{}'")
    record = read_fixture(path)[0]
    assert record["display_job"] == {"status": None}
    assert record["steps"][0]["metadata"]["duration_ms"] is None
    report = cli.summarize_file(path, "synthetic-run")
    assert report["phases"]["llm_primary"]["duration"]["recorded"] == 0
    assert report["phases"]["llm_primary"]["duration"]["unrecorded"] == 1


def test_protected_columns_never_read_and_queries_are_batched(tmp_path, monkeypatch):
    path = fixture_db(tmp_path / "batch.db", count=201)
    original = sqlite3.connect
    statements, opened = [], []
    protected = {"payload_ciphertext", "result_json", "extra_fields", "input_ciphertext", "output_ciphertext",
        "snapshot_ciphertext", "tool_calls_json"}
    def connect(database, **kwargs):
        opened.append((database, kwargs))
        db = original(database, **kwargs)
        def authorized(action, _table, column, *_args):
            if action == sqlite3.SQLITE_READ and column in protected:
                return sqlite3.SQLITE_DENY
            # SQLite declares json_each's eponymous virtual schema through
            # authorizer UPDATE callbacks on sqlite_master. This is not an
            # executed SQL write; mode=ro + query_only still forbid real DML.
            if action == sqlite3.SQLITE_UPDATE and _table == "sqlite_master":
                return sqlite3.SQLITE_OK
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorized)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(cli.sqlite3, "connect", connect)
    report = cli.summarize_file(path, "synthetic-run")
    assert report["cases"] == 201
    assert len(opened) == 1 and opened[0][0].endswith("?mode=ro") and opened[0][1]["uri"] is True
    assert any(statement == "PRAGMA query_only=ON" for statement in statements)
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP"))
        for statement in statements)
    assert len(statements) < 45


@pytest.mark.parametrize("limit", [0, 10001, "not-a-number"])
def test_argument_errors_never_echo_supplied_path_id_or_value(capsys, limit):
    assert cli.main(["--database-file", MARKER, "--test-run-id", MARKER, "--max-cases", str(limit)]) == 2
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err) == {"error": "invalid_arguments"}
    assert MARKER not in output.err


def test_case_limit_refuses_truncation_missing_run_and_path_are_safe(tmp_path, capsys):
    path = fixture_db(tmp_path / "limit.db", count=2)
    for database, identifier, limit, error in (
        (path, "synthetic-run", "1", "case_limit_exceeded"),
        (path, "missing-private-run", "5000", "test_run_unavailable"),
        (tmp_path / MARKER, "synthetic-run", "5000", "latency_diagnostics_unavailable"),
    ):
        assert cli.main(["--database-file", str(database), "--test-run-id", identifier, "--max-cases", limit]) == 2
        output = capsys.readouterr()
        assert not output.out and json.loads(output.err) == {"error": error}
        assert str(database) not in output.err and identifier not in output.err
    assert not (tmp_path / MARKER).exists()


def test_missing_schema_and_bad_json_are_not_reported_as_successful_zero(tmp_path, capsys):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert cli.main(["--database-file", str(empty), "--test-run-id", "synthetic-run"]) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "database_schema_unsupported"}
    path = fixture_db(tmp_path / "bad-meta.db")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE agent_steps SET metadata_json=?", (MARKER,))
    record = read_fixture(path)[0]
    assert record["steps"][0]["metadata"]["duration_ms"] is None


def test_read_transaction_is_consistent_if_another_connection_adds_retry(tmp_path, monkeypatch):
    path = fixture_db(tmp_path / "snapshot.db")
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("UPDATE analyses SET status='failed'")
    original = sqlite3.connect
    inserted = []
    def connect(database, **kwargs):
        db = original(database, **kwargs)
        def change(statement):
            if statement.startswith("WITH RECURSIVE") and not inserted:
                inserted.append(True)
                with original(path) as writer:
                    writer.execute("INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?)", (
                        "new-concurrent-retry", "completed", "test", "analysis-00000", "2026-01-01 00:00:04",
                        "2026-01-01 00:00:05", "2026-01-01 00:00:07", MARKER, MARKER, MARKER))
        db.set_trace_callback(change)
        return db
    monkeypatch.setattr(cli.sqlite3, "connect", connect)
    first = cli.summarize_file(path, "synthetic-run")
    second = cli.summarize_file(path, "synthetic-run")
    assert inserted and first["statuses"] == {"failed": 1}
    assert second["statuses"] == {"completed": 1}
