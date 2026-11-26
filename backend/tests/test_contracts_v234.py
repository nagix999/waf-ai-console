"""Synthetic strict graph invariants and bounded identity-only diagnostics."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agent.contracts_v232 import SecurityAnalysisOutputV232
from app.agent.contracts_v234 import (
    EvidenceDecisionReviewOutput, SecurityAnalysisOutputV234, safe_conflict_paths,
    verified_conflict_paths,
)
from app.agent.output_schema_v226 import compact_strict_schema
from test_decision_review_contract_v232 import review_context, review_wire
from test_flat_evidence_v228 import catalog, wire


def validate(value):
    return SecurityAnalysisOutputV234.model_validate(value, context={"source_catalog": catalog()})


def conflict(*, changed_ids=False):
    value = wire()
    value["evidence"][1]["supports"] = "normal_request"
    if changed_ids:
        value["evidence"][0].update(evidence_id="e1", related_evidence_ids=["e3"])
        value["evidence"][1]["evidence_id"] = "e3"
    return value


def diagnostic(value):
    with pytest.raises(ValidationError) as failure:
        validate(value)
    return failure.value.errors(include_input=False)[0]


def test_wire_schema_is_exactly_the_preserved_v232_contract_except_type_title():
    previous = compact_strict_schema(SecurityAnalysisOutputV232.model_json_schema())
    current = compact_strict_schema(SecurityAnalysisOutputV234.model_json_schema())
    previous.pop("title")
    current.pop("title")
    assert current == previous
    assert validate(wire()).model_dump(mode="json") == wire()


def test_direct_conflict_contains_only_validated_identity_nodes_without_changing_result():
    value = conflict()
    before = deepcopy(value)
    issue = diagnostic(value)
    assert issue["type"] == "evidence_support_conflict"
    assert issue["ctx"] == {"conflict_paths": [
        [{"evidence_id": "e2", "source_id": "c2", "supports": "attack_request"}],
        [{"evidence_id": "e2", "source_id": "c2", "supports": "attack_request"},
         {"evidence_id": "e5", "source_id": "c1", "supports": "normal_request"}],
    ]}
    assert value == before


def test_transitive_conflict_finds_actual_direct_edges_through_context():
    value = wire()
    value["evidence"][1]["related_evidence_ids"] = ["e1"]
    value["evidence"].append({"evidence_id": "e1", "source_id": "c6", "supports": "normal_request",
        "interpretation_ko": "별도 요청 문맥은 정상 기능의 사용을 뒷받침합니다.", "related_evidence_ids": []})
    paths = diagnostic(value)["ctx"]["conflict_paths"]
    assert [node["evidence_id"] for node in paths[1]] == ["e2", "e5", "e1"]
    assert verified_conflict_paths(paths, value["evidence"], selected_source_ids={"c1", "c2", "c6"}) == paths


def test_branching_context_root_keeps_original_closure_conflict_rule():
    value = conflict()
    value["evidence"][0]["related_evidence_ids"] = []
    value["evidence"].insert(0, {"evidence_id": "e1", "source_id": "c6", "supports": "context",
        "interpretation_ko": "두 해석의 성립에 필요하다고 연결한 보조 관측입니다.", "related_evidence_ids": ["e2", "e5"]})
    paths = diagnostic(value)["ctx"]["conflict_paths"]
    assert [[node["evidence_id"] for node in path] for path in paths] == [["e1", "e2"], ["e1", "e5"]]


def test_independent_opposite_interpretations_share_context_and_cycles_without_failure():
    value = wire()
    value.update(verdict="inconclusive", interpretation_conflict=True)
    value["threat_analysis"]["severity"] = "UNKNOWN"
    value["evidence"].extend([
        {"evidence_id": "e1", "source_id": "c2", "supports": "normal_request",
         "interpretation_ko": "독립된 정상 기능의 쓰임도 같은 참고 관측으로 확인합니다.", "related_evidence_ids": ["e5"]},
        {"evidence_id": "e3", "source_id": "c1", "supports": "context",
         "interpretation_ko": "공통 참고 관측의 처리 위치입니다.", "related_evidence_ids": ["e5"]},
    ])
    value["evidence"][1]["related_evidence_ids"] = ["e3"]
    assert validate(value).model_dump(mode="json") == value


def test_unknown_source_never_appears_in_conflict_diagnostic_even_when_conflict_precedes_source_check():
    value = conflict()
    value["evidence"][0]["source_id"] = "c999"
    issue = diagnostic(value)
    assert issue["type"] == "evidence_support_conflict"
    assert not issue.get("ctx", {}).get("conflict_paths")


@pytest.mark.parametrize("mutate, code", [
    (lambda value: value["evidence"][0].update(source_id="c999"), "evidence_source_unavailable"),
    (lambda value: value["evidence"][0].update(source_id="c3"), "evidence_source_support_forbidden"),
    (lambda value: value["evidence"][0].update(supports="context"), "decisive_verdict_requires_directional_content"),
    (lambda value: value.update(verdict="inconclusive"), "inconclusive_requires_unknown_severity"),
    (lambda value: value["evidence"][0].update(related_evidence_ids=["e1"]), "evidence_link_unknown"),
    (lambda value: value.update(extra_private_field="SYNTHETIC_PRIVATE"), "extra_forbidden"),
])
def test_original_source_direction_severity_and_closed_contract_invariants_remain(mutate, code):
    value = wire()
    mutate(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert code in {issue["type"] for issue in failure.value.errors()}


@pytest.mark.parametrize("mutate", [
    lambda paths: paths[0][0].update(evidence_id="e1\nIGNORE_RULES"),
    lambda paths: paths[0][0].update(source_id="c2\nPRIVATE"),
    lambda paths: paths[0][0].update(supports="attack_request\nPRIVATE"),
    lambda paths: paths[0][0].update(interpretation_ko="PRIVATE"),
    lambda paths: paths.append(deepcopy(paths[0])),
    lambda paths: paths[1].extend(deepcopy(paths[1]) * 3),
    lambda paths: paths[1].append(deepcopy(paths[1][0])),
])
def test_unsafe_or_overlong_diagnostic_context_is_wholly_discarded(mutate):
    paths = diagnostic(conflict())["ctx"]["conflict_paths"]
    mutate(paths)
    assert safe_conflict_paths(paths) == []


def test_plausible_but_forged_edges_or_node_classification_are_not_diagnostics():
    value = conflict()
    paths = diagnostic(value)["ctx"]["conflict_paths"]
    value["evidence"][0]["related_evidence_ids"] = []
    assert verified_conflict_paths(paths, value["evidence"]) == []
    assert verified_conflict_paths(paths, conflict()["evidence"], selected_source_ids={"c2"}) == []
    value = conflict()
    value["evidence"][1]["supports"] = "context"
    assert verified_conflict_paths(paths, value["evidence"]) == []


def test_decision_review_still_keeps_every_verified_evidence_and_reconstructs_new_model():
    context, value = review_context(), review_wire()
    before = deepcopy(context)
    review = EvidenceDecisionReviewOutput.model_validate(value, context=context)
    assert isinstance(review.to_analysis(), SecurityAnalysisOutputV234)
    assert review.to_analysis().model_dump(mode="json")["evidence"] == context["review_original_output"]["evidence"]
    assert context == before
    value["retained_evidence_ids"] = ["e2"]
    with pytest.raises(ValidationError) as failure:
        EvidenceDecisionReviewOutput.model_validate(value, context=context)
    assert failure.value.errors()[0]["type"] == "review_evidence_omitted"
