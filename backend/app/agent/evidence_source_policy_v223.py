"""Pinned context-only provenance for detection metadata and registered text.

Do not infer provenance from excerpt equality. A payload field bearing the
same name is still request content, not the server-managed reference.
"""
from collections.abc import Mapping
from copy import deepcopy

from .evidence_source_policy import is_signature_source_field, SIGNATURE_SUPPORT_ERROR


VERSION = "evidence-source-policy-v2"
RULES_VERSIONS = frozenset({"waf-system-v2.23"})
CATALOG_VERSION = "evidence-selection-v5"
_DIRECTIONAL_SUPPORTS = frozenset({
    "attack_request", "normal_request", "true_positive", "false_positive",
})


def is_reference_source_field(field):
    if not isinstance(field, str):
        return False
    normalized = field.strip().removeprefix("event.")
    return is_signature_source_field(field) or normalized in {
        "signature_explanation", "extra_fields.signature_explanation",
    }


def catalog_for_source_policy(catalog, *, fixed_rules_version):
    if fixed_rules_version not in RULES_VERSIONS:
        return catalog
    result = deepcopy(catalog)
    result["version"] = CATALOG_VERSION
    for item in result.get("items", []):
        if is_reference_source_field(item.get("field")):
            item["allowed_supports"] = ["context"]
    return result


def signature_support_issues(selection, catalog, *, fixed_rules_version):
    """Enforce immutable source roles after first selection AND correction."""
    if fixed_rules_version not in RULES_VERSIONS or not isinstance(catalog, Mapping):
        return []
    context_ids = {
        item.get("source_id")
        for item in catalog.get("items", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("source_id"), str)
        and is_reference_source_field(item.get("field"))
    }
    return [
        {"index": index, "reason": SIGNATURE_SUPPORT_ERROR}
        for index, item in enumerate(selection.evidence)
        if item.source_id in context_ids and item.supports in _DIRECTIONAL_SUPPORTS
    ]
