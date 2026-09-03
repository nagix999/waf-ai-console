import asyncio
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from ..models import ModelTestMode, VLLMProfile
from .crypto import CryptoService


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


def safe_http_error(exc: Exception) -> CheckFailed:
    if isinstance(exc, httpx.TimeoutException):
        return CheckFailed("vllm_timeout", "vLLM request timed out")
    if isinstance(exc, httpx.ConnectError):
        return CheckFailed("vllm_connection_failed", "Could not connect to the configured vLLM target")
    if isinstance(exc, httpx.HTTPStatusError):
        return CheckFailed(f"vllm_http_{exc.response.status_code}", "vLLM returned a non-success HTTP status")
    if isinstance(exc, (json.JSONDecodeError, ValueError, KeyError, TypeError)):
        return CheckFailed("vllm_invalid_response", "vLLM returned an invalid or unexpected response")
    if isinstance(exc, CheckFailed):
        return exc
    return CheckFailed("vllm_test_error", f"vLLM test failed with {type(exc).__name__}")


def chat_payload(profile: VLLMProfile, messages: list[dict[str, str]], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": profile.model_name,
        "messages": messages,
        "temperature": 0,
        "max_tokens": min(64, profile.max_output_tokens),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    payload.update(overrides)
    return payload


async def run_vllm_test(profile: VLLMProfile, crypto: CryptoService, mode: str) -> VLLMTestResult:
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {"mode": mode, "thinking_enabled": False}
    api_key = crypto.decrypt_text(profile.api_key_ciphertext) if profile.api_key_ciphertext else None
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
            started = time.perf_counter()
            response = await client.request(method, f"{profile.base_url}{path}", json=body)
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
                safe = safe_http_error(exc)
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
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise CheckFailed("empty_chat_response", "Chat completion returned empty content")
            return {"non_empty_content": True, "sample": content[:200]}, {"chat_latency_ms": latency}

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
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            valid = (
                parsed.get("status") == "ok"
                and isinstance(parsed.get("result"), dict)
                and isinstance(parsed["result"].get("code"), int)
                and isinstance(parsed["result"].get("message"), str)
            )
            if not valid:
                raise CheckFailed("json_schema_validation_failed", "Response did not match the required nested JSON schema")
            return {"nested_schema_valid": True, "parsed": parsed}, {"json_schema_latency_ms": latency}

        try:
            await check("models", models_check)
            await check("basic_chat", basic_chat_check)
            await check("nested_json_schema", json_schema_check)

            if mode == ModelTestMode.full.value:
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
                    content = body["choices"][0]["message"]["content"]
                    if "SYSTEM_ROLE_OK" not in content:
                        raise CheckFailed("system_role_not_applied", "System role marker was missing from the response")
                    return {"system_role_applied": True}, {"system_role_latency_ms": latency}

                async def near_context_check() -> tuple[dict[str, Any], dict[str, Any]]:
                    target_tokens = max(1024, profile.context_window - 2048)
                    synthetic = "x " * target_tokens
                    body, latency = await request_json(
                        "POST",
                        "/chat/completions",
                        chat_payload(
                            profile,
                            [{"role": "user", "content": f"Synthetic context follows.\n{synthetic}\nReply OK."}],
                            max_tokens=8,
                        ),
                    )
                    content = body["choices"][0]["message"]["content"]
                    if not isinstance(content, str) or not content.strip():
                        raise CheckFailed("near_context_empty_response", "Near-context request returned empty content")
                    prompt_tokens = body.get("usage", {}).get("prompt_tokens")
                    if not isinstance(prompt_tokens, int):
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
                        _body, latency = await request_json(
                            "POST",
                            "/chat/completions",
                            chat_payload(profile, [{"role": "user", "content": f"Concurrency test {index}. Reply OK."}], max_tokens=8),
                        )
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
                await check("near_32k_context", near_context_check)
                await check("concurrency", concurrency_check)
        except CheckFailed as exc:
            return VLLMTestResult(False, checks, metrics, exc.code, exc.safe_message)

    metrics["total_latency_ms"] = round(sum(item["latency_ms"] for item in checks), 2)
    return VLLMTestResult(True, checks, metrics)
