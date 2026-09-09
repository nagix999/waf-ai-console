"""Read-only discovery of the reviewed team Gateway deployment.

No team source, environment, runtime file, Docker object, or database is written.
Subprocess output is captured by Runner and never used as an error message.
"""
from copy import deepcopy
import ipaddress
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from .common import DeployError, digest, json_output, regular_file


IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
TEMPLATE_PATH = "/etc/platform-gateway/server.conf.template"
REVIEWED_HELPERS = {
    "scripts/check_production_subnet_conflicts.py": "f8fbca0f1035beee5fb0d2067fc1d653ff876f0e4a162c50173c574e3444ed8c",
    "scripts/validate_production_network.py": "9a8af5fa80c9fd9316a8ace33c21fe7d1a20e871842e1332b24f51bafc79fc98",
    "infra/host/check_gpu_runtime.py": "c5b0c4ffa1fdf31b59ed1001f0941d6053b4516b3cc990a2b8bb10aa44d6b489",
}
SOURCE_FILES = (
    "compose.production.yaml", "scripts/production.sh", "gateway/production.conf",
    "gateway/Dockerfile.production", "gateway/entrypoint-production.sh",
    "gateway/nginx-main.production.conf", "gateway/proxy-https.conf",
    *REVIEWED_HELPERS,
)
ENV_FIELDS = {
    "PRODUCTION_COMPOSE_PROJECT_NAME", "PLATFORM_GATEWAY_BIND_IP", "PLATFORM_TLS_CERT_FILE",
    "PLATFORM_TLS_KEY_FILE", "PLATFORM_INGRESS_CIDRS_FILE", "PLATFORM_TLS_GID",
    "PLATFORM_GPU_RUNTIME_CONFIG_FILE",
}
GATEWAY_FIELDS = {
    "build", "image", "ports", "volumes", "group_add", "environment", "depends_on",
    "networks", "read_only", "tmpfs", "security_opt", "cap_drop", "restart", "logging",
}
GATEWAY_ENV = {
    "PLATFORM_GATEWAY_MODE", "PLATFORM_PORTAL_HOST", "PLATFORM_HUB_HOST",
    "PLATFORM_USER_DOMAIN", "PLATFORM_TLS_MIN_VALIDITY_SECONDS",
}
TLS_TARGETS = {
    "/run/platform-tls/tls.crt": "PLATFORM_TLS_CERT_FILE",
    "/run/platform-tls/tls.key": "PLATFORM_TLS_KEY_FILE",
    "/run/platform-ingress/company-vpn-cidrs.txt": "PLATFORM_INGRESS_CIDRS_FILE",
}


def require(condition, code):
    if not condition:
        raise DeployError(code)


def safe_path(value, *, private=False):
    require(isinstance(value, str) and value.startswith("/"), "team_absolute_path_required")
    path = Path(value)
    require(all(not item.is_symlink() for item in (path, *path.parents)), "team_symlink_not_allowed")
    return regular_file(path, private=private)


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise DeployError("team_file_unreadable") from None


def file_hash(path):
    try:
        return digest(path.read_bytes())
    except OSError:
        raise DeployError("team_file_unreadable") from None


def read_environment(path):
    """Match production.sh's restricted KEY=value format without shell sourcing."""
    result, seen = {}, set()
    for line in read_text(path).splitlines():
        if not line or line.startswith("#"):
            continue
        require(re.fullmatch(r"[A-Z][A-Z0-9_]*=[A-Za-z0-9_./:@-]+", line), "team_environment_format_unsupported")
        name, value = line.split("=", 1)
        require(name not in seen, "team_environment_duplicate_key")
        seen.add(name)
        if name in ENV_FIELDS:
            result[name] = value
    require((ENV_FIELDS - {"PLATFORM_GPU_RUNTIME_CONFIG_FILE"}).issubset(result), "team_environment_missing_setting")
    return result


def _version(value, code):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[+-][0-9A-Za-z._+~:-]+)?", value.strip()) if isinstance(value, str) else None
    require(match is not None, code)
    return tuple(map(int, match.groups()))


def local_docker(runner):
    """Refuse remote contexts and freeze the resolved local socket for children."""
    endpoint = runner.env.get("DOCKER_HOST", "")
    context = runner.env.get("DOCKER_CONTEXT", "")
    require(not context or context == "default", "remote_or_nondefault_docker_context")
    require(not endpoint or endpoint.startswith("unix:///"), "remote_docker_not_allowed")
    require(not runner.env.get("DOCKER_TLS_VERIFY") and not runner.env.get("DOCKER_CERT_PATH"), "docker_tls_context_not_allowed")
    if not endpoint:
        active = runner.run(["docker", "context", "show"], code="docker_context_unavailable").strip()
        require(active == "default", "remote_or_nondefault_docker_context")
        info = json_output(runner, ["docker", "context", "inspect", "default"], code="docker_context_invalid")
        require(isinstance(info, list) and len(info) == 1, "docker_context_invalid")
        endpoint = info[0].get("Endpoints", {}).get("docker", {}).get("Host", "")
    require(isinstance(endpoint, str) and re.fullmatch(r"unix:///[A-Za-z0-9_./-]+", endpoint), "remote_docker_not_allowed")
    runner.env["DOCKER_HOST"] = endpoint
    runner.env.pop("DOCKER_CONTEXT", None)
    engine = json_output(runner, ["docker", "version", "--format", "{{json .Server}}"], code="docker_engine_unavailable")
    require(isinstance(engine, dict) and engine.get("Os") == "linux", "linux_docker_required")
    version = _version(engine.get("Version"), "docker_engine_version_unsupported")
    require(version >= (27, 1, 2), "docker_engine_version_unsupported")
    compose = _version(runner.run(["docker", "compose", "version", "--short"], code="compose_version_unavailable"), "compose_version_unsupported")
    require(compose >= (2, 24, 4), "compose_version_unsupported")
    return version[0]


def inspect_one(runner, kind, name):
    result = json_output(runner, ["docker", kind, "inspect", name], code="team_docker_inspection_failed")
    require(isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict), "team_docker_inspection_invalid")
    return result[0]


def environment_map(values):
    require(isinstance(values, list), "team_runtime_environment_invalid")
    result = {}
    for value in values:
        require(isinstance(value, str) and "=" in value, "team_runtime_environment_invalid")
        name, content = value.split("=", 1)
        require(name not in result, "team_runtime_environment_duplicate")
        result[name] = content
    return result


def _security_option(value):
    return value.removesuffix(":true") if isinstance(value, str) else value


def decode_compose(value):
    """Compose config serializes literal dollars doubled; snapshots store raw values."""
    if isinstance(value, str):
        return value.replace("$$", "$")
    if isinstance(value, list):
        return [decode_compose(item) for item in value]
    if isinstance(value, dict):
        return {key: decode_compose(item) for key, item in value.items()}
    return value


def _verify_runtime(gateway, runtime, image, environment, managed_image, managed_edge, network_config, runner, project):
    config = runtime.get("Config", {})
    host = runtime.get("HostConfig", {})
    state = runtime.get("State", {})
    require(state.get("Running") is True and state.get("Status") == "running" and state.get("Health", {}).get("Status") == "healthy", "team_gateway_not_healthy")
    require(host.get("ReadonlyRootfs") is True and not host.get("Privileged") and not host.get("CapAdd"), "team_gateway_runtime_security_drift")
    require(set(host.get("CapDrop") or []) == {"ALL"}, "team_gateway_runtime_security_drift")
    require({_security_option(item) for item in host.get("SecurityOpt", [])} == {"no-new-privileges"}, "team_gateway_runtime_security_drift")
    require(not host.get("PublishAllPorts") and not host.get("Devices"), "team_gateway_runtime_security_drift")
    require(not host.get("PidMode") and host.get("IpcMode", "private") == "private", "team_gateway_runtime_security_drift")
    require(not any(host.get(name) for name in ("ExtraHosts", "Dns", "DnsSearch", "DnsOptions", "Links")), "team_gateway_runtime_dns_drift")
    image_config = image.get("Config", {})
    require(image_config.get("User") == "nginx" and config.get("User") == "nginx", "team_gateway_runtime_user_drift")
    for key in ("Entrypoint", "Cmd", "WorkingDir", "Healthcheck"):
        require(config.get(key) == image_config.get(key), "team_gateway_runtime_command_drift")
    require(config.get("Entrypoint") == ["/usr/local/bin/platform-gateway-entrypoint"], "team_gateway_entrypoint_unsupported")
    require(config.get("Cmd") == ["nginx", "-g", "daemon off;"], "team_gateway_command_unsupported")
    expected_env = environment_map(image_config.get("Env", []))
    expected_env.update(gateway["environment"])
    require(environment_map(config.get("Env", [])) == expected_env, "team_gateway_runtime_environment_drift")
    require(set(host.get("GroupAdd") or []) == {str(item) for item in gateway["group_add"]}, "team_gateway_runtime_group_drift")
    require(host.get("RestartPolicy", {}).get("Name") == gateway["restart"], "team_gateway_runtime_restart_drift")
    require(host.get("LogConfig") == {"Type": gateway["logging"]["driver"], "Config": gateway["logging"].get("options", {})}, "team_gateway_runtime_logging_drift")
    tmpfs = dict(item.split(":", 1) for item in gateway["tmpfs"])
    require(set(tmpfs) == {"/tmp", "/var/cache/nginx", "/var/run"}, "team_gateway_runtime_tmpfs_drift")
    require(host.get("Tmpfs") == tmpfs, "team_gateway_runtime_tmpfs_drift")
    mounts = runtime.get("Mounts", [])
    require(isinstance(mounts, list), "team_gateway_runtime_mount_drift")
    binds = {}
    for item in mounts:
        target = item.get("Destination")
        if item.get("Type") == "tmpfs" and target in tmpfs:
            continue
        require(item.get("Type") == "bind" and target in TLS_TARGETS and not item.get("RW") and target not in binds, "team_gateway_nonbaked_or_extra_mount")
        require(item.get("Propagation", "rprivate") == "rprivate", "team_gateway_runtime_mount_drift")
        binds[target] = item.get("Source")
    require(binds == {target: environment[key] for target, key in TLS_TARGETS.items()}, "team_gateway_runtime_mount_drift")
    ports = {key: value for key, value in runtime.get("NetworkSettings", {}).get("Ports", {}).items() if value}
    wanted = {"3030/tcp": [{"HostIp": environment["PLATFORM_GATEWAY_BIND_IP"], "HostPort": "3030"}]}
    require(ports == wanted, "team_gateway_runtime_port_drift")
    live = runtime.get("NetworkSettings", {}).get("Networks", {})
    require(isinstance(live, dict), "team_gateway_runtime_network_drift")
    expected, result = {}, {}
    for key, attachment in gateway["networks"].items():
        require(key in network_config, "team_gateway_network_missing")
        specification = network_config[key]
        name = specification.get("name", f"{project}_{key}")
        expected[name] = attachment or {}
        require(name in live, "team_gateway_runtime_network_drift")
        inspected = inspect_one(runner, "network", name)
        require(inspected.get("Driver") == "bridge" and inspected.get("Scope") == "local", "team_gateway_network_driver_unsupported")
        require(inspected.get("Internal") is bool(specification.get("internal", False)), "team_gateway_network_configuration_drift")
        expected_subnets = {item.get("subnet") for item in specification.get("ipam", {}).get("config", [])}
        actual_subnets = {item.get("Subnet") for item in inspected.get("IPAM", {}).get("Config", [])}
        require(expected_subnets == actual_subnets, "team_gateway_network_configuration_drift")
        require(live[name].get("NetworkID") == inspected.get("Id"), "team_gateway_runtime_network_drift")
        if expected[name].get("ipv4_address"):
            require(live[name].get("IPAddress") == expected[name]["ipv4_address"], "team_gateway_static_ip_drift")
        result[key] = {"external": True, "name": name}
    allowed = set(expected)
    if managed_image and runtime["Image"] == managed_image and managed_edge:
        allowed.add(managed_edge)
    require(set(live) == allowed, "team_gateway_runtime_network_drift")
    return result


def _discover_team(runner, settings, *, managed_image=None, managed_edge=None):
    """Return a private JSON-compatible Gateway snapshot, without creating objects."""
    directory = Path(settings["team_dir"])
    require(directory.is_absolute() and directory.is_dir() and all(not p.is_symlink() for p in (directory, *directory.parents)), "team_directory_invalid")
    directory = directory.resolve()
    project = settings["team_project"]
    require(isinstance(project, str) and PROJECT.fullmatch(project), "team_project_invalid")
    env_file = safe_path(str(directory / ".env.production"), private=True)
    environment = read_environment(env_file)
    require(environment["PRODUCTION_COMPOSE_PROJECT_NAME"] == project, "team_project_mismatch")
    operator_lock = safe_path(str(directory / ".runtime/production/operator.lock"), private=True)
    engine_major = local_docker(runner)
    compose_files = [str(directory / "compose.production.yaml")]
    sources = [*SOURCE_FILES, ".env.production"]
    if engine_major == 27:
        compose_files.append(str(directory / "compose.production.docker27.yaml"))
        sources.append("compose.production.docker27.yaml")
    hashes = {str(directory / name): file_hash(safe_path(str(directory / name))) for name in sources}
    for name, expected in REVIEWED_HELPERS.items():
        require(hashes[str(directory / name)] == expected, "team_helper_version_requires_review")
    compose_args = ["docker", "compose", "--project-directory", str(directory), "--env-file", str(env_file), "--project-name", project]
    for path in compose_files:
        compose_args.extend(["-f", path])
    model = decode_compose(json_output(runner, [*compose_args, "config", "--format", "json"], code="team_compose_invalid"))
    require(isinstance(model, dict) and model.get("name") == project, "team_compose_project_mismatch")
    gateway = model.get("services", {}).get("gateway")
    require(isinstance(gateway, dict) and not set(gateway) - GATEWAY_FIELDS, "team_gateway_configuration_unsupported")
    require(gateway.get("read_only") is True and gateway.get("restart") == "unless-stopped", "team_gateway_configuration_unsupported")
    require(set(gateway.get("cap_drop", [])) == {"ALL"} and {_security_option(item) for item in gateway.get("security_opt", [])} == {"no-new-privileges"}, "team_gateway_configuration_unsupported")
    require(isinstance(gateway.get("environment"), dict) and set(gateway["environment"]) == GATEWAY_ENV, "team_gateway_environment_unsupported")
    require(gateway["environment"]["PLATFORM_GATEWAY_MODE"] == "production", "team_gateway_not_production")
    require(gateway["environment"]["PLATFORM_TLS_MIN_VALIDITY_SECONDS"] == "86400", "team_gateway_tls_policy_unsupported")
    portal = gateway["environment"]["PLATFORM_PORTAL_HOST"]
    hub = gateway["environment"]["PLATFORM_HUB_HOST"]
    domain = gateway["environment"]["PLATFORM_USER_DOMAIN"]
    origin = urlsplit(settings["origin"])
    require(origin.scheme == "https" and origin.port in (None, 443) and origin.username is None and origin.password is None
            and not origin.path and not origin.query and not origin.fragment and origin.hostname not in {portal, hub}
            and origin.hostname.endswith("." + domain), "waf_origin_conflicts_with_team")
    require(set(gateway.get("networks", {})) == {"edge", "ingress"}, "team_gateway_network_configuration_unsupported")
    require(isinstance(gateway.get("tmpfs"), list) and len(gateway["tmpfs"]) == 3 and all(isinstance(item, str) and ":" in item for item in gateway["tmpfs"]), "team_gateway_tmpfs_unsupported")
    ports = gateway.get("ports", [])
    require(len(ports) == 1 and ports[0].get("target") == 3030 and str(ports[0].get("published")) == "3030" and ports[0].get("host_ip") == environment["PLATFORM_GATEWAY_BIND_IP"] and ports[0].get("protocol", "tcp") == "tcp", "team_gateway_port_configuration_unsupported")
    volumes = gateway.get("volumes", [])
    require(len(volumes) == 3, "team_gateway_nonbaked_or_extra_mount")
    for item in volumes:
        target = item.get("target")
        require(item.get("type") == "bind" and target in TLS_TARGETS and item.get("read_only") is True and item.get("source") == environment[TLS_TARGETS[target]], "team_gateway_nonbaked_or_extra_mount")
        require(item.get("bind") == {"create_host_path": False}, "team_gateway_bind_configuration_unsupported")
    image_name = gateway.get("image")
    require(isinstance(image_name, str) and image_name and not image_name.startswith("-"), "team_gateway_image_unsupported")
    configured_image = inspect_one(runner, "image", image_name)
    require(IMAGE_ID.fullmatch(configured_image.get("Id", "")), "team_gateway_image_invalid")
    ids = runner.run([*compose_args, "ps", "--status", "running", "-q", "gateway"], code="team_gateway_unavailable").split()
    require(len(ids) == 1 and CONTAINER_ID.fullmatch(ids[0]), "team_gateway_not_unique")
    runtime = inspect_one(runner, "container", ids[0])
    labels = runtime.get("Config", {}).get("Labels", {})
    require(labels.get("com.docker.compose.project") == project and labels.get("com.docker.compose.service") == "gateway", "team_gateway_identity_mismatch")
    require(runtime.get("Image") in {configured_image["Id"], managed_image}, "team_gateway_image_drift")
    require(IMAGE_ID.fullmatch(runtime.get("Image", "")), "team_gateway_image_invalid")
    current_image = configured_image if runtime["Image"] == configured_image["Id"] else inspect_one(runner, "image", runtime["Image"])
    networks = _verify_runtime(gateway, runtime, current_image, environment, managed_image, managed_edge, model.get("networks", {}), runner, project)
    def baked(path):
        value = runner.run(["docker", "exec", ids[0], "/bin/cat", path], code="team_baked_configuration_unreadable")
        require(len(value.encode("utf-8")) <= 512 * 1024, "team_baked_configuration_too_large")
        return value
    require(baked("/usr/local/share/platform-gateway-config-mode").strip() == "production", "team_gateway_baked_mode_mismatch")
    require(baked("/etc/nginx/nginx.conf") == read_text(directory / "gateway/nginx-main.production.conf"), "team_gateway_main_configuration_drift")
    require(baked("/usr/local/bin/platform-gateway-entrypoint") == read_text(directory / "gateway/entrypoint-production.sh"), "team_gateway_entrypoint_drift")
    require(baked("/etc/nginx/includes/proxy-https.conf") == read_text(directory / "gateway/proxy-https.conf"), "team_gateway_proxy_configuration_drift")
    template = baked(TEMPLATE_PATH)
    if runtime["Image"] != managed_image:
        require(template == read_text(directory / "gateway/production.conf"), "team_gateway_template_drift")
    normalized = deepcopy(gateway)
    normalized.pop("build", None)
    normalized.pop("depends_on", None)
    normalized["image"] = runtime["Image"]
    return {"project": project, "directory": str(directory), "compose_files": compose_files,
            "compose_args": compose_args, "env_file": str(env_file), "gateway_id": ids[0],
            "image_id": runtime["Image"], "gateway": normalized, "networks": networks,
            "template": template, "portal_host": portal, "hub_host": hub,
            "source_hashes": hashes, "operator_lock": str(operator_lock), "engine_major": engine_major,
            "isolation_mode": "inhibit-ipv4" if engine_major == 27 else "isolated",
            "host_contract": {**environment, "waf_host": origin.hostname},
            "managed_runtime": runtime["Image"] == managed_image}


def discover_team(runner, settings, *, managed_image=None, managed_edge=None):
    try:
        return _discover_team(runner, settings, managed_image=managed_image, managed_edge=managed_edge)
    except (KeyError, TypeError, AttributeError, ValueError, OSError):
        raise DeployError("team_discovery_input_or_result_invalid") from None


JUPYTER_STATIC_CHECK = '''
import importlib.util, ipaddress, sys
spec = importlib.util.spec_from_file_location("reviewed_network", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
contract = module.NetworkContract("platform-jupyter-compose-production", sys.argv[2], sys.argv[3],
    ipaddress.IPv4Network("172.29.0.0/24"), ipaddress.IPv4Network("172.29.0.128/25"),
    frozenset((ipaddress.IPv4Address("172.29.0.10"), ipaddress.IPv4Address("172.29.0.20"))))
version = module.inspect_docker_server()
network = module.inspect_network_optional(contract.network_name)
if network is None:
    raise SystemExit(1)
network_id = module.validate_network_configuration(network, contract, version)
module.validate_endpoints(network, contract, require_reserved=True)
module.validate_host_bridge(network_id)
'''


def _run_team_checks(runner, team):
    """Repeat reviewed read-only host/TLS/network checks; no live probe creation."""
    for path, expected in team["source_hashes"].items():
        require(file_hash(safe_path(path, private=path == team["env_file"])) == expected, "team_source_changed_during_operation")
    directory, contract = Path(team["directory"]), team["host_contract"]
    try:
        bind_ip = ipaddress.IPv4Address(contract["PLATFORM_GATEWAY_BIND_IP"])
        require(not (bind_ip.is_unspecified or bind_ip.is_loopback or bind_ip.is_multicast), "team_bind_ip_invalid")
    except ValueError:
        raise DeployError("team_bind_ip_invalid") from None
    addresses = json_output(runner, ["ip", "-j", "-4", "address", "show"], code="team_host_address_inspection_failed")
    require(isinstance(addresses, list) and any(item.get("local") == str(bind_ip) for interface in addresses for item in interface.get("addr_info", [])), "team_bind_ip_not_owned")
    files = {target: safe_path(contract[key]) for target, key in TLS_TARGETS.items()}
    certificate, key, cidrs = (files[target] for target in TLS_TARGETS)
    metadata = key.stat()
    require(stat.S_IMODE(metadata.st_mode) in {0o400, 0o440, 0o600, 0o640} and str(metadata.st_gid) == contract["PLATFORM_TLS_GID"], "team_tls_key_permissions_invalid")
    runner.run(["openssl", "x509", "-in", str(certificate), "-noout", "-checkend", "86400"], code="team_tls_expiring_or_invalid")
    for hostname in (team["portal_host"], contract["waf_host"]):
        # x509 -checkhost can exit zero even when its output reports a mismatch.
        # Accept only the exact affirmative result for this requested hostname.
        checked = runner.run(["openssl", "x509", "-in", str(certificate), "-noout", "-checkhost", hostname], code="team_tls_hostname_mismatch")
        require(checked.strip() == f"Hostname {hostname} does match certificate", "team_tls_hostname_mismatch")
    sans = runner.run(["openssl", "x509", "-in", str(certificate), "-noout", "-ext", "subjectAltName"], code="team_tls_sans_unavailable")
    san_names = set(re.findall(r"DNS:([^,\s]+)", sans))
    require({team["hub_host"], "*." + team["gateway"]["environment"]["PLATFORM_USER_DOMAIN"]}.issubset(san_names), "team_tls_san_missing")
    cert_public = runner.run(["openssl", "x509", "-in", str(certificate), "-pubkey", "-noout"], code="team_tls_public_key_invalid").strip()
    key_public = runner.run(["openssl", "pkey", "-in", str(key), "-passin", "pass:", "-pubout"], code="team_tls_public_key_invalid").strip()
    require(bool(cert_public) and cert_public == key_public, "team_tls_key_mismatch")
    networks = []
    for line in read_text(cidrs).splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            require(re.fullmatch(r"[0-9.]+/(?:[0-9]|[12][0-9]|3[0-2])", value), "team_ingress_file_invalid")
            network = ipaddress.IPv4Network(value, strict=False)
            require(network.prefixlen > 0, "team_ingress_file_invalid")
            networks.append(network)
        except ValueError:
            raise DeployError("team_ingress_file_invalid") from None
    require(bool(networks), "team_ingress_file_invalid")
    # -B also prevents importlib from writing __pycache__ into the team repo.
    runner.run(["python3", "-I", "-B", str(directory / "scripts/check_production_subnet_conflicts.py"), "--compose-project", team["project"]], code="team_subnet_conflict")
    runner.run(["python3", "-I", "-B", "-c", JUPYTER_STATIC_CHECK, str(directory / "scripts/validate_production_network.py"), team["project"], team["isolation_mode"]], code="team_jupyter_network_contract_failed")
    gpu = contract.get("PLATFORM_GPU_RUNTIME_CONFIG_FILE", "disabled")
    if gpu != "disabled":
        safe_path(gpu)
        runner.run(["python3", "-I", "-B", str(directory / "infra/host/check_gpu_runtime.py"), "--config", gpu, "--print-device-ids"], code="team_gpu_policy_invalid")
        # production.sh requires these binaries but does not run a GPU workload.
        runner.run(["python3", "-I", "-B", "-c", "import shutil; assert shutil.which('nvidia-smi') and shutil.which('nvidia-ctk')"], code="team_gpu_host_tools_missing")
    for service in ("api", "frontend", "egress-proxy", "jupyterhub"):
        ids = runner.run([*team["compose_args"], "ps", "--status", "running", "-q", service], code="team_control_plane_unavailable").split()
        require(len(ids) == 1 and CONTAINER_ID.fullmatch(ids[0]), "team_control_plane_unavailable")
        state = inspect_one(runner, "container", ids[0]).get("State", {})
        require(state.get("Running") is True and state.get("Status") == "running", "team_control_plane_unavailable")


def run_team_checks(runner, team):
    try:
        _run_team_checks(runner, team)
    except (KeyError, TypeError, AttributeError, ValueError, OSError):
        raise DeployError("team_check_input_or_result_invalid") from None
