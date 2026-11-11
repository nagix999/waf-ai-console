"""Source-role constraints, separate from exact citation and semantic checks.

The catalog is server-built provenance. A model's interpretation, selected
field alias or a coincidentally equal request value must not replace it.
"""
from collections.abc import Mapping
from copy import deepcopy


VERSION = "evidence-source-policy-v1"
RULES_VERSIONS = frozenset({"waf-system-v2.21"})
SIGNATURE_SUPPORT_ERROR = "signature_support_not_allowed"
_DIRECTIONAL_SUPPORTS = frozenset({
    "attack_request", "normal_request", "true_positive", "false_positive",
})


def is_signature_source_field(field: object) -> bool:
    """Recognize only the canonical metadata field and its resolver alias.

    A request parameter or extension called ``signature`` is not the WAF's
    top-level detection metadata. Never infer provenance from excerpt text.
    """
    return isinstance(field, str) and field.strip() in {"signature", "event.signature"}


def catalog_for_source_policy(catalog, *, fixed_rules_version):
    """Add explicit allowed roles to a fresh v2.21-only catalog copy.

    The input builder must include this metadata in its serialized budget. All
    excerpts, offsets, ordering and unrelated fields remain unchanged; this
    does not remove detection metadata from the model's analysis input.
    """
    if fixed_rules_version not in RULES_VERSIONS:
        return catalog
    result = deepcopy(catalog)
    result["version"] = "evidence-selection-v4"
    for item in result.get("items", []):
        if is_signature_source_field(item.get("field")):
            item["allowed_supports"] = ["context"]
    return result


def signature_support_issues(selection, catalog, *, fixed_rules_version):
    """Return payload-free citation issues; never rewrite evidence direction.

    This accepts the strict request wire output and its existing legacy bridge.
    The worker must run it after every source selection/correction and exclude
    unresolved indexes from canonical evidence and source resolutions. It can
    pass these issues to the existing bounded citation correction path. A
    change in interpretation or support is not a citation-only correction and
    must remain subject to the existing reanalysis/hold behavior.

    Source existence, offset validity and duplicate catalog IDs are still
    checked by ``resolve_selection``. If an ambiguous ID includes a signature
    source we also flag its directional use instead of trusting whichever
    duplicate happened to be last.
    """
    if fixed_rules_version not in RULES_VERSIONS or not isinstance(catalog, Mapping):
        return []
    signature_ids = {
        item.get("source_id")
        for item in catalog.get("items", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("source_id"), str)
        and is_signature_source_field(item.get("field"))
    }
    return [
        {"index": index, "reason": SIGNATURE_SUPPORT_ERROR}
        for index, item in enumerate(selection.evidence)
        if item.source_id in signature_ids and item.supports in _DIRECTIONAL_SUPPORTS
    ]
