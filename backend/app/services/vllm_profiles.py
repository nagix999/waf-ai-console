import hashlib
import ipaddress
import json
import socket
from urllib.parse import urlsplit, urlunsplit

from ..models import VLLMProfile, VLLMTestRun
from ..schemas import VLLMProfileResponse, VLLMTestRunResponse


class TargetNotAllowedError(ValueError):
    pass


def _split_allowed_target(value: str) -> tuple[str, int]:
    host, separator, port_text = value.strip().rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise TargetNotAllowedError("invalid_vllm_allowlist_entry")
    return host.strip().lower(), int(port_text)


def normalize_and_validate_base_url(base_url: str, allowed_targets: str) -> str:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise TargetNotAllowedError("vllm_url_scheme_must_be_http_or_https")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TargetNotAllowedError("invalid_vllm_base_url")
    path = parsed.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise TargetNotAllowedError("vllm_base_url_path_must_be_v1")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise TargetNotAllowedError("invalid_vllm_base_url_port") from exc
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        raise TargetNotAllowedError("ipv6_vllm_targets_are_not_supported_yet")
    entries = [_split_allowed_target(item) for item in allowed_targets.split(",") if item.strip()]
    if not entries:
        raise TargetNotAllowedError("vllm_allowlist_is_empty")

    exact_match = any(hostname == allowed_host and port == allowed_port for allowed_host, allowed_port in entries)
    if not exact_match:
        networks = []
        for allowed_host, allowed_port in entries:
            if allowed_port != port or "/" not in allowed_host:
                continue
            try:
                networks.append(ipaddress.ip_network(allowed_host, strict=False))
            except ValueError as exc:
                raise TargetNotAllowedError("invalid_vllm_allowlist_cidr") from exc
        if not networks:
            raise TargetNotAllowedError("vllm_target_not_allowed")
        try:
            addresses = {
                ipaddress.ip_address(info[4][0])
                for info in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as exc:
            raise TargetNotAllowedError("vllm_target_dns_resolution_failed") from exc
        if not addresses or any(not any(address in network for network in networks) for address in addresses):
            raise TargetNotAllowedError("vllm_target_not_allowed")

    netloc = f"{hostname}:{port}"
    return urlunsplit((parsed.scheme, netloc, "/v1", "", ""))


def profile_fingerprint(profile: VLLMProfile) -> str:
    stable = {
        "base_url": profile.base_url,
        "model_name": profile.model_name,
        "api_key_ciphertext_hash": hashlib.sha256((profile.api_key_ciphertext or "").encode()).hexdigest(),
        "timeout_seconds": profile.timeout_seconds,
        "context_window": profile.context_window,
        "max_output_tokens": profile.max_output_tokens,
        "test_concurrency": profile.test_concurrency,
        "tls_verify": profile.tls_verify,
        "thinking_enabled": False,
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def to_profile_response(profile: VLLMProfile) -> VLLMProfileResponse:
    return VLLMProfileResponse(
        id=profile.id,
        name=profile.name,
        base_url=profile.base_url,
        model_name=profile.model_name,
        has_api_key=bool(profile.api_key_ciphertext),
        timeout_seconds=profile.timeout_seconds,
        context_window=profile.context_window,
        max_output_tokens=profile.max_output_tokens,
        test_concurrency=profile.test_concurrency,
        tls_verify=profile.tls_verify,
        thinking_enabled=False,
        status=profile.status,
        last_verified_at=profile.last_verified_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def to_test_response(test_run: VLLMTestRun) -> VLLMTestRunResponse:
    return VLLMTestRunResponse(
        id=test_run.id,
        profile_id=test_run.profile_id,
        mode=test_run.mode,
        status=test_run.status,
        profile_fingerprint=test_run.profile_fingerprint,
        checks=test_run.checks_json,
        metrics=test_run.metrics_json,
        error_code=test_run.error_code,
        error_message=test_run.error_message,
        started_at=test_run.started_at,
        completed_at=test_run.completed_at,
        created_at=test_run.created_at,
    )
