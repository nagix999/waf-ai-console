"""Synthetic, offline evidence links: source validity is not semantic proof."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent.contracts import SecurityAnalysisOutputV216, SecurityAnalysisOutputV220, StrictContract
from app.agent.evidence_relations import (
    VERSION, EvidenceRelations, EvidenceRelationsError, assert_relation_sources,
    build_evidence_relations, validate_evidence_relations,
)
from app.services.provider_options import strict_json_schema


def wire():
    return {
        "signature_assessment": {"version": "signature-assessment-v2", "relation": "unknown",
            "matched_points": [], "mismatched_points": [], "uncertainty_ko": "구체적인 탐지 설명이 없습니다.", "explanation_ko": None},
        "evidence": [
            {"source_id": "c1", "supports": "context", "interpretation_ko": "입력값이 들어간 요청 위치입니다."},
            {"source_id": "c2", "supports": "attack_request", "interpretation_ko": "요청 위치와 입력 구문을 함께 해석합니다."},
        ],
        "evidence_relations": [{"representative_index": 1, "related_indexes": [0]}],
        "interpretation_conflict": False, "input_truncated": False,
        "summary_ko": "입력 구문과 요청의 사용 문맥을 비교했습니다.", "verdict": "true_positive", "confidence_score": .8,
        "threat_analysis": {"severity": "HIGH", "category": "synthetic", "target": "payload",
            "technique_ko": "입력 구문과 문맥을 연결해 해석했습니다.", "obfuscations": [], "potential_impact_ko": "실제 실행 여부는 확인되지 않았습니다."},
        "decision_issue": None,
    }


def evidence(role="primary", prefix=0):
    return [
        {"evidence_id": f"e{prefix + 1}", "supports": "context", "field": "payload", "excerpt": "synthetic request",
         "interpretation_ko": "입력값이 들어간 요청 위치입니다.", "origins": [{"role": role, "index": 0, "source_id": "c1"}]},
        {"evidence_id": f"e{prefix + 2}", "supports": "attack_request", "field": "payload", "excerpt": "synthetic body",
         "interpretation_ko": "요청 위치와 입력 구문을 함께 해석합니다.", "origins": [{"role": role, "index": 1, "source_id": "c2"}]},
    ]


def links(role="primary", prefix=0):
    return {"version": VERSION, "groups": [{"role": role, "representative_evidence_id": f"e{prefix + 2}",
        "related_evidence_ids": [f"e{prefix + 1}"]}]}


def call():
    raw = wire()
    final = SecurityAnalysisOutputV220.model_validate(raw).legacy_bridge().model_dump(mode="json")
    return SimpleNamespace(succeeded=True, telemetry={}, grounding_history=[{
        "output": raw, "assessment": final,
        "source_resolutions": [{"index": 0, "source_id": "c1"}, {"index": 1, "source_id": "c2"}],
    }])


def test_new_wire_is_independent_and_preserves_legacy_bridge_and_order():
    data = wire()
    model = SecurityAnalysisOutputV220.model_validate(data)
    assert issubclass(SecurityAnalysisOutputV220, StrictContract)
    assert not issubclass(SecurityAnalysisOutputV220, SecurityAnalysisOutputV216)
    old = {key: value for key, value in data.items() if key != "evidence_relations"}
    assert model.legacy_bridge() == SecurityAnalysisOutputV216.model_validate(old).legacy_bridge()
    assert "evidence_relations" not in model.legacy_bridge().model_dump()
    assert model.model_dump(mode="json") == data
    old_schema = SecurityAnalysisOutputV216.model_json_schema()
    new_schema = strict_json_schema(SecurityAnalysisOutputV220.model_json_schema())
    assert [key for key in new_schema["properties"] if key != "evidence_relations"] == list(old_schema["properties"])
    assert "evidence_relations" not in old_schema["properties"]
    assert new_schema["required"] == list(new_schema["properties"])
    assert new_schema["additionalProperties"] is False


def test_empty_relations_are_required_but_valid_and_do_not_require_more_evidence():
    data = wire()
    data["evidence_relations"] = []
    data["evidence"] = data["evidence"][1:]
    assert SecurityAnalysisOutputV220.model_validate(data).evidence_relations == []
    del data["evidence_relations"]
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV220.model_validate(data)


@pytest.mark.parametrize("relations", [
    [{"representative_index": 1, "related_indexes": [1]}],
    [{"representative_index": 1, "related_indexes": [0, 0]}],
    [{"representative_index": 2, "related_indexes": [0]}],
    [{"representative_index": 1, "related_indexes": [2]}],
    [{"representative_index": 1, "related_indexes": []}],
    [{"representative_index": True, "related_indexes": [0]}],
    [{"representative_index": 1, "related_indexes": [False]}],
    [{"representative_index": "1", "related_indexes": [0]}],
    [{"representative_index": 1, "related_indexes": [0], "interpretation_ko": "별도 설명 금지"}],
    [{"representative_index": 1, "related_indexes": [0]}] * 2,
])
def test_wire_rejects_invalid_connections(relations):
    data = wire()
    data["evidence_relations"] = relations
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV220.model_validate(data)


def test_wire_does_not_merge_opposite_directions_or_infer_from_verdict():
    data = wire()
    data["evidence"][0]["supports"] = "normal_request"
    with pytest.raises(ValidationError) as failure:
        SecurityAnalysisOutputV220.model_validate(data)
    assert failure.value.errors()[0]["type"] == "evidence_relation_conflicting_supports"
    data["evidence"][1]["supports"] = "normal_request"
    assert SecurityAnalysisOutputV220.model_validate(data).verdict.value == "true_positive"


def test_original_sources_and_canonical_explanations_are_not_modified():
    source, canonical = call(), evidence()
    before_history, before_evidence = deepcopy(source.grounding_history), deepcopy(canonical)
    assert build_evidence_relations(source, evidence=canonical) == links()
    assert source.grounding_history == before_history and canonical == before_evidence
    assert EvidenceRelations.model_validate(links()).model_dump(mode="json") == links()


def test_two_roles_keep_separate_links_even_with_identical_raw_source_ids():
    assert build_evidence_relations(call(), call(), evidence=[*evidence(), *evidence("verifier", 2)]) == {
        "version": VERSION, "groups": [*links()["groups"], *links("verifier", 2)["groups"]],
    }


def test_citation_repair_uses_final_source_id_and_original_relation_indexes():
    source, canonical = call(), evidence()
    source.grounding_history[0]["output"]["evidence"][0]["source_id"] = "c999"
    repaired = deepcopy(source.grounding_history[-1])
    repaired.pop("output")
    source.grounding_history.append(repaired)
    assert build_evidence_relations(source, evidence=canonical) == links()


@pytest.mark.parametrize("mutation,code", [
    (lambda history: history["source_resolutions"].pop(), "evidence_relation_source_missing"),
    (lambda history: history["source_resolutions"][0].update(source_id="c3"), "evidence_relation_source_mismatch"),
    (lambda history: history["source_resolutions"].append({"index": 0, "source_id": "c1"}), "evidence_relations_invalid"),
])
def test_partial_or_mismatched_source_resolution_never_keeps_integrated_claim(mutation, code):
    source = call()
    mutation(source.grounding_history[-1])
    with pytest.raises(EvidenceRelationsError) as failure:
        build_evidence_relations(source, evidence=evidence())
    assert failure.value.code == code and str(failure.value) == code


@pytest.mark.parametrize("mutation", [
    lambda items: items.pop(),
    lambda items: items[0]["origins"][0].update(source_id="c3"),
    lambda items: items[0]["origins"][0].update(role="verifier"),
    lambda items: items[0]["origins"][0].update(index=2),
])
def test_mapping_requires_exact_role_index_and_corrected_source_id(mutation):
    canonical = evidence()
    mutation(canonical)
    with pytest.raises(EvidenceRelationsError):
        build_evidence_relations(call(), evidence=canonical)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(version="future"),
    lambda value: value.update(explanation_ko="새 설명 금지"),
    lambda value: value["groups"][0].update(role="other"),
    lambda value: value["groups"][0].update(supports="attack_request"),
    lambda value: value["groups"][0].update(related_evidence_ids=["e2"]),
    lambda value: value["groups"][0].update(related_evidence_ids=["e1", "e1"]),
    lambda value: value["groups"][0].update(related_evidence_ids=["e999"]),
    lambda value: value["groups"].append(deepcopy(value["groups"][0])),
])
def test_admin_record_rejects_unknown_fields_invalid_ids_self_or_duplicate_links(mutation):
    value = links()
    mutation(value)
    with pytest.raises(EvidenceRelationsError):
        validate_evidence_relations(value, evidence())


def test_admin_record_checks_source_role_direction_and_origin_uniqueness():
    for mutation in (
        lambda items: items[0].update(supports="normal_request"),
        lambda items: items[0]["origins"][0].update(role="verifier"),
        lambda items: items[0]["origins"].append(deepcopy(items[0]["origins"][0])),
    ):
        canonical = evidence()
        mutation(canonical)
        with pytest.raises(EvidenceRelationsError):
            validate_evidence_relations(links(), canonical)


def test_missing_canonical_source_does_not_return_partial_groups():
    with pytest.raises(EvidenceRelationsError) as failure:
        validate_evidence_relations(links(), evidence()[:1])
    assert failure.value.code == "evidence_relation_source_missing"


def test_identical_evidence_shared_by_roles_keeps_both_role_links():
    canonical = evidence()
    for item in canonical:
        item["origins"].append({**item["origins"][0], "role": "verifier"})
    expected = links()
    expected["groups"].extend(links("verifier")["groups"])
    assert build_evidence_relations(call(), call(), evidence=canonical) == expected


def test_canonical_deduplication_cannot_create_a_self_link():
    canonical = evidence()
    canonical[0]["origins"].extend(canonical[1]["origins"])
    canonical.pop()
    with pytest.raises(EvidenceRelationsError):
        build_evidence_relations(call(), evidence=canonical)


@pytest.mark.parametrize("mutation", [
    lambda items: items[0].update(supports=[]),
    lambda items: items[0]["origins"][0].update(role=[]),
    lambda items: items[0]["origins"][0].update(index=True),
    lambda items: items[0]["origins"][0].update(source_id="c000"),
    lambda items: items[0].update(evidence_id="e0"),
    lambda items: items[0].update(evidence_id="e001"),
])
def test_malformed_canonical_metadata_uses_fixed_errors(mutation):
    canonical = evidence()
    mutation(canonical)
    with pytest.raises(EvidenceRelationsError):
        validate_evidence_relations(links(), canonical)


@pytest.mark.parametrize("key,flag", [
    ("evidence_grounding", "downgraded_to_inconclusive"),
    ("evidence_grounding", "correction_requires_review"),
    ("evidence_grounding_retry", "requires_review"),
])
def test_downgraded_or_reanalysis_required_role_does_not_present_integrated_claim(key, flag):
    source = call()
    source.telemetry[key] = {flag: True}
    assert build_evidence_relations(source, evidence=evidence()) == {"version": VERSION, "groups": []}


def test_legacy_failed_and_absent_calls_have_no_inferred_relations():
    source = call()
    source.grounding_history[0]["output"].pop("evidence_relations")
    assert build_evidence_relations(source, evidence=evidence()) == {"version": VERSION, "groups": []}
    source.succeeded = False
    assert build_evidence_relations(source, evidence=[]) == {"version": VERSION, "groups": []}
    assert build_evidence_relations(None, evidence=[]) == {"version": VERSION, "groups": []}


def test_source_error_does_not_expose_sensitive_input_or_pydantic_document():
    raw = wire()
    raw["evidence_relations"][0]["related_indexes"] = ["SYNTHETIC_COOKIE_SECRET"]
    with pytest.raises(EvidenceRelationsError) as failure:
        assert_relation_sources(raw, [])
    assert str(failure.value) == "evidence_relations_invalid"
    assert "SYNTHETIC_COOKIE_SECRET" not in repr(failure.value)
