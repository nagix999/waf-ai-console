import hashlib
import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy import select

from ..models import VLLMProfile, VLLMTestRun
from ..schemas import VLLMProfileResponse, VLLMTestRunResponse


class TargetNotAllowedError(ValueError):
    pass


OPENAI_BASE_URL = "https://api.openai.com/v1"


def _provider(profile: VLLMProfile) -> str:
    # Unflushed legacy ORM instances have not received their SQLAlchemy default.
    value = getattr(profile, "provider", None)
    return "vllm" if value is None else value


def validate_profile_provider_settings(profile: VLLMProfile, *, has_api_key: bool) -> None:
    provider = _provider(profile)
    if provider not in {"vllm", "openai"}:
        raise TargetNotAllowedError("model_provider_not_supported")
    approved = getattr(profile, "external_data_approved", False)
    if provider == "vllm":
        if approved:
            raise TargetNotAllowedError("external_data_approval_not_applicable")
        return
    if not isinstance(profile.model_name, str) or not profile.model_name.strip():
        raise TargetNotAllowedError("openai_model_name_required")
    if approved is not True:
        raise TargetNotAllowedError("openai_external_data_approval_required")
    if profile.tls_verify is not True:
        raise TargetNotAllowedError("openai_tls_verification_required")
    if not has_api_key:
        raise TargetNotAllowedError("openai_api_key_required")


def normalize_and_validate_profile_url(profile: VLLMProfile, allowed_targets: str) -> str:
    provider = _provider(profile)
    if provider == "vllm":
        try:
            return normalize_and_validate_base_url(profile.base_url, allowed_targets)
        except TargetNotAllowedError:
            raise
        except ValueError:
            raise TargetNotAllowedError("invalid_vllm_base_url") from None
    if provider != "openai":
        raise TargetNotAllowedError("model_provider_not_supported")
    value = profile.base_url
    # Check the source string too: urlsplit removes some control characters and
    # cannot distinguish an absent query/fragment from an empty '?' or '#'.
    if not isinstance(value, str) or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise TargetNotAllowedError("openai_base_url_not_allowed")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.netloc.lower() in {"api.openai.com", "api.openai.com:443"}
            and parsed.hostname == "api.openai.com"
            and parsed.port in {None, 443}
            and parsed.username is None and parsed.password is None
            and parsed.path in {"/v1", "/v1/"}
            and "?" not in value and "#" not in value
        )
    except ValueError:
        valid = False
    if not valid:
        raise TargetNotAllowedError("openai_base_url_not_allowed")
    return OPENAI_BASE_URL


def _split_allowed_target(value: str) -> tuple[str, int]:
    host, separator, port_text = value.strip().rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise TargetNotAllowedError("invalid_vllm_allowlist_entry")
    try:
        address = ipaddress.ip_address(host.strip().strip("[]"))
    except ValueError:
        raise TargetNotAllowedError("invalid_vllm_allowlist_entry") from None
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise TargetNotAllowedError("invalid_vllm_allowlist_entry")
    return str(address), port


def normalize_and_validate_base_url(base_url: str, allowed_targets: str) -> str:
    # Do not let URL parser normalization hide whitespace, controls, userinfo,
    # an empty query/fragment, or non-canonical numeric hostname spellings.
    if not isinstance(base_url, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in base_url):
        raise TargetNotAllowedError("invalid_vllm_base_url")
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        raise TargetNotAllowedError("invalid_vllm_base_url") from None
    if parsed.scheme not in {"http", "https"}:
        raise TargetNotAllowedError("vllm_url_scheme_must_be_http_or_https")
    if not parsed.hostname or "@" in parsed.netloc or "?" in base_url or "#" in base_url or "%" in parsed.netloc or "\\" in base_url:
        raise TargetNotAllowedError("invalid_vllm_base_url")
    if parsed.path not in {"", "/", "/v1", "/v1/"}:
        raise TargetNotAllowedError("vllm_base_url_path_must_be_v1")
    try:
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise TargetNotAllowedError("invalid_vllm_base_url_port") from exc
    if not 1 <= port <= 65535 or parsed.netloc.endswith(":"):
        raise TargetNotAllowedError("invalid_vllm_base_url_port")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        raise TargetNotAllowedError("vllm_base_url_must_use_ip_address") from None
    from .internal_egress import normalize_internal_ip
    try:
        hostname = normalize_internal_ip(str(address))
    except ValueError:
        raise TargetNotAllowedError("vllm_target_not_allowed") from None
    entries = [_split_allowed_target(item) for item in allowed_targets.split(",") if item.strip()]
    if (hostname, port) not in entries:
        raise TargetNotAllowedError("vllm_target_not_allowed")

    netloc = f"[{hostname}]:{port}" if address.version == 6 else f"{hostname}:{port}"
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
    # Keep the vLLM hash byte-for-byte compatible with already passed full tests.
    # OpenAI approvals/provider must never share a vLLM verification identity.
    if _provider(profile) != "vllm":
        stable.update({
            "provider": _provider(profile),
            "external_data_approved": getattr(profile, "external_data_approved", False),
            "thinking_enabled": None,
        })
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assignment_block_reason(db, profile: VLLMProfile) -> str | None:
    """Roles never participate in the immutable settings fingerprint."""
    if profile.status not in {"verified", "production"}:
        return "profile_not_verified"
    if db is None or db.scalar(select(VLLMTestRun.id).where(
        VLLMTestRun.profile_id == profile.id, VLLMTestRun.mode == "full",
        VLLMTestRun.status == "passed", VLLMTestRun.profile_fingerprint == profile_fingerprint(profile),
    ).limit(1)) is None:
        return "matching_full_test_required"
    return None


def to_profile_response(profile: VLLMProfile, db=None) -> VLLMProfileResponse:
    reason = assignment_block_reason(db, profile)
    return VLLMProfileResponse(
        id=profile.id,
        profile_fingerprint=profile_fingerprint(profile),
        name=profile.name,
        provider=_provider(profile),
        external_data_approved=bool(getattr(profile, "external_data_approved", False)),
        base_url=profile.base_url,
        model_name=profile.model_name,
        has_api_key=bool(profile.api_key_ciphertext),
        timeout_seconds=profile.timeout_seconds,
        context_window=profile.context_window,
        max_output_tokens=profile.max_output_tokens,
        test_concurrency=profile.test_concurrency,
        tls_verify=profile.tls_verify,
        thinking_enabled=None if _provider(profile) == "openai" else False,
        status=profile.status,
        is_test=bool(profile.is_test), can_assign=reason is None, assignment_block_reason=reason,
        last_verified_at=profile.last_verified_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def to_test_response(test_run: VLLMTestRun, db=None) -> VLLMTestRunResponse:
    dataset = None
    named_id = None
    if db is not None:
        from ..models import TestRun
        from sqlalchemy import select
        named_id = db.scalar(select(TestRun.id).where(TestRun.model_test_run_id == test_run.id))
    if test_run.include_dataset and db is not None:
        from .model_validation import dataset_evaluation
        dataset = dataset_evaluation(db, test_run)
    return VLLMTestRunResponse(
        id=test_run.id,
        profile_id=test_run.profile_id,
        mode=test_run.mode,
        status=test_run.status,
        profile_fingerprint=test_run.profile_fingerprint,
        include_dataset=bool(test_run.include_dataset),
        dataset_evaluation=dataset,
        name=test_run.name, test_run_id=named_id,
        checks=test_run.checks_json,
        metrics=test_run.metrics_json,
        error_code=test_run.error_code,
        error_message=test_run.error_message,
        started_at=test_run.started_at,
        completed_at=test_run.completed_at,
        created_at=test_run.created_at,
    )
