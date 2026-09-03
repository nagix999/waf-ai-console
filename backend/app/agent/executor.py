from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Mapping

from pydantic import BaseModel

from ..models import VLLMProfile
from .contracts import WAFAnalysisOutput


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


def framework_version() -> str:
    try:
        return version("moduagent")
    except PackageNotFoundError:
        return "not-installed"


async def execute_structured_agent(
    *,
    profile: VLLMProfile,
    api_key: str | None,
    instructions: str,
    user_input: str,
    session_id: str,
    agent_name: str,
) -> AgentCallResult:
    # ModuAgent 0.6.2's public VLLMClient constructor does not document a TLS
    # verification override. Never silently weaken or ignore an HTTPS profile.
    if profile.base_url.startswith("https://") and not profile.tls_verify:
        raise ValueError("moduagent_https_requires_tls_verification")

    # Import lazily so stub mode and pure policy tests do not require ModuAgent.
    from moduagent import Agent, RetryConfig, RunLimits, VLLMClient

    options = {
        "temperature": 0,
        "max_tokens": profile.max_output_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    async with VLLMClient(
        base_url=profile.base_url,
        model=profile.model_name,
        api_key=api_key,
        timeout=profile.timeout_seconds,
        default_options=options,
    ) as model:
        agent = Agent.create(
            name=agent_name,
            model=model,
            instructions=instructions,
            output=WAFAnalysisOutput,
            retry=RetryConfig(max_attempts=2),
            limits=RunLimits(
                max_model_turns=2,
                no_progress_model_turn_threshold=2,
                timeout_seconds=profile.timeout_seconds * 2,
            ),
        )
        spec = agent.inspect()
        result = await agent.run(user_input, session_id=session_id)
        output = result.output if isinstance(result.output, WAFAnalysisOutput) else None
        finish_reason = getattr(result.finish_reason, "value", result.finish_reason)
        telemetry = {
            "framework": "moduagent",
            "framework_version": framework_version(),
            "execution": "standard",
            "usage": _jsonable(result.usage),
            "run_usage": _jsonable(result.run_usage),
            "metadata": _jsonable(result.metadata),
            "tool_trace": _jsonable(result.tool_trace),
            "error_summary": _jsonable(result.error_summary),
            "raw_provider_body_stored": False,
            "thinking_enabled": False,
        }
        return AgentCallResult(
            output=output,
            framework_run_id=str(result.run_id) if result.run_id is not None else None,
            agent_fingerprint=str(spec.agent_fingerprint) if spec.agent_fingerprint is not None else None,
            finish_reason=str(finish_reason),
            failure_id=str(result.failure_id) if result.failure_id is not None else None,
            error=str(result.error) if result.error is not None else None,
            telemetry=telemetry,
        )
