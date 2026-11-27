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
                       "Env": [f"{k}={v}" for k, v in env.items()],
                       "Labels": {"com.docker.compose.project": "waf-test",
                                  "com.docker.compose.service": role,
                                  "com.docker.compose.project.working_dir": str(redeploy.ROOT)},
                       "Hostname": role},
            "State": {"Running": True, "Status": "running", "Paused": False,
                      "Restarting": False, "Dead": False,
                      "StartedAt": "2026-11-27T01:00:00Z", "FinishedAt": "0001-01-01T00:00:00Z"},
            "HostConfig": {"PortBindings": redeploy.ports(service)},
            "Mounts": [{"Type": "volume", "Name": "existing-data", "Destination": "/data", "RW": True}] if backend else [],
            "NetworkSettings": {"Networks": {"existing-network": {}}}}
    return model, rows


def stop_services(rows, roles=None, status="exited"):
    for role in rows if roles is None else roles:
        rows[role]["State"].update(Running=False, Status=status,
                                  FinishedAt="2026-11-27T02:00:00Z")


class ConfigurationTests(unittest.TestCase):
    def test_checked_in_compose_workers_use_the_same_current_launcher(self):
        root = Path(__file__).resolve().parents[1]
        command = "command: " + json.dumps(list(redeploy.WORKER_COMMAND))
        for filename in ("docker-compose.yml", "docker-compose.production.yml"):
            with self.subTest(compose=filename):
                self.assertEqual((root / filename).read_text(encoding="utf-8").count(command), 2)

    def test_web_probe_keeps_production_gateway_admission_closed(self):
        production = {"volumes": [{"target": "/etc/nginx/waf-security"}]}
        self.assertEqual(redeploy.web_probe_command({}), ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"])
        command = redeploy.web_probe_command(production)
        self.assertEqual(command[:2], ["sh", "-c"])
        self.assertIn("status != 403", command[2])
        self.assertNotIn("--header", command[2])
        self.assertNotIn("X-Forwarded", command[2])

    def test_current_runtime_and_both_web_names(self):
        for web in ("frontend", "waf-web"):
            model, rows = fixture(web)
            self.assertEqual(redeploy.validate(model, rows), "existing-data")

    def test_existing_stopped_services_only_allowed_for_preflight(self):
        for web in ("frontend", "waf-web"):
            for stopped in (("api",), ("worker",), ("model-tester",), ("api", "worker", "model-tester", web)):
                for status in ("exited", "created"):
                    with self.subTest(web=web, stopped=stopped, status=status):
                        model, rows = fixture(web)
                        stop_services(rows, stopped, status)
                        self.assertEqual(redeploy.validate(model, rows, allow_stopped=True), "existing-data")
                        with self.assertRaises(DeployError):
                            redeploy.validate(model, rows)

    def test_stopped_services_do_not_bypass_configuration_or_missing_service_checks(self):
        for mutation in (
            lambda model, rows: model["services"]["api"]["environment"].update(WAF_AGENT_MODE="stub"),
            lambda model, rows: model["volumes"]["data"].update(name="another-volume"),
            lambda model, rows: model["services"]["frontend"]["ports"][0].update(published="28080"),
            lambda model, rows: rows.pop("worker"),
        ):
            with self.subTest(mutation=mutation):
                model, rows = fixture()
                stop_services(rows)
                mutation(model, rows)
                with self.assertRaises(DeployError):
                    redeploy.validate(model, rows, allow_stopped=True)

    def test_transitional_invalid_or_unknown_states_are_always_refused(self):
        changes = (
            {"Running": True, "Paused": True},
            {"Running": True, "Restarting": True},
            {"Running": False, "Status": "dead", "Dead": True},
            {"Running": False, "Status": "removing"},
            {"Running": False, "Status": "restarting", "Restarting": True},
            {"Running": False, "Status": "unknown"},
            {"Running": False, "Status": "running"},
            {"Running": True, "Status": "exited"},
            {"Running": False, "Status": None},
        )
        for change in changes:
            with self.subTest(change=change):
                model, rows = fixture()
                rows["api"]["State"].update(change)
                with self.assertRaises(DeployError):
                    redeploy.validate(model, rows, allow_stopped=True)
                with self.assertRaises(DeployError):
                    redeploy.lifecycle_snapshot(rows)

    def test_lifecycle_snapshot_detects_restarts_without_retaining_secrets(self):
        _, rows = fixture()
        original = redeploy.lifecycle_snapshot(rows)
        self.assertNotIn("synthetic$test", json.dumps(original))
        self.assertNotIn("WAF_DATA_ENCRYPTION_KEY", json.dumps(original))
        for field, value in (("StartedAt", "2026-11-27T03:00:00Z"),
                             ("FinishedAt", "2026-11-27T02:59:00Z")):
            with self.subTest(field=field):
                changed = deepcopy(rows)
                changed["api"]["State"][field] = value
                self.assertNotEqual(redeploy.lifecycle_snapshot(changed), original)
        for field, value in (("Id", "replacement-api"), ("RestartCount", 1)):
            with self.subTest(field=field):
                changed = deepcopy(rows)
                changed["api"][field] = value
                self.assertNotEqual(redeploy.lifecycle_snapshot(changed), original)
        changed = deepcopy(rows)
        stop_services(changed, ("worker",))
        self.assertNotEqual(redeploy.lifecycle_snapshot(changed), original)

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

    def test_only_image_launcher_runtime_identity_and_external_resource_flags_change(self):
        model, rows = fixture()
        before = deepcopy(model)
        target = redeploy.target_model(model, "waf-test", "fixed")
        self.assertEqual(model, before)
        for role, service in target["services"].items():
            expected = deepcopy(model["services"][role])
            if role in ("worker", "model-tester"):
                expected["command"] = list(redeploy.WORKER_COMMAND)
            for key in ("image", "build", "pull_policy", "user", "sysctls"):
                expected.pop(key, None)
            self.assertEqual({k: v for k, v in service.items() if k not in ("image", "build", "pull_policy", "user", "sysctls")}, expected)
            self.assertEqual(service["user"], "10001:10001" if role in redeploy.BACKENDS else "101:101")
            if role not in redeploy.BACKENDS:
                self.assertEqual(service["sysctls"]["net.ipv4.ip_unprivileged_port_start"], "0")
            self.assertEqual(service.get("ports"), model["services"][role].get("ports"))
        self.assertEqual(len({target["services"][role]["image"] for role in redeploy.BACKENDS}), 1)
        self.assertEqual(target["volumes"]["data"], {"external": True, "name": "existing-data"})
        self.assertEqual(compose_model(json.loads(compose_text(target))), target)

    def test_legacy_workers_upgrade_launcher_without_changing_execution_settings(self):
        for web in ("frontend", "waf-web"):
            with self.subTest(web=web):
                model, _ = fixture(web)
                for role, worker_role in (("worker", "analysis"), ("model-tester", "model_test")):
                    model["services"][role]["command"] = ["python", "-m", "app.worker"]
                    model["services"][role]["environment"]["WAF_WORKER_ROLE"] = worker_role
                original = deepcopy(model)
                target = redeploy.target_model(model, "waf-test", "fixed")
                self.assertEqual(model, original)
                for role in ("worker", "model-tester"):
                    self.assertEqual(target["services"][role]["command"],
                                     ["python", "-m", "app.worker_entrypoint_v239"])
                    self.assertEqual(target["services"][role]["environment"],
                                     original["services"][role]["environment"])
                    self.assertEqual(target["services"][role]["environment"]["WAF_AGENT_MODE"], "moduagent")
                for role in ("api", web):
                    self.assertEqual(target["services"][role]["command"],
                                     original["services"][role]["command"])
                # Each service owns its command list; modifying one cannot
                # silently change the other worker's launch configuration.
                target["services"]["worker"]["command"].append("--synthetic")
                self.assertEqual(target["services"]["model-tester"]["command"],
                                 ["python", "-m", "app.worker_entrypoint_v239"])

    def test_key_mismatch_between_workers_rejected(self):
        model, rows = fixture()
        model["services"]["worker"]["environment"]["WAF_DATA_ENCRYPTION_KEY"] = "other"
        rows["worker"]["Config"]["Env"] = [f"{k}={v}" for k, v in model["services"]["worker"]["environment"].items()]
        with self.assertRaises(DeployError):
            redeploy.validate(model, rows)

    def test_target_keeps_unrelated_sysctls_and_overrides_root_identity(self):
        model, _ = fixture()
        model["services"]["frontend"]["sysctls"] = {"net.ipv4.tcp_syncookies": "1"}
        model["services"]["api"]["user"] = "0:0"
        target = redeploy.target_model(model, "waf-test", "fixed")
        self.assertEqual(target["services"]["api"]["user"], "10001:10001")
        self.assertEqual(target["services"]["frontend"]["sysctls"], {
            "net.ipv4.tcp_syncookies": "1", "net.ipv4.ip_unprivileged_port_start": "0"})


class ComposePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = self.root / "original"
        self.original.mkdir()
        (self.original / "compose.json").write_text("{}")
        self.caller = self.root / "caller"
        self.caller.mkdir()
        self.labels = {"com.docker.compose.project.working_dir": str(self.original),
                       "com.docker.compose.project.config_files": "compose.json"}

    def command(self, env=None):
        return redeploy.existing_compose_command("synthetic", self.labels, env_file=env, cwd=self.caller)

    def test_null_device_from_previous_redeploy_is_allowed(self):
        self.labels["com.docker.compose.project.environment_file"] = "/dev/null"
        command = self.command()
        self.assertEqual(command[command.index("--env-file") + 1], "/dev/null")
        self.assertEqual(command[command.index("-f") + 1], str(self.original / "compose.json"))
        self.assertEqual(self.command("/dev/null")[command.index("--env-file") + 1], "/dev/null")

    def test_relative_recorded_paths_use_original_directory(self):
        (self.original / ".env").write_text("SYNTHETIC_ONLY=yes")
        self.labels["com.docker.compose.project.environment_file"] = ".env"
        command = self.command()
        self.assertEqual(command[command.index("--env-file") + 1], str(self.original / ".env"))

    def test_explicit_env_overrides_stale_label_and_keeps_comma_in_filename(self):
        (self.caller / "my,settings.env").write_text("SYNTHETIC_ONLY=yes")
        self.labels["com.docker.compose.project.environment_file"] = "/missing/.env"
        command = self.command("my,settings.env")
        self.assertEqual(command[command.index("--env-file") + 1], str(self.caller / "my,settings.env"))

    def test_missing_env_is_not_silently_ignored(self):
        self.labels["com.docker.compose.project.environment_file"] = ".env.missing"
        with self.assertRaisesRegex(DeployError, "--env-file") as caught:
            self.command()
        self.assertIn(str(self.original / ".env.missing"), str(caught.exception))

    def test_other_special_devices_and_directories_are_refused(self):
        for path in ("/dev/zero", str(self.original)):
            with self.subTest(path=path), self.assertRaises(DeployError):
                self.command(path)

    def test_multiple_recorded_env_files_keep_order(self):
        for name in ("base.env", "override.env"):
            (self.original / name).write_text("SYNTHETIC_ONLY=yes")
        self.labels["com.docker.compose.project.environment_file"] = "base.env,override.env"
        command = self.command()
        values = [command[i + 1] for i, item in enumerate(command) if item == "--env-file"]
        self.assertEqual(values, [str(self.original / name) for name in ("base.env", "override.env")])


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


class IdlePreflightTests(unittest.TestCase):
    def task(self, stopped=False):
        task = redeploy.Redeploy(Namespace(project=None, file=None, env_file=None))
        task.model, task.rows = fixture()
        task.volume = "existing-data"
        task.db_code = "synthetic read-only database check"
        if stopped:
            stop_services(task.rows, ("api",))
        return task

    def test_running_api_uses_existing_exec_without_starting_services(self):
        task = self.task()
        with patch.object(task, "run", return_value="") as run:
            task.idle()
        command = run.call_args.args[0]
        self.assertEqual(command, ["docker", "exec", "container-api", "python", "-c",
                                   task.db_code, "idle", "/data/waf.db"])

    def test_stopped_api_uses_existing_immutable_image_and_read_only_exact_volume(self):
        task = self.task(stopped=True)
        with patch.object(task, "run", return_value="") as run:
            task.idle()
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index("--user") + 1], "0:0")
        self.assertIn("--read-only", command)
        mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
        self.assertEqual(mounts, ["type=volume,source=existing-data,target=/data,readonly"])
        self.assertEqual(command[command.index("--entrypoint"):],
                         ["--entrypoint", "python", "sha256:old", "-c", task.db_code,
                          "idle", "/data/waf.db"])
        self.assertNotIn("old-image:api", command)
        self.assertNotIn("compose", command)
        self.assertIsNone(task.directory)

    def test_failed_stopped_database_check_never_starts_api_or_workers(self):
        task = self.task(stopped=True)
        with patch.object(task, "run", side_effect=DeployError("synthetic database failure")) as run:
            with self.assertRaises(DeployError):
                task.idle()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(task.rows["api"]["State"]["Running"])

    def test_postdeploy_runtime_still_rejects_stopped_containers(self):
        task = self.task(stopped=True)
        task.web = "frontend"
        task.compose = lambda *args, **kwargs: "container-api container-worker container-model-tester container-frontend"
        task.inspect = lambda ids: list(task.rows.values())
        with self.assertRaises(DeployError):
            task.verify_runtime(task.model)

    def test_discovery_accepts_existing_stopped_stack_without_starting_it(self):
        for stopped in (("api",), ("worker",), ("api", "worker", "model-tester", "frontend")):
            with self.subTest(stopped=stopped):
                task = self.task()
                stop_services(task.rows, stopped)
                model, rows = task.model, task.rows
                commands = []
                task.runner.env = {}
                def run(command, *args, **kwargs):
                    commands.append(command)
                    if command[:3] == ["docker", "compose", "version"]:
                        return "Docker Compose version v2.synthetic"
                    if command[:3] == ["docker", "context", "inspect"]:
                        return json.dumps("unix:///var/run/docker.sock")
                    if command[:3] == ["docker", "ps", "-aq"]:
                        return " ".join(row["Id"] for row in rows.values())
                    if command[:3] == ["docker", "ps", "-q"]:
                        return " ".join(row["Id"] for row in rows.values() if row["State"]["Running"])
                    if command[:2] == ["docker", "inspect"]:
                        return json.dumps([row for row in rows.values() if row["Id"] in command[2:]])
                    if command[:3] == ["docker", "compose", "synthetic-config"]:
                        return json.dumps(model)
                    if command[:2] in (["docker", "exec"], ["docker", "run"]):
                        return ""
                    self.fail("Unexpected Docker command: " + repr(command))
                task.run = run
                with patch.object(redeploy, "existing_compose_command", return_value=["docker", "compose", "synthetic-config"]):
                    task.discover()
                self.assertEqual(task.volume, "existing-data")
                self.assertEqual(task.project, "waf-test")
                self.assertEqual([role for role, row in task.rows.items() if not row["State"]["Running"]], list(stopped))
                self.assertFalse(any("up" in command or "start" in command or "stop" in command or "build" in command for command in commands))

    def test_no_existing_containers_remains_a_new_install_error(self):
        task = self.task()
        task.runner.env = {}
        def run(command, *args, **kwargs):
            if command[:3] == ["docker", "compose", "version"]:
                return "Docker Compose version v2.synthetic"
            if command[:3] == ["docker", "context", "inspect"]:
                return json.dumps("unix:///var/run/docker.sock")
            if command[:3] == ["docker", "ps", "-aq"]:
                return ""
            self.fail("Unexpected Docker command: " + repr(command))
        task.run = run
        with self.assertRaisesRegex(DeployError, "신규 설치"):
            task.discover()


class WorkflowTests(unittest.TestCase):
    def task(self, root, fail=None, initially_stopped=(), lifecycle_change=None):
        task = redeploy.Redeploy(Namespace(project=None, file=None, env_file=None))
        task.project, task.volume, task.web = "waf-test", "existing-data", "frontend"
        task.model, task.rows = fixture()
        stop_services(task.rows, initially_stopped)
        task.db_code = "synthetic helper"
        actions = []
        def compose(*args, **kwargs):
            actions.append(("compose", *args))
            if fail == args[0]:
                raise DeployError("synthetic_failure")
            if args[0] == "build" and lifecycle_change:
                lifecycle_change(task.rows)
            if args[0] == "stop":
                for row in task.rows.values():
                    row["State"]["Running"] = False
                    row["State"]["Status"] = "exited"
        def helper(image, command, **kwargs):
            action = ("permissions" if command[0] == "python" and command[1] == "/opt/waf-data-permissions.py"
                      else command[3] if command[0] == "python" else "migrate")
            actions.append(("helper", command[0], action, kwargs.get("live", False)))
            if action == "backup":
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

    def execute(self, fail=None, initially_stopped=(), lifecycle_change=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deploy").mkdir()
            (root / "deploy/redeploy_db.py").touch()
            task, actions = self.task(root, fail, initially_stopped, lifecycle_change)
            with patch.object(redeploy, "ROOT", root), redirect_stdout(io.StringIO()):
                if fail or lifecycle_change:
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
        self.assertLess(actions.index(("helper", "python", "verify", True)), actions.index(("helper", "python", "permissions", True)))
        self.assertLess(actions.index(("helper", "python", "permissions", True)), up[0])
        self.assertLess(actions.index(("ready",)), up[1])
        self.assertFalse(task.stopped)
        self.assertNotIn("down", repr(actions))

    def test_build_failure_does_not_stop_service(self):
        task, actions = self.execute("build")
        self.assertFalse(task.stopped)
        self.assertFalse(any(action[:2] == ("compose", "stop") for action in actions))

    def test_deploy_starts_new_services_after_backup_when_old_services_are_stopped(self):
        for stopped in (("api",), ("worker",), ("api", "worker", "model-tester", "frontend")):
            with self.subTest(stopped=stopped):
                task, actions = self.execute(initially_stopped=stopped)
                up = [action for action in actions if action[:2] == ("compose", "up")]
                self.assertEqual(len(up), 2)
                self.assertEqual(up[0][-1], "api")
                self.assertEqual(up[1][-3:], ("worker", "model-tester", "frontend"))
                self.assertLess(actions.index(("helper", "python", "verify", True)), actions.index(up[0]))
                self.assertFalse(task.stopped)

    def test_build_failure_keeps_initially_stopped_services_stopped(self):
        for stopped in (("api",), ("worker",), ("api", "worker", "model-tester", "frontend")):
            with self.subTest(stopped=stopped):
                task, actions = self.execute("build", initially_stopped=stopped)
                for role, row in task.rows.items():
                    self.assertEqual(row["State"]["Running"], role not in stopped)
                self.assertFalse(task.stopped)
                self.assertFalse(any(action[:2] in (("compose", "stop"), ("compose", "up")) for action in actions))

    def test_state_change_during_build_stops_before_stop_backup_or_start(self):
        def restarted(rows):
            rows["api"]["State"]["StartedAt"] = "2026-11-27T03:00:00Z"
            rows["api"]["RestartCount"] += 1
        task, actions = self.execute(lifecycle_change=restarted)
        self.assertFalse(task.stopped)
        self.assertFalse(any(action[0] == "helper" or action[:2] in (("compose", "stop"), ("compose", "up")) for action in actions))

    def test_external_start_of_stopped_service_during_build_is_detected(self):
        def started(rows):
            rows["api"]["State"].update(Running=True, Status="running", StartedAt="2026-11-27T03:00:00Z")
        task, actions = self.execute(initially_stopped=("api",), lifecycle_change=started)
        self.assertFalse(task.stopped)
        self.assertFalse(any(action[0] == "helper" or action[:2] in (("compose", "stop"), ("compose", "up")) for action in actions))

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

    def test_check_with_all_stopped_never_deploys_or_starts_services(self):
        original_umask = os.umask(0o077)
        self.addCleanup(os.umask, original_umask)
        def discover(task):
            task.model, task.rows = fixture()
            stop_services(task.rows)
            task.project = "waf-test"
        with patch.object(redeploy.Redeploy, "discover", discover), patch.object(redeploy.Redeploy, "deploy") as deploy, \
             patch.object(redeploy.Redeploy, "run") as run:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(redeploy.main(["--check"]), 0)
            deploy.assert_not_called()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
