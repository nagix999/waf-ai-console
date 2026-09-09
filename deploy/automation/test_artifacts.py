"""Synthetic stdlib tests: no Docker daemon, team changes, or LLM calls."""
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from .artifacts import END_MARKER, MARKER, gateway_bundle, load_settings, runtime_env, secrets_for_install
from .common import DeployError, compose_model, compose_text


ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "FixtureOnly-9Admin$Password"
SESSION = "FixtureOnlySession-7_abcdefghijklmnopqrstuvwxyz012345"
FERNET = base64.urlsafe_b64encode(bytes(range(32))).decode()


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="waf-artifact-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.team_dir = self.directory / "team"
        self.team_dir.mkdir()
        self.path = self.directory / "waf.env"
        self.values = {"WAF_TEAM_PROJECT_DIR": str(self.team_dir), "WAF_TEAM_PROJECT_NAME": "team-production",
                       "WAF_PUBLIC_ORIGIN": "https://waf.fixture.test", "WAF_ADMIN_USERNAME": "admin",
                       "WAF_ADMIN_PASSWORD": PASSWORD}

    def write(self, overrides=None, *, content=None):
        values = {**self.values, **(overrides or {})}
        self.path.write_text(content if content is not None else "\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
        self.path.chmod(0o600)
        return self.path

    def settings(self, overrides=None):
        return load_settings(self.write(overrides), ROOT)

    def test_defaults_and_literal_dollar_preserved(self):
        settings = self.settings()
        self.assertEqual(settings["peer"], "172.30.250.2")
        self.assertEqual(settings["web_ip"], "172.30.251.10")
        self.assertEqual(settings["values"]["WAF_ADMIN_PASSWORD"], PASSWORD)
        self.assertEqual(settings["values"]["WAF_AGENT_MODE"], "moduagent")
        self.assertEqual(len(settings["fingerprint"]), 64)
        self.assertNotIn(PASSWORD, settings["fingerprint"])

    def test_ambient_settings_do_not_override_file(self):
        with patch.dict(os.environ, {"WAF_ADMIN_PASSWORD": "bad", "WAF_PUBLIC_ORIGIN": "http://bad.invalid"}):
            self.assertEqual(self.settings()["origin"], "https://waf.fixture.test")

    def test_fingerprint_tracks_secret_without_echoing_it(self):
        first = self.settings()["fingerprint"]
        second = self.settings({"WAF_ADMIN_PASSWORD": PASSWORD + "Changed"})["fingerprint"]
        self.assertNotEqual(first, second)

    def test_shell_expression_is_literal_and_never_executed(self):
        marker = self.directory / "not-created"
        password = PASSWORD + "$(touch " + str(marker) + ")"
        settings = self.settings({"WAF_ADMIN_PASSWORD": password})
        self.assertEqual(settings["values"]["WAF_ADMIN_PASSWORD"], password)
        self.assertFalse(marker.exists())

    def test_quotes_are_literal_wrappers(self):
        for quote in ("'", '"'):
            with self.subTest(quote=quote):
                self.assertEqual(self.settings({"WAF_ADMIN_PASSWORD": quote + PASSWORD + quote})["values"]["WAF_ADMIN_PASSWORD"], PASSWORD)

    def test_env_unsafe_permissions_rejected(self):
        self.write().chmod(0o640)
        with self.assertRaisesRegex(DeployError, "0600"):
            load_settings(self.path, ROOT)

    def test_env_symlink_rejected(self):
        self.write()
        link = self.directory / "linked.env"
        link.symlink_to(self.path)
        with self.assertRaisesRegex(DeployError, "symlink"):
            load_settings(link, ROOT)

    def test_env_symlink_parent_rejected(self):
        self.write()
        link = self.directory / "linked"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(DeployError, "symlink"):
            load_settings(link / self.path.name, ROOT)

    def test_env_oversize_rejected(self):
        self.write(content="#" + "a" * 65536)
        with self.assertRaises(DeployError):
            load_settings(self.path, ROOT)

    def test_duplicate_assignment_rejected(self):
        self.write()
        content = self.path.read_text() + "WAF_ADMIN_PASSWORD=secret-not-shown\n"
        self.write(content=content)
        with self.assertRaisesRegex(DeployError, "duplicate") as error:
            load_settings(self.path, ROOT)
        self.assertNotIn("secret-not-shown", str(error.exception))

    def test_non_waf_and_export_assignments_rejected(self):
        for line in ("export WAF_PUBLIC_ORIGIN=https://waf.fixture.test", "COMPOSE_PROJECT_NAME=other", "source private.env"):
            with self.subTest(line=line):
                self.write(content=line)
                with self.assertRaises(DeployError):
                    load_settings(self.path, ROOT)

    def test_unrepresentable_literals_rejected(self):
        for suffix in ("'quote", "\\backslash", "\x00", "\u2028", "\u2029"):
            with self.subTest(suffix=repr(suffix)):
                with self.assertRaises(DeployError):
                    self.settings({"WAF_ADMIN_PASSWORD": PASSWORD + suffix})

    def test_relative_or_waf_team_path_rejected(self):
        for directory in ("team", str(ROOT), "/"):
            with self.subTest(directory=directory):
                with self.assertRaises(DeployError):
                    self.settings({"WAF_TEAM_PROJECT_DIR": directory})

    def test_empty_or_invalid_project_rejected(self):
        for project in ("", "Team", "-team", "team/name", "team name"):
            with self.subTest(project=project), self.assertRaises(DeployError):
                self.settings({"WAF_TEAM_PROJECT_NAME": project})

    def test_weak_passwords_rejected(self):
        for password in ("change-me-now", "long-but-lowercase-password", "Aaaaaaa1!Aaaaaaa1!", "FixtureExample-123456789"):
            with self.subTest(password=password), self.assertRaisesRegex(DeployError, "password_too_weak"):
                self.settings({"WAF_ADMIN_PASSWORD": password})

    def test_unsafe_origins_rejected(self):
        for origin in ("http://waf.fixture.test", "https://WAF.fixture.test", "https://waf.fixture.test/",
                       "https://waf.fixture.test:8443", "https://waf.fixture.test:443", "https://127.0.0.1",
                       "https://localhost", "https://*.fixture.test", "https://waf.fixture.test;return 200;"):
            with self.subTest(origin=origin), self.assertRaises(DeployError):
                self.settings({"WAF_PUBLIC_ORIGIN": origin})

    def test_edge_and_private_network_overlap_rejected(self):
        with self.assertRaisesRegex(DeployError, "edge_address_plan"):
            self.settings({"WAF_EDGE_SUBNET": "172.30.250.0/23"})

    def test_gateway_peer_inside_dynamic_range_rejected(self):
        with self.assertRaisesRegex(DeployError, "edge_address_plan"):
            self.settings({"WAF_TRUSTED_PROXY_IP": "172.30.250.130"})

    def test_gateway_reserved_peer_rejected(self):
        for peer in ("172.30.250.0", "172.30.250.1", "172.30.250.255", "172.30.249.2"):
            with self.subTest(peer=peer), self.assertRaises(DeployError):
                self.settings({"WAF_TRUSTED_PROXY_IP": peer})

    def test_invalid_edge_dynamic_networks_rejected(self):
        for dynamic in ("172.30.249.128/25", "172.30.250.129/25", "172.30.250.128/31"):
            with self.subTest(dynamic=dynamic), self.assertRaises(DeployError):
                self.settings({"WAF_EDGE_IP_RANGE": dynamic})

    def test_public_edge_network_rejected(self):
        with self.assertRaisesRegex(DeployError, "edge_subnet"):
            self.settings({"WAF_EDGE_SUBNET": "203.0.113.0/24"})

    def test_security_downgrade_rejected(self):
        for override in ({"WAF_SESSION_HTTPS_ONLY": "false"}, {"WAF_ENVIRONMENT": "development"}):
            with self.subTest(override=override), self.assertRaises(DeployError):
                self.settings(override)

    def test_invalid_operational_values_rejected(self):
        for key, value in (("WAF_WEB_PORT", "80"), ("WAF_WEB_PORT", "18080:80"),
                           ("WAF_AGENT_MODE", "other"), ("WAF_EDGE_NETWORK", "host"),
                           ("WAF_WORKER_POLL_SECONDS", "nan"), ("WAF_JOB_LEASE_SECONDS", "0"),
                           ("WAF_VERIFIER_CONFIDENCE_THRESHOLD", "inf")):
            with self.subTest(key=key, value=value), self.assertRaises(DeployError):
                self.settings({key: value})

    def test_empty_network_fields_receive_defaults(self):
        self.assertEqual(self.settings({"WAF_PRIVATE_WEB_IP": ""})["web_ip"], "172.30.251.10")

    def test_generate_secrets_once_and_preserve_inputs(self):
        values = self.settings()["values"]
        original = copy.deepcopy(values)
        first = secrets_for_install(values)
        second = secrets_for_install(values, first)
        self.assertEqual(first, second)
        self.assertEqual(values, original)
        self.assertEqual(len(base64.urlsafe_b64decode(first["WAF_DATA_ENCRYPTION_KEY"])), 32)
        self.assertGreaterEqual(len(first["WAF_SESSION_SECRET"]), 32)

    def test_explicit_secrets_preserved(self):
        values = {"WAF_SESSION_SECRET": SESSION, "WAF_DATA_ENCRYPTION_KEY": FERNET}
        state = secrets_for_install(values)
        self.assertEqual(state["WAF_SESSION_SECRET"], SESSION)
        self.assertEqual(state["WAF_DATA_ENCRYPTION_KEY"], FERNET)

    def test_secret_rotation_rejected(self):
        state = secrets_for_install({"WAF_SESSION_SECRET": SESSION, "WAF_DATA_ENCRYPTION_KEY": FERNET})
        for change in ({"WAF_SESSION_SECRET": SESSION + "changed"}, {"WAF_ENCRYPTION_KEY_VERSION": "prod-v2"},
                       {"WAF_DATA_ENCRYPTION_KEY": base64.urlsafe_b64encode(bytes(range(1, 33))).decode()}):
            with self.subTest(key=next(iter(change))), self.assertRaises(DeployError):
                secrets_for_install(change, state)

    def test_bad_existing_secret_state_never_regenerates(self):
        for state in ({}, [], {"WAF_SESSION_SECRET": SESSION}, {"WAF_SESSION_SECRET": "bad", "WAF_DATA_ENCRYPTION_KEY": FERNET,
                                                                  "WAF_ENCRYPTION_KEY_VERSION": "prod-v1"}):
            with self.subTest(state_type=type(state).__name__), self.assertRaises(DeployError):
                secrets_for_install({}, state)

    def test_invalid_supplied_secrets_rejected(self):
        for key, value in (("WAF_SESSION_SECRET", "local-session-secret-change-before-deploy"),
                           ("WAF_SESSION_SECRET", "replace-with-a-long-random-session-secret"),
                           ("WAF_SESSION_SECRET", "x" * 64),
                           ("WAF_DATA_ENCRYPTION_KEY", "A" * 43 + "="),
                           ("WAF_DATA_ENCRYPTION_KEY", FERNET[:-1]),
                           ("WAF_DATA_ENCRYPTION_KEY", "secret-not-shown")):
            with self.subTest(key=key), self.assertRaises(DeployError) as error:
                secrets_for_install({key: value})
            self.assertNotIn(value, str(error.exception))

    def test_runtime_env_literal_and_secure(self):
        state = secrets_for_install({})
        text = runtime_env({"WAF_ADMIN_PASSWORD": PASSWORD, "WAF_SESSION_HTTPS_ONLY": "false"}, state)
        self.assertIn("WAF_ADMIN_PASSWORD='" + PASSWORD + "'\n", text)
        self.assertIn("WAF_SESSION_HTTPS_ONLY='true'\n", text)
        self.assertIn("WAF_ENVIRONMENT='production'\n", text)

    def test_runtime_env_rejects_foreign_keys_and_unrepresentable_value(self):
        for value in ({"HOME": "value"}, {"WAF_ADMIN_PASSWORD": "value'"}, {"WAF_ADMIN_PASSWORD": "line\nbreak"}):
            with self.subTest(key=next(iter(value))), self.assertRaises(DeployError):
                runtime_env(value, {})

    @unittest.skipUnless(os.environ.get("WAF_AUTOMATION_COMPOSE_TEST") == "1", "opt-in Compose parser test")
    def test_real_compose_preserves_literal_env_dollars(self):
        # config parses files only; it does not contact a daemon, pull, or start.
        expected = PASSWORD + "${UNRELATED_AMBIENT}$$(touch never-created)#literal"
        env_file = self.directory / "runtime.env"
        env_file.write_text(runtime_env({"WAF_ADMIN_PASSWORD": expected}, {}))
        env_file.chmod(0o600)
        model = {"services": {"fixture": {"image": "fixture:never-started",
                                           "environment": {"VALUE": "${WAF_ADMIN_PASSWORD}"}}}}
        compose_file = self.directory / "compose.json"
        compose_file.write_text(json.dumps(model))
        environ = {key: value for key, value in os.environ.items() if not key.startswith(("WAF_", "COMPOSE_"))}
        environ["UNRELATED_AMBIENT"] = "must-not-expand"
        result = subprocess.run(["docker", "compose", "--env-file", str(env_file), "-p", "waf-artifact-parser",
                                 "-f", str(compose_file), "config", "--format", "json"],
                                env=environ, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, "Compose config-only parser failed")
        parsed = json.loads(result.stdout)
        # config re-escapes every dollar for a reusable Compose document. It is
        # not a dump of the actual environment passed to a started container.
        self.assertEqual(parsed["services"]["fixture"]["environment"]["VALUE"], expected.replace("$", "$$"))
        self.assertEqual(compose_model(parsed)["services"]["fixture"]["environment"]["VALUE"], expected)

    def test_compose_raw_model_dollar_roundtrip(self):
        raw = {"services": {"gateway": {"environment": {"SECRET": "$one$$two${THREE}"},
                                         "command": ["sh", "-c", "echo $$pid $value"],
                                         "labels": {"literal.key": "$label"}, "read_only": True}}}
        original = copy.deepcopy(raw)
        self.assertEqual(compose_model(json.loads(compose_text(raw))), raw)
        self.assertEqual(raw, original)

    @unittest.skipUnless(os.environ.get("WAF_AUTOMATION_COMPOSE_TEST") == "1", "opt-in Compose parser test")
    def test_real_compose_roundtrip_preserves_all_runtime_dollars(self):
        literal = "$one$$two${THREE}$(never-executed)"
        raw = {"services": {"fixture": {"image": "fixture:never-started", "environment": {"VALUE": literal},
                                         "labels": {"fixture.value": literal}, "command": ["echo", literal],
                                         "healthcheck": {"test": ["CMD", "echo", literal]}}}}
        compose_file = self.directory / "raw-compose.json"
        environ = {key: value for key, value in os.environ.items() if not key.startswith(("WAF_", "COMPOSE_"))}
        environ.update({"one": "wrong", "THREE": "wrong"})
        for _ in range(2):
            compose_file.write_text(compose_text(raw))
            result = subprocess.run(["docker", "compose", "--env-file", "/dev/null", "-p", "waf-artifact-parser",
                                     "-f", str(compose_file), "config", "--format", "json"],
                                    env=environ, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, "Compose config-only roundtrip failed")
            raw = compose_model(json.loads(result.stdout))
            service = raw["services"]["fixture"]
            self.assertEqual(service["environment"]["VALUE"], literal)
            self.assertEqual(service["labels"]["fixture.value"], literal)
            self.assertEqual(service["command"], ["echo", literal])
            self.assertEqual(service["healthcheck"]["test"], ["CMD", "echo", literal])

    def team(self):
        return {"project": "team-production", "template": "# Original UTF-8 주석\nserver { listen 3030 ssl; server_name portal.fixture.test; }",
                "image_id": "sha256:" + "a" * 64,
                "gateway": {"image": "team:production", "read_only": True, "user": "101:101", "init": True,
                            "entrypoint": ["/usr/local/bin/gateway-entrypoint.sh"], "command": ["nginx", "-g", "daemon off;"],
                            "environment": {"FIXTURE_LITERAL": "$value$$literal"}, "container_name": "existing-gateway",
                            "ports": [{"published": "3030", "target": 3030}], "build": {"context": "/not-built"},
                            "depends_on": {"api": {"condition": "service_healthy"}},
                            "networks": {"edge": {"ipv4_address": "172.29.3.10"}, "ingress": {}},
                            "volumes": [{"type": "bind", "source": "/fixture/tls", "target": "/run/platform-tls", "read_only": True}]},
                "networks": {"edge": {"external": True, "name": "team_edge"}, "ingress": {"external": True, "name": "team_ingress"}}}

    def test_bundle_preserves_original_template_prefix(self):
        team = self.team()
        original = copy.deepcopy(team)
        bundle = gateway_bundle(team, self.settings(), "waf-gateway:fixture")
        self.assertTrue(bundle["template"].startswith(team["template"] + "\n"))
        self.assertEqual(bundle["template"].count(MARKER), 1)
        self.assertEqual(bundle["template"].count(END_MARKER), 1)
        self.assertIn("server_name waf.fixture.test;", bundle["template"])
        self.assertIn("if ($platform_ingress_allowed = 0)", bundle["template"])
        self.assertEqual(team, original)

    def test_bundle_only_gateway_and_derived_image(self):
        bundle = gateway_bundle(self.team(), self.settings(), "waf-gateway:fixture")
        model = bundle["gateway"]
        self.assertEqual(set(model["services"]), {"gateway"})
        self.assertEqual(model["services"]["gateway"]["image"], "waf-gateway:fixture")
        self.assertEqual(model["services"]["gateway"]["networks"]["waf-edge"], {"ipv4_address": "172.30.250.2"})
        self.assertEqual(model["networks"]["waf-edge"], {"external": True, "name": "waf-console-edge"})
        self.assertNotIn("depends_on", model["services"]["gateway"])
        self.assertNotIn("build", model["services"]["gateway"])

    def test_bundle_preserves_security_and_unescaped_runtime_strings(self):
        team = self.team()
        bundle = gateway_bundle(team, self.settings(), "waf-gateway:fixture")
        service = bundle["gateway"]["services"]["gateway"]
        for key in ("user", "init", "read_only", "ports", "volumes", "entrypoint", "command", "environment"):
            self.assertEqual(service[key], team["gateway"][key])
        self.assertEqual(service["environment"]["FIXTURE_LITERAL"], "$value$$literal")

    def test_bundle_rollback_pins_original_image_no_waf_edge(self):
        team = self.team()
        model = gateway_bundle(team, self.settings(), "waf-gateway:fixture")["rollback"]
        self.assertEqual(model["services"]["gateway"]["image"], team["image_id"])
        self.assertEqual(model["networks"], team["networks"])
        self.assertNotIn("waf-edge", model["services"]["gateway"]["networks"])

    def test_bundle_check_has_no_published_port_or_static_ip(self):
        check = gateway_bundle(self.team(), self.settings(), "waf-gateway:fixture")["check"]
        service = check["services"]["gateway"]
        self.assertEqual(service["ports"], [])
        self.assertEqual(service["networks"], {"edge": {}})
        self.assertEqual(set(check["networks"]), {"edge"})
        self.assertNotIn("container_name", service)
        self.assertEqual(service["restart"], "no")

    def test_dockerfile_only_inherits_and_adds_template(self):
        text = gateway_bundle(self.team(), self.settings(), "waf-gateway:fixture")["dockerfile"]
        self.assertEqual(text.splitlines(), ["ARG BASE_IMAGE", "FROM ${BASE_IMAGE}",
                                             "COPY --chmod=0444 server.conf.template /etc/platform-gateway/server.conf.template"])

    def test_existing_or_unknown_managed_template_rejected(self):
        for append in (MARKER, "# waf-managed-other", "$waf_web_target", "server_name waf.fixture.test;"):
            team = self.team()
            team["template"] += "\n" + append
            with self.subTest(append=append), self.assertRaises(DeployError):
                gateway_bundle(team, self.settings(), "waf-gateway:fixture")

    def test_existing_network_key_or_name_conflict_rejected(self):
        for network in ({"waf-edge": {"external": True, "name": "other"}},
                        {"other": {"external": True, "name": "waf-console-edge"}}):
            team = self.team()
            team["networks"].update(network)
            with self.subTest(network=network), self.assertRaises(DeployError):
                gateway_bundle(team, self.settings(), "waf-gateway:fixture")

    def test_invalid_derived_image_rejected(self):
        for name in ("waf-gateway", "WAF:fixture", "waf-gateway:fixture\nRUN bad", "waf:tag;other"):
            with self.subTest(name=name), self.assertRaises(DeployError):
                gateway_bundle(self.team(), self.settings(), name)


if __name__ == "__main__":
    unittest.main()
