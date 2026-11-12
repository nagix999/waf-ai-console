"""The v2.21 safety policy, explicitly reused without editing pinned modules."""
from dataclasses import replace

from . import execution_policy_v221 as _legacy


RULES_VERSION = "waf-system-v2.22"
INTEGRITY_RULES_VERSIONS = _legacy.INTEGRITY_RULES_VERSIONS | {RULES_VERSION}
affected_normal_findings = _legacy.affected_normal_findings
apply_integrity_guard = _legacy.apply_integrity_guard
apply_final_followup_policy = _legacy.apply_final_followup_policy


def verifier_reasons(primary, context):
    # Same policy, including parser-status-only exclusion. Do not retire any
    # additional reason or share the Primary's output with the Verifier.
    if context.fixed_rules_version == RULES_VERSION:
        context = replace(context, fixed_rules_version=_legacy.RULES_VERSION)
    return _legacy.verifier_reasons(primary, context)


def integrity_version_for_rules(fixed_rules_version):
    return _legacy.integrity_version_for_rules(
        _legacy.RULES_VERSION if fixed_rules_version == RULES_VERSION else fixed_rules_version)


def verifier_policy_metadata(fixed_rules_version):
    return _legacy.verifier_policy_metadata(
        _legacy.RULES_VERSION if fixed_rules_version == RULES_VERSION else fixed_rules_version)
