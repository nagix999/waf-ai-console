"""Recovery-only version binding; verdict and Verifier policies remain unchanged."""
from dataclasses import replace

from . import execution_policy_v238 as legacy

RULES_VERSION = "waf-system-v2.39"
INTEGRITY_RULES_VERSIONS = legacy.INTEGRITY_RULES_VERSIONS | {RULES_VERSION}
affected_normal_findings = legacy.affected_normal_findings
apply_integrity_guard = legacy.apply_integrity_guard
apply_final_followup_policy = legacy.apply_final_followup_policy


def _rules(version):
    return legacy.RULES_VERSION if version == RULES_VERSION else version


def verifier_reasons(primary, context):
    if context.fixed_rules_version == RULES_VERSION:
        context = replace(context, fixed_rules_version=legacy.RULES_VERSION)
    return legacy.verifier_reasons(primary, context)


def integrity_version_for_rules(fixed_rules_version):
    return legacy.integrity_version_for_rules(_rules(fixed_rules_version))


def verifier_policy_metadata(fixed_rules_version):
    return legacy.verifier_policy_metadata(_rules(fixed_rules_version))
