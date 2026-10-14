"""Offline confusion-matrix drilldown benchmark; synthetic DB only.

Measures identical DB/HTTP requests before and after a code change, with large
synthetic result JSON, nine verdict/reference pairs and two Test reference modes.
No running service, environment file, model, plaintext payload or SQL bindings.
"""
import argparse
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import update

from benchmark_sqlite_reads import measure, seed
from app.config import Settings
from app.main import create_app
from app.models import Analysis, AnalysisLabel, TestRun
from app.database import Base


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--padding", type=int, default=16384)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", default="before.json")
    parser.add_argument("--explain-only", action="store_true", help="Capture the first expensive read's plan without executing it")
    args = parser.parse_args()
    if not 10 <= args.rows <= 10000 or not 0 <= args.padding <= 262144 or not 1 <= args.repetitions <= 5:
        parser.error("invalid synthetic benchmark limits")
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    database, manifest_file = directory / "synthetic.sqlite", directory / "matrix-manifest.json"
    if args.seed and database.exists():
        parser.error("Refusing to overwrite any database. Use a new directory.")
    if not args.seed and not manifest_file.exists():
        parser.error("Matrix synthetic manifest required; service databases are not accepted.")
    settings = Settings(_env_file=None, database_url=f"sqlite+pysqlite:///{database}", agent_mode="stub",
        environment="development", public_origin="http://testserver", session_https_only=False,
        admin_username="benchmark", admin_password="synthetic-benchmark-only",
        session_secret="synthetic-benchmark-session-only", data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    app = create_app(settings, create_schema=args.seed)
    app.state.engine.hide_parameters = True
    if args.explain_only:
        from sqlalchemy import event
        from app.services.test_runs import describe_run
        manifest = json.loads(manifest_file.read_text())
        captured = []
        class Captured(Exception):
            pass
        def capture(_connection, _cursor, statement, parameters, _context, _many):
            if "AS outcome" in statement:
                captured.append((statement, parameters))
                raise Captured()
        with app.state.session_factory() as db:
            run = db.get(TestRun, manifest["modes"]["ground_truth"])
            event.listen(app.state.engine, "before_cursor_execute", capture)
            try:
                describe_run(db, run, reference_basis="latest", reference_verdict="false_positive", verdict="true_positive", evaluation_outcome="false_positive")
            except Captured:
                pass
            finally:
                event.remove(app.state.engine, "before_cursor_execute", capture)
        with app.state.engine.connect() as connection:
            for statement, parameters in captured:
                plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
                print(json.dumps({"template_hash": hashlib.sha256(statement.encode()).hexdigest()[:16], "plan": [row[3] for row in plan]}, indent=2))
        return
    if args.seed:
        Base.metadata.create_all(app.state.engine)
        manifest = seed(app, args.rows * 2 + 1000, [args.rows, args.rows - 1])
        verdicts = ("true_positive", "false_positive", "inconclusive")
        with app.state.session_factory() as db:
            for start in range(0, args.rows * 2 - 1, 500):
                indices = range(start, min(start + 500, args.rows * 2 - 1))
                db.execute(update(Analysis), [{"id": f"analysis-{i}", "verdict": verdicts[i % 3],
                    "result_json": {"verdict": verdicts[i % 3], "agent": {"framework": "moduagent"}, "synthetic_padding": "x" * args.padding}}
                    for i in indices])
                db.execute(update(AnalysisLabel), [{"id": f"label-{i}", "verdict": verdicts[(i // 3) % 3]} for i in indices])
            db.get(TestRun, manifest["runs"][str(args.rows - 1)]).evaluation_mode = "reference"
            db.commit()
        manifest.update(padding=args.padding, modes={"ground_truth": manifest["runs"][str(args.rows)], "latest": manifest["runs"][str(args.rows - 1)]})
        manifest_file.write_text(json.dumps(manifest))
    else:
        manifest = json.loads(manifest_file.read_text())
    results = {}
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "benchmark", "password": "synthetic-benchmark-only"}).status_code == 200
        for mode, identifier in manifest["modes"].items():
            for cell, reference, prediction, outcome in [("tp", "true_positive", "true_positive", "match"),
                ("fp", "false_positive", "true_positive", "false_positive"),
                ("fn", "true_positive", "false_positive", "false_negative"),
                ("tn", "false_positive", "false_positive", "match")]:
                query = {"limit": 25, "reference_basis": "latest", "reference_verdict": reference, "verdict": prediction, "evaluation_outcome": outcome}
                from urllib.parse import urlencode
                path = f"/api/v1/test-runs/{identifier}?{urlencode(query)}"
                name = f"{mode}:{cell}"
                result = measure(client, "GET", path, None, args.repetitions)
                data = client.get(path).json()
                result.update(total_items=data["total_items"], evaluation_total=data["evaluation_summary"]["total"],
                    response_hash=hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())
                results[name] = result
                print(name, json.dumps({key: value for key, value in result.items() if key not in ("slow_query_plans", "response_hash")}), flush=True)
    (directory / args.output).write_text(json.dumps({"manifest": manifest, "repetitions": args.repetitions, "results": results}, indent=2))


if __name__ == "__main__":
    main()
