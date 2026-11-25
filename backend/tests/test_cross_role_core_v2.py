"""Synthetic proof-bound representative responses, never a model evaluation."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.core_result_contracts import (
    CoreAnalysisResultV3, core_legacy_result_projection, revalidate_core_result,
    validate_core_projection,
)
from app.result_contracts import AnalysisResultV3, validate_new_result
from app.services.core_evidence_v2 import build_core_result
from app.services.evidence_display_plan_v2 import build_display_plan
from test_core_evidence_meaning_groups import two_role_same_excerpt
from test_core_evidence_v3 import group, issue


EMPTY = {"version": "evidence-relations-v1", "groups": []}


def cross_role_result():
    result = two_role_same_excerpt()
    result["decision_issues"] = [issue("e1", "e2"), issue("e3", "e4", origins=["verifier"])]
    result["primary"]["decision_issues"] = [deepcopy(result["decision_issues"][0])]
    result["verifier"]["output"]["decision_issues"] = [deepcopy(result["decision_issues"][1])]
    return validate_new_result(result)


def view(result, *, groups=None, relations=None, status="completed"):
    relations = relations or EMPTY
    groups = groups or [group("e1", "e2", "e3", "e4", representative="e3")]
    plan = build_display_plan(result, relations, groups=groups, status=status)
    return {"version": "evidence-display-view-v2", "status": status, "plan": plan, "relations": relations}


def test_same_point_across_roles_and_own_issues_becomes_one_without_fabricated_origin():
    original = cross_role_result()
    before = deepcopy(original)
    result = build_core_result(original, display_view=view(original))
    typed = result["result"]
    assert isinstance(typed, CoreAnalysisResultV3)
    data = typed.model_dump(mode="json", exclude_unset=True)
    assert data["evidence"] == [before["evidence"][2]]
    assert data["primary"]["evidence_ids"] == []
    assert data["verifier"]["output"]["evidence_ids"] == ["e3"]
    for old, new in zip(original["decision_issues"], data["decision_issues"], strict=True):
        assert {key: value for key, value in new.items() if key != "evidence_ids"} == {
            key: value for key, value in old.items() if key != "evidence_ids"}
        assert new["evidence_ids"] == ["e3"]
    assert data["primary"]["decision_issues"][0]["evidence_ids"] == ["e3"]
    assert data["verifier"]["output"]["decision_issues"][0]["evidence_ids"] == ["e3"]
    assert original == before
    assert result["metadata"] == {"version": "core-evidence-v2", "status": "completed",
        "original_count": 4, "returned_count": 1, "group_count": 1, "aliased_count": 3,
        "issue_reference_reduction": 3, "required_dependency_count": 0}
    assert set(data) == set(before)
    for field in ("verdict", "summary_ko", "confidence_score", "threat_analysis", "signature_assessment", "diagnostics"):
        assert data[field] == before[field]


def test_stored_result_validator_stays_strict_and_core_dict_cannot_bypass_it():
    original = cross_role_result()
    typed = build_core_result(original, display_view=view(original))["result"]
    data = typed.model_dump(mode="json", exclude_unset=True)
    assert revalidate_core_result(typed).model_dump(mode="json", exclude_unset=True) == data
    with pytest.raises(ValidationError, match="result_issue_origin_invalid"):
        AnalysisResultV3.model_validate(data)
    with pytest.raises(ValidationError, match="core_response_projection_unproven"):
        CoreAnalysisResultV3.model_validate(data)
    with pytest.raises(ValueError, match="core_response_projection_unproven"):
        revalidate_core_result(data)
    assert validate_new_result(original) == original


@pytest.mark.parametrize("damage", ["copy", "nested", "unsealed"])
def test_modified_or_unsealed_core_instance_is_not_accepted_as_proven(damage):
    original = cross_role_result()
    typed = build_core_result(original, display_view=view(original))["result"]
    if damage == "copy":
        typed = typed.model_copy(update={"summary_ko": "not an original summary"})
    elif damage == "nested":
        typed.evidence[0].interpretation_ko = "not an original explanation"
    else:
        typed = CoreAnalysisResultV3.model_construct(**typed.model_dump(mode="python", exclude_unset=True))
    with pytest.raises(ValidationError, match="core_response_projection_unproven"):
        revalidate_core_result(typed)


def test_only_actual_policy_member_can_represent_a_group_with_an_anchor():
    original = cross_role_result()
    original["policy"]["decision_evidence_ids"] = ["e1"]
    result = build_core_result(original, display_view=view(original))["result"].model_dump(mode="json", exclude_unset=True)
    assert result["evidence"] == [original["evidence"][0]]
    assert result["policy"]["decision_evidence_ids"] == ["e1"]
    assert result["primary"]["evidence_ids"] == ["e1"]
    assert result["verifier"]["output"]["evidence_ids"] == []
    assert all(item["evidence_ids"] == ["e1"] for item in result["decision_issues"])


def test_confidence_scores_are_not_a_grouping_or_representative_gate():
    original = cross_role_result()
    original["primary"]["confidence_score"] = .98
    original["verifier"]["output"]["confidence_score"] = .55
    typed = build_core_result(original, display_view=view(original))["result"]
    assert len(typed.evidence) == 1
    assert typed.primary.confidence_score == .98
    assert typed.verifier.output.confidence_score == .55


def test_different_points_in_singletons_are_not_merged_by_the_server():
    original = cross_role_result()
    document = view(original, groups=[group(key) for key in ("e1", "e2", "e3", "e4")])
    result = build_core_result(original, display_view=document)["result"].model_dump(mode="json", exclude_unset=True)
    assert result["evidence"] == original["evidence"]
    assert result["decision_issues"] == original["decision_issues"]


@pytest.mark.parametrize("damage", ["source_hash", "source_text", "role_issue", "missing_plan", "failed", "pending"])
def test_invalid_or_unavailable_optional_plan_uses_safe_fallback_without_a_new_call(damage):
    original = cross_role_result()
    document = view(original)
    if damage == "source_hash":
        document["plan"]["source_hash"] = "0" * 64
    elif damage == "source_text":
        original["evidence"][0]["interpretation_ko"] += " Changed input."
    elif damage == "role_issue":
        original["primary"]["decision_issues"][0]["point_ko"] += " Changed role issue."
    elif damage == "missing_plan":
        document["plan"] = None
    else:
        document["status"] = damage
    result = build_core_result(original, display_view=document)
    assert result["metadata"]["status"] == "fallback"
    assert len(result["result"]["evidence"]) == 4
    assert validate_new_result(result["result"]) == result["result"]


@pytest.mark.parametrize("damage", ["outside_group", "missing", "chain", "unselected_issue", "foreign_id", "policy"])
def test_projection_boundary_rejects_unproven_reference_aliases(damage):
    original = cross_role_result()
    document = view(original)
    mapping = {key: "e3" for key in ("e1", "e2", "e3", "e4")}
    selected = ["e3"]
    if damage == "outside_group":
        document = view(original, groups=[group("e1", "e2"), group("e3", "e4")])
    elif damage == "missing":
        mapping.pop("e4")
    elif damage == "chain":
        mapping["e2"] = "e1"
    elif damage == "unselected_issue":
        selected = []
    elif damage == "foreign_id":
        mapping["e4"] = "e9"
    else:
        original["policy"]["decision_evidence_ids"] = ["e1"]
        document = view(original)
    with pytest.raises((ValueError, ValidationError)):
        validate_core_projection(original, mapping=mapping, selected_ids=selected,
            plan=document["plan"], relations=EMPTY)


def test_legacy_v2_conversion_only_accepts_sealed_core_and_preserves_selected_list():
    original = cross_role_result()
    typed = build_core_result(original, display_view=view(original))["result"]
    legacy = core_legacy_result_projection(typed)
    assert legacy["schema_version"] == "waf-analysis-v2"
    assert len(legacy["analyst_assessment"]["evidence"]) == 1
    assert legacy["analyst_assessment"]["evidence"][0]["origins"] == original["evidence"][2]["origins"]
    assert all(item["evidence_ids"] == ["e3"] for item in legacy["analyst_assessment"]["decision_issues"])
    with pytest.raises(ValueError):
        core_legacy_result_projection(typed.model_dump(mode="json", exclude_unset=True))


def test_old_display_view_never_adopts_the_new_cross_role_reference_semantics():
    from app.services.evidence_display_plan import build_display_plan as old_plan
    original = cross_role_result()
    groups = [group("e1", "e2"), group("e3", "e4")]
    document = {"version": "evidence-display-view-v1", "status": "completed", "relations": EMPTY,
        "plan": old_plan(original, EMPTY, groups=groups, status="completed")}
    output = build_core_result(original, display_view=document)
    assert output["metadata"]["version"] == "core-evidence-v1"
    assert output["result"]["evidence"] == original["evidence"]
    assert output["result"]["decision_issues"] == original["decision_issues"]


def opposing_result():
    original = cross_role_result()
    original["evidence"][3]["supports"] = "attack_request"
    original["evidence"][3]["interpretation_ko"] = "다른 처리 조건에서는 입력이 서버 동작을 바꾸려는 요청입니다."
    original["policy"]["decision_evidence_ids"] = ["e1", "e2", "e3"]
    return validate_new_result(original)


def test_opposing_interpretation_is_retained_beside_the_single_shared_point():
    original = opposing_result()
    document = view(original, groups=[group("e1", "e2", "e3", representative="e3"), group("e4")])
    data = build_core_result(original, display_view=document)["result"].model_dump(mode="json", exclude_unset=True)
    assert [item["evidence_id"] for item in data["evidence"]] == ["e3", "e4"]
    assert {item["supports"] for item in data["evidence"]} == {"attack_request", "normal_request"}
    assert data["decision_issues"][1]["evidence_ids"] == ["e3", "e4"]


def test_proof_boundary_also_rejects_a_callers_attempt_to_omit_counter_evidence():
    original = opposing_result()
    document = view(original, groups=[group("e1", "e2", "e3", representative="e3"), group("e4")])
    with pytest.raises(ValueError, match="core_response_counter_evidence_missing"):
        validate_core_projection(original, mapping={"e1": "e3", "e2": "e3", "e3": "e3", "e4": "e4"},
            selected_ids=["e3"], plan=document["plan"], relations=EMPTY)
