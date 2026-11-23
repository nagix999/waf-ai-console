"""Synthetic-only cycle consumers preserve complete grounded evidence groups."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app import worker_v229 as worker
from app.agent.evidence_relations import (
    EvidenceRelationsError, assert_relation_sources, build_evidence_relations,
    validate_evidence_relations,
)
from app.result_contracts import validate_new_result
from app.services.core_evidence_v3 import CoreEvidenceError, build_core_result
from app.services.evidence_display_plan import build_display_plan, validate_display_plan
from test_core_evidence_v3 import assert_raw_unchanged, dense_result, issue
from test_evidence_relations import call, evidence


def cycle_relations(identifiers, *, role="primary"):
    return {"version": "evidence-relations-v1", "groups": [
        {"role": role, "representative_evidence_id": identifier,
         "related_evidence_ids": [identifiers[(index + 1) % len(identifiers)]]}
        for index, identifier in enumerate(identifiers)
    ]}


@pytest.mark.parametrize("count", [2, 3, 5])
def test_core_and_display_keep_every_required_member_of_cycle(count):
    original = dense_result(count, verdict="true_positive",
                            supports=["attack_request", *(["context"] * (count - 1))])
    identifiers = [item["evidence_id"] for item in original["evidence"]]
    links = cycle_relations(identifiers)
    before = deepcopy(original)

    plan = build_display_plan(original, links)
    assert validate_display_plan(original, links, plan) == plan
    for group in plan["groups"]:
        assert set(group["related_ids"]) == set(identifiers) - {group["representative_id"]}
        assert group["representative_id"] not in group["related_ids"]
    view = {"version": "evidence-display-view-v1", "status": plan["status"],
            "relations": links, "plan": plan, "metadata": {}}

    for display_view in (None, view, {"status": "fallback"}):
        projected = build_core_result(original, relations=links, display_view=display_view)["result"]
        assert {item["evidence_id"] for item in projected["evidence"]} == set(identifiers)
        assert len(projected["evidence"]) == count  # Required sources beat the soft target of three.
        assert projected["verdict"] == "true_positive"
        assert_raw_unchanged(original, projected)
    assert original == before


def test_two_role_cycles_keep_distinct_origins_and_reused_local_source_ids():
    original = dense_result(10, verdict="true_positive", supports=[
        "attack_request", "context", "context", "context", "context",
        "attack_request", "context", "context", "context", "context",
    ])
    # Each role can independently select c1..c5 without sharing model outputs.
    for index, item in enumerate(original["evidence"][5:]):
        item["origins"][0]["source_id"] = f"c{index + 1}"
    original["decision_issues"] = [issue("e1"), issue("e6", origins=["verifier"])]
    original = validate_new_result(original)
    primary = cycle_relations([f"e{index}" for index in range(1, 6)])
    verifier = cycle_relations([f"e{index}" for index in range(6, 11)], role="verifier")
    links = {"version": primary["version"], "groups": [*primary["groups"], *verifier["groups"]]}
    before = deepcopy(original)

    plan = build_display_plan(original, links)
    projected = build_core_result(original, relations=links)["result"]
    assert len(projected["evidence"]) == 10
    assert_raw_unchanged(original, projected)
    for group in plan["groups"]:
        related_roles = {origin["role"] for item in original["evidence"]
                         if item["evidence_id"] in group["related_ids"] for origin in item["origins"]}
        representative_role = next(item["origins"][0]["role"] for item in original["evidence"]
                                   if item["evidence_id"] == group["representative_id"])
        assert related_roles == {representative_role}
    assert original == before


def resolved_cycle(count, missing_index=None):
    relations = [{"representative_index": index, "related_indexes": [(index + 1) % count]}
                 for index in range(count)]
    selection = SimpleNamespace(evidence=[SimpleNamespace(supports="context") for _ in range(count)])
    resolutions = [{"index": index, "source_id": f"c{index + 1}"}
                   for index in range(count) if index != missing_index]
    output = SimpleNamespace(evidence=[SimpleNamespace(field="payload", excerpt=f"synthetic-{item['index']}",
                                                      interpretation_ko=f"원문 {item['index']}의 역할입니다.")
                                       for item in resolutions])
    return relations, selection, resolutions, output


@pytest.mark.parametrize("count", [2, 3, 5])
def test_valid_resolved_cycle_does_not_exclude_any_representative(count):
    assert worker._invalid_relation_representatives(*resolved_cycle(count)) == set()


@pytest.mark.parametrize("missing_index", range(5))
def test_missing_source_propagates_through_entire_cycle(missing_index):
    assert worker._invalid_relation_representatives(*resolved_cycle(5, missing_index)) == set(range(5))


def test_source_loss_invalidates_dependent_cycle_without_removing_independent_evidence():
    relations, selection, resolutions, output = resolved_cycle(5, missing_index=2)
    # e5 is independent, so the four-member cycle must not cause its rejection.
    relations = [{"representative_index": index, "related_indexes": [(index + 1) % 4]}
                 for index in range(4)]
    assert worker._invalid_relation_representatives(relations, selection, resolutions, output) == {0, 1, 2, 3}


def cyclic_call():
    source = call()
    source.grounding_history[0]["output"]["evidence_relations"].append(
        {"representative_index": 0, "related_indexes": [1]})
    return source


def test_final_origin_mapping_keeps_both_roles_cycles_without_mutating_history():
    primary, verifier = cyclic_call(), cyclic_call()
    canonical = [*evidence(), *evidence("verifier", 2)]
    before = deepcopy((primary.grounding_history, verifier.grounding_history, canonical))
    links = build_evidence_relations(primary, verifier, evidence=canonical)
    assert links["groups"] == [
        {"role": "primary", "representative_evidence_id": "e2", "related_evidence_ids": ["e1"]},
        {"role": "primary", "representative_evidence_id": "e1", "related_evidence_ids": ["e2"]},
        {"role": "verifier", "representative_evidence_id": "e4", "related_evidence_ids": ["e3"]},
        {"role": "verifier", "representative_evidence_id": "e3", "related_evidence_ids": ["e4"]},
    ]
    assert (primary.grounding_history, verifier.grounding_history, canonical) == before


@pytest.mark.parametrize("mutation, code", [
    (lambda history: history["source_resolutions"].pop(), "evidence_relation_source_missing"),
    (lambda history: history["source_resolutions"][0].update(source_id="c3"), "evidence_relation_source_mismatch"),
])
def test_cycle_does_not_bypass_final_source_validation(mutation, code):
    source = cyclic_call()
    history = source.grounding_history[-1]
    mutation(history)
    with pytest.raises(EvidenceRelationsError, match=f"^{code}$"):
        assert_relation_sources(history["assessment"], history["source_resolutions"],
                                relations=history["output"]["evidence_relations"])


def test_missing_canonical_dependency_cannot_be_resurrected_by_cyclic_origin_mapping():
    canonical = evidence()[:1]
    with pytest.raises(EvidenceRelationsError, match="^evidence_relation_source_missing$"):
        build_evidence_relations(cyclic_call(), evidence=canonical)


def test_cross_role_cycle_remains_invalid_even_when_source_ids_match():
    canonical = [*evidence(), *evidence("verifier", 2)]
    links = cycle_relations(["e1", "e3"])
    with pytest.raises(EvidenceRelationsError, match="^evidence_relation_role_mismatch$"):
        validate_evidence_relations(links, canonical)


def test_core_rejects_missing_dependency_instead_of_exposing_partial_cycle():
    original = dense_result(2, verdict="true_positive", supports=["attack_request", "context"])
    links = cycle_relations(["e1", "e2", "e3"])
    with pytest.raises(CoreEvidenceError, match="^core_evidence_source_invalid$"):
        build_core_result(original, relations=links)
