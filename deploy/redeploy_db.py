"""Offline SQLite backup and preservation checks. Never print stored values."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys


def connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def check(db):
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RuntimeError("database_integrity_failed")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("database_foreign_keys_failed")


def idle(db):
    for table, states in (("analyses", ("pending", "processing")),
                          ("vllm_test_runs", ("pending", "running"))):
        marks = ",".join("?" for _ in states)
        if db.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ({marks})", states).fetchone()[0]:
            raise RuntimeError("unfinished_jobs_wait_before_redeploy")


def fingerprint(db, columns=None):
    if columns is None:
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'alembic_version' ORDER BY name")]
        columns = {name: [row[1] for row in db.execute(f"PRAGMA table_info({quote(name)})")]
                   for name in names}
    result = {}
    for name, fields in columns.items():
        selected = ",".join(map(quote, fields))
        digest = hashlib.sha256()
        count = 0
        for row in db.execute(f"SELECT {selected} FROM {quote(name)} ORDER BY {selected}"):
            encoded = json.dumps(row, ensure_ascii=True, separators=(",", ":"),
                                 default=lambda item: {"bytes": item.hex()}).encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            count += 1
        result[name] = {"columns": fields, "count": count, "sha256": digest.hexdigest()}
    return result


def backup(source, destination):
    target = Path(destination)
    # Refuse overwrite, including a dangling symlink.
    with target.open("xb"):
        pass
    target.chmod(0o600)
    with connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        check(dst)
        return {"revision": dst.execute("SELECT version_num FROM alembic_version").fetchone()[0],
                "tables": fingerprint(dst)}


def verify(source, baseline):
    with connect(source) as db:
        check(db)
        columns = {name: value["columns"] for name, value in baseline["tables"].items()}
        if fingerprint(db, columns) != baseline["tables"]:
            raise RuntimeError("existing_database_values_changed")


def main():
    action, source = sys.argv[1:3]
    if action == "idle":
        with connect(source) as db:
            idle(db)
    elif action == "backup":
        destination, manifest, uid, gid = sys.argv[3:7]
        baseline = backup(source, destination)
        with open(manifest, "x") as stream:
            json.dump(baseline, stream)
        for path in (destination, manifest):
            os.chmod(path, 0o600)
            os.chown(path, int(uid), int(gid))
    elif action == "verify":
        verify(source, json.loads(Path(sys.argv[3]).read_text()))
    else:
        raise RuntimeError("unknown_database_action")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Database check failed; stored values are not printed.", file=sys.stderr)
        sys.exit(1)
