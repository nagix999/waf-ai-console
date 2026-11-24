"""Preserve source roles before AND after bounded source-only correction.

Source roles come from server-created field paths, never matching text. The
legacy bridge uses verdict names for directional supports; normalize those
names only for comparison, without changing the evidence itself.
"""
from . import evidence_source_policy_v229 as legacy

VERSION = "evidence-source-policy-v3"
CATALOG_VERSION = legacy.CATALOG_VERSION
RULES_VERSION = "waf-system-v2.30"
RULES_VERSIONS = legacy.RULES_VERSIONS | {RULES_VERSION}
is_reference_source_field = legacy.is_reference_source_field
_SUPPORT_NAMES = {"true_positive": "attack_request", "false_positive": "normal_request"}


def catalog_for_source_policy(catalog, *, fixed_rules_version):
    rules = legacy.RULES_VERSION if fixed_rules_version == RULES_VERSION else fixed_rules_version
    return legacy.catalog_for_source_policy(catalog, fixed_rules_version=rules)


def source_support_issues(selection, catalog, *, fixed_rules_version):
    """Return fixed issue codes only; unknown IDs remain a separate failure.

    Both V230 and its legacy selection bridge expose source_id and supports.
    A reference field remains context-only even if a malformed catalog claims
    otherwise. Explicit allowed_supports on any other source is also binding.
    """
    if fixed_rules_version != RULES_VERSION:
        return legacy.signature_support_issues(selection, catalog, fixed_rules_version=fixed_rules_version)
    by_id = {item["source_id"]: item for item in catalog["items"]}
    issues = []
    for index, item in enumerate(selection.evidence):
        source = by_id.get(item.source_id)
        if source is None:
            continue
        support = _SUPPORT_NAMES.get(item.supports, item.supports)
        if is_reference_source_field(source["field"]) and support != "context":
            issues.append({"index": index, "reason": "signature_support_not_allowed"})
        elif "allowed_supports" in source and support not in source["allowed_supports"]:
            issues.append({"index": index, "reason": "source_support_not_allowed"})
    return issues


def signature_support_issues(selection, catalog, *, fixed_rules_version):
    """Compatibility for source-policy callers; apply all pinned restrictions."""
    return source_support_issues(selection, catalog, fixed_rules_version=fixed_rules_version)
