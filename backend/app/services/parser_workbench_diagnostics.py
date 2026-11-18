"""Safe parser-authoring feedback; never persist model text or exception messages."""
from collections.abc import Mapping

from pydantic import ValidationError

from ..agent.structured_json import StructuredJSONRejected
from .parser_rule_engine import ParserRuleError
from .provider_options import CompletionRejected

STAGES = frozenset({"response", "json", "schema", "rule_validation", "authoring", "discovery", "transport"})
RULE_CODES = frozenset({
    "parser_extraction_empty", "parser_extraction_not_string", "parser_json_duplicate_keys",
    "parser_json_invalid", "parser_json_limit", "parser_json_pointer_missing", "parser_marker_ambiguous",
    "parser_marker_missing", "parser_regex_ambiguous", "parser_regex_capture_invalid",
    "parser_regex_empty_capture", "parser_regex_invalid", "parser_regex_match_capture_forbidden",
    "parser_regex_no_match", "parser_regex_unavailable", "parser_rule_bundle_invalid",
    "parser_rule_time_limit", "parser_source_invalid", "parser_source_mapping_invalid",
})
VALIDATION_TYPES = frozenset({
    "missing", "extra_forbidden", "literal_error", "string_type", "string_too_long", "string_too_short",
    "list_type", "dict_type", "model_type", "too_long", "too_short", "int_type", "bool_type",
    "union_tag_invalid", "union_tag_not_found", "value_error", "validation_error",
})
PROVIDER_CODES = {
    "vllm_output_incomplete": "parser_workbench_output_incomplete",
    "vllm_refusal": "parser_workbench_refusal",
    "vllm_completion_not_finished": "parser_workbench_completion_not_finished",
    "vllm_invalid_response": "parser_workbench_invalid_response",
}


class WorkbenchOutputError(ValueError):
    """Only a fixed code enters args/repr. The prior rule is in-memory feedback only."""

    def __init__(self, code, stage, *, issues=None, previous_rule=None, failed_sample_ids=None):
        super().__init__(code)
        self.code, self.stage = code, stage
        self.issues = issues or []
        self.previous_rule = previous_rule
        self.failed_sample_ids = failed_sample_ids or []


def validation_issues(exc, output_model):
    # Location keys can be attacker-controlled (extra properties). Keep schema
    # property names only, not Pydantic's input/context/message or unknown keys.
    names, pending = set(), [output_model.model_json_schema()]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            names.update(value.get("properties", {}))
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    result = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False)[:12]:
        parts = ["[]" if type(part) is int else part if isinstance(part, str) and part in names
                 else "<unknown>" for part in error.get("loc", ())]
        result.append({"field": (".".join(parts) or "$")[:256],
                       "type": error["type"] if error.get("type") in VALIDATION_TYPES else "validation_error"})
    return result or [{"field": "$", "type": "validation_error"}]


def output_error(exc, stage, output_model, previous_rule=None):
    if isinstance(exc, WorkbenchOutputError):
        return exc
    if isinstance(exc, CompletionRejected):
        return WorkbenchOutputError(PROVIDER_CODES.get(exc.code, "parser_workbench_invalid_response"), "response")
    if isinstance(exc, StructuredJSONRejected):
        return WorkbenchOutputError("parser_workbench_json_invalid", "json",
                                    issues=[{"field": "$", "type": exc.code}])
    if isinstance(exc, ValidationError):
        return WorkbenchOutputError("parser_workbench_schema_invalid", "schema",
                                    issues=validation_issues(exc, output_model), previous_rule=previous_rule)
    if isinstance(exc, ParserRuleError):
        code = exc.code if exc.code in RULE_CODES else "parser_workbench_invalid_rule"
        return WorkbenchOutputError(code, stage, issues=[{"field": "extract", "type": code}],
                                    previous_rule=previous_rule)
    return WorkbenchOutputError("parser_workbench_json_invalid" if stage == "json"
                                else "parser_workbench_schema_invalid", stage)


def response_metadata(envelope):
    """Fixed finish reasons and non-negative token counters, never arbitrary JSON."""
    if not isinstance(envelope, Mapping):
        return {}
    result = {}
    choices = envelope.get("choices")
    if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], Mapping):
        reason = choices[0].get("finish_reason")
        if isinstance(reason, str) and reason in {"stop", "length", "content_filter", "tool_calls", "function_call"}:
            result["finish_reason"] = reason
    usage = envelope.get("usage")
    if isinstance(usage, Mapping):
        counters = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if type(usage.get(key)) is int and 0 <= usage[key] <= 2**63 - 1}
        if counters:
            result["usage"] = counters
    return result
