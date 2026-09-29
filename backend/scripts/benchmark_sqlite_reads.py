"""Offline HTTP benchmark. Creates ONLY synthetic DBs under an explicit directory.

Run from backend: .venv/bin/python scripts/benchmark_sqlite_reads.py --directory
/tmp/waf-read-benchmark --seed --analyses 10000 --output before.json
Repeat without --seed after changes. Never points at the configured service DB.
Output contains only aggregate timings/counts, never SQL, bindings, or event text.
"""
import argparse
from collections import Counter
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event, insert
from app.config import Settings
from app.main import create_app
from app.models import (Analysis, AnalysisLabel, Review, TestRunItem, ValidationDataset,
    ValidationDatasetVersion, ValidationDatasetItem, ValidationDatasetWorkingItem,
    ValidationDatasetWorkingState, utcnow)
from app.services.production_configurations import ensure_baseline
from app.services.test_runs import create_run_record


def seed(app, count, sizes):
    crypto = app.state.crypto
    now = utcnow()
    event = {"event_id": "synthetic", "company_name": "Benchmark", "src_ip": "192.0.2.1",
        "dest_ip": "198.51.100.1", "src_port": 12345, "dest_port": 443,
        "waf_vendor": "generic", "waf_action": "D", "signature": "Synthetic only",
        "event_name": "benchmark", "payload": "GET /synthetic HTTP/1.1\r\n\r\n" + "x" * 2048}
    with app.state.session_factory() as db:
        ensure_baseline(db, crypto, app.state.settings)
        runs = []
        for size in sizes:
            run, _ = create_run_record(db, crypto, app.state.settings, name=f"Synthetic {size}",
                idempotency_key=f"synthetic-{size}", request_hash="0" * 64, kind="dataset", actor="benchmark")
            runs.append(run)
        db.commit()
        schema = runs[0].input_schema_snapshot_ciphertext
        payload = crypto.encrypt_text(event["payload"])
        result = {"verdict": "true_positive", "agent": {"framework": "moduagent"}, "synthetic_padding": "x" * 2048}
        for start in range(0, count, 1000):
            db.execute(insert(Analysis), [{"id": f"analysis-{i}", **{k: v for k, v in event.items() if k != "payload"},
                "event_id": f"synthetic-{i}", "source_system": "benchmark", "analysis_purpose": "test" if i < sum(sizes) else "production",
                "ingest_channel": "test_lab", "payload_ciphertext": payload, "encryption_key_version": crypto.key_version,
                "prompt_snapshot_ciphertext": runs[0].prompt_snapshot_ciphertext,
                "input_schema_snapshot_ciphertext": schema, "execution_snapshot_ciphertext": payload,
                "result_json": result, "status": "completed", "verdict": "true_positive", "summary_ko": "Synthetic benchmark",
                "model_profile": "synthetic-offline", "created_at": now - timedelta(hours=i % 168, seconds=5),
                "started_at": now - timedelta(hours=i % 168, seconds=4), "completed_at": now - timedelta(hours=i % 168, seconds=1)
                } for i in range(start, min(start + 1000, count))])
        db.execute(insert(Review), [{"analysis_id": f"analysis-{i}", "source_system": "benchmark", "external_review_id": str(i),
            "event_id": f"synthetic-{i}", "decision": "deferred", "comment": "synthetic" * 200} for i in range(count)])
        cursor = 0
        for size, run in zip(sizes, runs):
            dataset = ValidationDataset(id=f"dataset-{size}", name=f"Synthetic {size}", description="Offline fixture", revision=1, created_by="benchmark")
            db.add(dataset)
            db.flush()
            snapshots, working, items, labels = [], [], [], []
            ids, cases = [], {}
            for j in range(size):
                identifier, case_id, analysis_id = f"item-{size}-{j}", f"case-{size}-{j}", f"analysis-{cursor+j}"
                values = {"dataset_id": dataset.id, "item_id": case_id, "event_ciphertext": crypto.encrypt_text(json.dumps({**event, "event_id": case_id})),
                    "schema_snapshot_ciphertext": schema, "encryption_key_version": crypto.key_version, "input_hash": f"{j:064x}",
                    "reference_verdict": "true_positive", "source_kind": "reference", "internal_only": False,
                    "difficulty": "hard" if j % 2 else "medium", "test_category": "synthetic", "case_name": f"Case {j}", "created_by": "benchmark"}
                snapshots.append({**values, "id": identifier, "revision": 1, "review_status": "approved"})
                working.append({**values, "content_hash": f"{j:064x}", "excluded": False, "validation_state": "ready",
                    "reference_origin": "manual", "tags": [], "validation_issues_json": []})
                ids.append(identifier)
                cases[case_id] = {"content_hash": f"{j:064x}", "tags": [], "reference_origin": "manual", "provenance": {}}
                label_id = f"label-{cursor+j}"
                labels.append({"id": label_id, "analysis_id": analysis_id, "revision": 1, "verdict": "true_positive", "source_kind": "reference",
                    "source_ref": "synthetic", "ai_visible": False, "created_by": "benchmark", "attachment_id": "benchmark", "token_digest": f"{cursor+j:064x}"})
                items.append({"test_run_id": run.id, "row_number": j+1, "analysis_id": analysis_id, "dataset_item_version_id": identifier,
                    "label_id": label_id, "ingest_status": "accepted", "event_id": case_id, "difficulty": values["difficulty"], "test_category": "synthetic"})
            db.execute(insert(ValidationDatasetItem), snapshots)
            db.execute(insert(ValidationDatasetWorkingItem), working)
            db.add(ValidationDatasetWorkingState(dataset_id=dataset.id, working_revision=1))
            version = ValidationDatasetVersion(id=f"version-{size}", dataset_id=dataset.id, revision=1, name=dataset.name,
                description=dataset.description, item_version_ids=ids, is_published=True, created_by="benchmark", publish_metadata={"cases": cases})
            db.add(version)
            db.flush()
            db.execute(insert(AnalysisLabel), labels)
            db.execute(insert(TestRunItem), items)
            run.dataset_version_id, run.approved_item_version_ids = version.id, ids
            run.evaluation_mode, run.metrics_version = "ground_truth", "waf-three-way-v1"
            cursor += size
        db.commit()
        return {"sizes": sizes, "analyses": count, "runs": {str(s): r.id for s, r in zip(sizes, runs)}}


def measure(client, method, path, body, repetitions):
    samples = []
    for _ in range(repetitions):
        durations, materialized, slow = [], Counter(), []
        decrypt = [0, 0.0]
        def before(_conn, _cursor, _statement, _parameters, context, _many):
            context._benchmark_start = time.perf_counter()
        def after(_conn, _cursor, _statement, _parameters, context, _many):
            duration = (time.perf_counter() - context._benchmark_start) * 1000
            durations.append(duration)
            if duration >= 30 and _statement.lstrip().upper().startswith(("SELECT", "WITH")):
                slow.append((duration, _statement, _parameters))
        def loaded(_session, instance):
            materialized[type(instance).__name__] += 1
        original = client.app.state.crypto.decrypt_text
        def timed(value):
            start = time.perf_counter()
            try:
                return original(value)
            finally:
                decrypt[0] += 1
                decrypt[1] += (time.perf_counter() - start) * 1000
        engine = client.app.state.engine
        from sqlalchemy.orm import Session
        sql_event.listen(engine, "before_cursor_execute", before)
        sql_event.listen(engine, "after_cursor_execute", after)
        sql_event.listen(Session, "loaded_as_persistent", loaded)
        client.app.state.crypto.decrypt_text = timed
        try:
            start = time.perf_counter()
            response = client.request(method, path, **({"json": body} if body is not None else {}))
            elapsed = (time.perf_counter() - start) * 1000
            if response.status_code != 200:
                raise RuntimeError(f"benchmark request failed: HTTP {response.status_code}")
            data = response.json()
            samples.append({"elapsed_ms": elapsed, "sql_count": len(durations), "sql_ms": sum(durations), "slowest_sql_ms": max(durations, default=0),
                "response_bytes": len(response.content), "returned_items": len(data.get("items", [])),
                "total": data.get("total", data.get("request_count")), "decrypt_count": decrypt[0], "decrypt_ms": decrypt[1], "materialized": dict(materialized)})
        finally:
            client.app.state.crypto.decrypt_text = original
            sql_event.remove(engine, "before_cursor_execute", before)
            sql_event.remove(engine, "after_cursor_execute", after)
            sql_event.remove(Session, "loaded_as_persistent", loaded)
    plans = []
    with engine.connect() as connection:
        for duration, statement, parameters in sorted(slow, reverse=True, key=lambda row: row[0])[:5]:
            plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
            plans.append({"template_hash": hashlib.sha256(statement.encode()).hexdigest()[:16], "duration_ms": round(duration, 3),
                "plan": [row[3] for row in plan]})
    result = {key: round(statistics.median(s[key] for s in samples), 3) for key in samples[0] if key != "materialized" and samples[0][key] is not None}
    result["materialized"] = samples[-1]["materialized"]
    result["slow_query_plans"] = plans
    return result


def index_probe(engine):
    """Reversible A/B on this script's synthetic database only, not service DBs."""
    results = {}
    index = next(index for index in Analysis.__table__.indexes if index.name == "ix_analyses_created_id")
    for enabled in (False, True):
        samples = []
        for _ in range(3):
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN")
                try:
                    if enabled:
                        index.create(connection, checkfirst=True)
                    else:
                        index.drop(connection, checkfirst=True)
                    plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN SELECT id,status FROM analyses "
                        "WHERE created_at >= ? ORDER BY created_at DESC,id DESC LIMIT 20", ("2000-01-01",)).all()
                    start = time.perf_counter()
                    connection.execute(insert(Analysis.__table__), [{"source_system": "synthetic-write-probe", "event_id": str(uuid.uuid4()),
                        "company_name": "Synthetic", "src_ip": "192.0.2.1", "dest_ip": "198.51.100.1", "waf_vendor": "generic", "waf_action": "D",
                        "payload_ciphertext": "synthetic-not-a-real-payload", "encryption_key_version": "synthetic"} for _ in range(1000)])
                    samples.append((time.perf_counter() - start) * 1000)
                finally:
                    connection.rollback()  # Includes DDL and every synthetic row.
        results["with_index" if enabled else "without_index"] = {"insert_1000_ms": round(statistics.median(samples), 3),
            "plan": [row[3] for row in plan]}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--analyses", type=int, default=10000)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--apply-read-index", action="store_true", help="Apply ONLY the read index to this isolated synthetic DB")
    parser.add_argument("--index-probe-only", action="store_true", help="Rollback-only insert/plan comparison on this synthetic DB")
    parser.add_argument("--output", default="before.json")
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "synthetic.sqlite"
    if args.seed and database.exists():
        parser.error("Refusing to overwrite an existing database. Use a new directory.")
    if not args.seed and not (directory / "synthetic-manifest.json").exists():
        parser.error("Synthetic manifest required; operating databases are not accepted.")
    settings = Settings(_env_file=None, database_url=f"sqlite+pysqlite:///{database}", agent_mode="stub",
        environment="development", public_origin="http://testserver", session_https_only=False,
        admin_username="benchmark", admin_password="synthetic-benchmark-only", session_secret="synthetic-benchmark-session-secret-only",
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    app = create_app(settings, create_schema=args.seed)
    app.state.engine.hide_parameters = True
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        if args.apply_read_index:
            next(index for index in Analysis.__table__.indexes if index.name == "ix_analyses_created_id").create(app.state.engine, checkfirst=True)
        if args.seed:
            sizes = [150, 1000, 5000] if args.analyses >= 6150 else [150]
            manifest = seed(app, args.analyses, sizes)
            (directory / "synthetic-manifest.json").write_text(json.dumps(manifest))
        else:
            manifest = json.loads((directory / "synthetic-manifest.json").read_text())
        if args.index_probe_only:
            results = index_probe(app.state.engine)
            print(json.dumps(results, indent=2))
            (directory / args.output).write_text(json.dumps(results, indent=2))
            return
        assert client.post("/api/v1/auth/login", json={"username": "benchmark", "password": "synthetic-benchmark-only"}).status_code == 200
        calls = [(f"Analysis list {n}", "GET", f"/api/v1/analyses?limit={n}", None) for n in (25, 50, 100)]
        calls += [("Analysis detail", "GET", "/api/v1/analyses/analysis-0", None)]
        for size in manifest["sizes"]:
            calls.append((f"GT {size}", "POST", f"/api/v1/validation-datasets/dataset-{size}/working/search", {"limit": 50}))
            for n in (25, 50, 100):
                calls.append((f"Test {size} page {n}", "GET", f"/api/v1/test-runs/{manifest['runs'][str(size)]}?limit={n}", None))
        calls += [("Dataset catalog", "POST", "/api/v1/validation-datasets/search", {}),
            ("Overview", "GET", "/api/v1/admin/production-configurations/overview", None),
            *[(f"Runtime {window}", "GET", f"/api/v1/admin/runtime/status?window={window}", None) for window in ("24h", "7d")]]
        results = {}
        for name, method, path, body in calls:
            results[name] = measure(client, method, path, body, args.repetitions)
            print(name, json.dumps({k: v for k, v in results[name].items() if k != "slow_query_plans"}), flush=True)
        (directory / args.output).write_text(json.dumps({"manifest": manifest, "repetitions": args.repetitions, "results": results}, indent=2))


if __name__ == "__main__":
    main()
