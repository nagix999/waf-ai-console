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
from .validation_output_diagnostics import inspect_json_output, inspection_limit_exceeded, probe_json_decoder
from .vllm_profiles import TargetNotAllowedError
from .provider_options import (
    CompletionRejected,
    completion_content,
    generation_options,
    provider_name,
    strict_json_schema,
)


# Short technical probes need enough room for model response formatting.
# This cap does not change the profile or the WAF Agent's output allowance.
VLLM_PROBE_OUTPUT_TOKENS = 1024
_FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls", "function_call", "abort", "error"}


def _token_count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _response_diagnostics(body: Any) -> dict[str, Any]:
    """Allowlisted metadata only: never retain content, refusal text or errors."""
    body = body if isinstance(body, dict) else {}
    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    choices = body.get("choices")
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) else {}
    reason = choice.get("finish_reason")
    message = choice.get("message")
    return {
        "finish_reason": None if reason is None else reason if isinstance(reason, str) and reason in _FINISH_REASONS else "unknown",
        "refused": message.get("refusal") not in (None, "") if isinstance(message, dict) else None,
        **{name: _token_count(usage.get(name)) for name in ("prompt_tokens", "completion_tokens", "total_tokens")},
    }


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
        messages = {
            f"{provider}_output_incomplete": "출력이 토큰 한도에 도달해 중단되었습니다. 검증 항목 상세의 출력 한도·사용량과 응답 상태를 확인하세요. JSON 형식 오류와는 다른 사유입니다.",
            f"{provider}_refusal": "모델이 응답을 거절했습니다. 출력 길이 부족과는 다른 사유입니다. 서버의 거절 처리 설정을 확인하세요.",
            f"{provider}_completion_not_finished": "모델이 정상 종료 상태로 응답하지 않았습니다. 검증 항목 상세의 종료 사유를 확인하세요.",
            f"{provider}_invalid_response": "모델 응답이 비어 있거나 응답 형식이 올바르지 않습니다. 서버의 응답 형식 설정을 확인하세요.",
        }
        return CheckFailed(exc.code, messages.get(exc.code, f"{label} 응답을 검증하지 못했습니다."))
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
    # OpenAI checks continue to use the configured allowance.
    if max_output_tokens is None:
        max_output_tokens = profile.max_output_tokens if provider_name(profile) == "openai" else min(VLLM_PROBE_OUTPUT_TOKENS, profile.max_output_tokens)
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
    concurrency_engine=None,
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
        request_records: list[dict[str, Any]] = []

        async def request_json(
            method: str, path: str, body: dict[str, Any] | None = None, *, request_index: int = 1,
        ) -> tuple[dict[str, Any], float]:
            from .concurrency import call_slot
            record = None
            if method == "POST" and path == "/chat/completions":
                record = {
                    "request_index": request_index,
                    "requested_max_output_tokens": _token_count((body or {}).get("max_completion_tokens" if provider == "openai" else "max_tokens")),
                    "http_status": None,
                    "error_code": None,
                    **_response_diagnostics(None),
                }
                if (body or {}).get("response_format", {}).get("type") == "json_schema":
                    record["json_output"] = inspect_json_output(None)
                request_records.append(record)
            started = time.perf_counter()
            try:
                async with call_slot(concurrency_engine, profile, egress_check,
                                     timeout_seconds=profile.timeout_seconds + 5):
                    response = await client.request(method, f"{base_url}{path}", json=body)
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                if record is not None:
                    record["http_status"] = response.status_code
                response.raise_for_status()
                result = response.json()
                if record is not None:
                    record.update(_response_diagnostics(result))
                    if "json_output" in record:
                        record["json_output"] = inspect_json_output(result)
                    # Record completion failures on the individual request,
                    # including when other concurrent probes succeed or fail.
                    completion_content(result, provider)
                return result, elapsed_ms
            except Exception as exc:
                if record is not None:
                    record["error_code"] = safe_http_error(exc, provider).code
                raise

        async def check(
            name: str,
            operation: Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]],
        ) -> bool:
            request_records.clear()
            started = time.perf_counter()
            try:
                detail, extra_metrics = await operation()
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                checks.append({"name": name, "status": "passed", "latency_ms": latency_ms, "detail": detail,
                               "response_diagnostics": list(request_records)})
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
                        "response_diagnostics": list(request_records),
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
                    "properties": {"code": {"type": "integer", "enum": [200]}, "message": {"type": "string", "enum": ["OK"]}},
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
                    [{"role": "user", "content": 'Return exactly {"status":"ok","result":{"code":200,"message":"OK"}}. No explanation.'}],
                    response_format={"type": "json_schema", "json_schema": {"name": "waf_test", "strict": True, "schema": schema}},
                ),
            )
            content = completion_content(body, provider)
            if inspection_limit_exceeded(content):
                raise CheckFailed("json_schema_validation_failed", "검증용 JSON 출력이 너무 길거나 중첩이 깊습니다.")
            try:
                parsed = probe_json_decoder().decode(content)
            except (ValueError, RecursionError):
                raise CheckFailed("json_schema_validation_failed", "응답이 지정한 JSON 형식과 다릅니다.") from None
            valid = (
                isinstance(parsed, dict)
                and set(parsed) == {"status", "result"}
                and parsed.get("status") == "ok"
                and isinstance(parsed.get("result"), dict)
                and set(parsed["result"]) == {"code", "message"}
                and type(parsed["result"].get("code")) is int
                and parsed["result"]["code"] == 200
                and parsed["result"].get("message") == "OK"
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
                        ),
                    )
                    completion_content(body, provider)
                    prompt_tokens = _response_diagnostics(body)["prompt_tokens"]
                    if prompt_tokens is None:
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
                    from .concurrency import configured_server_limit
                    if concurrency_engine is not None and configured_server_limit(concurrency_engine, profile) < profile.test_concurrency:
                        raise CheckFailed("concurrency_limit_below_test", "Agent server limit is below the requested verification concurrency; adjust the limit or test setting explicitly")
                    async def one(index: int) -> float:
                        body, latency = await request_json(
                            "POST",
                            "/chat/completions",
                            chat_payload(profile, [{"role": "user", "content": f"Concurrency test {index}. Reply OK."}]),
                            request_index=index + 1,
                        )
                        completion_content(body, provider)
                        return latency

                    # Settle every submitted probe before saving diagnostics and
                    # closing the client. A failed probe still fails the check;
                    # it is never retried or replaced with another response.
                    durations = await asyncio.gather(*(one(index) for index in range(profile.test_concurrency)), return_exceptions=True)
                    for duration in durations:
                        if isinstance(duration, BaseException):
                            raise duration
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
