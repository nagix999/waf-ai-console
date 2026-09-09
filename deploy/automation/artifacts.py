"""Literal WAF settings and in-memory Gateway artifacts; no deployment writes.

The operator's env file is data, never shell code. Compose models returned by
this module are raw dictionaries: the writer escapes Compose dollar signs once.
"""
import base64
import copy
import ipaddress
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import urlsplit

from .common import DeployError, canonical, digest
from deploy.security.render_proxy import PRIVATE, PolicyError, validate as validate_proxy


DEFAULTS = {
    "WAF_EDGE_NETWORK": "waf-console-edge",
    "WAF_EDGE_SUBNET": "172.30.250.0/24",
    "WAF_EDGE_IP_RANGE": "172.30.250.128/25",
    "WAF_TRUSTED_PROXY_IP": "172.30.250.2",
    "WAF_PRIVATE_SUBNET": "172.30.251.0/24",
    "WAF_PRIVATE_WEB_IP": "172.30.251.10",
    "WAF_WEB_PORT": "18080",
    "WAF_AGENT_MODE": "moduagent",
    "WAF_SESSION_HTTPS_ONLY": "true",
    "WAF_ENCRYPTION_KEY_VERSION": "prod-v1",
    "WAF_WORKER_POLL_SECONDS": "1",
    "WAF_JOB_LEASE_SECONDS": "300",
    "WAF_VLLM_TEST_LEASE_SECONDS": "900",
    "WAF_VERIFIER_CONFIDENCE_THRESHOLD": "0.75",
}
SECRET_KEYS = ("WAF_SESSION_SECRET", "WAF_DATA_ENCRYPTION_KEY")
MARKER = "# WAF-MANAGED-GATEWAY v1"
END_MARKER = "# END WAF-MANAGED-GATEWAY v1"


def _literal(value):
    if (not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char in value for char in ("'", "\\", "\u0085", "\u2028", "\u2029"))):
        raise DeployError("dotenv_literal_unsupported")
    return value


def _read_env(path):
    """One assignment per line, optional literal quotes, no inline comments."""
    try:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise DeployError("env_symlink_not_allowed")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > 65536):
                raise DeployError("env_regular_file_mode_0600_required")
            content = stream.read(65537)
    except (OSError, UnicodeError):
        raise DeployError("env_file_unavailable") from None
    if any(char in content for char in ("\x00", "\u0085", "\u2028", "\u2029")):
        raise DeployError("dotenv_literal_unsupported")
    values = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"WAF_[A-Z0-9_]+", key) or key in values:
            raise DeployError("env_assignment_invalid_or_duplicate")
        value = value.strip()
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise DeployError("env_literal_quotes_invalid")
            value = value[1:-1]
        elif value.endswith(("'", '"')):
            raise DeployError("env_literal_quotes_invalid")
        values[key] = _literal(value)
    return values


def _network(value, error):
    try:
        network = ipaddress.IPv4Network(value, strict=True)
        if (not any(network.subnet_of(parent) for parent in PRIVATE)
                or not 16 <= network.prefixlen <= 28):
            raise ValueError()
        return network
    except (ValueError, TypeError):
        raise DeployError(error) from None


def _secret_valid(key, value):
    _literal(value)
    if key == "WAF_SESSION_SECRET":
        if (len(value.encode("utf-8")) < 32 or len(set(value)) < 8
                or value in {"local-session-secret-change-before-deploy", "replace-with-a-long-random-session-secret"}
                or any(word in value.lower() for word in ("change-me", "changeme", "example", "replace-me"))):
            raise DeployError("session_secret_too_weak")
    elif key == "WAF_DATA_ENCRYPTION_KEY":
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}=", value):
                raise ValueError()
            decoded = base64.b64decode(value, altchars=b"-_", validate=True)
            if (len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode() != value
                    or len(set(decoded)) < 8):
                raise ValueError()
        except ValueError:
            raise DeployError("encryption_key_invalid") from None


def load_settings(env_path, root):
    """Validate the operator's WAF-only file without ambient env interpolation."""
    root = Path(root).absolute()
    path = Path(env_path)
    if not path.is_absolute():
        path = root / path
    values = _read_env(path)
    for key, value in DEFAULTS.items():
        if not values.get(key):
            values[key] = value
    team_path = Path(values.get("WAF_TEAM_PROJECT_DIR", ""))
    if (not team_path.is_absolute() or not team_path.is_dir()
            or team_path == Path("/") or team_path.resolve() == root.resolve()):
        raise DeployError("team_project_directory_required")
    if any(parent.is_symlink() for parent in (team_path, *team_path.parents)):
        raise DeployError("team_project_symlink_not_allowed")
    project = values.get("WAF_TEAM_PROJECT_NAME", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
        raise DeployError("team_project_name_required")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", values.get("WAF_ADMIN_USERNAME", "")):
        raise DeployError("admin_username_required")
    password = values.get("WAF_ADMIN_PASSWORD", "")
    groups = sum(bool(re.search(pattern, password)) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^a-zA-Z0-9]"))
    if (len(password) < 16 or len(password) > 1024 or len(set(password)) < 8 or groups < 3
            or any(word in password.lower() for word in ("change-me", "changeme", "replace-me", "example"))):
        raise DeployError("admin_password_too_weak")
    try:
        proxy = validate_proxy(values)
    except PolicyError:
        raise DeployError("proxy_settings_invalid") from None
    if proxy["port"] != 443 or not proxy["hostname"].count("."):
        raise DeployError("public_https_domain_port_443_required")
    try:
        ipaddress.ip_address(proxy["hostname"])
    except ValueError:
        pass
    else:
        raise DeployError("public_https_domain_port_443_required")
    edge = _network(values["WAF_EDGE_SUBNET"], "edge_subnet_invalid")
    private = _network(values["WAF_PRIVATE_SUBNET"], "private_subnet_invalid")
    try:
        dynamic = ipaddress.IPv4Network(values["WAF_EDGE_IP_RANGE"], strict=True)
        peer = ipaddress.IPv4Address(proxy["peer"])
        if (not dynamic.subnet_of(edge) or dynamic.prefixlen >= 31
                or edge.overlaps(private) or peer not in edge or peer in dynamic
                or int(peer) <= int(edge.network_address) + 1 or peer == edge.broadcast_address):
            raise ValueError()
    except ValueError:
        raise DeployError("edge_address_plan_invalid") from None
    edge_name = values["WAF_EDGE_NETWORK"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", edge_name) or edge_name in {"host", "bridge", "none"}:
        raise DeployError("edge_network_name_invalid")
    try:
        port = int(values["WAF_WEB_PORT"])
        if str(port) != values["WAF_WEB_PORT"] or not 1024 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise DeployError("web_loopback_port_invalid") from None
    if values["WAF_SESSION_HTTPS_ONLY"] != "true" or values.get("WAF_ENVIRONMENT", "production") != "production":
        raise DeployError("production_https_session_required")
    if values["WAF_AGENT_MODE"] not in {"moduagent", "stub"}:
        raise DeployError("agent_mode_invalid")
    for key, lower, upper in (("WAF_WORKER_POLL_SECONDS", 0.1, 60),
                              ("WAF_JOB_LEASE_SECONDS", 30, 3600),
                              ("WAF_VLLM_TEST_LEASE_SECONDS", 60, 3600),
                              ("WAF_VERIFIER_CONFIDENCE_THRESHOLD", 0, 1)):
        try:
            number = float(values[key]) if key in {"WAF_WORKER_POLL_SECONDS", "WAF_VERIFIER_CONFIDENCE_THRESHOLD"} else int(values[key])
            if not lower <= number <= upper:
                raise ValueError()
        except ValueError:
            raise DeployError("worker_settings_invalid") from None
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", values["WAF_ENCRYPTION_KEY_VERSION"]):
        raise DeployError("encryption_key_version_invalid")
    for key in SECRET_KEYS:
        if values.get(key):
            _secret_valid(key, values[key])
    return {"team_dir": str(team_path), "team_project": project,
            "origin": proxy["origin"], "edge_name": edge_name, "edge_subnet": str(edge),
            "edge_range": str(dynamic), "peer": str(peer), "private_subnet": str(private),
            "web_ip": proxy["web"], "web_port": port, "values": values,
            "root": str(root), "env_file": str(path), "fingerprint": digest(canonical(values))}


def secrets_for_install(values, existing_secret_state=None):
    """Generate once; never rotate encryption/session keys during a reconnect."""
    version = values.get("WAF_ENCRYPTION_KEY_VERSION") or DEFAULTS["WAF_ENCRYPTION_KEY_VERSION"]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", version):
        raise DeployError("encryption_key_version_invalid")
    state = {} if existing_secret_state is None else copy.deepcopy(existing_secret_state)
    if existing_secret_state is not None:
        if (not isinstance(state, dict) or set(state) != {*SECRET_KEYS, "WAF_ENCRYPTION_KEY_VERSION"}
                or state["WAF_ENCRYPTION_KEY_VERSION"] != version):
            raise DeployError("existing_secret_state_invalid_or_changed")
    for key in SECRET_KEYS:
        supplied = values.get(key, "")
        if existing_secret_state is not None:
            _secret_valid(key, state[key])
            if supplied and not secrets.compare_digest(supplied.encode(), state[key].encode()):
                raise DeployError("existing_secret_change_not_allowed")
        else:
            state[key] = supplied or (secrets.token_urlsafe(48) if key == "WAF_SESSION_SECRET"
                                      else base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
            _secret_valid(key, state[key])
    state["WAF_ENCRYPTION_KEY_VERSION"] = version
    return state


def runtime_env(values, secret_state):
    """Single quotes disable Compose interpolation of literal dollar signs."""
    merged = {**values, **secret_state, "WAF_ENVIRONMENT": "production", "WAF_SESSION_HTTPS_ONLY": "true"}
    lines = []
    for key, value in sorted(merged.items()):
        if not re.fullmatch(r"WAF_[A-Z0-9_]+", key):
            raise DeployError("runtime_env_key_invalid")
        lines.append(key + "='" + _literal(value) + "'")
    return "\n".join(lines) + "\n"


def gateway_bundle(team, settings, derived_image):
    """Return original/derived/check models without modifying team source."""
    template = team["template"]
    hostname = urlsplit(settings["origin"]).hostname
    if (not isinstance(template, str) or not template.strip() or "\x00" in template
            or "waf-managed" in template.lower() or "$waf_web_target" in template
            or re.search(r"(?<![a-z0-9.-])" + re.escape(hostname) + r"(?![a-z0-9.-])", template, re.I)):
        raise DeployError("gateway_template_already_modified_or_invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}", derived_image):
        raise DeployError("derived_image_reference_invalid")
    original = {"name": team["project"], "services": {"gateway": copy.deepcopy(team["gateway"])},
                "networks": copy.deepcopy(team["networks"])}
    original["services"]["gateway"]["image"] = team["image_id"]
    if set(original["services"]["gateway"].get("networks", {})) - original["networks"].keys():
        raise DeployError("gateway_network_snapshot_incomplete")
    if "waf-edge" in original["networks"] or settings["edge_name"] in {
            value.get("name") for value in original["networks"].values()}:
        raise DeployError("gateway_edge_network_conflict")
    for key in ("build", "depends_on"):
        original["services"]["gateway"].pop(key, None)
    try:
        block = (Path(settings["root"]) / "deploy/security/gateway-waf.server.conf.example").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise DeployError("gateway_addition_template_unavailable") from None
    if block.count("waf.cyberailabs.team") != 2:
        raise DeployError("gateway_addition_template_changed")
    block = block.replace("waf.cyberailabs.team", hostname)
    combined = template + "\n" + MARKER + "\n" + block + "\n" + END_MARKER + "\n"
    deployed = copy.deepcopy(original)
    deployed["services"]["gateway"]["image"] = derived_image
    deployed["services"]["gateway"]["networks"]["waf-edge"] = {"ipv4_address": settings["peer"]}
    deployed["networks"]["waf-edge"] = {"external": True, "name": settings["edge_name"]}
    check = copy.deepcopy(deployed)
    service = check["services"]["gateway"]
    if "edge" not in original["services"]["gateway"]["networks"]:
        raise DeployError("gateway_original_edge_missing")
    service["networks"] = {"edge": {}}
    service["ports"] = []
    service.pop("container_name", None)
    service["restart"] = "no"
    check["networks"] = {"edge": copy.deepcopy(original["networks"]["edge"])}
    return {"template": combined,
            "dockerfile": "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nCOPY --chmod=0444 server.conf.template /etc/platform-gateway/server.conf.template\n",
            "gateway": deployed, "rollback": original, "check": check}
