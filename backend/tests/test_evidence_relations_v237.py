"""Public-v3 provenance and final reviewed-slot mapping for source contributions."""
from copy import deepcopy
from dataclasses import replace

import pytest

from app.agent.executor import AgentCallResult
from app.agent.evidence_relations_v237 import EvidenceRelationsError, build_evidence_relations
from app.agent.evidence_decision_review_v237 import review_base
from test_contracts_v237 import validate, wire, opposite_roots
from test_flat_evidence_v228 import catalog


def call_for(original, final=None, *, reviewed=False):
    final = original if final is None else final
    expanded = final.to_selection(input_truncated=False)
    assessment = expanded.legacy_bridge().model_dump(mode="json", include={"evidence", "decision_issue"})
    resolutions = [{"index": index, "source_id": item.source_id} for index, item in enumerate(expanded.evidence)]
    record = {"output": original.model_dump(mode="json"),
        "expanded_output": original.to_selection(input_truncated=False).model_dump(mode="json"),
        "assessment": assessment, "source_resolutions": resolutions}
    history = [record]
    if reviewed:
        history.append({"kind": "decision_review", "decision_review_validated": True,
            "expanded_output": expanded.model_dump(mode="json"),
            "assessment": assessment, "source_resolutions": resolutions})
    return AgentCallResult(output=final, finish_reason="completed", framework_run_id=None,
        agent_fingerprint=None, failure_id=None, error=None,
        telemetry={"evidence_decision_review": {"recovered": reviewed}}, grounding_history=history)


def canonical_for(output, *, role="primary"):
    candidates = {item["source_id"]: item for item in catalog()["items"]}
    return [{"evidence_id": f"e{index + 1}", "field": candidates[item.source_id]["field"],
        "excerpt": candidates[item.source_id]["excerpt"], "interpretation_ko": item.interpretation_ko,
        "supports": item.supports.value,
        "origins": [{"role": role, "index": index, "source_id": item.source_id}]}
        for index, item in enumerate(output.evidence)]


def test_native_memberships_map_to_same_public_source_origins_without_new_fields():
    output = validate(wire())
    call = call_for(output)
    before = deepcopy(call.grounding_history)
    links = build_evidence_relations(call, evidence=canonical_for(output))
    assert links == {"version": "evidence-relations-v1", "groups": [
        {"role": "primary", "representative_evidence_id": "e1", "related_evidence_ids": ["e2"]}]}
    assert call.grounding_history == before
    assert "contributes_to_evidence_ids" not in str(links)


def test_reviewed_removed_root_and_shared_leaf_use_final_reindexed_slots_only():
    original = validate(opposite_roots())
    selection = original.to_selection(input_truncated=False)
    resolutions = [{"index": index, "source_id": item.source_id} for index, item in enumerate(selection.evidence)]
    final, blocked = review_base(original, selection, resolutions, catalog(), {0})
    assert blocked == {0}
    assert [item.evidence_id for item in final.evidence] == ["e5", "e1"]
    call = call_for(original, final, reviewed=True)
    before = deepcopy(call.grounding_history)
    canonical = canonical_for(final)
    links = build_evidence_relations(call, evidence=canonical)
    assert links == {"version": "evidence-relations-v1", "groups": [
        {"role": "primary", "representative_evidence_id": "e2", "related_evidence_ids": ["e1"]}]}
    assert [item["origins"][0]["source_id"] for item in canonical] == ["c1", "c6"]
    assert call.grounding_history == before


def test_missing_final_source_does_not_emit_partial_interpretation_group():
    output = validate(wire())
    with pytest.raises(EvidenceRelationsError):
        build_evidence_relations(call_for(output), evidence=canonical_for(output)[:1])


def test_review_flag_without_validated_final_expansion_cannot_restore_old_links():
    output = validate(wire())
    call = call_for(output)
    call = replace(call, telemetry={"evidence_decision_review": {"recovered": True}})
    with pytest.raises(EvidenceRelationsError):
        build_evidence_relations(call, evidence=canonical_for(output))
