"""Eligibility for recovery from a length-stopped source-only correction.

This never decodes a failed response, edits a support, or grants model budget.
Only a completed, typed first analysis may supply the review's frozen basis.
"""
from .contracts_v238 import SecurityAnalysisOutputV238

VERSION = "source-length-recovery-v1"
_SOURCE_ERRORS = frozenset({"signature_support_not_allowed", "source_support_not_allowed"})
_INCOMPLETE_ERRORS = frozenset({"model_output_incomplete", "vllm_output_incomplete", "openai_output_incomplete"})


def eligibility_reason(initial_call, failed_call, *, initial_output, initial_selection,
                       initial_record, attempts, requested_indexes):
    """Return a fixed rejection code, or None for a possible same-role review.

    The caller must still remove all rejected claims and their dependents,
    check input/attempt budgets, and validate the complete reviewed decision.
    A remaining directional quote alone never authorizes a recovered verdict.
    """
    if (initial_call is None or not initial_call.succeeded
            or type(initial_output) is not SecurityAnalysisOutputV238
            or initial_selection is None or not isinstance(initial_record, dict)):
        return "initial_grounding_unavailable"
    if len(attempts) != 2 or failed_call.succeeded or failed_call.output is not None:
        return "source_correction_not_failed"
    retry = failed_call.telemetry.get("output_validation_retry")
    if not isinstance(retry, dict):
        return "source_failure_unverified"
    records = retry.get("attempts")
    if (type(retry.get("attempt_count")) is not int or retry["attempt_count"] != 1
            or type(records) is not list or len(records) != 1 or type(records[0]) is not dict):
        return "source_failure_unverified"
    last = records[0]
    if (last.get("provider_finish_reason") not in {"length", "max_tokens"}
            or last.get("error_code") not in _INCOMPLETE_ERRORS):
        return "source_failure_not_length"
    issues = initial_record.get("rejected_evidence")
    count = len(initial_selection.evidence)
    if (type(issues) is not list or not issues or not requested_indexes
            or any(type(issue) is not dict or issue.get("reason") not in _SOURCE_ERRORS
                   or type(issue.get("index")) is not int or not 0 <= issue["index"] < count
                   for issue in issues)):
        return "initial_source_errors_not_isolated"
    if {issue["index"] for issue in issues} != set(requested_indexes):
        return "source_correction_scope_ambiguous"
    resolutions = initial_record.get("source_resolutions")
    resolved = initial_record.get("resolved_output")
    if (type(resolutions) is not list or type(resolved) is not dict
            or type(resolved.get("evidence")) is not list
            or len(resolutions) != len(resolved["evidence"])
            or any(type(item) is not dict or type(item.get("index")) is not int
                   or not 0 <= item["index"] < count for item in resolutions)
            or len({item["index"] for item in resolutions}) != len(resolutions)):
        return "initial_grounding_unavailable"
    return None
