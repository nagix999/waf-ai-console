"""Final display policy, applied after verdict/integrity guards, never to role records."""

from .contracts import AgentVerdict, AnalystCheckPurpose, WAFAnalysisOutput

VERSION = "followup-policy-v1"
RULES_VERSIONS = frozenset({"waf-system-v2.14"})


def apply_final_followup_policy(
    output: WAFAnalysisOutput, waf_action: str, fixed_rules_version: str,
) -> tuple[WAFAnalysisOutput, dict | None]:
    if fixed_rules_version not in RULES_VERSIONS:
        return output, None

    denied_attack = output.verdict == AgentVerdict.true_positive and waf_action == "D"
    kept, reasons = [], []
    for check in output.analyst_checks:
        reason = None
        if denied_attack and check.purpose == AnalystCheckPurpose.impact_followup:
            reason = "true_positive_denied_impact_followup_suppressed"
        elif check.purpose == AnalystCheckPurpose.tuning_validation and not output.tuning_recommendation.recommended:
            reason = "tuning_validation_without_recommendation"
        if reason:
            reasons.append(reason)
        else:
            kept.append(check)

    # Do not mutate objects shared with Primary/Verifier or encrypted history.
    sanitized = output.model_copy(update={"analyst_checks": kept}) if reasons else output
    return sanitized, {
        "version": VERSION,
        "suppressed_count": len(reasons),
        "reason_codes": list(dict.fromkeys(reasons)),
    }
