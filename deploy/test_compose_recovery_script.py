"""Exercise the operator script with fake Docker, never real services or DBs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "docker-compose.sh"
CHECKS = SCRIPT.parents[1] / "deploy" / "compose_recovery_checks.py"


class ComposeRecoveryScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="waf-compose-script-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "deploy").mkdir()
        (self.root / "bin").mkdir()
        shutil.copyfile(SCRIPT, self.root / "scripts/docker-compose.sh")
        shutil.copyfile(CHECKS, self.root / "deploy/compose_recovery_checks.py")
        (self.root / ".env").write_text("SYNTHETIC_SECRET=never-print-this-value\n")
        (self.root / "docker-compose.yml").write_text("services: {}\n")
        (self.root / "deploy/redeploy_db.py").write_text("# mock fixture only\n")
        self.log = self.root / "docker-calls.jsonl"
        for name in ("bash", "dirname", "flock", "mktemp", "mkdir", "id"):
            (self.root / "bin" / name).symlink_to(shutil.which(name))
        docker = self.root / "bin/docker"
        docker.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
            import json, os, subprocess, sys
            args = sys.argv[1:]
            case = os.environ.get("FAKE_CASE", "")
            override = None
            if args[0] == "inspect":
                step = "check-image-id" if "{{.Image}}" in args else "inspect"
            elif args[0] == "image":
                step = "image-inspect"
            elif args[0] == "ps":
                step = "consumers"
            elif args[0] == "run":
                step = "docker-run-help" if "--help" in args else "config-check"
            else:
                files = [i for i, value in enumerate(args) if value == "-f"]
                action = args[files[-1] + 2:]
                if len(files) > 1:
                    with open(args[files[-1] + 1]) as stream:
                        override = json.load(stream)
                if action[0] == "config":
                    step = "config-json" if "json" in action else "config-images" if "--images" in action else "config-quiet"
                elif action[0] == "ps":
                    step = "existing-api" if "-aq" in action else "status"
                elif action[0] != "run":
                    step = action[0]
                elif "backup" in action:
                    step = "backup"
                elif "verify" in action:
                    step = "verify"
                elif "--stopped-and-backed-up" in action:
                    step = "permissions"
                elif "upgrade" in action:
                    step = "migration"
                elif "current" in action:
                    step = "revision"
                else:
                    step = "mount-check"
            log = os.environ["FAKE_DOCKER_LOG"]
            previous = []
            if os.path.exists(log):
                with open(log) as stream:
                    previous = [json.loads(line) for line in stream]
            with open(log, "a") as stream:
                stream.write(json.dumps({"step": step, "args": args, "cwd": os.getcwd(), "override": override}) + "\\n")
            # Reproduce a server whose Compose does not accept --pull.
            if args[0] == "compose" and any(value.startswith("--pull") for value in args):
                sys.exit("unknown flag: --pull")
            if step == os.environ.get("FAKE_FAIL"):
                sys.exit(2)
            if case == "missing-local-image" and step == "image-inspect":
                sys.exit(2)
            if case == "unsupported-policy" and step == "config-quiet" and override:
                sys.exit(2)
            if case == "preservation-failure" and step == "verify" and any(p["step"] == "verify" for p in previous):
                sys.exit(2)
            if step == "existing-api":
                print("" if case == "missing-api" else "api-one\\napi-two" if case == "multiple-api" else "api-one")
            elif step == "check-image-id":
                print("not-an-image-id" if case == "invalid-check-image" else "sha256:" + "a" * 64)
            elif step == "docker-run-help":
                print("docker run options" if case == "unsupported-docker-pull" else "--pull string (always|missing|never)")
            elif step == "config-check":
                mount = args[args.index("--mount") + 1]
                helper = next(item[4:] for item in mount.split(",") if item.startswith("src="))
                data = sys.stdin.read()
                if case == "config-check-failure":
                    sys.exit(2)
                checked = subprocess.run([sys.executable, helper, "config", args[-1]],
                                         input=data, text=True, capture_output=True)
                sys.stdout.write(checked.stdout)
                sys.stderr.write(checked.stderr)
                sys.exit(checked.returncode)
            elif step == "inspect":
                print("" if case == "readonly-mount" else "waf-standalone_waf-data")
            elif step == "consumers" and case == "active-consumer":
                print("another-container")
            elif step == "config-images" and case != "empty-images":
                print("waf-standalone-api\\nwaf-standalone-worker\\nwaf-standalone-model-tester\\nwaf-standalone-frontend")
            elif step == "config-json":
                url = "sqlite+pysqlite:////data/waf.db"
                if case == "different-url":
                    url = "sqlite+pysqlite:////other/waf.db"
                name = "other-volume" if case == "different-volume" else "waf-standalone_waf-data"
                services = {"api": {
                    "environment": {"WAF_DATABASE_URL": url, "PASSWORD": "never-print-this-value"},
                    "volumes": [{"type": "volume", "source": "waf-data", "target": "/data"}]
                }, "worker": {}, "model-tester": {}, "frontend": {}}
                print(json.dumps({"services": services, "volumes": {"waf-data": {"name": name}}}))
        '''))
        docker.chmod(0o700)

    def run_script(self, case="", fail="", *, host_python=True):
        environment = os.environ.copy()
        for name in ("WAF_PROJECT", "WAF_ENV_FILE", "WAF_COMPOSE_FILE"):
            environment.pop(name, None)
        path = str(self.root / "bin")
        if host_python:
            path += os.pathsep + environment["PATH"]
        environment.update(PATH=path,
                           FAKE_DOCKER_LOG=str(self.log), FAKE_CASE=case, FAKE_FAIL=fail)
        result = subprocess.run(["bash", str(self.root / "scripts/docker-compose.sh")],
                                cwd="/tmp", env=environment, capture_output=True,
                                text=True, timeout=20)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        self.assertNotIn("never-print-this-value", result.stdout + result.stderr)
        self.assertTrue(all(call["cwd"] == str(self.root) for call in calls))
        return result, calls

    def test_complete_order_and_no_build_pull_or_git(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["step"] for call in calls if call["step"] != "config-check"], [
            "config-quiet", "existing-api", "inspect", "check-image-id", "image-inspect",
            "docker-run-help", "config-json",
            "config-quiet", "config-images", *["image-inspect"] * 4, "stop",
            "consumers", "mount-check", "backup", "verify", "permissions",
            "migration", "verify", "revision", "up", "status"])
        for call in calls:
            args = call["args"]
            if args[0] == "compose":
                self.assertEqual(args[:7], ["compose", "--env-file", ".env", "-p",
                                           "waf-standalone", "-f", "docker-compose.yml"])
            self.assertNotIn("build", args)
            self.assertNotIn("down", args)
            if args[0] == "compose":
                self.assertFalse(any(value.startswith("--pull") for value in args))
            if call["step"] in ("mount-check", "backup", "verify", "permissions", "migration", "revision", "up"):
                self.assertEqual(call["override"], {"services": {
                    name: {"pull_policy": "never"}
                    for name in ("api", "worker", "model-tester", "frontend")}})
            if call["step"] in ("migration", "revision"):
                self.assertEqual(args[args.index("--user") + 1], "10001:10001")
        bootstrap = next(call["args"] for call in calls if call["step"] == "config-check")
        self.assertIn("--pull=never", bootstrap)
        self.assertEqual(bootstrap[bootstrap.index("--network") + 1], "none")
        self.assertIn("--read-only", bootstrap)
        self.assertNotIn("-e", bootstrap)
        self.assertFalse(any("/data" in value or ".env" in value for value in bootstrap))
        backups = list((self.root / ".local-deploy").glob("db-recovery.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)

    def test_failure_does_not_execute_any_later_step(self):
        for failed in ("stop", "mount-check", "backup", "verify", "permissions", "migration", "up"):
            with self.subTest(failed=failed):
                if self.log.exists():
                    self.log.unlink()
                result, calls = self.run_script(fail=failed)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls[-1]["step"], failed)
                self.assertIn("중단된 단계:", result.stderr)

    def test_wrong_deployment_stops_before_service_stop(self):
        for case in ("missing-api", "multiple-api", "readonly-mount", "different-url", "different-volume"):
            with self.subTest(case=case):
                if self.log.exists():
                    self.log.unlink()
                result, calls = self.run_script(case=case)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("stop", [call["step"] for call in calls])

    def test_active_consumer_prevents_backup_and_permissions(self):
        result, calls = self.run_script(case="active-consumer")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1]["step"], "consumers")

    def test_missing_images_or_unsupported_policy_stops_before_services(self):
        for case in ("missing-local-image", "empty-images", "unsupported-policy",
                     "invalid-check-image", "unsupported-docker-pull", "config-check-failure"):
            with self.subTest(case=case):
                if self.log.exists():
                    self.log.unlink()
                result, calls = self.run_script(case=case)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("stop", [call["step"] for call in calls])

    def test_server_path_without_python_still_completes(self):
        for name in ("python", "python3"):
            self.assertIsNone(shutil.which(name, path=str(self.root / "bin")))
        result, calls = self.run_script(host_python=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1]["step"], "status")
        self.assertNotRegex(SCRIPT.read_text(), r"\bpython3?\s+-c(?:\s|$)")

    def test_override_contains_no_resolved_environment_and_keeps_source_files(self):
        before = (self.root / "docker-compose.yml").read_bytes()
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        overrides = list((self.root / ".local-deploy").glob("compose-local-images.*.json"))
        self.assertEqual(len(overrides), 1)
        self.assertNotIn("never-print-this-value", overrides[0].read_text())
        self.assertEqual(overrides[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "docker-compose.yml").read_bytes(), before)

    def test_failed_preservation_prevents_service_start(self):
        result, calls = self.run_script(case="preservation-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1]["step"], "verify")
        self.assertIn("migration", [call["step"] for call in calls])
        self.assertNotIn("up", [call["step"] for call in calls])

    def test_missing_env_never_calls_docker(self):
        (self.root / ".env").unlink()
        result, calls = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
