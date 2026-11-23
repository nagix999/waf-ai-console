"""Keep v2.28 verdict and Verifier conditions unchanged for the new candidate."""
from dataclasses import replace

from . import execution_policy_v228 as legacy

RULES_VERSION = "waf-system-v2.29"
INTEGRITY_RULES_VERSIONS = legacy.INTEGRITY_RULES_VERSIONS | {RULES_VERSION}
affected_normal_findings = legacy.affected_normal_findings
apply_integrity_guard = legacy.apply_integrity_guard
apply_final_followup_policy = legacy.apply_final_followup_policy


def verifier_reasons(primary, context):
    # A model's capture estimate is deliberately not a policy input. Actual
    # input omission, confidence, grounding and disagreement retain their rules.
    if context.fixed_rules_version == RULES_VERSION:
        context = replace(context, fixed_rules_version=legacy.RULES_VERSION)
    return legacy.verifier_reasons(primary, context)


def integrity_version_for_rules(fixed_rules_version):
    return legacy.integrity_version_for_rules(
        legacy.RULES_VERSION if fixed_rules_version == RULES_VERSION else fixed_rules_version)


def verifier_policy_metadata(fixed_rules_version):
    return legacy.verifier_policy_metadata(
        legacy.RULES_VERSION if fixed_rules_version == RULES_VERSION else fixed_rules_version)
