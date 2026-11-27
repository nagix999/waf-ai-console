"""Retain context-only detector metadata and all source-specific restrictions."""
from . import evidence_source_policy_v236 as legacy

VERSION = legacy.VERSION
CATALOG_VERSION = legacy.CATALOG_VERSION
RULES_VERSION = "waf-system-v2.38"
RULES_VERSIONS = legacy.RULES_VERSIONS | {RULES_VERSION}
is_reference_source_field = legacy.is_reference_source_field


def _rules(version):
    return legacy.RULES_VERSION if version == RULES_VERSION else version


def catalog_for_source_policy(catalog, *, fixed_rules_version):
    return legacy.catalog_for_source_policy(catalog, fixed_rules_version=_rules(fixed_rules_version))


def source_support_issues(selection, catalog, *, fixed_rules_version):
    return legacy.source_support_issues(selection, catalog, fixed_rules_version=_rules(fixed_rules_version))


signature_support_issues = source_support_issues
