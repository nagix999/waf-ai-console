"""Synthetic-only tests: no Docker daemon, production DB or model calls."""
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from deploy import redeploy, redeploy_db
from deploy.automation.common import DeployError, compose_model, compose_text


def fixture(web="frontend"):
    model = {"name": "waf-test", "services": {}, "volumes": {"data": {"name": "existing-data"}},
             "networks": {"default": {"name": "existing-network"}}}
    rows = {}
    for role in (*redeploy.BACKENDS, web):
        backend = role in redeploy.BACKENDS
        env = {"WAF_DATABASE_URL": redeploy.DB_URL, "WAF_AGENT_MODE": "moduagent",
               "WAF_DATA_ENCRYPTION_KEY": "synthetic$test", "WAF_ENCRYPTION_KEY_VERSION": "test"} if backend else {}
        service = {"image": "old-image:" + role, "environment": env, "command": ["python", "test.py"],
                   "networks": {"default": {}}, "volumes": [{"type": "volume", "source": "data", "target": "/data"}] if backend else []}
        if role == web:
            service["ports"] = [{"target": 80, "published": "18080", "host_ip": "127.0.0.1", "protocol": "tcp"}]
        model["services"][role] = service
        rows[role] = {"Id": "container-" + role, "Image": "sha256:old", "RestartCount": 0,
            "Config": {"Image": service["image"], "Cmd": service["command"], "Entrypoint": None,
                       "Env": [f"{k}={v}" for k, v in env.items()], "Labels": {}, "Hostname": role},
            "State": {"Running": True}, "HostConfig": {"PortBindings": redeploy.ports(service)},
            "Mounts": [{"Type": "volume", "Name": "existing-data", "Destination": "/data", "RW": True}] if backend else [],
            "NetworkSettings": {"Networks": {"existing-network": {}}}}
    return model, rows


class ConfigurationTests(unittest.TestCase):
    def test_current_runtime_and_both_web_names(self):
        for web in ("frontend", "waf-web"):
            model, rows = fixture(web)
            self.assertEqual(redeploy.validate(model, rows), "existing-data")

    def test_refuse_setting_port_volume_network_and_command_drift(self):
        changes = (
            lambda m: m["services"]["api"]["environment"].update(WAF_AGENT_MODE="stub"),
            lambda m: m["services"]["api"]["environment"].pop("WAF_DATA_ENCRYPTION_KEY"),
            lambda m: m["services"]["frontend"]["ports"][0].update(published="8080"),
            lambda m: m["volumes"]["data"].update(name="empty-data"),
            lambda m: m["networks"]["default"].update(name="other-network"),
            lambda m: m["services"]["api"].update(command=["other-command"]),
            lambda m: m["services"].update(gateway={}),
        )
        for change in changes:
            with self.subTest(change=change):
                model, rows = fixture()
                change(model)
                with self.assertRaises(DeployError):
                    redeploy.validate(model, rows)

    def test_only_image_build_and_external_resource_flags_change(self):
        model, rows = fixture()
        before = deepcopy(model)
        target = redeploy.target_model(model, "waf-test", "fixed")
        self.assertEqual(model, before)
        for role, service in target["services"].items():
            expected = deepcopy(model["services"][role])
            for key in ("image", "build", "pull_policy"):
                expected.pop(key, None)
            self.assertEqual({k: v for k, v in service.items() if k not in ("image", "build", "pull_policy")}, expected)
        self.assertEqual(len({target["services"][role]["image"] for role in redeploy.BACKENDS}), 1)
        self.assertEqual(target["volumes"]["data"], {"external": True, "name": "existing-data"})
        self.assertEqual(compose_model(json.loads(compose_text(target))), target)

    def test_key_mismatch_between_workers_rejected(self):
        model, rows = fixture()
        model["services"]["worker"]["environment"]["WAF_DATA_ENCRYPTION_KEY"] = "other"
        rows["worker"]["Config"]["Env"] = [f"{k}={v}" for k, v in model["services"]["worker"]["environment"].items()]
        with self.assertRaises(DeployError):
            redeploy.validate(model, rows)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        self.db = sqlite3.connect(self.source)
        self.addCleanup(self.db.close)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE alembic_version(version_num TEXT);
            INSERT INTO alembic_version VALUES ('0021_official_evaluation');
            CREATE TABLE analyses(id INTEGER PRIMARY KEY, status TEXT, encrypted BLOB);
            INSERT INTO analyses VALUES (1, 'completed', x'12345678');
            CREATE TABLE vllm_test_runs(id INTEGER PRIMARY KEY, status TEXT);
        """)
        self.db.commit()

    def test_wal_backup_integrity_and_no_plaintext_output(self):
        target = self.root / "backup.db"
        baseline = redeploy_db.backup(self.source, target)
        self.assertEqual(baseline["revision"], "0021_official_evaluation")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("12345678", json.dumps(baseline))
        redeploy_db.verify(target, baseline)
        with self.assertRaises(FileExistsError):
            redeploy_db.backup(self.source, target)

    def test_missing_source_does_not_create_empty_database(self):
        missing = self.root / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            redeploy_db.connect(missing)
        self.assertFalse(missing.exists())

    def test_added_tables_and_columns_allowed_but_existing_values_protected(self):
        baseline = redeploy_db.backup(self.source, self.root / "backup.db")
        self.db.executescript("ALTER TABLE analyses ADD COLUMN new_column TEXT; CREATE TABLE new_history(id TEXT);")
        self.db.commit()
        redeploy_db.verify(self.source, baseline)
        self.db.execute("UPDATE analyses SET encrypted=x'1111' WHERE id=1")
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError, "existing_database_values_changed"):
            redeploy_db.verify(self.source, baseline)

    def test_unfinished_analysis_or_model_validation_refused(self):
        redeploy_db.idle(self.db)
        for table, states in (("analyses", ("pending", "processing")), ("vllm_test_runs", ("pending", "running"))):
            for state in states:
                self.db.execute(f"INSERT INTO {table}(id,status) VALUES (2,?)", (state,))
                with self.assertRaisesRegex(RuntimeError, "unfinished_jobs"):
                    redeploy_db.idle(self.db)
                self.db.execute(f"DELETE FROM {table} WHERE id=2")

    def test_deletions_and_foreign_key_errors_refused(self):
        baseline = redeploy_db.backup(self.source, self.root / "backup.db")
        self.db.execute("DELETE FROM analyses")
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError, "existing_database_values_changed"):
            redeploy_db.verify(self.source, baseline)
        self.db.executescript("CREATE TABLE child(parent INTEGER REFERENCES analyses(id)); INSERT INTO child VALUES(99);")
        with self.assertRaisesRegex(RuntimeError, "foreign_keys_failed"):
            redeploy_db.check(self.db)


class WorkflowTests(unittest.TestCase):
    def task(self, root, fail=None):
        task = redeploy.Redeploy(Namespace(project=None, file=None, env_file=None))
        task.project, task.volume, task.web = "waf-test", "existing-data", "frontend"
        task.model, task.rows = fixture()
        task.db_code = "synthetic helper"
        actions = []
        def compose(*args, **kwargs):
            actions.append(("compose", *args))
            if fail == args[0]:
                raise DeployError("synthetic_failure")
            if args[0] == "stop":
                for row in task.rows.values():
                    row["State"]["Running"] = False
        def helper(image, command, **kwargs):
            actions.append(("helper", command[0], command[3] if command[0] == "python" else "migrate", kwargs.get("live", False)))
            if command[0] == "python" and command[3] == "backup":
                (task.directory / "backup.db").touch()
            if fail == "rehearsal" and command[0] == "alembic" and not kwargs.get("live"):
                raise DeployError("synthetic_rehearsal_failure")
        task.compose = compose
        task.helper = helper
        task.idle = lambda: actions.append(("idle",))
        task.inspect = lambda ids: list(task.rows.values())
        task.wait_api = lambda: actions.append(("ready",))
        task.verify_runtime = lambda model: actions.append(("verified",))
        return task, actions

    def execute(self, fail=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deploy").mkdir()
            (root / "deploy/redeploy_db.py").touch()
            task, actions = self.task(root, fail)
            with patch.object(redeploy, "ROOT", root), redirect_stdout(io.StringIO()):
                if fail:
                    with self.assertRaises(DeployError):
                        task.deploy()
                else:
                    task.deploy()
                    self.assertTrue((task.directory / "previous-compose.json").is_file())
                    self.assertEqual(json.loads((task.directory / "state.json").read_text())["phase"], "재배포 완료")
            return task, actions

    def test_order_backup_rehearsal_migration_then_start(self):
        task, actions = self.execute()
        self.assertLess(actions.index(("helper", "python", "backup", True)), actions.index(("helper", "alembic", "migrate", False)))
        self.assertLess(actions.index(("helper", "python", "verify", False)), actions.index(("helper", "alembic", "migrate", True)))
        up = [i for i, action in enumerate(actions) if action[:2] == ("compose", "up")]
        self.assertLess(actions.index(("helper", "python", "verify", True)), up[0])
        self.assertLess(actions.index(("ready",)), up[1])
        self.assertFalse(task.stopped)
        self.assertNotIn("down", repr(actions))

    def test_build_failure_does_not_stop_service(self):
        task, actions = self.execute("build")
        self.assertFalse(task.stopped)
        self.assertFalse(any(action[:2] == ("compose", "stop") for action in actions))

    def test_rehearsal_failure_never_migrates_live_or_restarts(self):
        task, actions = self.execute("rehearsal")
        self.assertTrue(task.stopped)
        self.assertNotIn(("helper", "alembic", "migrate", True), actions)
        self.assertFalse(any(action[:2] == ("compose", "up") for action in actions))

    def test_check_never_deploys(self):
        original_umask = os.umask(0o077)
        self.addCleanup(os.umask, original_umask)
        def discover(task):
            task.model, task.rows = fixture()
            task.project = "waf-test"
        with patch.object(redeploy.Redeploy, "discover", discover), patch.object(redeploy.Redeploy, "deploy") as deploy:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(redeploy.main(["--check"]), 0)
            deploy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
