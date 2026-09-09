"""Opt-in real Compose parser integration; no daemon or running services.

WAF_AUTOMATION_COMPOSE_TEST=1 enables WAF Compose parsing. To also exercise the
reviewed team source, set WAF_AUTOMATION_TEAM_SOURCE to a read-only checkout. No
real env, certificate, key, or DB is opened: all parser values are synthetic.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from .artifacts import gateway_bundle, load_settings, runtime_env, secrets_for_install
from .common import compose_model, compose_text, digest


ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("WAF_AUTOMATION_COMPOSE_TEST") == "1"
TEAM_SOURCE = Path(os.environ.get("WAF_AUTOMATION_TEAM_SOURCE", "/nonexistent-waf-team-parser-fixture"))
TEAM_AVAILABLE = (TEAM_SOURCE / "compose.production.yaml").is_file()
BASE_ID = "sha256:" + "a" * 64
DERIVED_ID = "sha256:" + "b" * 64
FRONTEND_ID = "sha256:" + "c" * 64
PASSWORD = "ParserOnly-9${UNRELATED_AMBIENT}$$NeverExecuted"


class ParserFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="waf-compose-parser-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.team_fixture = self.directory / "team"
        self.team_fixture.mkdir()
        self.operator_env = self.directory / "operator.env"
        values = {"WAF_TEAM_PROJECT_DIR": str(self.team_fixture), "WAF_TEAM_PROJECT_NAME": "fixture-team",
                  "WAF_PUBLIC_ORIGIN": "https://waf.cyberailabs.team", "WAF_ADMIN_USERNAME": "fixture-admin",
                  "WAF_ADMIN_PASSWORD": PASSWORD, "WAF_BACKEND_IMAGE": BASE_ID, "WAF_FRONTEND_IMAGE": FRONTEND_ID}
        self.operator_env.write_text("\n".join(key + "=" + value for key, value in values.items()) + "\n")
        self.operator_env.chmod(0o600)
        self.settings = load_settings(self.operator_env, ROOT)
        self.values = self.settings["values"]
        self.runtime = self.directory / "runtime.env"
        self.runtime.write_text(runtime_env(self.values, secrets_for_install(self.values)))
        self.runtime.chmod(0o600)
        # Even hostile ambient interpolation values cannot replace file literals.
        self.environ = {key: value for key, value in os.environ.items()
                        if not key.startswith(("WAF_", "COMPOSE_", "PLATFORM_", "PRODUCTION_", "JUPYTERHUB_"))}
        self.environ["UNRELATED_AMBIENT"] = "must-never-expand"

    def parse(self, files, *, project="fixture-waf", directory=ROOT, env_file=None, expected_code=0):
        command = ["docker", "compose", "--project-directory", str(directory), "--env-file",
                   str(env_file or self.runtime), "-p", project]
        for path in files:
            command.extend(["-f", str(path)])
        command.extend(["config", "--format", "json"])
        result = subprocess.run(command, env=self.environ, capture_output=True, text=True, timeout=30)
        if expected_code:
            self.assertNotEqual(result.returncode, 0, "Compose should reject incomplete fixture configuration")
            return None
        self.assertEqual(result.returncode, 0, "Compose parser failed; captured output is not printed")
        return compose_model(json.loads(result.stdout))

    def roundtrip(self, model, filename, project):
        path = self.directory / filename
        path.write_text(compose_text(model))
        path.chmod(0o600)
        return self.parse([path], project=project, directory=self.directory)


@unittest.skipUnless(ENABLED, "opt-in Compose parser integration")
class WafComposeIntegration(ParserFixture):
    def test_real_waf_compose_roundtrip_preserves_literals_and_security(self):
        model = self.parse([ROOT / "docker-compose.production.yml", ROOT / "deploy/security/docker-compose.gateway.yml"])
        self.assertEqual(set(model["services"]), {"api", "worker", "model-tester", "waf-web"})
        repeated = self.roundtrip(model, "waf-roundtrip.json", "fixture-waf")
        self.assertEqual(repeated, model)
        for role in ("api", "worker", "model-tester"):
            with self.subTest(role=role):
                service = repeated["services"][role]
                self.assertFalse(service.get("ports"))
                self.assertEqual(service["image"], BASE_ID)
                self.assertEqual(set(service["networks"]), {"waf-private"})
                self.assertEqual(service["environment"]["WAF_ADMIN_PASSWORD"], PASSWORD)
                self.assertEqual(service["environment"]["WAF_ENVIRONMENT"], "production")
                self.assertEqual(service["environment"]["WAF_SESSION_HTTPS_ONLY"], "true")
                self.assertEqual(service["environment"]["WAF_PUBLIC_ORIGIN"], self.settings["origin"])
                self.assertEqual(service["environment"]["FORWARDED_ALLOW_IPS"], self.settings["web_ip"])
                self.assertIn("no-new-privileges:true", service["security_opt"])
        self.assertIn("--proxy-headers", repeated["services"]["api"]["command"][-1])
        self.assertIn("--no-access-log", repeated["services"]["api"]["command"][-1])
        web = repeated["services"]["waf-web"]
        self.assertEqual(web["image"], FRONTEND_ID)
        self.assertEqual(set(web["networks"]), {"waf-private", "waf-edge"})
        self.assertEqual(web["networks"]["waf-private"]["ipv4_address"], self.settings["web_ip"])
        self.assertEqual(web["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(str(web["ports"][0]["published"]), str(self.settings["web_port"]))
        mounts = {mount["target"]: mount for mount in web["volumes"]}
        for target in ("/etc/nginx/waf-security", "/etc/nginx/conf.d/default.conf"):
            with self.subTest(target=target):
                self.assertEqual(mounts[target]["type"], "bind")
                self.assertTrue(mounts[target]["read_only"])
                self.assertFalse(mounts[target].get("bind", {}).get("create_host_path", False))
        self.assertEqual(repeated["networks"]["waf-edge"]["name"], self.settings["edge_name"])
        self.assertTrue(repeated["networks"]["waf-edge"]["external"])
        self.assertEqual(repeated["volumes"]["waf-data"]["name"], "waf-ai-console-production-data")

    def test_real_waf_compose_rejects_missing_secrets_without_starting(self):
        missing = self.directory / "missing.env"
        missing.write_text(runtime_env({key: value for key, value in self.values.items()
                                        if key not in {"WAF_ADMIN_PASSWORD", "WAF_SESSION_SECRET", "WAF_DATA_ENCRYPTION_KEY"}}, {}))
        missing.chmod(0o600)
        self.parse([ROOT / "docker-compose.production.yml", ROOT / "deploy/security/docker-compose.gateway.yml"],
                   env_file=missing, expected_code=1)


@unittest.skipUnless(ENABLED and TEAM_AVAILABLE, "opt-in reviewed team checkout parser integration")
class TeamComposeIntegration(ParserFixture):
    def team_model(self, compatibility=False):
        # Absolute fixture paths need not exist for config parsing. In particular
        # no real private key or company ingress file is opened by this test.
        team_values = {"PRODUCTION_COMPOSE_PROJECT_NAME": "fixture-team", "PLATFORM_SECRET_GID": "2901", "DOCKER_GID": "2902",
                       "PLATFORM_GATEWAY_BIND_IP": "192.0.2.10", "PLATFORM_TLS_GID": "2903",
                       "PLATFORM_TLS_CERT_FILE": str(self.directory / "fixture-tls.crt"),
                       "PLATFORM_TLS_KEY_FILE": str(self.directory / "fixture-tls.key"),
                       "PLATFORM_INGRESS_CIDRS_FILE": str(self.directory / "fixture-ingress.txt")}
        env_file = self.directory / "team-fixture.env"
        env_file.write_text("\n".join(key + "='" + value + "'" for key, value in team_values.items()) + "\n")
        env_file.chmod(0o600)
        files = [TEAM_SOURCE / "compose.production.yaml"]
        if compatibility:
            files.append(TEAM_SOURCE / "compose.production.docker27.yaml")
        hashes = {str(path): digest(path.read_bytes()) for path in files}
        model = self.parse(files, project="fixture-team", directory=TEAM_SOURCE, env_file=env_file)
        self.assertEqual(hashes, {str(path): digest(path.read_bytes()) for path in files})
        return model

    def exercise_bundle(self, compatibility):
        model = self.team_model(compatibility)
        gateway = copy.deepcopy(model["services"]["gateway"])
        # This is the same narrow snapshot shape produced by the read-only
        # inspector; actual container/image drift checks are separate tests.
        gateway.pop("build", None)
        gateway.pop("depends_on", None)
        gateway["image"] = BASE_ID
        networks = {key: {"external": True, "name": model["networks"][key]["name"]} for key in gateway["networks"]}
        template_path = TEAM_SOURCE / "gateway/production.conf"
        template = template_path.read_bytes().decode("utf-8")
        original_template_hash = digest(template_path.read_bytes())
        team = {"gateway": gateway, "networks": networks, "project": "fixture-team", "template": template, "image_id": BASE_ID}
        before = copy.deepcopy(team)
        bundle = gateway_bundle(team, self.settings, "waf-gateway:fixture")
        for key in ("gateway", "check"):
            bundle[key]["services"]["gateway"]["image"] = DERIVED_ID
        parsed = {name: self.roundtrip(bundle[name], name + ".json", "fixture-team") for name in ("gateway", "check", "rollback")}
        for name, result in parsed.items():
            with self.subTest(artifact=name):
                # Normalization may omit empty ports and add empty IPAM to
                # external networks. Assert every runtime field, then ensure a
                # second serialization/parse is fully idempotent.
                self.assertEqual(self.roundtrip(result, name + "-again.json", "fixture-team"), result)
                self.assertEqual(set(result["services"]), {"gateway"})
                service = result["services"]["gateway"]
                for field, expected in bundle[name]["services"]["gateway"].items():
                    self.assertEqual(service.get(field, [] if field == "ports" else None), expected)
                self.assertEqual(set(result["networks"]), set(bundle[name]["networks"]))
                for key, expected in bundle[name]["networks"].items():
                    network = result["networks"][key]
                    self.assertEqual(network["name"], expected["name"])
                    self.assertTrue(network["external"])
                    self.assertFalse(network.get("ipam"))
                self.assertNotIn("depends_on", service)
                self.assertNotIn("build", service)
                for field in ("group_add", "volumes", "read_only", "tmpfs", "security_opt", "cap_drop", "environment", "logging"):
                    self.assertEqual(service[field], gateway[field])
        live = parsed["gateway"]["services"]["gateway"]
        rollback = parsed["rollback"]["services"]["gateway"]
        candidate = parsed["check"]["services"]["gateway"]
        self.assertEqual(live["image"], DERIVED_ID)
        self.assertEqual(rollback["image"], BASE_ID)
        self.assertEqual(candidate["image"], DERIVED_ID)
        self.assertEqual(live["ports"], gateway["ports"])
        self.assertEqual(rollback["ports"], gateway["ports"])
        self.assertEqual(live["networks"]["edge"], gateway["networks"]["edge"])
        self.assertEqual(live["networks"]["ingress"], gateway["networks"]["ingress"])
        self.assertEqual(live["networks"]["waf-edge"], {"ipv4_address": self.settings["peer"]})
        self.assertEqual(rollback["networks"], gateway["networks"])
        self.assertEqual(candidate["networks"], {"edge": {}})
        self.assertFalse(candidate.get("ports"))
        self.assertEqual(candidate["restart"], "no")
        self.assertNotIn("container_name", candidate)
        self.assertEqual(set(parsed["check"]["networks"]), {"edge"})
        self.assertTrue(bundle["template"].startswith(template + "\n"))
        self.assertEqual(team, before)
        self.assertEqual(digest(template_path.read_bytes()), original_template_hash)

    def test_real_team_gateway_bundle_roundtrip(self):
        self.exercise_bundle(False)

    def test_real_team_docker27_gateway_bundle_roundtrip(self):
        self.exercise_bundle(True)


if __name__ == "__main__":
    unittest.main()
