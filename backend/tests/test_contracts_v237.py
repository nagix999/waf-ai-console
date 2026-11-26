"""Synthetic root/contribution wire, strict old-v3 bridge and source roles."""
from copy import deepcopy

from pydantic import ValidationError
import pytest

from app.agent.contracts_v237 import (
    SecurityAnalysisOutputV237, normalize_contribution_references,
    CONTRIBUTION_REFERENCE_DUPLICATES, DECISION_REFERENCE_DUPLICATES,
)
from app.agent.contracts_v234 import SecurityAnalysisOutputV234
from app.agent.output_schema_v226 import compact_strict_schema
from test_flat_evidence_v228 import catalog, wire as previous_wire


def wire():
    value = previous_wire()
    value["evidence"][0].pop("related_evidence_ids")
    value["evidence"][0]["contributes_to_evidence_ids"] = []
    value["evidence"][1].pop("related_evidence_ids")
    value["evidence"][1]["contributes_to_evidence_ids"] = ["e2"]
    return value


def validate(value, **context):
    return SecurityAnalysisOutputV237.model_validate(value, context={"source_catalog": catalog(), **context})


def opposite_roots():
    value = wire()
    value["evidence"][1]["contributes_to_evidence_ids"].append("e1")
    value["evidence"].append({"evidence_id": "e1", "source_id": "c6", "supports": "normal_request",
        "interpretation_ko": "같은 보조 관측을 독립적인 정상 기능의 사용 문맥으로 해석합니다.",
        "contributes_to_evidence_ids": []})
    value.update(verdict="inconclusive", interpretation_conflict=True)
    value["threat_analysis"]["severity"] = "UNKNOWN"
    return value


def test_actual_closed_generation_schema_only_replaces_one_required_field():
    schema = compact_strict_schema(SecurityAnalysisOutputV237.model_json_schema())
    assert schema["properties"]["evidence"]["maxItems"] == 5
    item = schema["$defs"]["FlatEvidenceInterpretation"]
    assert list(item["properties"]) == ["evidence_id", "source_id", "interpretation_ko", "supports", "contributes_to_evidence_ids"]
    assert item["properties"]["contributes_to_evidence_ids"]["maxItems"] == 4
    assert "related_evidence_ids" not in str(schema)
    assert "related_sources" not in str(schema)
    for node in (schema, *schema["$defs"].values()):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
    for mutate in (lambda value: value["evidence"][0].pop("contributes_to_evidence_ids"),
                   lambda value: value["evidence"][0].update(related_evidence_ids=[])):
        value = wire()
        mutate(value)
        with pytest.raises(ValidationError):
            validate(value)


def test_bridge_preserves_every_slot_identity_text_support_and_condition_reference():
    value = wire()
    value["decision_issue"] = {"point_ko": "입력 위치와 값을 함께 검토했습니다.",
        "evidence_ids": ["e5", "e2"], "missing_condition_ko": None}
    before = deepcopy(value)
    output = validate(value)
    legacy = output.to_interpretations()
    assert isinstance(legacy, SecurityAnalysisOutputV234)
    assert legacy.evidence[0].related_evidence_ids == ["e5"]
    assert legacy.evidence[1].related_evidence_ids == []
    expanded = output.to_selection(input_truncated=True)
    assert expanded.evidence_relations[0].model_dump() == {"representative_index": 0, "related_indexes": [1]}
    assert expanded.decision_issue.evidence_indexes == [1, 0]
    assert expanded.input_truncated is True
    assert output.model_dump(mode="json") == value == before
    for original, selected in zip(value["evidence"], expanded.evidence, strict=True):
        assert selected.model_dump(mode="json") == {key: original[key] for key in ("source_id", "interpretation_ko", "supports")}


def test_contribution_may_precede_root_without_reordering_or_creating_evidence():
    value = wire()
    value["evidence"].reverse()
    output = validate(value)
    evidence, relations = output.expanded_evidence()
    assert [item["source_id"] for item in evidence] == ["c1", "c2"]
    assert relations == [{"representative_index": 1, "related_indexes": [0]}]
    assert output.model_dump(mode="json") == value


def test_opposite_independent_roots_can_share_one_neutral_contribution():
    value = opposite_roots()
    output = validate(value)
    _, relations = output.expanded_evidence()
    assert relations == [{"representative_index": 0, "related_indexes": [1]},
                         {"representative_index": 2, "related_indexes": [1]}]
    assert output.evidence[0].supports == "attack_request"
    assert output.evidence[2].supports == "normal_request"
    assert output.evidence[1].contributes_to_evidence_ids == ["e2", "e1"]
    assert output.model_dump(mode="json") == value


def test_context_roots_and_context_only_inconclusive_outputs_are_not_forced_directional():
    value = wire()
    value["evidence"] = [value["evidence"][1]]
    value["evidence"][0].update(source_id="c3", contributes_to_evidence_ids=[])
    value.update(verdict="inconclusive", decision_issue={"point_ko": "실제 요청과 탐지 설명의 관계를 확인해야 합니다.",
        "evidence_ids": ["e5"], "missing_condition_ko": "요청의 실제 사용 맥락이 필요합니다."})
    value["threat_analysis"]["severity"] = "UNKNOWN"
    output = validate(value)
    assert output.evidence[0].supports == "context"
    assert output.expanded_evidence()[1] == []


@pytest.mark.parametrize("mutate,code", [
    (lambda v: v["evidence"][1].update(supports="attack_request"), "evidence_contribution_support_invalid"),
    (lambda v: v["evidence"][1].update(supports="normal_request"), "evidence_contribution_support_invalid"),
    (lambda v: v["evidence"][1].update(contributes_to_evidence_ids=["e1"]), "evidence_link_unknown"),
    (lambda v: v["evidence"][1].update(contributes_to_evidence_ids=["e5"]), "evidence_link_self"),
    (lambda v: v["evidence"][1].update(evidence_id="e2", contributes_to_evidence_ids=[]), "evidence_id_repeated"),
    (lambda v: v["evidence"][0].update(source_id="c999"), "evidence_source_unavailable"),
    (lambda v: v["evidence"][0].update(source_id="c3"), "evidence_source_support_forbidden"),
    (lambda v: v["evidence"][0].update(source_id="c4"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(supports="context"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(interpretation_ko="  "), "evidence_text_required"),
    (lambda v: v.update(decision_issue={"point_ko": "구분 조건", "evidence_ids": ["e1"], "missing_condition_ko": None}), "decision_evidence_unselected"),
])
def test_invalid_contributions_and_existing_source_verdict_guards_still_fail(mutate, code):
    value = wire()
    mutate(value)
    before = deepcopy(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert code in {issue["type"] for issue in failure.value.errors()}
    assert value == before


def test_leaf_to_leaf_and_context_cycles_are_not_allowed_or_silently_rewritten():
    value = wire()
    value["evidence"].append({"evidence_id": "e1", "source_id": "c6", "supports": "context",
        "interpretation_ko": "다른 입력 위치를 연결하는 보조 관측입니다.", "contributes_to_evidence_ids": ["e5"]})
    for cycle in (False, True):
        candidate = deepcopy(value)
        if cycle:
            candidate["evidence"][1]["contributes_to_evidence_ids"] = ["e1"]
        with pytest.raises(ValidationError) as failure:
            validate(candidate)
        assert failure.value.errors()[0]["type"] == "evidence_contribution_target_not_root"


def test_known_identical_references_only_are_deduplicated_without_demoting_or_deleting():
    value = wire()
    value["evidence"][1]["contributes_to_evidence_ids"] = ["e2", "e2"]
    value["decision_issue"] = {"point_ko": "원문을 확인했습니다.", "evidence_ids": ["e5", "e5"], "missing_condition_ko": None}
    before = deepcopy(value)
    document, codes = normalize_contribution_references(value)
    assert set(codes) == {CONTRIBUTION_REFERENCE_DUPLICATES, DECISION_REFERENCE_DUPLICATES}
    assert document["evidence"][1]["contributes_to_evidence_ids"] == ["e2"]
    assert document["decision_issue"]["evidence_ids"] == ["e5"]
    assert value == before
    assert validate(value).model_dump(mode="json") == document
    invalid = deepcopy(value)
    invalid["evidence"][1]["contributes_to_evidence_ids"] = ["e1", "e1"]
    assert normalize_contribution_references(invalid) == (invalid, ())


def test_source_role_deferral_is_only_an_explicit_temporary_grounding_context():
    value = wire()
    value["evidence"][0]["source_id"] = "c3"
    with pytest.raises(ValidationError):
        validate(value)
    output = validate(value, defer_source_support_validation=True)
    assert output.evidence[0].source_id == "c3"
    assert output.evidence[0].supports == "attack_request"


def test_repeated_source_can_keep_independent_directions_without_auto_merging():
    value = opposite_roots()
    value["evidence"][2]["source_id"] = "c2"
    output = validate(value)
    assert [item.source_id for item in output.evidence] == ["c2", "c1", "c2"]
    assert output.evidence[2].supports == "normal_request"


def test_total_output_is_still_five_slots_not_five_groups_expanded_into_more_sources():
    value = wire()
    for key in ("e1", "e3", "e4"):
        value["evidence"].append({"evidence_id": key, "source_id": "c1", "supports": "context",
            "interpretation_ko": "결합 해석에 필요한 별도 원문의 기여입니다.", "contributes_to_evidence_ids": ["e2"]})
    output = validate(value)
    assert len(output.to_selection(input_truncated=False).evidence) == 5
    assert len(output.expanded_evidence()[1][0]["related_indexes"]) == 4
    value["evidence"].append(deepcopy(value["evidence"][-1]))
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert failure.value.errors()[0]["type"] == "too_long"


def test_structural_guard_does_not_claim_to_classify_meaning_from_words():
    value = wire()
    value["evidence"][0]["interpretation_ko"] = "헤더에 환경 정보가 관찰됩니다."
    assert validate(value).evidence[0].supports == "attack_request"
