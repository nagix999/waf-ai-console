"""Pure mock regression: no Docker, network, real credentials, DB, or LLM."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .common import DeployError, digest
from . import team


BASE_IMAGE = "sha256:" + "a" * 64
MANAGED_IMAGE = "sha256:" + "b" * 64
GATEWAY_ID = "c" * 64
CONTROL_ID = "d" * 64


class FakeRunner:
    def __init__(self, fixture):
        self.fixture = fixture
        self.env = {}
        self.calls = []
        self.engine = "28.1.1"
        self.compose_version = "2.29.7"
        self.context = "default"
        self.endpoint = "unix:///var/run/docker.sock"
        self.public_key = "SYNTHETIC_PUBLIC_KEY"
        self.cert_key = self.public_key
        self.sans = "DNS:cyberailabs.team, DNS:*.cyberailabs.team"
        self.hostname_check_outputs = {}

    def run(self, args, **kwargs):
        args = list(map(str, args))
        self.calls.append(args)
        fixture = self.fixture
        if args == ["docker", "context", "show"]:
            return self.context
        if args == ["docker", "context", "inspect", "default"]:
            return json.dumps([{"Endpoints": {"docker": {"Host": self.endpoint}}}])
        if args[:2] == ["docker", "version"]:
            return json.dumps({"Os": "linux", "Version": self.engine})
        if args == ["docker", "compose", "version", "--short"]:
            return self.compose_version
        if args[-3:] == ["config", "--format", "json"]:
            return json.dumps(fixture.model)
        if args[:3] == ["docker", "image", "inspect"]:
            result = deepcopy(fixture.image)
            if args[3] == MANAGED_IMAGE:
                result["Id"] = MANAGED_IMAGE
            return json.dumps([result])
        if "ps" in args and args[-1] in {"gateway", "api", "frontend", "egress-proxy", "jupyterhub"}:
            return GATEWAY_ID if args[-1] == "gateway" else CONTROL_ID
        if args[:3] == ["docker", "container", "inspect"]:
            return json.dumps([fixture.runtime if args[3] == GATEWAY_ID else {"State": {"Running": True, "Status": "running"}}])
        if args[:3] == ["docker", "network", "inspect"]:
            return json.dumps([fixture.networks[args[3]]])
        if args[:4] == ["docker", "exec", GATEWAY_ID, "/bin/cat"]:
            return fixture.baked[args[-1]]
        if args == ["ip", "-j", "-4", "address", "show"]:
            return json.dumps([{"addr_info": [{"local": "10.20.30.40"}]}])
        if args[:2] == ["openssl", "x509"]:
            if "-checkhost" in args:
                hostname = args[args.index("-checkhost") + 1]
                return self.hostname_check_outputs.get(hostname, f"Hostname {hostname} does match certificate\n")
            if "-pubkey" in args:
                return self.cert_key
            if "-ext" in args:
                return self.sans
            return "checked"
        if args[:2] == ["openssl", "pkey"]:
            return self.public_key
        if args[:2] == ["python3", "-I"]:
            return ""
        raise AssertionError("unexpected mock command shape")


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="waf-team-mock-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.helper_hashes = {}
        for relative in (*team.SOURCE_FILES, "compose.production.docker27.yaml"):
            path = self.directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Synthetic reviewed source fixture\n")
            if relative in team.REVIEWED_HELPERS:
                self.helper_hashes[relative] = digest(path.read_bytes())
        self.addCleanup(patch.stopall)
        patch.object(team, "REVIEWED_HELPERS", self.helper_hashes).start()
        lock = self.directory / ".runtime/production/operator.lock"
        lock.parent.mkdir(parents=True)
        lock.touch(mode=0o600)
        self.env = {
            "PRODUCTION_COMPOSE_PROJECT_NAME": "team-workspace-production",
            "PLATFORM_GATEWAY_BIND_IP": "10.20.30.40",
            "PLATFORM_TLS_CERT_FILE": str(self.directory / "fixture-certificate"),
            "PLATFORM_TLS_KEY_FILE": str(self.directory / "fixture-key"),
            "PLATFORM_INGRESS_CIDRS_FILE": str(self.directory / "fixture-ingress"),
            "PLATFORM_TLS_GID": str(os.getgid()),
            "PLATFORM_GPU_RUNTIME_CONFIG_FILE": "disabled",
        }
        self.env_file = self.directory / ".env.production"
        self.write_env()
        for target in team.TLS_TARGETS.values():
            Path(self.env[target]).write_text("10.0.0.0/8\n" if target.endswith("CIDRS_FILE") else "SYNTHETIC_NOT_A_REAL_CREDENTIAL\n")
            Path(self.env[target]).chmod(0o600)
        gateway_env = {
            "PLATFORM_GATEWAY_MODE": "production", "PLATFORM_PORTAL_HOST": "platform.cyberailabs.team",
            "PLATFORM_HUB_HOST": "cyberailabs.team", "PLATFORM_USER_DOMAIN": "cyberailabs.team",
            "PLATFORM_TLS_MIN_VALIDITY_SECONDS": "86400",
        }
        gateway = {
            "image": "team-workspace-gateway:production", "build": {"context": str(self.directory / "gateway")},
            "depends_on": {"api": {"condition": "service_healthy"}}, "environment": gateway_env,
            "networks": {"edge": {"ipv4_address": "172.29.3.10"}, "ingress": {}},
            "ports": [{"host_ip": "10.20.30.40", "target": 3030, "published": "3030", "protocol": "tcp"}],
            "volumes": [{"type": "bind", "source": self.env[key], "target": target, "read_only": True, "bind": {"create_host_path": False}} for target, key in team.TLS_TARGETS.items()],
            "group_add": [self.env["PLATFORM_TLS_GID"]], "read_only": True, "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"], "restart": "unless-stopped",
            "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
            "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=16m", "/var/cache/nginx:rw,noexec,nosuid,nodev,size=32m", "/var/run:rw,noexec,nosuid,nodev,size=8m"],
        }
        self.model = {"name": self.env["PRODUCTION_COMPOSE_PROJECT_NAME"], "services": {"gateway": gateway}, "networks": {}}
        self.image = {"Id": BASE_IMAGE, "Config": {"User": "nginx", "Env": ["PATH=/usr/bin:/bin", "PLATFORM_GATEWAY_MODE=production"],
                       "Entrypoint": ["/usr/local/bin/platform-gateway-entrypoint"], "Cmd": ["nginx", "-g", "daemon off;"],
                       "WorkingDir": "", "Healthcheck": {"Test": ["CMD-SHELL", "fixture-health"]}}}
        actual_env = team.environment_map(self.image["Config"]["Env"])
        actual_env.update(gateway_env)
        runtime_config = {**deepcopy(self.image["Config"]), "Env": [f"{key}={value}" for key, value in actual_env.items()],
                          "Labels": {"com.docker.compose.project": self.model["name"], "com.docker.compose.service": "gateway"}}
        self.runtime = {
            "Image": BASE_IMAGE, "Config": runtime_config,
            "State": {"Running": True, "Status": "running", "Health": {"Status": "healthy"}},
            "HostConfig": {"ReadonlyRootfs": True, "Privileged": False, "CapAdd": [], "CapDrop": ["ALL"],
                           "SecurityOpt": ["no-new-privileges:true"], "GroupAdd": [self.env["PLATFORM_TLS_GID"]],
                           "RestartPolicy": {"Name": "unless-stopped"}, "LogConfig": {"Type": "json-file", "Config": gateway["logging"]["options"]},
                           "Tmpfs": dict(item.split(":", 1) for item in gateway["tmpfs"])},
            "Mounts": [{"Type": "bind", "Source": self.env[key], "Destination": target, "RW": False} for target, key in team.TLS_TARGETS.items()],
            "NetworkSettings": {"Ports": {"80/tcp": None, "3030/tcp": [{"HostIp": "10.20.30.40", "HostPort": "3030"}]}, "Networks": {}},
        }
        self.networks = {}
        for key, subnet, address, internal in (("edge", "172.29.3.0/24", "172.29.3.10", True), ("ingress", "172.29.1.0/24", "172.29.1.2", False)):
            name, identifier = self.model["name"] + "_" + key, ("e" if internal else "f") * 64
            self.model["networks"][key] = {"name": name, "internal": internal, "ipam": {"config": [{"subnet": subnet}]}}
            self.networks[name] = {"Id": identifier, "Name": name, "Driver": "bridge", "Scope": "local", "Internal": internal, "IPAM": {"Config": [{"Subnet": subnet}]}}
            self.runtime["NetworkSettings"]["Networks"][name] = {"NetworkID": identifier, "IPAddress": address}
        self.baked = {
            "/usr/local/share/platform-gateway-config-mode": "production\n",
            "/etc/nginx/nginx.conf": (self.directory / "gateway/nginx-main.production.conf").read_text(),
            "/usr/local/bin/platform-gateway-entrypoint": (self.directory / "gateway/entrypoint-production.sh").read_text(),
            "/etc/nginx/includes/proxy-https.conf": (self.directory / "gateway/proxy-https.conf").read_text(),
            team.TEMPLATE_PATH: (self.directory / "gateway/production.conf").read_text(),
        }
        self.runner = FakeRunner(self)
        self.settings = {"team_dir": str(self.directory), "team_project": self.model["name"], "origin": "https://waf.cyberailabs.team"}

    def write_env(self, extra=""):
        self.env_file.write_text("\n".join(f"{key}={value}" for key, value in self.env.items()) + "\nUNRELATED_SECRET=SYNTHETIC_DO_NOT_COPY\n" + extra)
        self.env_file.chmod(0o600)

    def discover(self, **kwargs):
        return team.discover_team(self.runner, self.settings, **kwargs)

    def test_discovery_is_private_read_only_and_gateway_only(self):
        before = {str(path): path.read_bytes() for path in self.directory.rglob("*") if path.is_file()}
        result = self.discover()
        self.assertEqual(result["image_id"], BASE_IMAGE)
        self.assertEqual(result["gateway"]["image"], BASE_IMAGE)
        self.assertNotIn("build", result["gateway"])
        self.assertNotIn("depends_on", result["gateway"])
        self.assertEqual(set(result["networks"]), {"edge", "ingress"})
        self.assertTrue(all(value["external"] for value in result["networks"].values()))
        self.assertNotIn("SYNTHETIC_DO_NOT_COPY", json.dumps(result))
        self.assertEqual(self.runner.env["DOCKER_HOST"], "unix:///var/run/docker.sock")
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.directory.rglob("*") if path.is_file()})
        self.assertFalse(any("run" in call or "create" in call or "up" in call for call in self.runner.calls))

    def test_engine27_uses_existing_compatibility_overlay(self):
        self.runner.engine = "27.5.1"
        result = self.discover()
        self.assertEqual(result["isolation_mode"], "inhibit-ipv4")
        self.assertEqual(len(result["compose_files"]), 2)

    def test_compose_literal_dollars_are_decoded_exactly_once(self):
        self.assertEqual(team.decode_compose({"a": ["$$VALUE", "$$$$VALUE", "literal"], "b": 2}),
                         {"a": ["$VALUE", "$$VALUE", "literal"], "b": 2})

    def test_remote_endpoint_and_context_are_rejected(self):
        for key, value in (("DOCKER_HOST", "tcp://10.0.0.2:2375"), ("DOCKER_HOST", "ssh://host"), ("DOCKER_CONTEXT", "production-remote"), ("DOCKER_TLS_VERIFY", "1")):
            with self.subTest(key=key, value=value):
                self.runner.env = {key: value}
                with self.assertRaises(DeployError):
                    self.discover()
        self.runner.env = {}
        self.runner.endpoint = "ssh://remote"
        with self.assertRaises(DeployError):
            self.discover()

    def test_engine_and_compose_minimum_versions(self):
        for engine, compose in (("27.1.1", "2.30.0"), ("26.0.0", "2.30.0"), ("28.1.1", "2.24.3"), ("unknown", "2.30.0")):
            with self.subTest(engine=engine, compose=compose):
                self.runner.engine, self.runner.compose_version = engine, compose
                with self.assertRaises(DeployError):
                    self.discover()

    def test_no_operator_lock_is_created(self):
        lock = self.directory / ".runtime/production/operator.lock"
        lock.unlink()
        with self.assertRaises(DeployError):
            self.discover()
        self.assertFalse(lock.exists())

    def test_environment_permissions_duplicates_and_shell_syntax_rejected(self):
        for extra in ("PRODUCTION_COMPOSE_PROJECT_NAME=other\n", "COMMAND=$(secret-command)\n", "export PRODUCTION_COMPOSE_PROJECT_NAME=other\n"):
            self.write_env(extra)
            with self.assertRaises(DeployError):
                self.discover()
        self.write_env()
        self.env_file.chmod(0o644)
        with self.assertRaises(DeployError):
            self.discover()

    def test_project_and_origin_conflicts_rejected(self):
        for changes in ({"team_project": "other"}, {"origin": "https://platform.cyberailabs.team"}, {"origin": "http://waf.cyberailabs.team"}, {"origin": "https://waf.other.test"}):
            with self.subTest(changes=changes), self.assertRaises(DeployError):
                team.discover_team(self.runner, {**self.settings, **changes})

    def test_unreviewed_helper_is_not_executed(self):
        (self.directory / "scripts/validate_production_network.py").write_text("# unreviewed\n")
        with self.assertRaisesRegex(DeployError, "helper_version_requires_review"):
            self.discover()
        self.assertFalse(any(call[0] == "python3" for call in self.runner.calls))

    def test_template_mount_and_extra_mount_rejected_before_exec(self):
        self.runtime["Mounts"].append({"Type": "bind", "Source": "/tmp/fixture", "Destination": team.TEMPLATE_PATH, "RW": False})
        with self.assertRaisesRegex(DeployError, "nonbaked_or_extra_mount"):
            self.discover()
        self.assertFalse(any(call[:2] == ["docker", "exec"] for call in self.runner.calls))

    def test_runtime_env_port_static_ip_user_and_health_drift(self):
        original = deepcopy(self.runtime)
        mutations = (
            lambda: self.runtime["Config"]["Env"].append("EXTRA_SECRET=DO_NOT_ECHO"),
            lambda: self.runtime["NetworkSettings"]["Ports"]["3030/tcp"][0].update(HostIp="0.0.0.0"),
            lambda: self.runtime["NetworkSettings"]["Networks"][self.model["name"] + "_edge"].update(IPAddress="172.29.3.22"),
            lambda: self.runtime["Config"].update(User="root"),
            lambda: self.runtime["State"]["Health"].update(Status="unhealthy"),
            lambda: self.runtime["HostConfig"].update(ReadonlyRootfs=False),
            lambda: self.runtime["HostConfig"].update(PidMode="host"),
            lambda: self.runtime["HostConfig"].update(ExtraHosts=["api:203.0.113.9"]),
        )
        for mutation in mutations:
            self.runtime = deepcopy(original)
            mutation()
            with self.assertRaises(DeployError) as caught:
                self.discover()
            self.assertNotIn("DO_NOT_ECHO", str(caught.exception))

    def test_baked_main_entrypoint_and_template_drift(self):
        original = deepcopy(self.baked)
        for key in original:
            self.baked = {**original, key: "changed\n"}
            with self.subTest(key=key), self.assertRaises(DeployError):
                self.discover()

    def test_managed_image_and_only_its_edge_are_allowed(self):
        self.runtime["Image"] = MANAGED_IMAGE
        self.runtime["NetworkSettings"]["Networks"]["waf-console-edge"] = {"NetworkID": "1" * 64, "IPAddress": "172.30.250.2"}
        self.baked[team.TEMPLATE_PATH] += "# managed WAF server\n"
        result = self.discover(managed_image=MANAGED_IMAGE, managed_edge="waf-console-edge")
        self.assertTrue(result["managed_runtime"])
        self.assertNotIn("waf-console-edge", result["networks"])
        self.assertEqual(result["image_id"], MANAGED_IMAGE)
        with self.assertRaises(DeployError):
            self.discover()
        self.runtime["NetworkSettings"]["Networks"]["unexpected-network"] = {}
        with self.assertRaises(DeployError):
            self.discover(managed_image=MANAGED_IMAGE, managed_edge="waf-console-edge")

    def test_source_change_after_discovery_blocks_checks(self):
        result = self.discover()
        self.env_file.write_text(self.env_file.read_text() + "NEW_SETTING=changed\n")
        with self.assertRaisesRegex(DeployError, "source_changed"):
            team.run_team_checks(self.runner, result)

    def test_read_only_host_checks_do_not_create_probe_or_touch_db(self):
        result = self.discover()
        team.run_team_checks(self.runner, result)
        self.assertTrue(any(call[0] == "openssl" for call in self.runner.calls))
        self.assertTrue(any("JUPYTER" not in call and team.JUPYTER_STATIC_CHECK in call for call in self.runner.calls))
        self.assertNotIn("run_probe(", team.JUPYTER_STATIC_CHECK)
        self.assertTrue(all("-B" in call for call in self.runner.calls if call[0] == "python3"))
        self.assertFalse(any("production.sh" in str(call) or "--probe-image" in call or "run" in call or "--network" in call for call in self.runner.calls))

    def test_tls_key_mismatch_or_missing_wildcard_fails(self):
        result = self.discover()
        self.runner.cert_key = "DIFFERENT_SYNTHETIC_KEY"
        with self.assertRaisesRegex(DeployError, "tls_key_mismatch"):
            team.run_team_checks(self.runner, result)
        self.runner.cert_key = self.runner.public_key
        self.runner.sans = "DNS:cyberailabs.team"
        with self.assertRaisesRegex(DeployError, "tls_san_missing"):
            team.run_team_checks(self.runner, result)

    def test_tls_hostname_mismatch_with_successful_exit_is_rejected(self):
        result = self.discover()
        for hostname in (result["portal_host"], result["host_contract"]["waf_host"]):
            for output in (f"Hostname {hostname} does NOT match certificate\n", "", "checked",
                           "Hostname other.cyberailabs.team does match certificate\n"):
                with self.subTest(hostname=hostname, output=output):
                    self.runner.hostname_check_outputs = {hostname: output}
                    with self.assertRaisesRegex(DeployError, "tls_hostname_mismatch"):
                        team.run_team_checks(self.runner, result)

    def test_tls_single_label_wildcard_cannot_cover_nested_waf_hostname(self):
        self.settings["origin"] = "https://nested.waf.cyberailabs.team"
        result = self.discover()
        # The existing apex + *.cyberailabs.team certificate passes SAN policy,
        # but OpenSSL reports a mismatch for this extra subdomain level, at exit 0.
        self.runner.hostname_check_outputs["nested.waf.cyberailabs.team"] = (
            "Hostname nested.waf.cyberailabs.team does NOT match certificate\n"
        )
        with self.assertRaisesRegex(DeployError, "tls_hostname_mismatch"):
            team.run_team_checks(self.runner, result)

    def test_existing_company_policy_is_not_replaced_or_weakened(self):
        result = self.discover()
        ingress = Path(self.env["PLATFORM_INGRESS_CIDRS_FILE"])
        for content in ("", "0.0.0.0/0\n", "not-a-cidr\n"):
            ingress.write_text(content)
            with self.subTest(content=content), self.assertRaisesRegex(DeployError, "ingress_file_invalid"):
                team.run_team_checks(self.runner, result)


if __name__ == "__main__":
    unittest.main()
