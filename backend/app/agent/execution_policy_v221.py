"""Version routing for the candidate's unchanged final-verdict safety policy.

The frozen v2.20 implementation is not edited or monkeypatched.  Parser state
remains diagnostic-only in v2.21, while every other Verifier trigger and the
source-scoped normal-verdict guard retain their established behavior.

``input_truncated`` describes the actual submitted input.  It is not itself a
forced verdict: independent roles may agree on a decisive interpretation from
the retained evidence.  Missing decisive content remains a model-reported
decision issue, and observed capture/framing defects retain the original guard.
"""
from . import policy as _policy
from . import security_analysis as _security_analysis
from .followup_policy import apply_final_followup_policy
from .input_integrity import affected_normal_findings, apply_integrity_guard
from ..services import request_integrity as _request_integrity


RULES_VERSION = "waf-system-v2.21"
PARSER_DIAGNOSTICS_ONLY_RULES_VERSIONS = (
    _policy.PARSER_DIAGNOSTICS_ONLY_RULES_VERSIONS | {RULES_VERSION}
)
INTEGRITY_VERSION = _request_integrity.INTEGRITY_VERSION
LEGACY_INTEGRITY_VERSION = _request_integrity.LEGACY_INTEGRITY_VERSION
INTEGRITY_V3_VERSION = _request_integrity.INTEGRITY_V3_VERSION
INTEGRITY_V2_RULES_VERSIONS = _request_integrity.INTEGRITY_V2_RULES_VERSIONS
INTEGRITY_V3_RULES_VERSIONS = _request_integrity.INTEGRITY_V3_RULES_VERSIONS | {RULES_VERSION}
INTEGRITY_RULES_VERSIONS = _request_integrity.INTEGRITY_RULES_VERSIONS | {RULES_VERSION}

# These objects are not replaced inside the frozen modules.  Sharing their
# immutable context/result types and pure guards avoids a second safety policy.
VerifierPolicyContext = _policy.VerifierPolicyContext
Finalization = _policy.Finalization
finalize_with_verifier = _policy.finalize_with_verifier
finalize_without_verifier = _policy.finalize_without_verifier
assess_request_integrity = _request_integrity.assess_request_integrity


def parser_status_triggers_verifier(fixed_rules_version: str | None) -> bool:
    if fixed_rules_version == RULES_VERSION:
        return False
    return _policy.parser_status_triggers_verifier(fixed_rules_version)


def verifier_reasons(primary, context: VerifierPolicyContext) -> list[str]:
    reasons = _policy.verifier_reasons(primary, context)
    if context.fixed_rules_version == RULES_VERSION:
        # Only extend the explicitly retired parser-only condition.  Preserve
        # real truncation, grounding, confidence and semantic-conflict signals.
        return [reason for reason in reasons if reason != "parser_incomplete"]
    return reasons


def integrity_version_for_rules(fixed_rules_version: str) -> str:
    if fixed_rules_version == RULES_VERSION:
        return INTEGRITY_V3_VERSION
    return _request_integrity.integrity_version_for_rules(fixed_rules_version)


def verifier_policy_metadata(fixed_rules_version):
    if fixed_rules_version != RULES_VERSION:
        return _security_analysis.verifier_policy_metadata(fixed_rules_version)
    return {
        "verifier_policy_version": "security-analysis-policy-v2",
        "retired_verifier_triggers": [*_security_analysis.RETIRED_VERIFIER_TRIGGERS, "parser_incomplete"],
    }
