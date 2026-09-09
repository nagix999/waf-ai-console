"""Compare one Gateway runtime with a private, authorized Compose snapshot.

Only Docker inspection is performed. Runtime mismatches never echo actual
environment values, mount contents, or command output.
"""
from pathlib import Path
import re

from .common import DeployError
from .team import CONTAINER_ID, IMAGE_ID, environment_map, inspect_one


def require(value, code):
    if not value:
        raise DeployError(code)


def _options(values):
    require(isinstance(values, list), "gateway_runtime_options_invalid")
    return {value.removesuffix(":true") for value in values if isinstance(value, str)}


def _port_map(value):
    require(isinstance(value, dict), "gateway_runtime_ports_invalid")
    result = {}
    for target, entries in value.items():
        if not entries:
            continue
        require(isinstance(entries, list), "gateway_runtime_ports_invalid")
        result[target] = sorted((item["HostIp"], item["HostPort"]) for item in entries)
    return result


def _expected_ports(ports):
    result = {}
    require(isinstance(ports, list), "gateway_snapshot_ports_invalid")
    for item in ports:
        require(isinstance(item, dict) and item.get("host_ip") and item.get("published"), "gateway_snapshot_ports_invalid")
        target = str(item["target"]) + "/" + item.get("protocol", "tcp")
        result.setdefault(target, []).append((item["host_ip"], str(item["published"])))
    return {key: sorted(values) for key, values in result.items()}


def _assert_matches(runner, container_id, model, allow_stopped):
    require(isinstance(container_id, str) and CONTAINER_ID.fullmatch(container_id), "gateway_runtime_id_invalid")
    require(isinstance(model, dict) and set(model.get("services", {})) == {"gateway"}, "gateway_snapshot_invalid")
    service = model["services"]["gateway"]
    expected_image = service.get("image")
    require(isinstance(expected_image, str) and IMAGE_ID.fullmatch(expected_image), "gateway_snapshot_image_not_pinned")
    runtime = inspect_one(runner, "container", container_id)
    require(runtime.get("Id") == container_id and runtime.get("Image") == expected_image, "gateway_runtime_identity_changed")
    state, actual, host = runtime.get("State", {}), runtime.get("Config", {}), runtime.get("HostConfig", {})
    allowed_states = {"running", "exited", "created", "restarting"} if allow_stopped else {"running"}
    require(state.get("Status") in allowed_states and not state.get("Paused"), "gateway_runtime_state_changed")
    running = state.get("Status") == "running"
    if running:
        require(state.get("Running") is True, "gateway_runtime_state_changed")
    elif state.get("Status") in {"exited", "created"}:
        require(state.get("Running") is False, "gateway_runtime_state_changed")
    if not allow_stopped:
        require(state.get("Health", {}).get("Status") == "healthy", "gateway_runtime_not_healthy")
    image = inspect_one(runner, "image", expected_image)
    require(image.get("Id") == expected_image, "gateway_runtime_image_changed")
    inherited = image.get("Config", {})
    labels = actual.get("Labels") or {}
    require(labels.get("com.docker.compose.project") == model.get("name")
            and labels.get("com.docker.compose.service") == "gateway"
            and labels.get("com.docker.compose.oneoff", "false").lower() == "false", "gateway_runtime_project_changed")
    expected_labels = {**(inherited.get("Labels") or {}), **(service.get("labels") or {})}
    require(all(labels.get(key) == value for key, value in expected_labels.items()), "gateway_runtime_labels_changed")
    expected_env = environment_map(inherited.get("Env") or [])
    require(isinstance(service.get("environment", {}), dict), "gateway_snapshot_environment_invalid")
    expected_env.update(service.get("environment", {}))
    require(environment_map(actual.get("Env") or []) == expected_env, "gateway_runtime_environment_changed")
    for compose_key, docker_key in (("command", "Cmd"), ("entrypoint", "Entrypoint"), ("working_dir", "WorkingDir"), ("user", "User")):
        wanted = service.get(compose_key)
        if wanted is None:
            wanted = inherited.get(docker_key)
        require(actual.get(docker_key) == wanted, "gateway_runtime_command_or_user_changed")
    # The supported team service inherits the baked healthcheck unchanged.
    require("healthcheck" not in service and actual.get("Healthcheck") == inherited.get("Healthcheck"), "gateway_runtime_healthcheck_changed")
    require(service.get("read_only") is True and host.get("ReadonlyRootfs") is True, "gateway_runtime_readonly_changed")
    require(not service.get("privileged") and not host.get("Privileged") and not host.get("Devices"), "gateway_runtime_privilege_changed")
    require({str(value) for value in host.get("CapDrop") or []} == {str(value) for value in service.get("cap_drop") or []}, "gateway_runtime_capabilities_changed")
    require({str(value) for value in host.get("CapAdd") or []} == {str(value) for value in service.get("cap_add") or []}, "gateway_runtime_capabilities_changed")
    require(_options(host.get("SecurityOpt") or []) == _options(service.get("security_opt") or []), "gateway_runtime_security_options_changed")
    require({str(value) for value in host.get("GroupAdd") or []} == {str(value) for value in service.get("group_add") or []}, "gateway_runtime_groups_changed")
    require(not host.get("PidMode") and host.get("IpcMode", "private") == "private", "gateway_runtime_namespace_changed")
    require(not host.get("UsernsMode") or host.get("UsernsMode") == "private", "gateway_runtime_namespace_changed")
    require(not any(host.get(key) for key in ("ExtraHosts", "Dns", "DnsSearch", "DnsOptions", "Links")), "gateway_runtime_dns_changed")
    require(host.get("RestartPolicy", {}).get("Name") == service.get("restart", "no"), "gateway_runtime_restart_changed")
    expected_logging = service.get("logging", {})
    require(host.get("LogConfig") == {"Type": expected_logging.get("driver"), "Config": expected_logging.get("options") or {}}, "gateway_runtime_logging_changed")
    tmpfs = dict(item.split(":", 1) for item in service.get("tmpfs", []))
    require((host.get("Tmpfs") or {}) == tmpfs, "gateway_runtime_tmpfs_changed")
    expected_mounts = {}
    for mount in service.get("volumes", []):
        require(mount.get("type") == "bind" and isinstance(mount.get("source"), str)
                and Path(mount["source"]).is_absolute() and mount.get("target") not in expected_mounts,
                "gateway_snapshot_mount_invalid")
        expected_mounts[mount["target"]] = (mount["source"], not mount.get("read_only", False),
                                             mount.get("bind", {}).get("propagation", "rprivate"))
    actual_mounts = {}
    for mount in runtime.get("Mounts", []):
        target = mount.get("Destination")
        if mount.get("Type") == "tmpfs" and target in tmpfs:
            continue
        require(mount.get("Type") == "bind" and target not in actual_mounts, "gateway_runtime_mount_changed")
        actual_mounts[target] = (mount.get("Source"), mount.get("RW"), mount.get("Propagation", "rprivate"))
    require(actual_mounts == expected_mounts, "gateway_runtime_mount_changed")
    ports = _expected_ports(service.get("ports", []))
    require(not host.get("PublishAllPorts") and _port_map(host.get("PortBindings") or {}) == ports, "gateway_runtime_port_configuration_changed")
    if running:
        require(_port_map(runtime.get("NetworkSettings", {}).get("Ports") or {}) == ports, "gateway_runtime_published_ports_changed")
    attachments = service.get("networks", {})
    networks = model.get("networks", {})
    require(isinstance(attachments, dict) and isinstance(networks, dict), "gateway_snapshot_networks_invalid")
    expected_networks = {}
    for key, options in attachments.items():
        require(key in networks and isinstance(networks[key].get("name"), str), "gateway_snapshot_networks_invalid")
        name = networks[key]["name"]
        require(name not in expected_networks, "gateway_snapshot_networks_invalid")
        expected_networks[name] = options or {}
    actual_networks = runtime.get("NetworkSettings", {}).get("Networks", {})
    require(set(actual_networks) == set(expected_networks) and host.get("NetworkMode") in expected_networks, "gateway_runtime_network_attachment_changed")
    for name, options in expected_networks.items():
        endpoint = actual_networks[name]
        inspected = inspect_one(runner, "network", name)
        require(inspected.get("Name") == name and inspected.get("Driver") == "bridge" and inspected.get("Scope") == "local", "gateway_runtime_network_identity_changed")
        # Stopped containers retain desired EndpointSettings but may have no
        # assigned address/NetworkID. Do not infer a lost fixed-IP intention.
        require(endpoint.get("NetworkID") in ({inspected.get("Id")} if running else {"", None, inspected.get("Id")}), "gateway_runtime_network_identity_changed")
        expected_ip = options.get("ipv4_address")
        if expected_ip:
            if running:
                require(endpoint.get("IPAddress") == expected_ip, "gateway_runtime_fixed_ip_changed")
            else:
                require(endpoint.get("IPAddress") in {"", None, expected_ip}
                        and (endpoint.get("IPAMConfig") or {}).get("IPv4Address") == expected_ip,
                        "gateway_stopped_fixed_ip_unverifiable")
        else:
            require(not (endpoint.get("IPAMConfig") or {}).get("IPv4Address"), "gateway_runtime_dynamic_ip_changed")
        require(not endpoint.get("GlobalIPv6Address") and not (endpoint.get("IPAMConfig") or {}).get("IPv6Address"), "gateway_runtime_ipv6_changed")


def assert_runtime_matches(runner, container_id, model, *, allow_stopped=False):
    try:
        _assert_matches(runner, container_id, model, allow_stopped)
    except (KeyError, TypeError, AttributeError, ValueError, OSError):
        raise DeployError("gateway_runtime_or_snapshot_invalid") from None
