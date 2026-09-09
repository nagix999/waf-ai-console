"""Read-only WAF resource preflight; never adopt an unmanaged Docker object."""
import ipaddress
import re
import socket

from .common import DeployError, json_output


OWNER_LABEL = "io.waf.deploy.owner"
WAF_PROJECT = "waf-ai-console-prod"
PRIVATE_NETWORK = WAF_PROJECT + "_waf-private"
DATA_VOLUME = "waf-ai-console-production-data"
SERVICES = {"api", "worker", "model-tester", "waf-web"}
IDENTIFIER = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition, code):
    if not condition:
        raise DeployError(code)


def _ids(output):
    values = output.split()
    _require(len(values) == len(set(values)) and all(IDENTIFIER.fullmatch(value) for value in values),
             "resource_identifier_invalid")
    return values


def _inspect(runner, kind, targets):
    if not targets:
        return []
    values = json_output(runner, ["docker", kind, "inspect", *targets], code="resource_inspect_failed")
    _require(isinstance(values, list) and len(values) == len(targets)
             and all(isinstance(value, dict) for value in values), "resource_inspect_invalid")
    expected = "Name" if kind == "volume" else "Id"
    _require({value.get(expected) for value in values} == set(targets), "resource_inspect_target_changed")
    return values


def _subnets(network):
    config = network.get("IPAM", {}).get("Config") or []
    _require(isinstance(config, list), "network_ipam_invalid")
    result = []
    for item in config:
        _require(isinstance(item, dict), "network_ipam_invalid")
        value = item.get("Subnet")
        if not value:
            continue
        try:
            parsed = ipaddress.ip_network(value, strict=True)
        except (ValueError, TypeError):
            raise DeployError("network_ipam_invalid") from None
        if parsed.version == 4:
            result.append(parsed)
    return result


def _owned(labels, owner, installation):
    _require(installation is not None and isinstance(labels, dict) and labels.get(OWNER_LABEL) == owner,
             "existing_waf_resource_not_owned")


def _check_network(network, name, subnet, settings, owner, installation):
    _owned(network.get("Labels"), owner, installation)
    edge = name == settings["edge_name"]
    _require(network.get("Driver") == "bridge" and network.get("Scope") == "local"
             and network.get("Internal") is edge and not network.get("EnableIPv6", False),
             "existing_waf_network_plan_changed")
    configs = network.get("IPAM", {}).get("Config") or []
    _require(len(configs) == 1 and configs[0].get("Subnet") == str(subnet),
             "existing_waf_network_plan_changed")
    config = configs[0]
    _require(config.get("Gateway") == str(subnet.network_address + 1)
             and not config.get("AuxiliaryAddresses"), "existing_waf_network_plan_changed")
    expected_range = settings["edge_range"] if edge else ""
    _require((config.get("IPRange") or "") == expected_range, "existing_waf_network_plan_changed")
    return (network.get("Options") or {}).get("com.docker.network.bridge.name") or "br-" + network["Id"][:12]


def _check_routes(runner, proposed, bridges):
    routes = json_output(runner, ["ip", "-j", "-4", "route", "show", "table", "all"],
                         code="host_routes_unavailable")
    _require(isinstance(routes, list), "host_routes_invalid")
    for route in routes:
        _require(isinstance(route, dict), "host_routes_invalid")
        destination = route.get("dst", "default")
        if destination in {"default", "0.0.0.0/0"}:
            continue
        try:
            subnet = ipaddress.IPv4Network(destination, strict=False)
        except (TypeError, ValueError):
            raise DeployError("host_routes_invalid") from None
        for planned in proposed:
            if not subnet.overlaps(planned):
                continue
            known = bridges.get(route.get("dev"))
            _require(known == planned and subnet.subnet_of(known), "waf_subnet_overlaps_host_route")


def _check_port(settings, containers):
    port = str(settings["web_port"])
    for container in containers:
        labels = container["Config"]["Labels"]
        if labels["com.docker.compose.service"] != "waf-web":
            continue
        bindings = container.get("NetworkSettings", {}).get("Ports", {}).get("80/tcp") or []
        if container.get("State", {}).get("Running") and bindings == [{"HostIp": "127.0.0.1", "HostPort": port}]:
            return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", int(port)))
    except OSError:
        raise DeployError("waf_loopback_port_unavailable") from None


def inspect_resources(runner, settings, owner, installation=None):
    """Return private inventories; no Docker objects, keys, files, or state written.

    `installation` is the caller's validated existing installation state. Its
    absence forbids adoption even when an object's owner label happens to match.
    The only host probe binds, without listening, to the configured loopback port
    and releases it immediately. This detects conflicts, not future reservation.
    """
    _require(isinstance(owner, str) and bool(owner), "resource_owner_required")
    try:
        edge = ipaddress.IPv4Network(settings["edge_subnet"], strict=True)
        private = ipaddress.IPv4Network(settings["private_subnet"], strict=True)
    except (KeyError, ValueError, TypeError):
        raise DeployError("resource_address_plan_invalid") from None
    _require(not edge.overlaps(private) and settings["edge_name"] != PRIVATE_NETWORK,
             "resource_address_plan_invalid")
    network_ids = _ids(runner.run(["docker", "network", "ls", "--quiet", "--no-trunc"],
                                 code="network_inventory_failed"))
    network_list = _inspect(runner, "network", network_ids)
    networks = {}
    bridges = {}
    for network in network_list:
        name = network.get("Name")
        _require(isinstance(name, str) and name and name not in networks, "network_inventory_invalid")
        networks[name] = network
        proposed = {settings["edge_name"]: edge, PRIVATE_NETWORK: private}
        if name in proposed:
            bridge = _check_network(network, name, proposed[name], settings, owner, installation)
            _require(bridge not in bridges, "network_bridge_name_conflict")
            bridges[bridge] = proposed[name]
        else:
            _require(not any(subnet.overlaps(planned) for subnet in _subnets(network)
                             for planned in (edge, private)), "waf_subnet_overlaps_docker_network")

    names = runner.run(["docker", "volume", "ls", "--format", "{{.Name}}"],
                       code="volume_inventory_failed").splitlines()
    volume = _inspect(runner, "volume", [DATA_VOLUME])[0] if DATA_VOLUME in names else None
    if volume is not None:
        _owned(volume.get("Labels"), owner, installation)
        _require(volume.get("Driver") == "local" and not volume.get("Options"), "waf_volume_driver_not_local")

    container_ids = _ids(runner.run(["docker", "ps", "--all", "--quiet", "--no-trunc", "--filter",
                                     "label=com.docker.compose.project=" + WAF_PROJECT],
                                    code="waf_container_inventory_failed"))
    containers = _inspect(runner, "container", container_ids)
    seen = set()
    for container in containers:
        labels = container.get("Config", {}).get("Labels") or {}
        _owned(labels, owner, installation)
        service = labels.get("com.docker.compose.service")
        _require(labels.get("com.docker.compose.project") == WAF_PROJECT and service in SERVICES
                 and service not in seen and labels.get("com.docker.compose.oneoff", "False").lower() == "false",
                 "waf_container_identity_changed")
        seen.add(service)
    by_id = {value["Id"]: value for value in containers}
    for name in (settings["edge_name"], PRIVATE_NETWORK):
        network = networks.get(name)
        if not network:
            continue
        members = network.get("Containers") or {}
        _require(isinstance(members, dict), "network_members_invalid")
        gateway_count = 0
        for identifier in members:
            _require(IDENTIFIER.fullmatch(identifier) is not None, "network_member_identifier_invalid")
            try:
                address = ipaddress.IPv4Interface(members[identifier]["IPv4Address"]).ip
            except (KeyError, ValueError, TypeError):
                raise DeployError("network_member_address_invalid") from None
            container = by_id.get(identifier)
            if container is None:
                container = _inspect(runner, "container", [identifier])[0]
            labels = container.get("Config", {}).get("Labels") or {}
            waf_service = labels.get("com.docker.compose.service") if identifier in by_id else None
            if name == PRIVATE_NETWORK:
                _require(waf_service in SERVICES, "unexpected_waf_private_network_member")
                _require(address in private and (waf_service != "waf-web" or str(address) == settings["web_ip"]),
                         "waf_private_member_address_changed")
            elif waf_service is not None:
                _require(waf_service == "waf-web", "unexpected_waf_edge_network_member")
                _require(address in ipaddress.IPv4Network(settings["edge_range"]), "waf_edge_member_address_changed")
            else:
                _require(labels.get("com.docker.compose.project") == settings["team_project"]
                         and labels.get("com.docker.compose.service") == "gateway",
                         "unexpected_waf_edge_network_member")
                gateway_count += 1
                _require(gateway_count <= 1, "multiple_waf_edge_gateways")
                _require(str(address) == settings["peer"], "waf_gateway_peer_address_changed")
    _check_routes(runner, (edge, private), bridges)
    _check_port(settings, containers)
    return {"networks": networks, "network_ids": network_ids, "containers": containers, "volume": volume}
