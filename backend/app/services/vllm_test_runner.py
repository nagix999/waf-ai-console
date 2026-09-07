import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from ..models import ModelTestMode, VLLMProfile
from ..agent.contracts import WAFAnalysisOutput
from .crypto import CryptoService
from .internal_egress import InternalEgressError
from .vllm_profiles import TargetNotAllowedError
from .provider_options import (
    CompletionRejected,
    completion_content,
    generation_options,
    provider_name,
    strict_json_schema,
)


@dataclass
class VLLMTestResult:
    passed: bool
    checks: list[dict[str, Any]]
    metrics: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None


class CheckFailed(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return round(ordered[index], 2)


def safe_http_error(exc: Exception, provider: str = "vllm") -> CheckFailed:
    label = "OpenAI" if provider == "openai" else "vLLM"
    if isinstance(exc, CheckFailed):
        return exc
    if isinstance(exc, (TargetNotAllowedError, InternalEgressError)):
        code = str(exc)
        if code not in {"vllm_target_not_allowed", "vllm_profile_unavailable", "internal_egress_configuration_invalid"}:
            code = "vllm_target_not_allowed"
        return CheckFailed(code, "vLLM request blocked by the current Internal Egress policy")
    if isinstance(exc, CompletionRejected):
        return CheckFailed(exc.code, f"{label} did not return a completed non-refused response")
    if isinstance(exc, httpx.TimeoutException):
        return CheckFailed(f"{provider}_timeout", f"{label} request timed out")
    if isinstance(exc, httpx.ConnectError):
        return CheckFailed(f"{provider}_connection_failed", f"Could not connect to the configured {label} target")
    if isinstance(exc, httpx.HTTPStatusError):
        return CheckFailed(f"{provider}_http_{exc.response.status_code}", f"{label} returned a non-success HTTP status")
    if isinstance(exc, (json.JSONDecodeError, ValueError, KeyError, TypeError)):
        return CheckFailed(f"{provider}_invalid_response", f"{label} returned an invalid or unexpected response")
    return CheckFailed(f"{provider}_test_error", f"{label} test failed")


def chat_payload(
    profile: VLLMProfile,
    messages: list[dict[str, str]],
    *,
    max_output_tokens: int | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    # Reasoning models may spend the completion allowance before visible text.
    # OpenAI checks use the configured allowance rather than an artificial 8/64.
    if max_output_tokens is None:
        max_output_tokens = profile.max_output_tokens if provider_name(profile) == "openai" else min(64, profile.max_output_tokens)
    payload: dict[str, Any] = {
        "model": profile.model_name,
        "messages": messages,
        **generation_options(profile, max_output_tokens=max_output_tokens),
    }
    payload.update(overrides)
    return payload


async def run_vllm_test(
    profile: VLLMProfile, crypto: CryptoService, mode: str,
    *, egress_check: Callable[[], None] | None = None,
) -> VLLMTestResult:
    checks: list[dict[str, Any]] = []
    provider = provider_name(profile)
    metrics: dict[str, Any] = {"mode": mode, "provider": provider, "thinking_enabled": None if provider == "openai" else False}
    api_key = crypto.decrypt_text(profile.api_key_ciphertext) if profile.api_key_ciphertext else None
    base_url = profile.base_url
    if provider == "vllm":
        if egress_check is None:
            raise ValueError("vllm_egress_check_required")
    if egress_check is not None:
        egress_check()
    if provider == "openai":
        from .vllm_profiles import (
            TargetNotAllowedError,
            normalize_and_validate_profile_url,
            validate_profile_provider_settings,
        )

        try:
            validate_profile_provider_settings(profile, has_api_key=bool(api_key))
            base_url = normalize_and_validate_profile_url(profile, "")
            if not isinstance(api_key, str) or not api_key or any(character.isspace() for character in api_key):
                raise TargetNotAllowedError("openai_api_key_required")
        except TargetNotAllowedError as exc:
            return VLLMTestResult(False, checks, metrics, str(exc), "OpenAI provider settings are not approved or valid")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    timeout = httpx.Timeout(profile.timeout_seconds)

    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        verify=profile.tls_verify,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        async def request_json(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[dict[str, Any], float]:
            if egress_check is not None:
                egress_check()
            started = time.perf_counter()
            response = await client.request(method, f"{base_url}{path}", json=body)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            response.raise_for_status()
            return response.json(), elapsed_ms

        async def check(
            name: str,
            operation: Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]],
        ) -> bool:
            started = time.perf_counter()
            try:
                detail, extra_metrics = await operation()
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                checks.append({"name": name, "status": "passed", "latency_ms": latency_ms, "detail": detail})
                metrics.update(extra_metrics)
                return True
            except Exception as exc:
                safe = safe_http_error(exc, provider)
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                checks.append(
                    {
                        "name": name,
                        "status": "failed",
                        "latency_ms": latency_ms,
                        "error_code": safe.code,
                        "message": safe.safe_message,
                    }
                )
                metrics["failed_check"] = name
                metrics["total_latency_ms"] = round(sum(item["latency_ms"] for item in checks), 2)
                raise safe

        async def models_check() -> tuple[dict[str, Any], dict[str, Any]]:
            body, latency = await request_json("GET", "/models")
            model_ids = [item.get("id") for item in body.get("data", []) if isinstance(item, dict)]
            if profile.model_name not in model_ids:
                raise CheckFailed("configured_model_not_served", "Configured model was not returned by /v1/models")
            return {"configured_model_found": True, "served_model_count": len(model_ids)}, {"models_latency_ms": latency}

        async def basic_chat_check() -> tuple[dict[str, Any], dict[str, Any]]:
            body, latency = await request_json(
                "POST",
                "/chat/completions",
                chat_payload(profile, [{"role": "user", "content": "Reply with WAF_TEST_OK."}]),
            )
            completion_content(body, provider)
            return {"non_empty_content": True}, {"chat_latency_ms": latency}

        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["ok"]},
                "result": {
                    "type": "object",
                    "properties": {"code": {"type": "integer"}, "message": {"type": "string"}},
                    "required": ["code", "message"],
                    "additionalProperties": False,
                },
            },
            "required": ["status", "result"],
            "additionalProperties": False,
        }

        async def json_schema_check() -> tuple[dict[str, Any], dict[str, Any]]:
            body, latency = await request_json(
                "POST",
                "/chat/completions",
                chat_payload(
                    profile,
                    [{"role": "user", "content": "Return status ok and result code 200 with a short message."}],
                    response_format={"type": "json_schema", "json_schema": {"name": "waf_test", "strict": True, "schema": schema}},
                ),
            )
            content = completion_content(body, provider)
            parsed = json.loads(content)
            valid = (
                isinstance(parsed, dict)
                and set(parsed) == {"status", "result"}
                and parsed.get("status") == "ok"
                and isinstance(parsed.get("result"), dict)
                and set(parsed["result"]) == {"code", "message"}
                and type(parsed["result"].get("code")) is int
                and isinstance(parsed["result"].get("message"), str)
            )
            if not valid:
                raise CheckFailed("json_schema_validation_failed", "Response did not match the required nested JSON schema")
            return {"nested_schema_valid": True}, {"json_schema_latency_ms": latency}

        try:
            await check("models", models_check)
            await check("basic_chat", basic_chat_check)
            await check("nested_json_schema", json_schema_check)

            if mode == ModelTestMode.full.value:
                if provider == "openai":
                    async def waf_schema_check() -> tuple[dict[str, Any], dict[str, Any]]:
                        body, latency = await request_json(
                            "POST",
                            "/chat/completions",
                            chat_payload(
                                profile,
                                [{"role": "user", "content": (
                                    "This is a synthetic WAF contract test, not a real security event. "
                                    "No payload or signature is provided. Return verdict inconclusive, "
                                    "severity UNKNOWN, empty evidence and conflicting_evidence, "
                                    "input_truncated false, tuning recommended false with nullable details null. "
                                    "Explain missing information briefly in Korean and fill every schema field."
                                )}],
                                response_format={"type": "json_schema", "json_schema": {
                                    "name": "WAFAnalysisOutput", "strict": True,
                                    "schema": strict_json_schema(WAFAnalysisOutput.model_json_schema()),
                                }},
                            ),
                        )
                        try:
                            output = WAFAnalysisOutput.model_validate_json(completion_content(body, provider))
                        except CompletionRejected:
                            raise
                        except ValueError:
                            raise CheckFailed("waf_schema_validation_failed", "Response did not match the WAF analysis contract") from None
                        if output.verdict.value != "inconclusive":
                            raise CheckFailed("waf_schema_validation_failed", "Synthetic missing-input test did not return inconclusive")
                        return {"waf_contract_valid": True}, {"waf_schema_latency_ms": latency}

                    await check("waf_analysis_schema", waf_schema_check)

                async def system_role_check() -> tuple[dict[str, Any], dict[str, Any]]:
                    body, latency = await request_json(
                        "POST",
                        "/chat/completions",
                        chat_payload(
                            profile,
                            [
                                {"role": "system", "content": "Always include the marker SYSTEM_ROLE_OK."},
                                {"role": "user", "content": "Confirm this test in one line."},
                            ],
                        ),
                    )
                    content = completion_content(body, provider)
                    if "SYSTEM_ROLE_OK" not in content:
                        raise CheckFailed("system_role_not_applied", "System role marker was missing from the response")
                    return {"system_role_applied": True}, {"system_role_latency_ms": latency}

                async def near_context_check() -> tuple[dict[str, Any], dict[str, Any]]:
                    target_tokens = (
                        max(1, profile.context_window - profile.max_output_tokens - 512)
                        if provider == "openai" else max(1024, profile.context_window - 2048)
                    )
                    synthetic = "x " * target_tokens
                    body, latency = await request_json(
                        "POST",
                        "/chat/completions",
                        chat_payload(
                            profile,
                            [{"role": "user", "content": f"Synthetic context follows.\n{synthetic}\nReply OK."}],
                            max_output_tokens=profile.max_output_tokens if provider == "openai" else 8,
                        ),
                    )
                    completion_content(body, provider)
                    prompt_tokens = body.get("usage", {}).get("prompt_tokens")
                    if type(prompt_tokens) is not int or prompt_tokens < 0:
                        raise CheckFailed("usage_missing", "Near-context response did not include prompt token usage")
                    utilization = round(prompt_tokens / profile.context_window, 4)
                    if utilization < 0.8:
                        raise CheckFailed("near_context_below_target", "Synthetic request used less than 80% of the configured context")
                    return {
                        "prompt_tokens": prompt_tokens,
                        "configured_context": profile.context_window,
                        "utilization": utilization,
                    }, {"near_context_latency_ms": latency, "near_context_prompt_tokens": prompt_tokens}

                async def concurrency_check() -> tuple[dict[str, Any], dict[str, Any]]:
                    async def one(index: int) -> float:
                        body, latency = await request_json(
                            "POST",
                            "/chat/completions",
                            chat_payload(profile, [{"role": "user", "content": f"Concurrency test {index}. Reply OK."}], max_output_tokens=profile.max_output_tokens if provider == "openai" else 8),
                        )
                        completion_content(body, provider)
                        return latency

                    durations = await asyncio.gather(*(one(index) for index in range(profile.test_concurrency)))
                    return {
                        "requests": len(durations),
                        "all_succeeded": True,
                        "p95_ms": percentile(durations, 0.95),
                    }, {
                        "concurrency": profile.test_concurrency,
                        "concurrency_p50_ms": percentile(durations, 0.5),
                        "concurrency_p95_ms": percentile(durations, 0.95),
                    }

                await check("system_role", system_role_check)
                await check("near_configured_context" if provider == "openai" else "near_32k_context", near_context_check)
                await check("concurrency", concurrency_check)
        except CheckFailed as exc:
            return VLLMTestResult(False, checks, metrics, exc.code, exc.safe_message)

    metrics["total_latency_ms"] = round(sum(item["latency_ms"] for item in checks), 2)
    return VLLMTestResult(True, checks, metrics)
