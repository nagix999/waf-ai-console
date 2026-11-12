"""Remove only redundant evidence edges from an already-decoded JSON object.

This is not relation inference or a substitute for strict output validation and
source grounding. The caller must supply complete, bounded JSON decoded by the
existing strict parser, then validate the returned object with the original
contract and source guards. No raw value is logged or attached to an exception.
"""
from copy import deepcopy

from pydantic import ValidationError

from .contracts import SecurityAnalysisOutputV220


VERSION = "relation-duplicate-recovery-v1"
RELATED_DUPLICATES = "relation_related_duplicates_removed"
GROUP_DUPLICATES = "relation_group_duplicates_removed"
NORMALIZATION_CODES = frozenset({RELATED_DUPLICATES, GROUP_DUPLICATES})
_GROUP_KEYS = frozenset({"representative_index", "related_indexes"})
_SUPPORTS = frozenset({"attack_request", "normal_request", "context"})


def recover_relation_duplicates(value: dict) -> tuple[dict, list[str]]:
    """Return an independent copy with only two lossless duplicate removals.

    Keep the first occurrence and the original order. A repeated representative
    must have an exactly identical original related-index sequence; different
    sequences are never merged, even if their unordered sets happen to match.
    Any unsafe shape or remaining contract error cancels the whole operation.
    Evidence, explanations, supports, verdict and every other field stay intact.
    """
    if type(value) is not dict:
        raise ValueError("relation_recovery_input_invalid")
    original = deepcopy(value)
    groups, evidence = original.get("evidence_relations"), original.get("evidence")
    if type(groups) is not list or not groups or type(evidence) is not list or not 1 <= len(evidence) <= 5:
        return original, []
    if any(type(item) is not dict or type(item.get("supports")) is not str
           or item["supports"] not in _SUPPORTS for item in evidence):
        return original, []

    # Check all groups before making edits. Do not turn a malformed reference
    # into an apparently usable integrated interpretation by deleting it.
    representatives = {}
    for group in groups:
        if type(group) is not dict or group.keys() != _GROUP_KEYS:
            return original, []
        representative, related = group["representative_index"], group["related_indexes"]
        if (type(representative) is not int or not 0 <= representative < len(evidence)
                or type(related) is not list or not related):
            return original, []
        if any(type(index) is not int or not 0 <= index < len(evidence)
               or index == representative for index in related):
            return original, []
        supports = {evidence[index]["supports"] for index in (representative, *related)}
        if {"attack_request", "normal_request"} <= supports:
            return original, []
        key = tuple(related)
        if representative in representatives and representatives[representative] != key:
            return original, []
        representatives[representative] = key

    candidate = deepcopy(original)
    recovered, seen_groups = [], set()
    related_changed = group_changed = False
    for group in candidate["evidence_relations"]:
        related = list(dict.fromkeys(group["related_indexes"]))
        related_changed |= related != group["related_indexes"]
        group["related_indexes"] = related
        key = (group["representative_index"], tuple(related))
        if key in seen_groups:
            group_changed = True
            continue
        seen_groups.add(key)
        recovered.append(group)
    if not (related_changed or group_changed):
        return original, []
    candidate["evidence_relations"] = recovered

    try:
        # Validate without serializing the model back: defaults, coercions or
        # serializer behavior must not rewrite unrelated output values.
        SecurityAnalysisOutputV220.model_validate(candidate)
    except (ValidationError, ValueError, TypeError, RecursionError):
        return original, []
    codes = ([RELATED_DUPLICATES] if related_changed else [])
    if group_changed:
        codes.append(GROUP_DUPLICATES)
    return candidate, codes
