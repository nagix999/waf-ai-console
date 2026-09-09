"""Transaction tests use only temporary WAF data and an in-memory Docker double."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .common import DeployError, private_write
from .deployment import Deployment, OWNER_LABEL, SERVICES


OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
BACKEND = "sha256:" + "c" * 64
FRONTEND = "sha256:" + "d" * 64
CID = "e" * 64


class FakeRunner:
    def __init__(self):
        self.env = {}
        self.commands = []
        self.image = OLD
        self.networks = {"team_edge": {"IPAddress": "172.29.3.10"}, "team_ingress": {"IPAddress": "172.29.4.2"}}
        self.fail_new = False
        self.fail_restore = False
        self.fail_waf = False
        self.fail_workers = False
        self.identifier = CID
        self.recreated = 0

    def run(self, args, **kwargs):
        args = list(map(str, args))
        self.commands.append(args)
        if args[:3] == ["docker", "container", "inspect"]:
            return json.dumps([{"Image": self.image, "State": {"Health": {"Status": "healthy"}},
                                "NetworkSettings": {"Networks": self.networks}}])
        if args[0] == "curl":
            if self.fail_waf and "/health/ready" in args[-1]:
                raise DeployError("https_health_or_certificate_check_failed")
            return '{"status":"ok"}'
        if args[:2] == ["docker", "compose"]:
            if "config" in args:
                services = {name: {"image": FRONTEND if name == "waf-web" else BACKEND,
                                   "labels": {}, "volumes": []} for name in SERVICES}
                services["waf-web"]["volumes"] = [
                    {"target": "/etc/nginx/waf-security", "source": "/unused"},
                    {"target": "/etc/nginx/conf.d/default.conf", "source": "/unused"}]
                return json.dumps({"name": "waf-ai-console-prod", "services": services,
                                   "networks": {"waf-private": {}}, "volumes": {"waf-data": {}}})
            if "ps" in args:
                return self.identifier + "\n"
            if "up" in args and args[-1] == "gateway":
                path = Path(args[args.index("-f") + 1])
                model = json.loads(path.read_text())
                self.image = model["services"]["gateway"]["image"]
                self.recreated += 1
                self.identifier = format(self.recreated, "064x")
                self.networks = {model["networks"][name]["name"]: {"IPAddress": (value or {}).get("ipv4_address", "172.29.4.2")}
                                 for name, value in model["services"]["gateway"]["networks"].items()}
                if self.image == NEW and self.fail_new:
                    raise DeployError("injected_gateway_failure")
                if self.image == OLD and self.fail_restore:
                    raise DeployError("injected_recovery_failure")
            if "up" in args and "model-tester" in args and self.fail_workers:
                raise DeployError("injected_worker_failure")
        return ""


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "frontend").mkdir()
        (self.root / "frontend/nginx.production.conf").write_text("# test nginx\n")
        (self.root / "deploy/security").mkdir(parents=True)
        (self.root / "deploy/security/gateway-waf.server.conf.example").write_text(
            "server_name waf.cyberailabs.team; # waf.cyberailabs.team\n")
        self.settings = {"root": str(self.root), "team_dir": "/team-read-only", "team_project": "team",
                         "origin": "https://waf.cyberailabs.team", "edge_name": "waf-console-edge",
                         "edge_subnet": "172.30.250.0/24", "edge_range": "172.30.250.128/25",
                         "peer": "172.30.250.2", "private_subnet": "172.30.251.0/24",
                         "web_ip": "172.30.251.10", "web_port": 18080, "fingerprint": "settings-v1",
                         "values": {"WAF_PUBLIC_ORIGIN": "https://waf.cyberailabs.team",
                                    "WAF_TRUSTED_PROXY_IP": "172.30.250.2", "WAF_PRIVATE_SUBNET": "172.30.251.0/24",
                                    "WAF_PRIVATE_WEB_IP": "172.30.251.10"}}
        self.team = {"project": "team", "directory": "/team-read-only", "image_id": OLD,
                     "gateway_id": CID, "gateway": {"image": OLD, "networks": {"edge": {"ipv4_address": "172.29.3.10"}, "ingress": {}}, "environment": {}},
                     "networks": {"edge": {"external": True, "name": "team_edge"}, "ingress": {"external": True, "name": "team_ingress"}},
                     "template": "# original baked config\n", "portal_host": "platform.cyberailabs.team",
                     "hub_host": "cyberailabs.team", "host_contract": {"PLATFORM_GATEWAY_BIND_IP": "10.1.2.3"},
                     "source_hashes": {}, "operator_lock": "/team-read-only/operator.lock", "managed_runtime": False}
        self.runner = FakeRunner()
        self.messages = []
        self.operation = Deployment(self.settings, runner=self.runner, report=self.messages.append)
        self.inventory = {"networks": {}, "containers": [], "volume": None}
        mocks = {
            "discover_team": lambda *a, **kw: deepcopy(self.team),
            "run_team_checks": lambda *a: None,
            "inspect_resources": lambda *a: deepcopy(self.inventory),
            "source_fingerprint": lambda *a: "source-v1",
            "build_waf": lambda *a: {"backend": BACKEND, "frontend": FRONTEND},
            "build_gateway": lambda *a: NEW,
            "operator_lock": lambda *a, **kw: nullcontext(),
        }
        for name, replacement in mocks.items():
            handle = patch("deploy.automation.deployment." + name, side_effect=replacement)
            handle.start()
            self.addCleanup(handle.stop)
        handle = patch("deploy.automation.runtime.assert_runtime_matches")
        handle.start()
        self.addCleanup(handle.stop)

    def deploy(self):
        self.operation.deploy()
        current = self.operation.state("current")
        return self.operation.record(current["generation"])

    def adopt_running(self, record):
        self.team["managed_runtime"] = True
        self.team["image_id"] = NEW
        self.inventory["networks"] = {"waf-console-edge": {}}
        self.inventory["containers"] = [
            {"Image": record["images"]["frontend" if service == "waf-web" else "backend"],
             "Config": {"Labels": {OWNER_LABEL: self.operation.owner,
                                     "io.waf.deploy.generation": record["generation"],
                                     "com.docker.compose.service": service}},
             "State": {"Running": True, "Health": {"Status": "healthy"}}} for service in SERVICES]

    def test_check_and_status_create_no_state_or_docker_objects(self):
        self.operation.check()
        self.operation.status()
        self.assertFalse(self.operation.state_dir.exists())
        self.assertTrue(all(command[0] == "curl" for command in self.runner.commands))

    def test_first_deploy_gateway_only_team_mutation(self):
        record = self.deploy()
        self.assertEqual(self.operation.state("transaction")["phase"], "complete")
        self.assertEqual(self.runner.image, NEW)
        for command in self.runner.commands:
            if "compose" in command and "-p" in command and command[command.index("-p") + 1] == "team":
                self.assertNotIn("down", command)
                self.assertNotIn("api", command)
                self.assertNotIn("jupyterhub", command)
                self.assertNotIn("--remove-orphans", command)
        self.assertEqual(record["before_model"]["services"]["gateway"]["image"], OLD)
        self.assertEqual(record["gateway_model"]["services"]["gateway"]["image"], NEW)

    def test_state_and_runtime_files_private(self):
        self.deploy()
        for path in self.operation.state_dir.rglob("*"):
            self.assertEqual(path.stat().st_mode & 0o077, 0, str(path))

    def test_same_successful_deployment_is_noop(self):
        record = self.deploy()
        self.adopt_running(record)
        self.runner.commands.clear()
        self.operation.deploy()
        self.assertTrue(all(command[0] == "curl" for command in self.runner.commands))
        self.assertEqual(self.operation.state("current")["generation"], record["generation"])

    def test_noop_requires_healthy_workers(self):
        record = self.deploy()
        self.adopt_running(record)
        self.inventory["containers"][-1]["State"]["Running"] = False
        self.assertFalse(self.operation.waf_running(record, self.inventory["containers"]))

    def test_new_gateway_failure_restores_previous_gateway(self):
        self.runner.fail_new = True
        with self.assertRaisesRegex(DeployError, "deployment_failed_gateway_restored"):
            self.operation.deploy()
        self.assertEqual(self.runner.image, OLD)
        self.assertEqual(self.operation.state("transaction")["phase"], "rolled_back")
        self.assertFalse(self.operation.state("current")["active"])

    def test_https_failure_restores_previous_gateway(self):
        self.runner.fail_waf = True
        with self.assertRaisesRegex(DeployError, "deployment_failed_gateway_restored"):
            self.operation.deploy()
        self.assertEqual(self.runner.image, OLD)

    def test_worker_failure_restores_gateway_not_database(self):
        self.runner.fail_workers = True
        with self.assertRaisesRegex(DeployError, "deployment_failed_gateway_restored"):
            self.operation.deploy()
        self.assertEqual(self.runner.image, OLD)
        self.assertFalse(any("down" in command or "rm" in command for command in self.runner.commands))

    def test_recovery_failure_preserves_journal(self):
        self.runner.fail_new = self.runner.fail_restore = True
        with self.assertRaisesRegex(DeployError, "gateway_recovery_failed"):
            self.operation.deploy()
        self.assertEqual(self.operation.state("transaction")["phase"], "recovery_failed")
        with self.assertRaisesRegex(DeployError, "interrupted_gateway_switch"):
            self.operation.deploy()

    def test_missing_secrets_never_regenerates_existing_installation(self):
        self.operation.write("installation", self.operation.identity())
        with self.assertRaisesRegex(DeployError, "existing_encryption_state_missing"):
            self.operation.deploy()
        self.assertIsNone(self.operation.state("secrets"))
        self.assertEqual(self.runner.commands, [])

    def test_partial_secrets_state_requires_review(self):
        self.operation.write("secrets", {"unexpected": "never-print"})
        with self.assertRaisesRegex(DeployError, "partial_installation_state"):
            self.operation.check()
        self.assertNotIn("never-print", "\n".join(self.messages))

    def test_secret_changes_are_refused(self):
        self.deploy()
        self.settings["values"]["WAF_SESSION_SECRET"] = "a-completely-distinct-session-secret-9876543210"
        with self.assertRaisesRegex(DeployError, "existing_secret_change_not_allowed"):
            self.operation.check()

    def test_network_identity_change_is_refused(self):
        self.deploy()
        self.settings["edge_name"] = "another-edge"
        with self.assertRaisesRegex(DeployError, "installation_identity_or_network_changed"):
            self.operation.check()

    def test_candidate_failure_never_replaces_gateway(self):
        original = self.operation.compose
        def failing(record, file, *args, **kwargs):
            if file == "candidate.json":
                raise DeployError("candidate_failed")
            return original(record, file, *args, **kwargs)
        with patch.object(self.operation, "compose", side_effect=failing):
            with self.assertRaisesRegex(DeployError, "candidate_failed"):
                self.operation.deploy()
        self.assertEqual(self.runner.image, OLD)
        self.assertFalse(any("network" in command and "create" in command for command in self.runner.commands))

    def test_generation_tampering_refused(self):
        record = self.deploy()
        path = self.operation.state_dir / "generations" / record["generation"] / "gateway.json"
        private_write(path, "{}")
        with self.assertRaisesRegex(DeployError, "generation_artifact_changed"):
            self.operation.record(record["generation"])

    def test_generation_traversal_refused(self):
        with self.assertRaisesRegex(DeployError, "generation_invalid"):
            self.operation.record("../../outside")

    def test_manual_rollback_restores_gateway(self):
        self.deploy()
        with patch("deploy.automation.team.local_docker"):
            self.operation.rollback()
        self.assertEqual(self.runner.image, OLD)
        self.assertFalse(self.operation.state("current")["active"])

    def test_manual_rollback_recovers_interrupted_switch(self):
        self.runner.fail_new = self.runner.fail_restore = True
        with self.assertRaises(DeployError):
            self.operation.deploy()
        self.runner.fail_new = self.runner.fail_restore = False
        with patch("deploy.automation.team.local_docker"):
            self.operation.rollback()
        self.assertEqual(self.operation.state("transaction")["phase"], "rolled_back")

    def test_unrelated_gateway_after_deploy_is_never_overwritten(self):
        record = self.deploy()
        self.runner.image = "sha256:" + "f" * 64
        self.runner.commands.clear()
        with self.assertRaisesRegex(DeployError, "rollback_unexpected_gateway"):
            self.operation.restore(record)
        self.assertFalse(any("up" in command or "stop" in command for command in self.runner.commands))

    def test_native_team_redeploy_is_reported_missing(self):
        self.deploy()
        with self.assertRaisesRegex(DeployError, "waf_gateway_attachment_missing"):
            self.operation.status()

    def test_managed_redeploy_uses_unmodified_base_template(self):
        first = self.deploy()
        self.adopt_running(first)
        self.team["template"] = "# WAF-MANAGED-GATEWAY v1 already attached"
        self.settings["fingerprint"] = "settings-v2"
        second = self.deploy()
        self.assertEqual(second["base_team"]["template"], "# original baked config\n")
        self.assertEqual(second["previous"]["generation"], first["generation"])
        self.assertEqual(second["before_model"], first["gateway_model"])

    def test_native_reconnect_rollback_does_not_claim_old_attachment(self):
        first = self.deploy()
        self.assertTrue(self.operation.state("current")["active"])
        self.runner.image = OLD
        self.settings["fingerprint"] = "settings-v2"
        second = self.deploy()
        self.assertEqual(second["historical_current"]["generation"], first["generation"])
        self.assertFalse(second["previous"]["active"])
        with patch("deploy.automation.team.local_docker"):
            self.operation.rollback()
        self.assertFalse(self.operation.state("current")["active"])

    def test_failed_restoration_stops_its_exact_container(self):
        self.runner.fail_new = self.runner.fail_restore = True
        with self.assertRaisesRegex(DeployError, "gateway_recovery_failed"):
            self.operation.deploy()
        stops = [command for command in self.runner.commands if command[:3] == ["docker", "container", "stop"]]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0][-1], self.runner.identifier)

    def test_network_replaced_while_building_blocks_before_waf_mutation(self):
        first = self.deploy()
        self.adopt_running(first)
        self.inventory["networks"]["waf-console-edge"] = {"Id": "original-edge"}
        self.settings["fingerprint"] = "settings-v2"
        changed = deepcopy(self.inventory)
        changed["networks"]["waf-console-edge"]["Id"] = "replacement-edge"
        self.runner.commands.clear()
        with patch("deploy.automation.deployment.inspect_resources", side_effect=[deepcopy(self.inventory), deepcopy(self.inventory), changed]):
            with self.assertRaisesRegex(DeployError, "waf_network_replaced_during_prepare"):
                self.operation.deploy()
        self.assertFalse(any("up" in command or "stop" in command for command in self.runner.commands))

    def test_volume_replaced_while_building_blocks_before_waf_mutation(self):
        first = self.deploy()
        self.adopt_running(first)
        self.inventory["volume"] = {"Name": "waf-ai-console-production-data", "CreatedAt": "original"}
        self.settings["fingerprint"] = "settings-v2"
        changed = deepcopy(self.inventory)
        changed["volume"]["CreatedAt"] = "replacement"
        self.runner.commands.clear()
        with patch("deploy.automation.deployment.inspect_resources", side_effect=[deepcopy(self.inventory), deepcopy(self.inventory), changed]):
            with self.assertRaisesRegex(DeployError, "waf_volume_replaced_during_prepare"):
                self.operation.deploy()
        self.assertFalse(any("up" in command or "stop" in command for command in self.runner.commands))


if __name__ == "__main__":
    unittest.main()
