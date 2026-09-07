"""Provider wire options, without changing the application's output contract.

OpenAI sampling/reasoning options are deliberately omitted: their availability
depends on the selected model. The provider default is not 'thinking disabled'.
``store=False`` opts out of stored completions, not all provider-side retention.
"""

from copy import deepcopy
from typing import Any, Mapping


def provider_name(profile: Any) -> str:
    provider = getattr(profile, "provider", None)
    if provider is None:
        provider = "vllm"
    if provider not in {"vllm", "openai"}:
        raise ValueError("model_provider_not_supported")
    return provider


def generation_options(profile: Any, *, max_output_tokens: int | None = None) -> dict[str, Any]:
    limit = profile.max_output_tokens if max_output_tokens is None else max_output_tokens
    if provider_name(profile) == "openai":
        return {"max_completion_tokens": limit, "store": False}
    return {
        "temperature": 0,
        "max_tokens": limit,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def strict_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Make a fresh strict wire schema; preserve nullable unions and constraints.

    A field with a default is still required on the wire. Existing nullable
    fields retain their null branch, and the original Pydantic codec continues
    enforcing semantic validators after decoding. No arbitrary map conversion
    or unsupported-schema fallback to unstructured JSON is attempted.
    """

    def adapt(value: Any) -> Any:
        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                if key == "default":
                    continue
                if key in {"properties", "$defs", "definitions", "patternProperties"} and isinstance(item, Mapping):
                    # Names inside these maps are data, even a field named 'default'.
                    result[key] = {name: adapt(child) for name, child in item.items()}
                elif key in {"anyOf", "allOf", "oneOf", "prefixItems", "items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames"}:
                    result[key] = adapt(item)
                else:
                    result[key] = deepcopy(item)
            if result.get("type") == "object" or "properties" in result:
                properties = result.get("properties", {})
                result["required"] = list(properties)
                result["additionalProperties"] = False
            return result
        if isinstance(value, list):
            return [adapt(item) for item in value]
        return value

    return adapt(schema)


def safe_openai_usage(value: Any) -> dict[str, Any] | None:
    """Allow only documented numeric usage counters into framework telemetry."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CompletionRejected("openai_invalid_response")

    def counters(source: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, int]:
        result = {}
        for name in names:
            if name in source:
                count = source[name]
                if type(count) is not int or count < 0:
                    raise CompletionRejected("openai_invalid_response")
                result[name] = count
        return result

    result: dict[str, Any] = counters(value, ("prompt_tokens", "completion_tokens", "total_tokens"))
    for field, allowed in (
        ("prompt_tokens_details", ("cached_tokens", "audio_tokens")),
        ("completion_tokens_details", ("reasoning_tokens", "audio_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens")),
    ):
        details = value.get(field)
        if details is not None:
            if not isinstance(details, Mapping):
                raise CompletionRejected("openai_invalid_response")
            result[field] = counters(details, allowed)
    return result


class CompletionRejected(ValueError):
    """Static diagnostic only: never retain refusal/content/HTTP response text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def completion_content(body: Any, provider: str) -> str:
    """Require a completed, non-refused, single text completion before decoding."""
    if not isinstance(body, Mapping) or body.get("error"):
        raise CompletionRejected(f"{provider}_invalid_response")
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise CompletionRejected(f"{provider}_invalid_response")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise CompletionRejected(f"{provider}_invalid_response")
    if message.get("refusal") not in (None, ""):
        raise CompletionRejected(f"{provider}_refusal")
    reason = choice.get("finish_reason")
    if reason != "stop":
        code = "output_incomplete" if reason == "length" else "completion_not_finished"
        raise CompletionRejected(f"{provider}_{code}")
    content = message.get("content")
    if message.get("tool_calls") or not isinstance(content, str) or not content.strip():
        raise CompletionRejected(f"{provider}_invalid_response")
    return content
