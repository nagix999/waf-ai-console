"""Synthetic resource preflight tests; no Docker, port binds, or real env reads."""
from copy import deepcopy
import ipaddress
import json
import unittest
from unittest.mock import patch

from .common import DeployError
from . import resources


OWNER = "fixture-owned-installation"
EDGE_ID = "a" * 64
PRIVATE_ID = "b" * 64
WEB_ID = "c" * 64
GATEWAY_ID = "d" * 64
API_ID = "e" * 64


def settings():
    return {"edge_name": "waf-console-edge", "edge_subnet": "172.30.250.0/24",
            "edge_range": "172.30.250.128/25", "peer": "172.30.250.2",
            "private_subnet": "172.30.251.0/24", "web_ip": "172.30.251.10",
            "web_port": 18080, "team_project": "fixture-team"}


def network(identifier, name, subnet, *, internal=False, owner=None, ip_range="", members=None):
    return {"Id": identifier, "Name": name, "Driver": "bridge", "Scope": "local",
            "Internal": internal, "EnableIPv6": False, "Options": {},
            "Labels": {} if owner is None else {resources.OWNER_LABEL: owner},
            "IPAM": {"Config": [{"Subnet": subnet, "Gateway": str(ipaddress.ip_network(subnet).network_address + 1),
                                  "IPRange": ip_range}]}, "Containers": members or {}}


def container(identifier, service, *, owner=OWNER, project=resources.WAF_PROJECT):
    labels = {"com.docker.compose.project": project, "com.docker.compose.service": service,
              "com.docker.compose.oneoff": "False"}
    if owner is not None:
        labels[resources.OWNER_LABEL] = owner
    ports = {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}]} if service == "waf-web" else {}
    return {"Id": identifier, "Config": {"Labels": labels}, "State": {"Running": True},
            "NetworkSettings": {"Ports": ports}}


class FakeRunner:
    def __init__(self):
        self.networks = {}
        self.containers = {}
        self.volume = None
        self.routes = [{"dst": "default", "dev": "eth0", "gateway": "10.10.10.1"},
                       {"dst": "10.10.10.0/24", "dev": "eth0"},
                       {"dst": "127.0.0.0/8", "dev": "lo", "type": "local"}]
        self.calls = []

    def run(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        if args == ["docker", "network", "ls", "--quiet", "--no-trunc"]:
            return "\n".join(self.networks)
        if args[:3] == ["docker", "network", "inspect"]:
            return json.dumps([self.networks[identifier] for identifier in args[3:]])
        if args == ["docker", "volume", "ls", "--format", "{{.Name}}"]:
            return resources.DATA_VOLUME + "\n" if self.volume else ""
        if args[:3] == ["docker", "volume", "inspect"]:
            return json.dumps([self.volume])
        if args[:2] == ["docker", "ps"]:
            return "\n".join(key for key, value in self.containers.items()
                             if value["Config"]["Labels"].get("com.docker.compose.project") == resources.WAF_PROJECT)
        if args[:3] == ["docker", "container", "inspect"]:
            return json.dumps([self.containers[identifier] for identifier in args[3:]])
        if args == ["ip", "-j", "-4", "route", "show", "table", "all"]:
            return json.dumps(self.routes)
        raise AssertionError("unexpected command in read-only fixture")


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.settings = settings()
        self.runner = FakeRunner()
        self.port = patch.object(resources.socket, "socket").start()
        self.addCleanup(patch.stopall)

    def inspect(self, installation=None):
        return resources.inspect_resources(self.runner, self.settings, OWNER, installation)

    def managed(self):
        self.runner.networks[EDGE_ID] = network(EDGE_ID, self.settings["edge_name"], self.settings["edge_subnet"],
                                                internal=True, owner=OWNER, ip_range=self.settings["edge_range"])
        self.runner.networks[PRIVATE_ID] = network(PRIVATE_ID, resources.PRIVATE_NETWORK,
                                                   self.settings["private_subnet"], owner=OWNER)
        self.runner.containers[WEB_ID] = container(WEB_ID, "waf-web")
        self.runner.containers[API_ID] = container(API_ID, "api")
        self.runner.containers[GATEWAY_ID] = container(GATEWAY_ID, "gateway", owner=None, project="fixture-team")
        self.runner.networks[EDGE_ID]["Containers"] = {
            WEB_ID: {"IPv4Address": "172.30.250.130/24"}, GATEWAY_ID: {"IPv4Address": "172.30.250.2/24"}}
        self.runner.networks[PRIVATE_ID]["Containers"] = {
            WEB_ID: {"IPv4Address": "172.30.251.10/24"}, API_ID: {"IPv4Address": "172.30.251.2/24"}}
        self.runner.volume = {"Name": resources.DATA_VOLUME, "Driver": "local", "Options": None,
                              "Labels": {resources.OWNER_LABEL: OWNER}}
        for identifier in (EDGE_ID, PRIVATE_ID):
            config = self.runner.networks[identifier]["IPAM"]["Config"][0]
            self.runner.routes.extend([
                {"dst": config["Subnet"], "dev": "br-" + identifier[:12]},
                {"dst": config["Gateway"], "dev": "br-" + identifier[:12], "type": "local"},
            ])

    def test_first_install_only_reads_and_probes_loopback(self):
        result = self.inspect()
        self.assertEqual(result, {"networks": {}, "network_ids": [], "containers": [], "volume": None})
        self.port.return_value.__enter__.return_value.bind.assert_called_once_with(("127.0.0.1", 18080))
        self.assertFalse(any(word in {"create", "up", "stop", "rm", "connect", "disconnect", "prune"}
                             for call in self.runner.calls for word in call))

    def test_managed_resources_match_without_mutation_or_port_probe(self):
        self.managed()
        before = deepcopy((self.runner.networks, self.runner.containers, self.runner.volume, self.runner.routes))
        result = self.inspect({"owner": OWNER})
        self.assertEqual(result["network_ids"], [EDGE_ID, PRIVATE_ID])
        self.assertEqual(len(result["containers"]), 2)
        self.assertEqual(result["volume"]["Name"], resources.DATA_VOLUME)
        self.assertEqual(before, (self.runner.networks, self.runner.containers, self.runner.volume, self.runner.routes))
        self.port.assert_not_called()

    def test_missing_installation_refuses_even_correct_labels(self):
        self.managed()
        with self.assertRaisesRegex(DeployError, "existing_waf_resource_not_owned"):
            self.inspect()

    def test_unmanaged_edge_refused(self):
        self.managed()
        self.runner.networks[EDGE_ID]["Labels"] = {}
        with self.assertRaisesRegex(DeployError, "existing_waf_resource_not_owned"):
            self.inspect({})

    def test_other_owner_private_refused(self):
        self.managed()
        self.runner.networks[PRIVATE_ID]["Labels"][resources.OWNER_LABEL] = "other"
        with self.assertRaisesRegex(DeployError, "existing_waf_resource_not_owned"):
            self.inspect({})

    def test_edge_plan_mismatch_refused(self):
        for field, value in (("Internal", False), ("Driver", "macvlan"), ("Scope", "swarm"), ("EnableIPv6", True)):
            with self.subTest(field=field):
                self.managed()
                self.runner.networks[EDGE_ID][field] = value
                with self.assertRaisesRegex(DeployError, "existing_waf_network_plan_changed"):
                    self.inspect({})

    def test_ipam_plan_mismatch_refused(self):
        for field, value in (("Subnet", "172.30.249.0/24"), ("Gateway", "172.30.250.2"),
                             ("IPRange", "172.30.250.0/25"), ("AuxiliaryAddresses", {"reserved": "172.30.250.3"})):
            with self.subTest(field=field):
                self.managed()
                self.runner.networks[EDGE_ID]["IPAM"]["Config"][0][field] = value
                with self.assertRaisesRegex(DeployError, "existing_waf_network_plan_changed"):
                    self.inspect({})

    def test_unmanaged_same_volume_refused_even_when_keys_supplied(self):
        self.runner.volume = {"Name": resources.DATA_VOLUME, "Driver": "local", "Labels": {}}
        with self.assertRaisesRegex(DeployError, "existing_waf_resource_not_owned"):
            self.inspect({"keys": "fixture-present"})

    def test_owned_volume_without_installation_refused(self):
        self.runner.volume = {"Name": resources.DATA_VOLUME, "Driver": "local", "Labels": {resources.OWNER_LABEL: OWNER}}
        with self.assertRaisesRegex(DeployError, "existing_waf_resource_not_owned"):
            self.inspect()

    def test_network_volume_options_refused(self):
        self.managed()
        self.runner.volume["Options"] = {"type": "nfs", "device": "fixture:/data"}
        with self.assertRaisesRegex(DeployError, "waf_volume_driver_not_local"):
            self.inspect({})

    def test_foreign_docker_overlap_refused(self):
        self.runner.networks[EDGE_ID] = network(EDGE_ID, "foreign", "172.30.0.0/16")
        with self.assertRaisesRegex(DeployError, "waf_subnet_overlaps_docker_network"):
            self.inspect()

    def test_foreign_disjoint_and_ipv6_network_allowed(self):
        value = network(EDGE_ID, "foreign", "172.29.0.0/16")
        value["IPAM"]["Config"].append({"Subnet": "fd00:abcd::/64"})
        self.runner.networks[EDGE_ID] = value
        self.assertIn("foreign", self.inspect()["networks"])

    def test_default_host_routes_are_allowed(self):
        self.runner.routes = [{"dst": "default", "dev": "eth0"}, {"dst": "0.0.0.0/0", "dev": "eth1"}]
        self.inspect()

    def test_foreign_host_vpn_overlap_refused(self):
        for destination in ("172.30.250.0/24", "172.16.0.0/12", "172.30.251.4/32"):
            with self.subTest(destination=destination):
                self.runner.routes = [{"dst": destination, "dev": "tun0"}]
                with self.assertRaisesRegex(DeployError, "waf_subnet_overlaps_host_route"):
                    self.inspect()

    def test_owned_bridge_name_does_not_allow_broader_routes(self):
        self.managed()
        self.runner.routes.append({"dst": "172.30.0.0/16", "dev": "br-" + EDGE_ID[:12]})
        with self.assertRaisesRegex(DeployError, "waf_subnet_overlaps_host_route"):
            self.inspect({})

    def test_different_device_on_owned_subnet_refused(self):
        self.managed()
        self.runner.routes.append({"dst": "172.30.250.0/24", "dev": "eth9"})
        with self.assertRaisesRegex(DeployError, "waf_subnet_overlaps_host_route"):
            self.inspect({})

    def test_custom_owned_bridge_routes_allowed(self):
        self.managed()
        self.runner.networks[EDGE_ID]["Options"]["com.docker.network.bridge.name"] = "waf-test-bridge"
        for route in self.runner.routes:
            if route.get("dev") == "br-" + EDGE_ID[:12]:
                route["dev"] = "waf-test-bridge"
        self.inspect({})

    def test_unmanaged_waf_project_container_refused(self):
        self.runner.containers[API_ID] = container(API_ID, "api", owner=None)
        with self.assertRaisesRegex(DeployError, "existing_waf_resource_not_owned"):
            self.inspect({})

    def test_duplicate_service_refused(self):
        self.managed()
        self.runner.containers["f" * 64] = container("f" * 64, "api")
        with self.assertRaisesRegex(DeployError, "waf_container_identity_changed"):
            self.inspect({})

    def test_unexpected_service_and_oneoff_refused(self):
        for label, value in (("com.docker.compose.service", "unrelated"), ("com.docker.compose.oneoff", "True")):
            with self.subTest(label=label):
                self.managed()
                self.runner.containers[API_ID]["Config"]["Labels"][label] = value
                with self.assertRaisesRegex(DeployError, "waf_container_identity_changed"):
                    self.inspect({})

    def test_api_must_not_join_edge(self):
        self.managed()
        self.runner.networks[EDGE_ID]["Containers"][API_ID] = {"IPv4Address": "172.30.250.131/24"}
        with self.assertRaisesRegex(DeployError, "unexpected_waf_edge_network_member"):
            self.inspect({})

    def test_gateway_must_not_join_private(self):
        self.managed()
        self.runner.networks[PRIVATE_ID]["Containers"][GATEWAY_ID] = {"IPv4Address": "172.30.251.3/24"}
        with self.assertRaisesRegex(DeployError, "unexpected_waf_private_network_member"):
            self.inspect({})

    def test_foreign_gateway_must_not_join_edge(self):
        self.managed()
        self.runner.containers[GATEWAY_ID]["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
        with self.assertRaisesRegex(DeployError, "unexpected_waf_edge_network_member"):
            self.inspect({})

    def test_gateway_peer_ip_drift_refused(self):
        self.managed()
        self.runner.networks[EDGE_ID]["Containers"][GATEWAY_ID]["IPv4Address"] = "172.30.250.3/24"
        with self.assertRaisesRegex(DeployError, "waf_gateway_peer_address_changed"):
            self.inspect({})

    def test_web_ip_or_dynamic_pool_drift_refused(self):
        for identifier, value, error in ((PRIVATE_ID, "172.30.251.11/24", "waf_private_member_address_changed"),
                                          (EDGE_ID, "172.30.250.4/24", "waf_edge_member_address_changed")):
            with self.subTest(identifier=identifier):
                self.managed()
                self.runner.networks[identifier]["Containers"][WEB_ID]["IPv4Address"] = value
                with self.assertRaisesRegex(DeployError, error):
                    self.inspect({})

    def test_port_collision_refused(self):
        self.port.return_value.__enter__.return_value.bind.side_effect = OSError("private upstream detail")
        with self.assertRaisesRegex(DeployError, "^waf_loopback_port_unavailable$"):
            self.inspect()

    def test_stopped_web_requires_port_probe(self):
        self.managed()
        self.runner.containers[WEB_ID]["State"]["Running"] = False
        self.inspect({})
        self.port.assert_called_once()

    def test_changed_web_port_requires_port_probe(self):
        self.managed()
        self.settings["web_port"] = 18081
        self.inspect({})
        self.port.return_value.__enter__.return_value.bind.assert_called_once_with(("127.0.0.1", 18081))

    def test_inspect_race_fails_closed(self):
        self.runner.networks[EDGE_ID] = network(PRIVATE_ID, "foreign", "172.29.0.0/16")
        with self.assertRaisesRegex(DeployError, "resource_inspect_target_changed"):
            self.inspect()

    def test_invalid_ipam_has_safe_error(self):
        self.runner.networks[EDGE_ID] = network(EDGE_ID, "foreign", "172.29.0.0/16")
        self.runner.networks[EDGE_ID]["IPAM"]["Config"][0]["Subnet"] = "SYNTHETIC_SECRET_DO_NOT_PRINT"
        with self.assertRaisesRegex(DeployError, "^network_ipam_invalid$"):
            self.inspect()

    def test_route_failure_never_silently_skipped(self):
        self.runner.routes = [{"dst": "SYNTHETIC_ROUTE_ERROR"}]
        with self.assertRaisesRegex(DeployError, "^host_routes_invalid$"):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
