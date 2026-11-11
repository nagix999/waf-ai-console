"""Deterministic, read-only core evidence projection of the existing v3 JSON.

Stored analysis, source IDs and individual evidence objects never change. A
validated saved display plan supplies meaning groups and ranking, not a new
decision and not a visibility policy. Invalid/failed plans use exact-duplicate
grouping and the original decision-evidence order, never an all-evidence reset.
"""
from collections.abc import Mapping
from copy import deepcopy

from ..agent.evidence_relations import validate_evidence_relations
from ..result_contracts import validate_new_result
from .evidence_display_plan import validate_display_plan


VERSION = "core-evidence-v1"
TARGET_COUNT = 3
_EMPTY_RELATIONS = {"version": "evidence-relations-v1", "groups": []}


class CoreEvidenceError(ValueError):
    """Fixed diagnostics only; never attach raw evidence or validation text."""

    def __init__(self, code="core_evidence_projection_invalid"):
        self.code = code
        super().__init__(code)


def _physical_key(item):
    field = item["field"].strip().removeprefix("event.")
    if field == "raw_payload":
        field = "payload"
    spans = tuple(sorted({(span["start"], span["end"]) for span in item.get("source_spans", [])}))
    return field, item["excerpt"], spans, item["supports"], item["interpretation_ko"]


def _code_groups(result, relations):
    """No semantic guesses: collapse only physically and textually exact repeats."""
    evidence = result["evidence"]
    order = list(dict.fromkeys([*result["policy"]["decision_evidence_ids"],
                               *(item["evidence_id"] for item in evidence)]))
    indexed = {item["evidence_id"]: item for item in evidence}
    dependencies = _dependencies(evidence, relations)
    groups, buckets = [], {}
    for identifier in order:
        memberships = frozenset(index for index, issue in enumerate(result["decision_issues"])
                                if identifier in issue["evidence_ids"])
        required_sources = frozenset(_physical_key(indexed[other])[:3] for other in dependencies[identifier])
        key = (_physical_key(indexed[identifier]), memberships, required_sources)
        group = buckets.get(key)
        if group is None:
            group = {"representative_id": identifier, "member_ids": []}
            buckets[key] = group
            groups.append(group)
        group["member_ids"].append(identifier)
    return groups


def _grouping(result, relations, display_view):
    if display_view is None:
        return _code_groups(result, relations), "skipped"
    if not isinstance(display_view, Mapping):
        return _code_groups(result, relations), "fallback"
    # A failed/missing model plan never restores every original item, and its
    # default_visible flags (which intentionally protect all on failure) are
    # not a core selection decision.
    if display_view.get("status") not in {"completed", "skipped", "disabled"}:
        return _code_groups(result, relations), "fallback"
    try:
        plan = validate_display_plan(result, relations, display_view.get("plan"))
        if plan["status"] not in {"completed", "skipped"}:
            raise ValueError()
        return ([{"representative_id": group["representative_id"], "member_ids": group["member_ids"]}
                 for group in plan["groups"]],
                "completed" if plan["status"] == "completed" else "skipped")
    except (ValueError, TypeError, KeyError):
        return _code_groups(result, relations), "fallback"


def _dependencies(evidence, relations):
    direct = {item["evidence_id"]: set() for item in evidence}
    for group in relations["groups"]:
        direct[group["representative_evidence_id"]].update(group["related_evidence_ids"])
    closure = {}
    for identifier in direct:
        pending, reached = list(direct[identifier]), set()
        while pending:
            other = pending.pop()
            if other == identifier or other in reached:
                continue
            reached.add(other)
            pending.extend(direct[other] - reached)
        closure[identifier] = reached
    return closure


def _select(result, groups, dependencies):
    evidence = result["evidence"]
    indexed = {item["evidence_id"]: item for item in evidence}
    ranks = {identifier: index for index, group in enumerate(groups) for identifier in group["member_ids"]}
    representatives = {group["representative_id"] for group in groups}
    original_order = {item["evidence_id"]: index for index, item in enumerate(evidence)}
    selected = set()

    def add(identifier):
        selected.add(identifier)
        selected.update(dependencies[identifier])

    # Root decision issues remain byte-identical. Every referenced source and
    # every original dependency is therefore mandatory, regardless of target.
    for issue in result["decision_issues"]:
        for identifier in issue["evidence_ids"]:
            add(identifier)

    policy_ids = result["policy"]["decision_evidence_ids"]
    direction = {"true_positive": "attack_request", "false_positive": "normal_request"}.get(result["verdict"])
    if direction is not None:
        anchors = [identifier for identifier in policy_ids if indexed[identifier]["supports"] == direction]
        if not anchors:
            # A decisive result with only contextual/differently directed policy
            # evidence is not silently legitimized by picking unrelated items.
            raise CoreEvidenceError("core_evidence_decision_evidence_missing")
        if not selected.intersection(anchors):
            policy_order = {identifier: index for index, identifier in enumerate(policy_ids)}
            anchor = min(anchors, key=lambda identifier: (
                ranks[identifier], identifier not in representatives, policy_order[identifier]))
            add(anchor)

    def ensure_direction(support):
        if any(indexed[identifier]["supports"] == support for identifier in selected):
            return
        group = next((value for value in groups
                      if indexed[value["representative_id"]]["supports"] == support), None)
        if group is not None:
            add(group["representative_id"])

    if result["verdict"] == "inconclusive":
        ensure_direction("attack_request")
        ensure_direction("normal_request")
    else:
        ensure_direction("normal_request" if direction == "attack_request" else "attack_request")

    directional_available = any(item["supports"] != "context" for item in evidence)
    for group in groups:
        if len(selected) >= TARGET_COUNT:
            break
        identifier = group["representative_id"]
        if selected.intersection(group["member_ids"]):
            continue  # Never refill a meaning group already anchored by an ID.
        if indexed[identifier]["supports"] == "context" and directional_available:
            continue
        add(identifier)

    # Keep the saved importance order, stable original IDs and representative
    # preference. Dependencies still point to their own original role/source.
    return sorted(selected, key=lambda identifier: (
        ranks[identifier], identifier not in representatives, original_order[identifier]))


def build_core_result(result, *, relations=None, display_view=None):
    """Return a strict v3 projection plus transport/internal-only count metadata."""
    try:
        canonical = validate_new_result(deepcopy(result))
        embedded_relations = display_view.get("relations") if isinstance(display_view, Mapping) else None
        links = validate_evidence_relations(
            relations if relations is not None else embedded_relations if embedded_relations is not None else _EMPTY_RELATIONS,
            canonical["evidence"],
        )
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise CoreEvidenceError("core_evidence_source_invalid") from None
    try:
        groups, status = _grouping(canonical, links, display_view)
        dependencies = _dependencies(canonical["evidence"], links)
        identifiers = _select(canonical, groups, dependencies)
        selected = set(identifiers)
        indexed = {item["evidence_id"]: item for item in canonical["evidence"]}
        projected = deepcopy(canonical)
        projected["evidence"] = [deepcopy(indexed[identifier]) for identifier in identifiers]
        projected["policy"]["decision_evidence_ids"] = [identifier for identifier in canonical["policy"]["decision_evidence_ids"]
                                                        if identifier in selected]
        for name, role in (("primary", projected["primary"]), ("verifier", projected["verifier"].get("output"))):
            if role is None:
                continue
            original = canonical["primary"] if name == "primary" else canonical["verifier"]["output"]
            role["evidence_ids"] = [identifier for identifier in original["evidence_ids"] if identifier in selected]
            role["decision_issues"] = [deepcopy(issue) for issue in original.get("decision_issues", [])
                                       if set(issue["evidence_ids"]) <= selected]
        # The old complete-list presentation map is invalid after projection.
        # Do not add a replacement field or change the existing v3 schema.
        projected.pop("evidence_presentation", None)
        projected = validate_new_result(projected)
        if any(item != indexed[item["evidence_id"]] for item in projected["evidence"]):
            raise ValueError()
        return {"result": projected, "metadata": {"version": VERSION, "status": status,
                "original_count": len(canonical["evidence"]), "returned_count": len(projected["evidence"])}}
    except CoreEvidenceError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise CoreEvidenceError() from None
