"""Synthetic bounded reference normalization and cycle-preserving traversal."""
from copy import deepcopy

from pydantic import ValidationError
import pytest

from app.agent.contracts_v228 import SecurityAnalysisOutputV228
from app.agent.contracts_v229 import (
    DECISION_REFERENCE_DUPLICATES, NORMALIZATION_CODES, REFERENCE_NORMALIZATION_VERSION,
    RELATED_REFERENCE_DUPLICATES, SecurityAnalysisOutputV229, normalize_duplicate_references,
)
from app.agent.output_schema_v226 import compact_strict_schema
from test_flat_evidence_v228 import catalog, wire


def validate(value):
    return SecurityAnalysisOutputV229.model_validate(value, context={"source_catalog": catalog()})


def cycle(size):
    value = wire()
    value["evidence"] = [{
        "evidence_id": f"e{index + 1}", "source_id": "c2",
        "interpretation_ko": f"관측 {index + 1}의 요청 문맥을 연결해 공격 해석을 뒷받침합니다.",
        "supports": "attack_request" if index == 0 else "context",
        "related_evidence_ids": [f"e{(index + 1) % size + 1}"],
    } for index in range(size)]
    return value


@pytest.mark.parametrize("size", [2, 3, 5])
def test_cycles_preserve_every_original_direct_edge_and_evidence(size):
    value = cycle(size)
    before = deepcopy(value)
    output = validate(value)
    assert output.model_dump(mode="json") == before
    assert value == before
    selected = output.to_selection(input_truncated=True)
    assert len(selected.evidence) == size
    assert [relation.model_dump() for relation in selected.evidence_relations] == [
        {"representative_index": index, "related_indexes": [(index + 1) % size]}
        for index in range(size)
    ]
    assert selected.input_truncated is True


def test_frozen_v228_still_rejects_the_same_cycle():
    with pytest.raises(ValidationError) as failure:
        SecurityAnalysisOutputV228.model_validate(cycle(2))
    assert failure.value.errors()[0]["type"] == "evidence_link_cycle"


def test_transitive_dependencies_and_cycle_are_not_collapsed_into_new_edges():
    value = cycle(3)
    value["evidence"][0]["related_evidence_ids"] = ["e3", "e2"]
    value["decision_issue"] = {"point_ko": "문맥을 확인한 원문 위치를 연결했습니다.", "evidence_ids": ["e3", "e1"], "missing_condition_ko": None}
    output = validate(value)
    evidence, links = output.expanded_evidence()
    assert len(evidence) == 3
    assert links == [{"representative_index": 0, "related_indexes": [2, 1]},
                     {"representative_index": 1, "related_indexes": [2]},
                     {"representative_index": 2, "related_indexes": [0]}]
    assert output.to_selection(input_truncated=False).decision_issue.evidence_indexes == [2, 0]


def test_independent_opposite_interpretations_may_share_a_context_cycle():
    value = cycle(4)
    value.update(verdict="inconclusive", interpretation_conflict=True)
    value["threat_analysis"]["severity"] = "UNKNOWN"
    value["evidence"][0].update(related_evidence_ids=["e3"])
    value["evidence"][1].update(supports="normal_request", related_evidence_ids=["e3"])
    value["evidence"][2].update(related_evidence_ids=["e4"])
    value["evidence"][3].update(related_evidence_ids=["e3"])
    assert validate(value).model_dump(mode="json") == value


@pytest.mark.parametrize("size", [2, 3, 5])
def test_opposite_support_in_a_reachable_cycle_is_not_reclassified_or_ignored(size):
    value = cycle(size)
    value["evidence"][-1]["supports"] = "normal_request"
    before = deepcopy(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert failure.value.errors()[0]["type"] == "evidence_support_conflict"
    assert value == before


def test_exact_duplicate_references_are_deduplicated_in_first_occurrence_order_only():
    value = cycle(3)
    value["evidence"][0]["related_evidence_ids"] = ["e3", "e2", "e3", "e2"]
    value["decision_issue"] = {"point_ko": "판단에 사용한 관측을 연결했습니다.",
                               "evidence_ids": ["e3", "e1", "e3", "e2", "e1"], "missing_condition_ko": None}
    before = deepcopy(value)
    normalized, codes = normalize_duplicate_references(value)
    assert codes == (RELATED_REFERENCE_DUPLICATES, DECISION_REFERENCE_DUPLICATES)
    assert set(codes) == NORMALIZATION_CODES
    assert REFERENCE_NORMALIZATION_VERSION == "evidence-reference-normalization-v1"
    assert normalized is not value and normalized["evidence"] is not value["evidence"]
    expected = deepcopy(value)
    expected["evidence"][0]["related_evidence_ids"] = ["e3", "e2"]
    expected["decision_issue"]["evidence_ids"] = ["e3", "e1", "e2"]
    assert normalized == expected
    assert value == before
    assert validate(value).model_dump(mode="json") == expected
    assert len(normalized["evidence"]) == len(value["evidence"])
    assert all(normalized[key] == value[key] for key in value.keys() - {"evidence", "decision_issue"})


def test_unchanged_input_retains_identity_and_normalization_is_idempotent():
    value = cycle(5)
    normalized, codes = normalize_duplicate_references(value)
    assert normalized is value and codes == ()
    value["evidence"][0]["related_evidence_ids"] *= 2
    normalized, codes = normalize_duplicate_references(value)
    again, repeated_codes = normalize_duplicate_references(normalized)
    assert codes == (RELATED_REFERENCE_DUPLICATES,)
    assert again is normalized and repeated_codes == ()


@pytest.mark.parametrize("mutate, expected", [
    (lambda v: v["evidence"][1].update(evidence_id="e2"), "evidence_id_repeated"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e1"]), "evidence_link_unknown"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e1", "e1"]), "evidence_link_repeated"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e2"]), "evidence_link_self"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e5"] * 5), "too_long"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e6"]), "literal_error"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=[1]), "literal_error"),
    (lambda v: v["evidence"][0].update(related_evidence_ids="e5"), "list_type"),
    (lambda v: v["evidence"][0].pop("related_evidence_ids"), "missing"),
    (lambda v: v["evidence"][0].update(evidence_id=" e2"), "literal_error"),
    (lambda v: v.update(decision_issue={"point_ko": "판단 조건입니다.", "evidence_ids": ["e1"], "missing_condition_ko": None}), "decision_evidence_unselected"),
    (lambda v: v.update(decision_issue={"point_ko": "판단 조건입니다.", "evidence_ids": ["e5"] * 6, "missing_condition_ko": None}), "too_long"),
    (lambda v: v.update(evidence=v["evidence"] * 3), "too_long"),
])
def test_unsafe_or_raw_oversize_shapes_are_not_repaired(mutate, expected):
    value = wire()
    mutate(value)
    before = deepcopy(value)
    normalized, codes = normalize_duplicate_references(value)
    assert normalized is value and codes == ()
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert expected in {issue["type"] for issue in failure.value.errors()}
    assert value == before


@pytest.mark.parametrize("source, expected", [
    ("c999", "evidence_source_unavailable"),
    ("c3", "evidence_source_support_forbidden"),
    ("c5", "evidence_source_support_forbidden"),
    ("c4", "decisive_verdict_requires_directional_content"),
])
def test_reference_normalization_never_bypasses_source_or_direction_validation(source, expected):
    value = wire()
    value["evidence"][0].update(source_id=source, related_evidence_ids=["e5", "e5"])
    before = deepcopy(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert expected in {issue["type"] for issue in failure.value.errors()}
    assert value == before


def test_shared_source_keeps_distinct_interpretations_and_exact_condition_references():
    value = wire()
    value["evidence"][1]["source_id"] = "c2"
    value["decision_issue"] = {"point_ko": "두 번째 해석에 연결한 조건입니다.", "evidence_ids": ["e5", "e5"], "missing_condition_ko": None}
    selected = validate(value).to_selection(input_truncated=False)
    assert [entry.source_id for entry in selected.evidence] == ["c2", "c2"]
    assert [entry.interpretation_ko for entry in selected.evidence] == [entry["interpretation_ko"] for entry in value["evidence"]]
    assert selected.decision_issue.evidence_indexes == [1]


def test_generation_schema_has_the_same_wire_keys_and_cardinality_as_frozen_v228():
    current = compact_strict_schema(SecurityAnalysisOutputV229.model_json_schema())
    previous = compact_strict_schema(SecurityAnalysisOutputV228.model_json_schema())
    current.pop("title")
    previous.pop("title")
    assert current == previous
