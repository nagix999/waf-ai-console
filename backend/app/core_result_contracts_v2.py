"""Candidate-only projection; immutable v3 fields and proof class are reused."""
from copy import deepcopy

from .core_result_contracts import (
    VERSION, CoreAnalysisResultV3, _CONTEXT_KEY, _ProjectionProof, _digest,
    _physical_source, _closed_references,
)
from .result_contracts import validate_new_result


def validate_core_projection(original, *, mapping, selected_ids, plan, relations):
    """Build, rather than trust, a response from an original and a saved plan.

    Mapping integrity is deterministic; semantic equivalence remains the
    Editor's judgment. No count/role/confidence heuristic creates a meaning
    group. The plan and source binding are checked again at this boundary.
    """
    from .services.evidence_display_plan_v3 import validate_display_plan
    from .agent.evidence_relations_v238 import validate_evidence_relations
    canonical = validate_new_result(deepcopy(original))
    links = validate_evidence_relations(relations, canonical["evidence"])
    checked = validate_display_plan(canonical, links, plan)
    if checked["status"] not in {"completed", "skipped"}:
        raise ValueError("core_response_plan_invalid")
    indexed = {item["evidence_id"]: item for item in canonical["evidence"]}
    if (not isinstance(mapping, dict) or set(mapping) != indexed.keys()
            or len(selected_ids) != len(set(selected_ids)) or not set(selected_ids) <= indexed.keys()):
        raise ValueError("core_response_mapping_invalid")
    selected = set(selected_ids)
    required_directions = {item["supports"] for item in canonical["evidence"] if item["supports"] != "context"}
    if not required_directions <= {indexed[key]["supports"] for key in selected}:
        raise ValueError("core_response_counter_evidence_missing")
    policy_ids = set(canonical["policy"]["decision_evidence_ids"])
    for group in checked["groups"]:
        members = group["member_ids"]
        targets = {mapping[identifier] for identifier in members}
        if len(targets) != 1:
            raise ValueError("core_response_mapping_invalid")
        target = next(iter(targets))
        if target not in members or mapping.get(target) != target:
            raise ValueError("core_response_mapping_invalid")
        if len(members) > 1:
            source = _physical_source(indexed[target])
            if not source[2] or any(_physical_source(indexed[key]) != source
                    or indexed[key]["supports"] != indexed[target]["supports"] for key in members):
                raise ValueError("core_response_mapping_invalid")
            if checked["status"] == "skipped" and any(indexed[key]["interpretation_ko"] != indexed[target]["interpretation_ko"]
                    for key in members):
                raise ValueError("core_response_mapping_invalid")
        if selected.intersection(members) and selected.intersection(members) != {target}:
            raise ValueError("core_response_mapping_invalid")
        if target in selected and policy_ids.intersection(members) and target not in policy_ids:
            raise ValueError("core_response_policy_anchor_changed")
    for relation in links["groups"]:
        if mapping[relation["representative_evidence_id"]] in selected:
            if not {mapping[key] for key in relation["related_evidence_ids"]} <= selected:
                raise ValueError("core_response_dependency_missing")

    projected = deepcopy(canonical)
    projected["evidence"] = [deepcopy(indexed[key]) for key in selected_ids]
    projected["policy"]["decision_evidence_ids"] = [key for key in canonical["policy"]["decision_evidence_ids"] if key in selected]

    def issues(values):
        output = deepcopy(values)
        for issue in output:
            issue["evidence_ids"] = list(dict.fromkeys(mapping[key] for key in issue["evidence_ids"]))
        return output

    projected["decision_issues"] = issues(canonical["decision_issues"])
    for name, role in (("primary", projected["primary"]), ("verifier", projected["verifier"].get("output"))):
        if role is not None:
            source = canonical["primary"] if name == "primary" else canonical["verifier"]["output"]
            role["evidence_ids"] = [key for key in source["evidence_ids"] if key in selected]
            role["decision_issues"] = issues(source["decision_issues"])
    projected.pop("evidence_presentation", None)
    _closed_references(projected)
    proof = _ProjectionProof(_digest(projected))
    return CoreAnalysisResultV3.model_validate(projected, context={_CONTEXT_KEY: proof})
