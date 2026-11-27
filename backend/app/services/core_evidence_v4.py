"""Same-point core selection with response-only, source-bound ID aliases.

Judgment and its original provenance remain immutable. The new Editor version
is the opt-in boundary; historical plans continue through core-evidence-v1.
"""
from copy import deepcopy

from ..agent.evidence_relations_v238 import validate_evidence_relations
from ..core_result_contracts_v2 import VERSION, validate_core_projection
from ..result_contracts import validate_new_result
from . import core_evidence_v3 as legacy
from .evidence_display_plan_v3 import validate_display_plan


EMPTY_RELATIONS = {"version": "evidence-relations-v2", "groups": []}


def _fallback(result, relations, reason):
    # Use only exact duplicate grouping and the established policy/counter-source
    # selection. Never erase a required mixed reference to reach the old reader.
    from .evidence_display_plan_v3 import build_display_plan, _located_source
    indexed = {item["evidence_id"]: item for item in result["evidence"]}
    groups = []
    for group in legacy._code_groups(result, relations):
        if len(group["member_ids"]) > 1 and any(
                _located_source(indexed[key]) is None for key in group["member_ids"]):
            groups.extend({"representative_id": key, "member_ids": [key]} for key in group["member_ids"])
        else:
            groups.append(group)
    plan = build_display_plan(result, relations, groups=groups,
                              status="skipped", reason="editor_unavailable_or_invalid")
    mapping, identifiers, metrics = _select(result, relations, plan)
    projected = validate_core_projection(result, mapping=mapping, selected_ids=identifiers,
                                         plan=plan, relations=relations)
    return {"result": projected, "metadata": {"version": VERSION, "status": "fallback",
        "reason": reason, "original_count": len(result["evidence"]),
        "returned_count": len(identifiers), **metrics}}


def _select(canonical, links, plan):
    indexed = {item["evidence_id"]: item for item in canonical["evidence"]}
    policies = canonical["policy"]["decision_evidence_ids"]
    policy_order = {key: index for index, key in enumerate(policies)}
    groups, aliases = [], {}
    original_order = {key: index for index, key in enumerate(indexed)}
    for group in plan["groups"]:
        members = group["member_ids"]
        eligible = [key for key in members if key in policy_order]
        suggested = group["representative_id"]
        # Retain an actual policy anchor, never rebrand another model's
        # recommended wording as the finalizer's original decision evidence.
        representative = (min(eligible, key=lambda key: (key != suggested, policy_order[key]))
                          if eligible else suggested)
        groups.append({"member_ids": members, "representative_id": representative})
        aliases.update({key: representative for key in members})
    dependencies = legacy._dependencies(canonical["evidence"], links)
    required = {group["representative_id"]: set() for group in groups}
    for group in groups:
        key = group["representative_id"]
        for member in group["member_ids"]:
            required[key].update(aliases[other] for other in dependencies[member])
        required[key].discard(key)
    selected = set()

    def add(identifier):
        pending = [aliases[identifier]]
        while pending:
            key = pending.pop()
            if key in selected:
                continue
            selected.add(key)
            pending.extend(required[key] - selected)

    issue_sets = [canonical["decision_issues"], canonical["primary"]["decision_issues"]]
    verifier = canonical["verifier"].get("output")
    if verifier:
        issue_sets.append(verifier["decision_issues"])
    original_issue_ids = set()
    for issues in issue_sets:
        for issue in issues:
            for key in issue["evidence_ids"]:
                original_issue_ids.add(key)
                add(key)

    direction = {"true_positive": "attack_request", "false_positive": "normal_request"}.get(canonical["verdict"])
    if direction:
        anchors = [key for key in policies if indexed[key]["supports"] == direction]
        if not anchors:
            raise legacy.CoreEvidenceError("core_evidence_decision_evidence_missing")
        if not any(aliases[key] in selected for key in anchors):
            rank = {key: index for index, group in enumerate(groups) for key in group["member_ids"]}
            add(min(anchors, key=lambda key: (rank[key], key != aliases[key], policy_order[key])))

    def ensure_support(support):
        if any(indexed[key]["supports"] == support for key in selected):
            return
        group = next((group for group in groups if indexed[group["representative_id"]]["supports"] == support), None)
        if group:
            add(group["representative_id"])

    if direction:
        ensure_support("normal_request" if direction == "attack_request" else "attack_request")
    else:
        ensure_support("attack_request")
        ensure_support("normal_request")
    directional = any(item["supports"] != "context" for item in indexed.values())
    for group in groups:
        if len(selected) >= legacy.TARGET_COUNT:
            break
        key = group["representative_id"]
        if key in selected or (directional and indexed[key]["supports"] == "context"):
            continue
        add(key)
    ranks = {group["representative_id"]: index for index, group in enumerate(groups)}
    identifiers = sorted(selected, key=lambda key: (ranks[key], original_order[key]))
    diagnostics = {"group_count": len(groups),
        "aliased_count": sum(key != target for key, target in aliases.items()),
        "issue_reference_reduction": len(original_issue_ids) - len({aliases[key] for key in original_issue_ids}),
        "required_dependency_count": len(set().union(*(required[key] for key in selected))) if selected else 0}
    return aliases, identifiers, diagnostics


def build_core_result(result, *, relations=None, display_view=None):
    """Only the new saved view opts into cross-role issue-reference semantics."""
    canonical = validate_new_result(deepcopy(result))
    embedded = display_view.get("relations") if isinstance(display_view, dict) else None
    links = validate_evidence_relations(relations if relations is not None else embedded or EMPTY_RELATIONS,
                                        canonical["evidence"])
    if not isinstance(display_view, dict) or display_view.get("version") != "evidence-display-view-v3":
        return _fallback(canonical, links, "organization_unavailable")
    if display_view.get("status") not in {"completed", "skipped"}:
        return _fallback(canonical, links, "organization_unavailable")
    try:
        plan = validate_display_plan(canonical, links, display_view.get("plan"))
        if plan["status"] not in {"completed", "skipped"}:
            raise ValueError()
        mapping, identifiers, metrics = _select(canonical, links, plan)
        projected = validate_core_projection(canonical, mapping=mapping, selected_ids=identifiers,
                                             plan=plan, relations=links)
        return {"result": projected, "metadata": {"version": VERSION, "status": plan["status"],
            "original_count": len(canonical["evidence"]), "returned_count": len(identifiers), **metrics}}
    except (ValueError, TypeError, KeyError):
        return _fallback(canonical, links, "representative_projection_unavailable")

