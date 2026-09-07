from contextlib import AsyncExitStack
from dataclasses import dataclass, is_dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable, Mapping

import httpx
from pydantic import BaseModel

from ..models import VLLMProfile
from ..services.provider_options import (
    CompletionRejected,
    completion_content,
    generation_options,
    provider_name,
    safe_openai_usage,
    strict_json_schema,
)
from .contracts import WAFAnalysisOutput


OUTPUT_VALIDATION_FAILURE_CODE = "output_validation_failed"
OUTPUT_VALIDATION_MAX_ATTEMPTS = 2
OUTPUT_VALIDATION_REPAIR_INSTRUCTIONS = """
이전 실행은 output_validation_failed로 종료되었다. 원본 이벤트를 처음부터 다시 분석하고,
다음 교정 규칙을 모두 지켜 완전한 구조화 결과를 새로 생성하라.

- 출력 스키마에 정의된 필수 필드를 하나도 빠뜨리지 말고 정의되지 않은 필드는 추가하지 않는다.
- verdict, signature relation, severity와 tuning scope는 스키마에 정의된 enum 값만 사용한다.
- threat_analysis, signature_assessment, tuning_recommendation의 중첩 객체 구조를 정확히 지킨다.
- true_positive 또는 false_positive에는 원문에 실제 존재하는 발췌 근거를 최소 1개 포함한다.
- evidence의 excerpt는 입력 원문을 그대로 인용하고, interpretation_ko는 관찰 사실과 판정의 연결을 설명한다.
- 숫자 범위, 문자열 길이와 배열 최대 개수를 지킨다.
- JSON 구조화 결과 외의 서문, 설명, Markdown 코드 블록을 출력하지 않는다.

이전의 잘못된 출력을 추측하거나 언급하지 말고 전체 결과를 다시 작성하라.
""".strip()


class _TrackingOutputCodec:
    def __init__(self, delegate: Any, *, strict: bool = False) -> None:
        self.delegate = delegate
        self.strict = strict
        self.validation_issues: list[dict[str, str]] = []

    def schema(self) -> Mapping[str, Any] | None:
        schema = self.delegate.schema()
        return strict_json_schema(schema) if self.strict and schema is not None else schema

    def decode(self, response: Any) -> WAFAnalysisOutput:
        try:
            return self.delegate.decode(response)
        except Exception as exc:
            self.validation_issues = _safe_validation_issues(exc)
            raise


class _OpenAICompletionTransport:
    """Guard the public ModuAgent HTTP transport before its permissive decoder."""

    def __init__(self, delegate: Any, before_request: Callable[[], None] | None = None) -> None:
        self.delegate = delegate
        self.before_request = before_request
        self.failure_code: str | None = None

    async def post_json(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        from moduagent import ModelProtocolError

        self.failure_code = None
        if self.before_request is not None:
            self.before_request()
        try:
            value = await self.delegate.post_json(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            self.failure_code = f"openai_http_{exc.response.status_code}"
            raise
        except httpx.TimeoutException:
            self.failure_code = "openai_timeout"
            raise
        except httpx.ConnectError:
            self.failure_code = "openai_connection_failed"
            raise
        except ModelProtocolError:
            self.failure_code = "openai_invalid_response"
            raise
        try:
            completion_content(value, "openai")
            usage = safe_openai_usage(value.get("usage"))
        except CompletionRejected as exc:
            self.failure_code = exc.code
            raise ModelProtocolError(exc.code) from None
        # Framework telemetry needs usage, not arbitrary provider metadata/text.
        return {"choices": value["choices"], "usage": usage}


class _VLLMEgressTransport:
    """Recheck the shared allowlist for every call, including SDK retries."""

    def __init__(self, delegate: Any, base_url: str, check: Callable[[], None]) -> None:
        self.delegate = delegate
        self.base_url = base_url
        self.check = check

    async def post_json(self, url: str, **kwargs: Any) -> Mapping[str, Any]:
        from ..services.vllm_profiles import TargetNotAllowedError

        if url != f"{self.base_url}/chat/completions":
            raise TargetNotAllowedError("vllm_target_not_allowed")
        self.check()
        return await self.delegate.post_json(url, **kwargs)


@dataclass(frozen=True)
class AgentCallResult:
    output: WAFAnalysisOutput | None
    framework_run_id: str | None
    agent_fingerprint: str | None
    finish_reason: str
    failure_id: str | None
    error: str | None
    telemetry: dict[str, Any]

    @property
    def succeeded(self) -> bool:
        return self.finish_reason == "completed" and self.output is not None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    return str(value)


def _usage_jsonable(value: Any) -> dict[str, int | None] | None:
    """Persist only numeric counters, never Usage's repr or provider contents.

    ModuAgent 0.6.2 returns a slots dataclass and adds zero-filled Usage values
    even when a provider omitted usage. An all-zero aggregate is therefore
    unknown, not evidence of a free request. This adapter does not reconstruct
    missing counts or rewrite earlier telemetry. A partial provider response
    may already have defaulted/derived counters in the framework aggregate;
    those cannot be recovered here and are not authoritative billing data.
    """
    if isinstance(value, Mapping):
        read = value.get
    elif is_dataclass(value) and not isinstance(value, type):
        read = lambda key: getattr(value, key, None)
    else:
        return None
    counters: dict[str, int | None] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        count = read(key)
        counters[key] = count if type(count) is int and count >= 0 else None
    return counters if any(count is not None and count > 0 for count in counters.values()) else None


def framework_version() -> str:
    try:
        return version("moduagent")
    except PackageNotFoundError:
        return "not-installed"


def _error_code(error_summary: Any) -> str | None:
    value = _jsonable(error_summary)
    if not isinstance(value, Mapping):
        return None
    code = value.get("code")
    return str(code) if code is not None else None


def _safe_validation_issues(exc: Exception) -> list[dict[str, str]]:
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return [{"field": "$", "type": "decode_error"}]
    try:
        values = errors(include_url=False, include_context=False, include_input=False)
    except TypeError:  # pragma: no cover - Pydantic v1 compatibility
        values = errors()
    issues: list[dict[str, str]] = []
    allowed_fields = _output_schema_field_names()
    for value in values[:12]:
        if not isinstance(value, Mapping):
            continue
        location = value.get("loc", ())
        if isinstance(location, (list, tuple)):
            parts = [
                "[]"
                if isinstance(item, int)
                else item
                if isinstance(item, str) and item in allowed_fields
                else "<unknown>"
                for item in location
            ]
            field = ".".join(parts)
        else:
            field = location if isinstance(location, str) and location in allowed_fields else "<unknown>"
        error_type = str(value.get("type") or "validation_error")
        if not error_type or not all(
            character in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for character in error_type
        ):
            error_type = "validation_error"
        issues.append(
            {
                "field": (field or "$")[:256],
                "type": error_type[:128],
            }
        )
    return issues or [{"field": "$", "type": "validation_error"}]


def _output_schema_field_names() -> frozenset[str]:
    schema = WAFAnalysisOutput.model_json_schema()
    fields: set[str] = set()
    pending: list[Any] = [schema]
    seen: set[int] = set()
    while pending and len(seen) < 512:
        value = pending.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                fields.update(str(name) for name in properties if isinstance(name, str))
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return frozenset(fields)


def _repair_instructions(validation_issues: list[dict[str, str]]) -> str:
    if not validation_issues:
        return OUTPUT_VALIDATION_REPAIR_INSTRUCTIONS
    issue_lines = "\n".join(
        f"- {issue['field']}: {issue['type']}" for issue in validation_issues
    )
    return (
        f"{OUTPUT_VALIDATION_REPAIR_INSTRUCTIONS}\n\n"
        "검증기가 식별한 실패 위치와 유형은 다음과 같다. 이전 출력값 자체는 보안상 제공하지 않는다.\n"
        f"{issue_lines}"
    )


def _attempt_telemetry(
    result: Any,
    attempt: int,
    validation_issues: list[dict[str, str]],
    agent_fingerprint: str | None,
) -> dict[str, Any]:
    finish_reason = getattr(result.finish_reason, "value", result.finish_reason)
    return {
        "attempt": attempt,
        "framework_run_id": str(result.run_id) if result.run_id is not None else None,
        "agent_fingerprint": agent_fingerprint,
        "finish_reason": str(finish_reason),
        "failure_id": str(result.failure_id) if result.failure_id is not None else None,
        "error_code": _error_code(result.error_summary),
        "validation_issues": validation_issues,
        "usage": _usage_jsonable(result.usage),
        "run_usage": _jsonable(result.run_usage),
    }


async def execute_structured_agent(
    *,
    profile: VLLMProfile,
    api_key: str | None,
    instructions: str,
    user_input: str,
    session_id: str,
    agent_name: str,
    egress_check: Callable[[], None] | None = None,
) -> AgentCallResult:
    provider = provider_name(profile)
    base_url = profile.base_url
    if provider == "vllm":
        if egress_check is None:
            raise ValueError("vllm_egress_check_required")
        egress_check()
    if provider == "openai":
        if egress_check is not None:
            egress_check()
        from ..services.vllm_profiles import (
            normalize_and_validate_profile_url,
            validate_profile_provider_settings,
        )

        validate_profile_provider_settings(profile, has_api_key=bool(api_key))
        base_url = normalize_and_validate_profile_url(profile, "")
        if not isinstance(api_key, str) or not api_key or any(character.isspace() for character in api_key):
            raise ValueError("openai_api_key_required")
    # ModuAgent 0.6.2's public VLLMClient constructor does not document a TLS
    # verification override. Never silently weaken or ignore an HTTPS profile.
    if profile.base_url.startswith("https://") and not profile.tls_verify:
        raise ValueError("moduagent_https_requires_tls_verification")

    # Import lazily so stub mode and pure policy tests do not require ModuAgent.
    from moduagent import Agent, HttpxTransport, OpenAICompatibleClient, RetryConfig, RunLimits, VLLMClient
    from moduagent.output import PydanticOutputCodec

    async with AsyncExitStack() as stack:
        guarded_transport = None
        client_kwargs = {
            "base_url": base_url,
            "model": profile.model_name,
            "api_key": api_key,
            "timeout": profile.timeout_seconds,
            "default_options": generation_options(profile),
        }
        client_type = VLLMClient
        if provider == "openai":
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(verify=True, follow_redirects=False, trust_env=False)
            )
            guarded_transport = _OpenAICompletionTransport(HttpxTransport(client=http_client), egress_check)
            client_kwargs["transport"] = guarded_transport
            client_type = OpenAICompatibleClient
        else:
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(verify=True, follow_redirects=False, trust_env=False)
            )
            client_kwargs["transport"] = _VLLMEgressTransport(
                HttpxTransport(client=http_client), base_url, egress_check,
            )
        model = await stack.enter_async_context(client_type(**client_kwargs))
        attempts: list[dict[str, Any]] = []
        result = None
        spec = None
        previous_validation_issues: list[dict[str, str]] = []
        for attempt in range(1, OUTPUT_VALIDATION_MAX_ATTEMPTS + 1):
            repairing_output = attempt > 1
            run_instructions = (
                f"{instructions}\n\n{_repair_instructions(previous_validation_issues)}"
                if repairing_output
                else instructions
            )
            output_codec = _TrackingOutputCodec(PydanticOutputCodec(WAFAnalysisOutput), strict=provider == "openai")
            agent = Agent.create(
                name=f"{agent_name}-output-repair" if repairing_output else agent_name,
                model=model,
                instructions=run_instructions,
                output=output_codec,
                retry=RetryConfig(max_attempts=2),
                limits=RunLimits(
                    max_model_turns=2,
                    no_progress_model_turn_threshold=2,
                    timeout_seconds=profile.timeout_seconds * 2,
                ),
            )
            spec = agent.inspect()
            result = await agent.run(
                user_input,
                session_id=session_id if not repairing_output else f"{session_id}:output-repair-{attempt - 1}",
            )
            attempts.append(
                _attempt_telemetry(
                    result,
                    attempt,
                    output_codec.validation_issues,
                    str(spec.agent_fingerprint) if spec.agent_fingerprint is not None else None,
                )
            )
            if guarded_transport is not None and guarded_transport.failure_code:
                attempts[-1]["error_code"] = guarded_transport.failure_code
            previous_validation_issues = output_codec.validation_issues
            if (
                result.output is not None
                or _error_code(result.error_summary) != OUTPUT_VALIDATION_FAILURE_CODE
                or attempt == OUTPUT_VALIDATION_MAX_ATTEMPTS
            ):
                break

        if result is None or spec is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("agent_execution_produced_no_result")
        output = result.output if isinstance(result.output, WAFAnalysisOutput) else None
        finish_reason = getattr(result.finish_reason, "value", result.finish_reason)
        error_summary = _jsonable(result.error_summary)
        safe_error = str(result.error) if result.error is not None else None
        if provider == "openai":
            error_code = attempts[-1]["error_code"]
            # OpenAI HTTP/protocol diagnostics never persist provider response text.
            error_summary = {"code": error_code} if error_code else None
            safe_error = error_code if result.error is not None else None
        telemetry = {
            "framework": "moduagent",
            "framework_version": framework_version(),
            "execution": "standard",
            "usage": _usage_jsonable(result.usage),
            "run_usage": _jsonable(result.run_usage),
            "metadata": _jsonable(result.metadata),
            "tool_trace": _jsonable(result.tool_trace),
            "error_summary": error_summary,
            "raw_provider_body_stored": False,
            "provider": provider,
            "thinking_enabled": None if provider == "openai" else False,
            "output_validation_retry": {
                "attempted": len(attempts) > 1,
                "attempt_count": len(attempts),
                "max_attempts": OUTPUT_VALIDATION_MAX_ATTEMPTS,
                "recovered": len(attempts) > 1 and str(finish_reason) == "completed" and output is not None,
                "attempts": attempts,
            },
        }
        return AgentCallResult(
            output=output,
            framework_run_id=str(result.run_id) if result.run_id is not None else None,
            agent_fingerprint=str(spec.agent_fingerprint) if spec.agent_fingerprint is not None else None,
            finish_reason=str(finish_reason),
            failure_id=str(result.failure_id) if result.failure_id is not None else None,
            error=safe_error,
            telemetry=telemetry,
        )
