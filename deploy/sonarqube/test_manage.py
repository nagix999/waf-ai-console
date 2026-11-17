"""Offline isolation and credential-safety tests; Docker and HTTP are mocked."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler
from http.client import HTTPException


_SPEC = importlib.util.spec_from_file_location("sonarqube_manage", Path(__file__).with_name("manage.py"))
manage = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(manage)


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "repository"
        self.root.mkdir()
        self.state = self.root / ".local-sonarqube"
        for name, value in (("ROOT", self.root), ("STATE", self.state), ("SENSITIVE", [])):
            replacement = patch.object(manage, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        # Any unmocked external process or HTTP operation fails immediately.
        for name in ("subprocess.Popen", "build_opener"):
            replacement = patch.object(
                manage.subprocess if name.startswith("subprocess") else manage,
                "Popen" if name.startswith("subprocess") else "build_opener",
                side_effect=AssertionError("External execution is forbidden in offline tests"),
            )
            replacement.start()
            self.addCleanup(replacement.stop)
        self.config = {"version": 1, "port": 19000, "images": {
            "server": "sonarqube@sha256:" + "a" * 64,
            "postgres": "postgres@sha256:" + "b" * 64,
            "scanner": "sonarsource/sonar-scanner-cli@sha256:" + "c" * 64,
            "browser": "nginx@sha256:" + "d" * 64,
        }}

    def make_state(self):
        manage.private_directory(self.state)

    def save_settings(self, config=None):
        self.make_state()
        manage.write_new(self.state / "settings.json", json.dumps(config if config is not None else self.config))

    def api_response(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        opener = MagicMock()
        opener.open.return_value = response
        return opener

    def submitted_run(self, name="20261116-120000-abcdef", task_id="synthetic-task-123"):
        self.make_state()
        runs = self.state / "runs"
        manage.private_directory(runs)
        run = runs / name
        manage.private_directory(run)
        manage.private_directory(run / "scanner")
        manage.write_new(run / "scanner" / "report-task.txt", "ceTaskId=" + task_id + "\n")
        return run

    def test_clean_environment_does_not_inherit_credentials_proxies_or_remote_docker(self):
        unsafe = {name: "synthetic-sensitive-value" for name in (
            "WAF_ADMIN_PASSWORD", "WAF_DATA_ENCRYPTION_KEY", "SONAR_TOKEN", "SONAR_HOST_URL",
            "COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "DOCKER_HOST", "DOCKER_CONTEXT",
            "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY",
        )}
        safe = {"PATH": "/usr/bin", "HOME": "/synthetic-user", "LANG": "C.UTF-8"}
        with patch.dict(os.environ, {**unsafe, **safe}, clear=True):
            self.assertEqual(manage.clean_environment(), safe)
            self.assertEqual(manage.clean_environment({"SONAR_SCAN_TOKEN": "explicit-token"}),
                             {**safe, "SONAR_SCAN_TOKEN": "explicit-token"})

    def test_private_directory_has_private_mode_and_rejects_existing_public_directory(self):
        self.make_state()
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.state.chmod(0o755)
        with self.assertRaisesRegex(manage.SonarError, "permissions"):
            manage.private_directory(self.state)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o755)

    def test_private_directory_rejects_symlink(self):
        real = self.base / "real"
        real.mkdir(mode=0o700)
        self.state.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(manage.SonarError, "symbolic link"):
            self.make_state()

    def test_secret_write_is_0600_and_never_overwrites(self):
        self.make_state()
        target = self.state / "secret"
        manage.write_new(target, "synthetic-value\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(manage.read_private(target), "synthetic-value")
        with self.assertRaises(FileExistsError):
            manage.write_new(target, "replacement")
        self.assertEqual(target.read_text(), "synthetic-value\n")

    def test_secret_write_and_read_reject_symlinks(self):
        self.make_state()
        real = self.base / "preserve"
        real.write_text("unchanged")
        real.chmod(0o600)
        target = self.state / "secret"
        target.symlink_to(real)
        with self.assertRaises(OSError):
            manage.write_new(target, "replacement")
        with self.assertRaises(OSError):
            manage.read_private(target)
        self.assertEqual(real.read_text(), "unchanged")

    def test_secret_read_rejects_public_mode_and_oversize(self):
        self.make_state()
        target = self.state / "secret"
        manage.write_new(target, "small")
        target.chmod(0o644)
        with self.assertRaisesRegex(manage.SonarError, "permissions"):
            manage.read_private(target)
        target.chmod(0o600)
        target.write_text("x" * 65537)
        with self.assertRaisesRegex(manage.SonarError, "limit"):
            manage.read_private(target)

    def test_secret_read_rejects_non_regular_file(self):
        self.make_state()
        target = self.state / "pipe"
        os.mkfifo(target, mode=0o600)
        with self.assertRaisesRegex(manage.SonarError, "ownership/permissions"):
            manage.read_private(target)

    def test_generated_secret_reuses_private_existing_value_and_scrubs_it(self):
        self.make_state()
        first = manage.generated_secret("admin-password")
        second = manage.generated_secret("admin-password")
        self.assertEqual(first, second)
        self.assertEqual(stat.S_IMODE((self.state / "admin-password").stat().st_mode), 0o600)
        self.assertNotIn(first, manage.scrub("before " + first + " after"))
        self.assertIn("[REDACTED]", manage.scrub(first))

    def test_generated_secret_rejects_invalid_existing_value(self):
        self.make_state()
        manage.write_new(self.state / "admin-password", "bad whitespace secret")
        with self.assertRaisesRegex(manage.SonarError, "Invalid local credential"):
            manage.generated_secret("admin-password")

    def test_existing_config_does_not_pull_images_or_change_port(self):
        self.save_settings()
        with patch.object(manage, "command") as command:
            self.assertEqual(manage.load_config(create=True, port=19000), self.config)
            with self.assertRaisesRegex(manage.SonarError, "keeps its port"):
                manage.load_config(create=True, port=19001)
        command.assert_not_called()
        self.assertEqual(json.loads((self.state / "settings.json").read_text()), self.config)

    def test_saved_config_rejects_invalid_port_types_and_ranges(self):
        for port in (True, False, "19000", 1023, 65536, None):
            with self.subTest(port=port):
                target = self.state / "settings.json"
                self.make_state()
                if target.exists():
                    target.unlink()
                config = {**self.config, "port": port}
                manage.write_new(target, json.dumps(config))
                with self.assertRaisesRegex(manage.SonarError, "port"):
                    manage.load_config()

    def test_saved_config_rejects_mutable_tags_missing_roles_and_extra_settings(self):
        cases = [
            {**self.config, "images": {**self.config["images"], "server": "sonarqube:community"}},
            {**self.config, "images": {"server": self.config["images"]["server"]}},
            {**self.config, "images": {**self.config["images"], "server": "sonarqube@sha256:abc"}},
            {**self.config, "unexpected": "value"},
            {**self.config, "version": 99},
        ]
        for config in cases:
            with self.subTest(config=config):
                self.make_state()
                target = self.state / "settings.json"
                if target.exists():
                    target.unlink()
                manage.write_new(target, json.dumps(config))
                with self.assertRaises(manage.SonarError):
                    manage.load_config()

    def test_role_images_are_official_and_cannot_be_swapped_or_repointed(self):
        self.assertTrue(manage.valid_image("server", "docker.io/library/sonarqube@sha256:" + "a" * 64))
        self.assertTrue(manage.valid_image("postgres", "docker.io/library/postgres@sha256:" + "b" * 64))
        self.assertTrue(manage.valid_image("scanner", "docker.io/sonarsource/sonar-scanner-cli@sha256:" + "c" * 64))
        self.assertTrue(manage.valid_image("browser", "docker.io/library/nginx@sha256:" + "d" * 64))
        for role, image in (("server", self.config["images"]["postgres"]),
                            ("scanner", "outside.invalid/sonarsource/sonar-scanner-cli@sha256:" + "c" * 64),
                            ("server", "unapproved/sonarqube@sha256:" + "a" * 64),
                            ("unknown", self.config["images"]["server"])):
            with self.subTest(role=role, image=image):
                self.assertFalse(manage.valid_image(role, image))

    def test_direct_config_creation_rejects_invalid_port_before_external_command(self):
        for port in (False, True, "19000", 0, 1023, 65536):
            with self.subTest(port=port):
                with self.assertRaisesRegex(manage.SonarError, "port"):
                    manage.load_config(create=True, port=port)

    def test_existing_volumes_without_settings_block_initialization(self):
        with patch.object(manage, "command", return_value="preserved-sonar-volume"), \
                patch.object(manage, "pinned_image") as pin:
            with self.assertRaisesRegex(manage.SonarError, "Existing SonarQube volumes"):
                manage.load_config(create=True)
        pin.assert_not_called()
        self.assertFalse((self.state / "settings.json").exists())

    def test_new_config_uses_validated_pins_and_private_settings(self):
        with patch.object(manage, "command", return_value=""), \
                patch.object(manage, "pinned_image", side_effect=list(self.config["images"].values())):
            self.assertEqual(manage.load_config(create=True, offline=True, port=19000), self.config)
        self.assertEqual(stat.S_IMODE((self.state / "settings.json").stat().st_mode), 0o600)
        for name in ("postgres-password", "admin-password"):
            self.assertTrue((self.state / name).is_file())
            self.assertEqual(stat.S_IMODE((self.state / name).stat().st_mode), 0o600)

    def test_missing_credential_in_existing_installation_is_not_regenerated(self):
        self.save_settings()
        for name in ("postgres-password", "admin-password"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(manage.SonarError, "[Rr]estore|missing"):
                    manage.generated_secret(name)
                self.assertFalse((self.state / name).exists())

    def test_offline_pin_never_pulls_and_rejects_missing_digest(self):
        with patch.object(manage, "command", return_value=self.config["images"]["server"]) as command:
            self.assertEqual(manage.pinned_image("server", "sonarqube:community", True), self.config["images"]["server"])
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0][:len(manage.DOCKER)], manage.DOCKER)
            self.assertNotIn("pull", command.call_args.args[0])
        for bad in ("", "<no value>", "sonarqube:community", "sonarqube@sha256:" + "A" * 64):
            with patch.object(manage, "command", return_value=bad):
                with self.assertRaisesRegex(manage.SonarError, "digest"):
                    manage.pinned_image("server", "sonarqube:community", True)

    def test_compose_is_scoped_and_secrets_are_only_in_explicit_environment(self):
        self.make_state()
        token = "synthetic-project-analysis-token"
        with patch.object(manage, "command", return_value="ok") as command:
            manage.compose(self.config, ["run", "--rm", "--no-deps", "scanner"],
                           extra={"SONAR_SCAN_TOKEN": token})
        argv = command.call_args.args[0]
        self.assertEqual(argv[:len(manage.DOCKER)], ["docker", "--host", "unix:///var/run/docker.sock"])
        self.assertIn("--env-file", argv)
        self.assertEqual(argv[argv.index("--env-file") + 1], "/dev/null")
        self.assertEqual(argv[argv.index("--project-name") + 1], "waf-ai-console-sonar")
        self.assertEqual(argv[argv.index("--file") + 1], str(manage.COMPOSE))
        explicit = command.call_args.kwargs["extra"]
        self.assertEqual(explicit["SONAR_SCAN_TOKEN"], token)
        self.assertEqual(explicit["SONAR_BROWSER_IMAGE"], self.config["images"]["browser"])
        self.assertNotIn(token, " ".join(argv))
        self.assertNotIn(explicit["SONAR_DB_PASSWORD"], " ".join(argv))
        self.assertFalse(any(value.startswith("WAF_") for value in explicit))

    def test_command_scrubs_stdout_and_private_log(self):
        self.make_state()
        secret = "synthetic-sensitive-token"
        manage.SENSITIVE.append(secret)
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = iter(["line " + secret + "\n"])
        process.wait.return_value = 0
        with patch.object(manage.subprocess, "Popen", return_value=process) as popen, \
                patch.dict(os.environ, {"WAF_ADMIN_PASSWORD": "not-inherited", "PATH": "/usr/bin"}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            result = manage.command(["synthetic-command"], stream=True, log=self.state / "scan.log")
        self.assertNotIn(secret, result + stdout.getvalue() + (self.state / "scan.log").read_text())
        self.assertEqual(popen.call_args.kwargs["env"], {"PATH": "/usr/bin"})
        self.assertEqual(stat.S_IMODE((self.state / "scan.log").stat().st_mode), 0o600)

    def test_api_uses_only_loopback_disables_proxies_and_redirects_and_keeps_token_out_of_url(self):
        opener = self.api_response({"valid": True})
        token = "synthetic-bearer-token"
        with patch.object(manage, "build_opener", return_value=opener) as build:
            self.assertEqual(manage.api(self.config, "/api/authentication/validate", auth=token,
                                        query={"component": "synthetic project"}), {"valid": True})
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers))
        self.assertTrue(any(isinstance(handler, manage.NoRedirect) for handler in handlers))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:19000/api/authentication/validate?component=synthetic+project")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + token)
        self.assertNotIn(token, request.full_url)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)
        self.assertIsNone(manage.NoRedirect().redirect_request(None, None, 302, "Moved", {}, "https://outside.invalid"))

    def test_api_rejects_arbitrary_absolute_and_nested_paths_before_any_request(self):
        for path in ("https://outside.invalid/api/system/status", "//outside.invalid/api/system/status",
                     "/api/system/status?target=outside", "/api/../status", "/api/system/status/more"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(manage.SonarError, "Unexpected API"):
                    manage.api(self.config, path)

    def test_api_encodes_basic_auth_and_post_body_without_command_arguments(self):
        opener = self.api_response({})
        with patch.object(manage, "build_opener", return_value=opener):
            manage.api(self.config, "/api/users/change_password", auth=("admin", "synthetic-password"),
                       data={"password": "synthetic-next-password"})
        request = opener.open.call_args.args[0]
        encoded = base64.b64encode(b"admin:synthetic-password").decode()
        self.assertEqual(request.get_header("Authorization"), "Basic " + encoded)
        self.assertEqual(request.data, b"password=synthetic-next-password")
        self.assertNotIn("synthetic-password", request.full_url)
        self.assertNotIn("synthetic-next-password", request.full_url)

    def test_api_failures_do_not_expose_upstream_body_or_credentials(self):
        for error in (HTTPError("http://127.0.0.1/", 403, "synthetic-secret", {}, None),
                      URLError("synthetic-secret"), TimeoutError("synthetic-secret"),
                      ConnectionError("synthetic-secret"), HTTPException("synthetic-secret")):
            opener = MagicMock()
            opener.open.side_effect = error
            with patch.object(manage, "build_opener", return_value=opener):
                with self.assertRaises(manage.SonarError) as caught:
                    manage.api(self.config, "/api/system/status", auth="synthetic-secret")
            self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_empty_api_responses_are_accepted_without_fabricating_fields(self):
        for payload in (b"", b" \r\n\t "):
            opener = self.api_response({})
            opener.open.return_value.__enter__.return_value.read.return_value = payload
            with patch.object(manage, "build_opener", return_value=opener):
                self.assertEqual(manage.api(self.config, "/api/system/status"), {})

    def test_malformed_api_json_raises_safe_error_without_body(self):
        opener = self.api_response({})
        opener.open.return_value.__enter__.return_value.read.return_value = b"{synthetic-sensitive-response}"
        with patch.object(manage, "build_opener", return_value=opener):
            with self.assertRaises(manage.SonarError) as caught:
                manage.api(self.config, "/api/system/status")
        self.assertIn("/api/system/status", str(caught.exception))
        self.assertNotIn("synthetic-sensitive-response", str(caught.exception))

    def test_api_rejects_oversize_body(self):
        opener = self.api_response({})
        opener.open.return_value.__enter__.return_value.read.return_value = b"x" * (16 * 1024 * 1024 + 1)
        with patch.object(manage, "build_opener", return_value=opener):
            with self.assertRaisesRegex(manage.SonarError, "exceeds"):
                manage.api(self.config, "/api/system/status")

    def test_existing_database_migration_is_not_automatically_applied(self):
        for status in ("DB_MIGRATION_NEEDED", "DB_MIGRATION_RUNNING"):
            with patch.object(manage, "api", return_value={"status": status}), \
                    patch.object(manage.time, "sleep") as sleep:
                with self.assertRaisesRegex(manage.SonarError, "migration"):
                    manage.wait_for_server(self.config)
            sleep.assert_not_called()

    def test_existing_admin_password_mismatch_does_not_attempt_reset(self):
        self.make_state()
        with patch.object(manage, "api", return_value={"valid": False}) as api:
            with self.assertRaisesRegex(manage.SonarError, "No password reset"):
                manage.bootstrap(self.config)
        self.assertEqual(api.call_count, 2)
        self.assertTrue(all(call.args[1] == "/api/authentication/validate" for call in api.call_args_list))

    def test_first_bootstrap_rotates_default_password_creates_private_project_and_private_token(self):
        self.make_state()
        token = "synthetic-generated-project-token"
        responses = [{"valid": False}, {"valid": True}, {}, {"valid": True},
                     manage.SonarError("SonarQube /api/components/show: HTTP 404"), {}, {}, {"token": token}]
        with patch.object(manage, "api", side_effect=responses) as api:
            self.assertEqual(manage.bootstrap(self.config), token)
        self.assertIn("/api/users/change_password", [call.args[1] for call in api.call_args_list])
        create = next(call for call in api.call_args_list if call.args[1] == "/api/projects/create")
        self.assertEqual(create.kwargs["data"]["visibility"], "private")
        generate = next(call for call in api.call_args_list if call.args[1] == "/api/user_tokens/generate")
        self.assertEqual(generate.kwargs["data"]["type"], "PROJECT_ANALYSIS_TOKEN")
        self.assertEqual(manage.read_private(self.state / "scan-token"), token)
        self.assertEqual(stat.S_IMODE((self.state / "scan-token").stat().st_mode), 0o600)

    def test_invalid_saved_token_is_not_replaced(self):
        self.make_state()
        manage.write_new(self.state / "scan-token", "synthetic-old-token\n")
        with patch.object(manage, "api", side_effect=[{"valid": True}, {}, {}, {"valid": False}]):
            with self.assertRaisesRegex(manage.SonarError, "Saved scan token is invalid"):
                manage.bootstrap(self.config)
        self.assertEqual(manage.read_private(self.state / "scan-token"), "synthetic-old-token")

    def test_occupied_port_does_not_change_existing_services(self):
        probe = MagicMock()
        probe.__enter__.return_value = probe
        probe.bind.side_effect = OSError("occupied")
        with patch.object(manage, "command", return_value=""), \
                patch.object(manage.socket, "socket", return_value=probe), \
                patch.object(manage, "compose") as compose:
            with self.assertRaisesRegex(manage.SonarError, "already occupied"):
                manage.up(self.config)
        compose.assert_not_called()

    def test_stop_is_only_scoped_service_stop_and_preserves_volumes(self):
        with patch.object(manage, "load_config", return_value=self.config), \
                patch.object(manage, "compose") as compose, \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(manage.main(["stop"]), 0)
        compose.assert_called_once_with(self.config, ["stop", "browser", "sonarqube", "db"])
        self.assertIn("volumes were preserved", stdout.getvalue())
        arguments = compose.call_args.args[1]
        self.assertFalse(set(arguments) & {"down", "-v", "--volumes", "prune", "api", "worker", "web"})

    def test_up_checks_own_browser_port_owner_and_only_starts_diagnostics_services(self):
        with patch.object(manage, "command", return_value="synthetic-owned-browser-id") as command, \
                patch.object(manage, "compose") as compose, \
                patch.object(manage, "wait_for_server") as wait, \
                patch.object(manage, "bootstrap") as bootstrap, \
                patch.object(manage.socket, "socket") as socket, \
                contextlib.redirect_stdout(io.StringIO()):
            manage.up(self.config)
        socket.assert_not_called()
        command.assert_called_once()
        self.assertIn("label=com.docker.compose.service=browser", command.call_args.args[0])
        self.assertIn("label=com.docker.compose.project=" + manage.PROJECT, command.call_args.args[0])
        self.assertEqual(compose.call_args_list[0].args, (self.config, ["config", "--quiet"]))
        self.assertEqual(compose.call_args_list[1].args,
                         (self.config, ["up", "-d", "--pull", "never", "db", "sonarqube", "browser"]))
        wait.assert_called_once_with(self.config)
        bootstrap.assert_called_once_with(self.config)

    def test_cli_rejects_invalid_ports_without_external_execution(self):
        for port in ("0", "1023", "65536"):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    manage.main(["up", "--port", port])
            self.assertEqual(caught.exception.code, 2)

    def test_check_does_not_initialize_or_start_services(self):
        with patch.object(manage, "check_platform") as check, \
                patch.object(manage, "load_config") as config, patch.object(manage, "up") as up:
            self.assertEqual(manage.main(["--check"]), 0)
        check.assert_called_once()
        config.assert_not_called()
        up.assert_not_called()

    def test_platform_low_file_limit_is_reported_without_changing_system_settings(self):
        def synthetic_limit(path, *args, **kwargs):
            values = {"/proc/sys/vm/max_map_count": "524288\n", "/proc/sys/fs/file-max": "65536\n"}
            return values[str(path)]

        with patch.object(manage.sys, "platform", "linux"), \
                patch.object(manage.shutil, "which", return_value="/synthetic/docker"), \
                patch.object(manage, "command", return_value="synthetic") as command, \
                patch.object(Path, "read_text", synthetic_limit):
            with self.assertRaisesRegex(manage.SonarError, "fs.file-max"):
                manage.check_platform()
        self.assertEqual(command.call_count, 2)
        self.assertTrue(all("sysctl" not in call.args[0] for call in command.call_args_list))

    def test_report_explicitly_marks_coverage_unmeasured_and_excludes_zero_coverage_metric(self):
        self.make_state()
        run = self.state / "run"
        manage.private_directory(run)
        responses = [
            {"task": {"status": "SUCCESS", "analysisId": "synthetic-analysis"}},
            {"component": {"measures": [{"metric": "coverage", "value": "0.0"}, {"metric": "bugs", "value": "2"}]}},
            {"total": 120, "issues": [{"severity": "MAJOR", "type": "BUG", "component": "app.py", "line": 1, "message": "synthetic | finding\ncontinued"}]},
            {"hotspots": [], "paging": {"total": 0}},
            {"projectStatus": {"status": "ERROR"}},
        ]
        with patch.object(manage, "api", side_effect=responses) as api, \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            manage.write_report(self.config, run, "synthetic-task")
        report = json.loads((run / "report.json").read_text())
        markdown = (run / "report.md").read_text()
        self.assertIs(report["coverage_measured"], False)
        self.assertIs(report["tests_run_by_scanner"], False)
        self.assertIn("Coverage was not measured or imported", markdown)
        self.assertNotIn("| coverage |", markdown)
        self.assertNotIn('"coverage"', stdout.getvalue())
        self.assertIn("first 1 of 120", markdown)
        self.assertIn("not confirmed vulnerabilities", markdown)
        self.assertIn("intentional duplication", markdown)
        self.assertIn("Quality Gate: ERROR", markdown)
        self.assertIn("synthetic \\| finding continued", markdown)
        hotspots = next(call for call in api.call_args_list if call.args[1] == "/api/hotspots/search")
        self.assertEqual(hotspots.kwargs["query"]["project"], manage.PROJECT_KEY)
        self.assertNotIn("projectKey", hotspots.kwargs["query"])
        for name in ("report.json", "report.md"):
            self.assertEqual(stat.S_IMODE((run / name).stat().st_mode), 0o600)

    def test_failed_server_analysis_preserves_submitted_job_without_reporting_success(self):
        self.make_state()
        run = self.state / "run"
        manage.private_directory(run)
        with patch.object(manage, "api", return_value={"task": {"status": "FAILED"}}):
            with self.assertRaisesRegex(manage.SonarError, "did not complete"):
                manage.write_report(self.config, run, "synthetic-task")
        self.assertFalse((run / "report.json").exists())

    def test_scan_passes_token_as_environment_not_scanner_arguments(self):
        self.make_state()
        app = self.root / "backend/app"
        app.mkdir(parents=True)
        (app / "main.py").write_text("print('synthetic source')\n")
        token = "synthetic-secret-scan-token"

        def fake_compose(config, args, *, extra, log):
            self.assertNotIn(token, " ".join(args))
            self.assertEqual(extra["SONAR_SCAN_TOKEN"], token)
            self.assertEqual(args[:4], ["run", "--rm", "--no-deps", "scanner"])
            self.assertTrue(Path(extra["SONAR_SCAN_SOURCE"]).is_dir())
            self.assertFalse((Path(extra["SONAR_SCAN_SOURCE"]) / ".local-sonarqube").exists())
            Path(extra["SONAR_SCAN_OUTPUT"], "report-task.txt").write_text("ceTaskId=synthetic-task-123\nserverUrl=https://ignored.invalid\n")
            return ""

        with patch.object(manage, "api", return_value={"status": "UP"}), \
                patch.object(manage, "bootstrap", return_value=token), \
                patch.object(manage, "compose", side_effect=fake_compose), \
                patch.object(manage, "write_report") as report, \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            manage.scan(self.config)
        self.assertNotIn(token, stdout.getvalue())
        self.assertEqual(report.call_args.args[2], "synthetic-task-123")

    def test_task_metadata_uses_only_validated_task_id_not_supplied_server_url(self):
        run = self.submitted_run()
        task = run / "scanner" / "report-task.txt"
        task.write_text("ceTaskId=synthetic-task-123\nserverUrl=https://ignored.invalid\nceTaskUrl=https://ignored.invalid/task\n")
        self.assertEqual(manage.read_task_id(run), "synthetic-task-123")

    def test_task_metadata_rejects_bad_missing_oversize_and_symlink_files(self):
        run = self.submitted_run()
        task = run / "scanner" / "report-task.txt"
        for value in ("", "ceTaskId=short\n", "ceTaskId=https://outside.invalid\n",
                      "ceTaskId=../../bad-task-id\n", "ceTaskId=" + "a" * 129,
                      "serverUrl=http://127.0.0.1\n", "x" * 16385):
            with self.subTest(value=value[:50]):
                task.write_text(value)
                with self.assertRaises(manage.SonarError):
                    manage.read_task_id(run)
        task.unlink()
        with self.assertRaises(manage.SonarError):
            manage.read_task_id(run)
        outside = self.base / "outside-task"
        outside.write_text("ceTaskId=synthetic-outside-task\n")
        task.symlink_to(outside)
        with self.assertRaises(manage.SonarError):
            manage.read_task_id(run)

    def test_report_resume_uses_latest_submitted_task_without_running_scanner(self):
        self.submitted_run("20261116-110000-aaaaaa", "synthetic-older-task")
        latest = self.submitted_run("20261116-120000-bbbbbb", "synthetic-newest-task")
        with patch.object(manage, "write_report") as report, \
                patch.object(manage, "scan") as scan, patch.object(manage, "compose") as compose:
            manage.resume_report(self.config)
        report.assert_called_once_with(self.config, latest, "synthetic-newest-task")
        scan.assert_not_called()
        compose.assert_not_called()

    def test_report_resume_preserves_complete_report_without_reexport(self):
        run = self.submitted_run()
        manage.write_new(run / "report.json", "preserved json")
        manage.write_new(run / "report.md", "preserved markdown")
        with patch.object(manage, "write_report") as report, \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            manage.resume_report(self.config)
        report.assert_not_called()
        self.assertIn("Saved report", stdout.getvalue())
        self.assertEqual((run / "report.json").read_text(), "preserved json")
        self.assertEqual((run / "report.md").read_text(), "preserved markdown")

    def test_partial_report_resume_uses_new_directory_and_preserves_old_file(self):
        run = self.submitted_run()
        manage.write_new(run / "report.json", "preserved partial json")
        with patch.object(manage, "write_report") as report:
            manage.resume_report(self.config)
        config, target, task_id = report.call_args.args
        self.assertEqual(config, self.config)
        self.assertEqual(target.parent, run)
        self.assertRegex(target.name, r"^report-retry-[0-9a-f]{8}$")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertEqual(task_id, "synthetic-task-123")
        self.assertEqual((run / "report.json").read_text(), "preserved partial json")

    def test_report_resume_refuses_bad_task_without_falling_back_or_resubmitting(self):
        self.submitted_run("20261116-110000-aaaaaa", "synthetic-older-task")
        self.submitted_run("20261116-120000-bbbbbb", "https://outside.invalid")
        with patch.object(manage, "write_report") as report, \
                patch.object(manage, "scan") as scan, patch.object(manage, "compose") as compose:
            with self.assertRaises(manage.SonarError):
                manage.resume_report(self.config)
        report.assert_not_called()
        scan.assert_not_called()
        compose.assert_not_called()

    def test_report_resume_rejects_symlink_run_directory(self):
        run = self.submitted_run()
        link = run.parent / "20261116-130000-cccccc"
        link.symlink_to(run, target_is_directory=True)
        with patch.object(manage, "write_report") as report:
            with self.assertRaisesRegex(manage.SonarError, "symbolic links"):
                manage.resume_report(self.config)
        report.assert_not_called()

    def test_report_resume_without_task_never_submits_an_analysis(self):
        self.make_state()
        manage.private_directory(self.state / "runs")
        with patch.object(manage, "write_report") as report, patch.object(manage, "scan") as scan:
            with self.assertRaisesRegex(manage.SonarError, "no analysis was resubmitted"):
                manage.resume_report(self.config)
        report.assert_not_called()
        scan.assert_not_called()

    def test_report_cli_exports_existing_task_without_platform_up_scan_or_compose_calls(self):
        with patch.object(manage, "load_config", return_value=self.config) as config, \
                patch.object(manage, "resume_report") as report, \
                patch.object(manage, "check_platform") as platform, \
                patch.object(manage, "up") as up, patch.object(manage, "scan") as scan, \
                patch.object(manage, "compose") as compose:
            self.assertEqual(manage.main(["report"]), 0)
        config.assert_called_once_with(create=False, port=None, offline=False)
        report.assert_called_once_with(self.config)
        platform.assert_not_called()
        up.assert_not_called()
        scan.assert_not_called()
        compose.assert_not_called()

    def test_compose_configuration_does_not_publish_postgres_or_mount_waf_or_docker_socket(self):
        content = manage.COMPOSE.read_text()
        self.assertIn('"127.0.0.1:${SONAR_LOCAL_PORT:-19000}:8080"', content)
        self.assertEqual(content.count("ports:"), 1)
        self.assertNotIn("5432:", content)
        self.assertNotIn("/var/run/docker.sock", content)
        self.assertNotIn(".env", content)
        self.assertNotIn("privileged:", content)
        self.assertNotIn("external: true", content)
        self.assertIn("read_only: true", content)
        self.assertIn("internal: true", content)
        self.assertIn("create_host_path: false", content)
        self.assertIn("PROJECT_ANALYSIS_TOKEN", Path(manage.__file__).read_text())

    def test_browser_proxy_is_only_public_facing_service_and_is_hardened(self):
        content = manage.COMPOSE.read_text()
        service_content = content.split("\nnetworks:", 1)[0]
        services = dict(re.findall(r"^  (db|sonarqube|browser|scanner):\n(.*?)(?=^  [a-z_]+:|\Z)",
                                   service_content, flags=re.MULTILINE | re.DOTALL))
        self.assertEqual(set(services), {"db", "sonarqube", "browser", "scanner"})
        for name in ("db", "sonarqube", "scanner"):
            self.assertIn("networks: [diagnostics]", services[name])
            self.assertNotIn("ports:", services[name])
        browser = services["browser"]
        self.assertIn("networks: [diagnostics, browser]", browser)
        self.assertIn('user: "101:101"', browser)
        self.assertIn("cap_drop: [ALL]", browser)
        self.assertIn("read_only: true", browser)
        self.assertIn("no-new-privileges:true", browser)
        self.assertIn("/tmp:rw,nosuid,nodev", browser)
        self.assertIn("create_host_path: false", browser)
        self.assertNotIn("SONAR_DB_PASSWORD", browser)
        self.assertNotIn("SONAR_SCAN_TOKEN", browser)
        nginx = manage.COMPOSE.with_name("nginx.conf").read_text()
        self.assertIn("access_log off;", nginx)
        self.assertIn("proxy_pass http://sonarqube:9000;", nginx)
        self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", nginx)
        self.assertIn("proxy_set_header X-Forwarded-Proto http;", nginx)
        self.assertNotIn("$proxy_add_x_forwarded_for", nginx)
        self.assertNotIn("$http_x_forwarded", nginx)
        self.assertIn("pid /tmp/nginx.pid;", nginx)

    def test_scanner_has_bounded_memory_for_combined_java_and_javascript_analysis(self):
        content = manage.COMPOSE.read_text()
        scanner = content.split("\n  scanner:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        self.assertIn("mem_limit: 3g", scanner)
        self.assertIn("cpus: 2", scanner)
        self.assertIn("SONAR_SCANNER_JAVA_OPTS: -Xmx512m", scanner)


if __name__ == "__main__":
    unittest.main()
