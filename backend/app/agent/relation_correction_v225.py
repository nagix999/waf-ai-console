"""Normalize only a provably unchanged relationship graph, without an LLM.

A valid association is a directed representative-to-related edge between
existing evidence indexes. Self references carry no association, and repeated
groups with the same representative can be represented as their ordered union.
No absent, malformed, out-of-range or contradictory association is inferred.
"""

from copy import deepcopy
import json

from pydantic import ValidationError

from .contracts_v224 import SecurityAnalysisOutputV224
from .evidence_source_policy_v223 import is_reference_source_field
from .structured_json import StructuredJSONRejected, normalize_structured_json


VERSION = "relation-graph-normalization-v1"
SELF_REFERENCE_REMOVED = "relation_self_reference_removed"
REPRESENTATIVE_GROUPS_MERGED = "relation_representative_groups_merged"
RELATED_DUPLICATES_REMOVED = "relation_related_duplicates_removed"
NORMALIZATION_CODES = frozenset({
    SELF_REFERENCE_REMOVED, REPRESENTATIVE_GROUPS_MERGED, RELATED_DUPLICATES_REMOVED,
})


def _catalog_selections_valid(value: dict, user_input: str) -> bool:
    """Check server catalog provenance before retaining a fixed interpretation.

    The worker still checks exact original source offsets and submitted spans
    after decoding. Catalog membership alone is not proof of semantic accuracy.
    """
    try:
        document = json.loads(normalize_structured_json(user_input).content)
    except (StructuredJSONRejected, TypeError, ValueError, RecursionError):
        return False
    catalog = document.get("evidence_candidates")
    if type(catalog) is not dict or catalog.get("version") != "evidence-selection-v5":
        return False
    items = catalog.get("items")
    if type(items) is not list or not 1 <= len(items) <= 999:
        return False
    by_id = {}
    for item in items:
        if (type(item) is not dict or type(item.get("source_id")) is not str
                or item["source_id"] in by_id):
            return False
        by_id[item["source_id"]] = item
    for evidence in value["evidence"]:
        item = by_id.get(evidence["source_id"])
        if (item is None or type(item.get("field")) is not str
                or type(item.get("excerpt")) is not str or not item["excerpt"].strip()):
            return False
        allowed = item.get("allowed_supports")
        if allowed is not None and (type(allowed) is not list or evidence["supports"] not in allowed):
            return False
        if is_reference_source_field(item["field"]) and evidence["supports"] != "context":
            return False
        start, end = item.get("start"), item.get("end")
        if item["field"] == "payload" or item["field"].startswith("payload."):
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end or end - start != len(item["excerpt"])):
                return False
        elif start is not None or end is not None:
            return False
    return True


def _unambiguous_edges(value: dict) -> frozenset[tuple[int, int]] | None:
    """Reject any group whose intended association is not fully identified."""
    groups, evidence = value.get("evidence_relations"), value.get("evidence")
    if (type(groups) is not list or not 1 <= len(groups) <= 5
            or type(evidence) is not list or not 2 <= len(evidence) <= 5):
        return None
    edges = set()
    for group in groups:
        if type(group) is not dict or group.keys() != {"representative_index", "related_indexes"}:
            return None
        representative, related = group["representative_index"], group["related_indexes"]
        if (type(representative) is not int or not 0 <= representative < len(evidence)
                or type(related) is not list or not 1 <= len(related) <= 4
                or any(type(index) is not int or not 0 <= index < len(evidence) for index in related)):
            return None
        indexes = {representative, *related}
        # A self-only or empty group cannot be deleted to make validation pass.
        if len(indexes) < 2:
            return None
        supports = {evidence[index]["supports"] for index in indexes}
        if {"attack_request", "normal_request"} <= supports:
            return None
        edges.update((representative, index) for index in related if index != representative)
    return frozenset(edges) if edges else None


def recover_relation_graph(value, user_input: str) -> tuple[dict, list[str]]:
    """Keep every valid directed edge and every non-relationship value unchanged.

    All-or-nothing validation prevents a valid group from hiding an unrelated
    invalid group or an error skipped by Pydantic's earlier nested validation.
    """
    if type(value) is not dict:
        return deepcopy(value), []
    original = deepcopy(value)
    try:
        # Replacing only the relation field allows every other field and domain
        # dependency to be validated even if a nested relation failed earlier.
        SecurityAnalysisOutputV224.model_validate({**original, "evidence_relations": []})
    except (ValidationError, ValueError, TypeError, RecursionError):
        return original, []
    edges = _unambiguous_edges(original)
    if edges is None or not _catalog_selections_valid(original, user_input):
        return original, []

    by_representative = {}
    self_removed = groups_merged = duplicates_removed = False
    for group in original["evidence_relations"]:
        representative = group["representative_index"]
        if representative in by_representative:
            groups_merged = True
        related = by_representative.setdefault(representative, [])
        for index in group["related_indexes"]:
            if index == representative:
                self_removed = True
            elif index in related:
                duplicates_removed = True
            else:
                related.append(index)
    if not (self_removed or groups_merged or duplicates_removed):
        return original, []
    candidate = deepcopy(original)
    candidate["evidence_relations"] = [
        {"representative_index": representative, "related_indexes": related}
        for representative, related in by_representative.items()
    ]
    normalized_edges = frozenset(
        (group["representative_index"], index)
        for group in candidate["evidence_relations"] for index in group["related_indexes"]
    )
    if normalized_edges != edges:  # Defensive invariant: no association is guessed or lost.
        return original, []
    try:
        SecurityAnalysisOutputV224.model_validate(candidate)
    except (ValidationError, ValueError, TypeError, RecursionError):
        return original, []
    codes = []
    if self_removed:
        codes.append(SELF_REFERENCE_REMOVED)
    if groups_merged:
        codes.append(REPRESENTATIVE_GROUPS_MERGED)
    if duplicates_removed:
        codes.append(RELATED_DUPLICATES_REMOVED)
    return candidate, codes
