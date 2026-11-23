"""Synthetic, offline checks for the bounded flat model contract."""
from copy import deepcopy

from pydantic import ValidationError
import pytest

from app.agent.contracts_v224 import SecurityAnalysisOutputV224
from app.agent.contracts_v228 import SecurityAnalysisOutputV228
from app.agent.output_schema_v226 import compact_strict_schema
from test_evidence_relations import wire as legacy_wire


def wire():
    value = legacy_wire()
    value.pop("input_truncated")
    value.pop("evidence_relations")
    for key in ("version", "explanation_ko"):
        value["signature_assessment"].pop(key)
    value["evidence"] = [
        {"evidence_id": "e2", "source_id": "c2", "interpretation_ko": "실행 대상을 지정하는 입력 문맥과 명령이 결합되어 서버 처리 변경을 시도합니다.",
         "supports": "attack_request", "related_evidence_ids": ["e5"]},
        {"evidence_id": "e5", "source_id": "c1", "interpretation_ko": "입력값을 실행 대상으로 다루는 위치여서 구문 해석에 필요합니다.",
         "supports": "context", "related_evidence_ids": []},
    ]
    value["capture_assessment"] = {"status": "no_clear_signs", "reason_ko": "수신본에서 뚜렷한 누락 징후는 관찰되지 않았습니다."}
    return value


def catalog():
    return {"version": "evidence-selection-v5", "items": [
        {"source_id": "c1", "field": "payload", "excerpt": "synthetic request"},
        {"source_id": "c2", "field": "payload", "excerpt": "synthetic body"},
        {"source_id": "c3", "field": "signature", "excerpt": "synthetic", "allowed_supports": ["context"]},
        {"source_id": "c4", "field": "waf_action", "excerpt": "D"},
        {"source_id": "c5", "field": "extra_fields.signature_explanation", "excerpt": "synthetic reference"},
        {"source_id": "c6", "field": "extra_fields.request", "excerpt": "synthetic extra request"},
    ]}


def validate(value):
    return SecurityAnalysisOutputV228.model_validate(value, context={"source_catalog": catalog()})


def test_actual_generation_schema_has_one_flat_evidence_list_bounded_to_five():
    schema = compact_strict_schema(SecurityAnalysisOutputV228.model_json_schema())
    for node in [schema, *schema["$defs"].values()]:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
    assert schema["properties"]["evidence"]["maxItems"] == 5
    evidence = schema["$defs"]["FlatEvidenceInterpretation"]
    assert list(evidence["properties"]) == ["evidence_id", "source_id", "interpretation_ko", "supports", "related_evidence_ids"]
    assert evidence["properties"]["evidence_id"]["enum"] == ["e1", "e2", "e3", "e4", "e5"]
    related = evidence["properties"]["related_evidence_ids"]
    assert related["maxItems"] == 4 and related["items"]["type"] == "string"
    assert "related_sources" not in str(schema)
    assert not {"evidence_relations", "input_truncated"} & schema["properties"].keys()
    assert not {"version", "explanation_ko"} & schema["$defs"]["SignatureComparison"]["properties"].keys()


def test_flat_order_and_nonconsecutive_local_ids_map_to_exact_indexes():
    value = wire()
    value["decision_issue"] = {"point_ko": "입력 위치의 역할을 확인했습니다.", "evidence_ids": ["e5", "e2"], "missing_condition_ko": None}
    before = deepcopy(value)
    output = validate(value)
    expanded = output.to_selection(input_truncated=True)
    assert isinstance(expanded, SecurityAnalysisOutputV224)
    assert expanded.evidence_relations[0].model_dump() == {"representative_index": 0, "related_indexes": [1]}
    assert expanded.decision_issue.evidence_indexes == [1, 0]
    assert expanded.signature_assessment.version == "signature-assessment-v2"
    assert expanded.signature_assessment.explanation_ko is None
    assert expanded.input_truncated is True
    assert output.model_dump(mode="json") == before
    for before_item, after_item in zip(before["evidence"], expanded.evidence, strict=True):
        assert after_item.model_dump(mode="json") == {key: before_item[key] for key in ("source_id", "interpretation_ko", "supports")}


def test_same_source_can_retain_two_distinct_interpretations_and_exact_condition_reference():
    value = wire()
    value["evidence"][1].update(source_id="c2", interpretation_ko="같은 구문을 합법적인 기능 입력으로 해석하려면 서비스 용도가 필요합니다.")
    value["decision_issue"] = {"point_ko": "두 번째 해석에 필요한 서비스 용도를 구분했습니다.", "evidence_ids": ["e5"], "missing_condition_ko": None}
    expanded = validate(value).to_selection(input_truncated=False)
    assert [item.source_id for item in expanded.evidence] == ["c2", "c2"]
    assert [item.interpretation_ko for item in expanded.evidence] == [item["interpretation_ko"] for item in value["evidence"]]
    assert expanded.decision_issue.evidence_indexes == [1]


def test_transitive_dependencies_remain_direct_edges_for_existing_closure():
    value = wire()
    value["evidence"][1]["related_evidence_ids"] = ["e1"]
    value["evidence"].append({"evidence_id": "e1", "source_id": "c6", "interpretation_ko": "보조 원문에 값이 전달되는 경로가 있어 문맥을 연결합니다.",
                              "supports": "context", "related_evidence_ids": []})
    _, relations = validate(value).expanded_evidence()
    assert relations == [{"representative_index": 0, "related_indexes": [1]}, {"representative_index": 1, "related_indexes": [2]}]


@pytest.mark.parametrize("mutate, code", [
    (lambda v: v["evidence"][1].update(evidence_id="e2", related_evidence_ids=[]), "evidence_id_repeated"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e1"]), "evidence_link_unknown"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e2"]), "evidence_link_self"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e5", "e5"]), "evidence_link_repeated"),
    (lambda v: v["evidence"][1].update(related_evidence_ids=["e2"]), "evidence_link_cycle"),
    (lambda v: v["evidence"][1].update(supports="normal_request"), "evidence_support_conflict"),
    (lambda v: v["evidence"][0].update(source_id="c999"), "evidence_source_unavailable"),
    (lambda v: v["evidence"][0].update(source_id="c3"), "evidence_source_support_forbidden"),
    (lambda v: v["evidence"][0].update(source_id="c5"), "evidence_source_support_forbidden"),
    (lambda v: v["evidence"][0].update(source_id="c4"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(supports="context"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(interpretation_ko="   "), "evidence_text_required"),
    (lambda v: v.update(decision_issue={"point_ko": "설명", "evidence_ids": ["e1"], "missing_condition_ko": None}), "decision_evidence_unselected"),
    (lambda v: v.update(decision_issue={"point_ko": "설명", "evidence_ids": ["e5", "e5"], "missing_condition_ko": None}), "decision_evidence_repeated"),
])
def test_rejects_checkable_link_source_and_direction_inconsistency(mutate, code):
    value = wire()
    mutate(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert code in {item["type"] for item in failure.value.errors()}


def test_indirect_opposite_support_is_rejected_without_relabeling():
    value = wire()
    value["evidence"][1]["related_evidence_ids"] = ["e1"]
    value["evidence"].append({"evidence_id": "e1", "source_id": "c6", "interpretation_ko": "승인된 기능 호출임을 뒷받침하는 서비스 용도입니다.",
                              "supports": "normal_request", "related_evidence_ids": []})
    before = deepcopy(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert failure.value.errors()[0]["type"] == "evidence_support_conflict"
    assert value == before


def test_opposite_independent_interpretations_can_share_context_without_merging():
    value = wire()
    value.update(verdict="inconclusive", interpretation_conflict=True)
    value["threat_analysis"]["severity"] = "UNKNOWN"
    value["evidence"].append({"evidence_id": "e1", "source_id": "c2", "interpretation_ko": "같은 입력을 정상 기능에 사용하는 서비스 문맥도 고려했습니다.",
                              "supports": "normal_request", "related_evidence_ids": ["e5"]})
    expanded = validate(value).to_selection(input_truncated=False)
    assert expanded.verdict == "inconclusive"
    assert [item.supports.value for item in expanded.evidence] == ["attack_request", "context", "normal_request"]
    assert [item.related_indexes for item in expanded.evidence_relations] == [[1], [1]]


def test_transitive_cycle_is_rejected():
    value = wire()
    value["evidence"][1]["related_evidence_ids"] = ["e1"]
    value["evidence"].append({"evidence_id": "e1", "source_id": "c6", "interpretation_ko": "보조 원문의 입력 위치입니다.",
                              "supports": "context", "related_evidence_ids": ["e2"]})
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert failure.value.errors()[0]["type"] == "evidence_link_cycle"


@pytest.mark.parametrize("path, extra", [
    ((), {"input_truncated": False}), ((), {"evidence_relations": []}),
    (("evidence", 0), {"related_sources": []}), (("evidence", 0), {"contribution_ko": "추가 설명"}),
    (("signature_assessment",), {"version": "signature-assessment-v2"}),
])
def test_undeclared_fields_are_rejected(path, extra):
    value = wire()
    target = value
    for key in path:
        target = target[key]
    target.update(extra)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert "extra_forbidden" in {item["type"] for item in failure.value.errors()}


def test_six_model_items_are_rejected_before_expansion():
    value = wire()
    value["evidence"] *= 3
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert failure.value.errors()[0]["type"] == "too_long"


def test_bridge_drops_local_ids_without_changing_public_legacy_fields():
    value = wire()
    output = validate(value)
    expanded = output.to_selection(input_truncated=True)
    result = expanded.legacy_bridge().model_dump(mode="json")
    assert not {"evidence_relations", "capture_assessment", "interpretation_conflict"} & result.keys()
    assert all(not {"evidence_id", "related_evidence_ids"} & item.keys() for item in result["evidence"])
    assert result["input_truncated"] is True
    assert result["summary_ko"] == value["summary_ko"]
    assert result["threat_analysis"] == value["threat_analysis"]
    assert result["evidence"][0]["supports"] == "true_positive"


@pytest.mark.parametrize("mutate, code", [
    (lambda v: v["threat_analysis"].update(severity="NONE"), "true_positive_requires_threat_severity"),
    (lambda v: v.update(summary_ko="정탐입니다. 요청에서 입력 구문을 확인했습니다."), "summary_must_describe_request"),
    (lambda v: v.update(decision_issue={"point_ko": "설명", "evidence_ids": ["e2"], "missing_condition_ko": "필수 확인 조건"}), "decisive_verdict_disallows_missing_condition"),
])
def test_established_verdict_summary_and_condition_invariants_remain(mutate, code):
    value = wire()
    mutate(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert code in {item["type"] for item in failure.value.errors()}
