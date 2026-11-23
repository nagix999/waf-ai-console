"""Reuse immutable v2.25 reference-only provenance without changing source IDs."""
from . import evidence_source_policy_v226 as legacy

VERSION = legacy.VERSION
CATALOG_VERSION = legacy.CATALOG_VERSION
RULES_VERSION = "waf-system-v2.27"
RULES_VERSIONS = legacy.RULES_VERSIONS | {RULES_VERSION}
is_reference_source_field = legacy.is_reference_source_field


def _rules(fixed_rules_version):
    return "waf-system-v2.26" if fixed_rules_version == RULES_VERSION else fixed_rules_version


def catalog_for_source_policy(catalog, *, fixed_rules_version):
    return legacy.catalog_for_source_policy(catalog, fixed_rules_version=_rules(fixed_rules_version))


def signature_support_issues(selection, catalog, *, fixed_rules_version):
    return legacy.signature_support_issues(selection, catalog, fixed_rules_version=_rules(fixed_rules_version))
