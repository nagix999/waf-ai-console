"""Synthetic same-excerpt cross-role equivalence; no model or production data."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agent.evidence_display_v3 import (
    DisplayEditorOutput, INSTRUCTIONS, MAX_INPUT_BYTES, MAX_OUTPUT_TOKENS, MAX_SECONDS,
    VERSION as EDITOR_VERSION, build_editor_input, input_fits, serialized_input,
)
from app.agent.evidence_editor import EvidenceEditorOutput
from app.services.evidence_display_plan_v2 import (
    VERSION, DisplayPlanError, build_display_plan, source_document, source_hash, validate_display_plan,
)
from test_evidence_display_plan import evidence, group, issue, result


def cross_role():
    first = evidence(1, excerpt="GET /synthetic-backup.zip HTTP/1.1", start=12,
                     interpretation="압축본 조회는 소스와 설정 파일 노출을 탐색하는 요청입니다.")
    second = evidence(6, excerpt=first["excerpt"], start=12,
                      interpretation="웹 소스 압축본의 공개 여부를 확인하려는 탐색 요청입니다.")
    value = result(first, second, decision_ids=["e1"])
    primary_issue = issue("e1", missing=None)
    verifier_issue = {**issue("e6", missing=None), "origins": ["verifier"]}
    value["decision_issues"] = [primary_issue, verifier_issue]
    value["primary"] = {"confidence_score": .91, "decision_issues": [deepcopy(primary_issue)]}
    value["verifier"] = {"output": {"confidence_score": .72,
                                      "decision_issues": [deepcopy(verifier_issue)]}}
    return value


def links(*pairs):
    return {"version": "evidence-relations-v1", "groups": [
        {"role": role, "representative_evidence_id": representative,
         "related_evidence_ids": list(related)} for role, representative, related in pairs]}


def test_wire_and_budgets_stay_identical_without_a_certainty_gate():
    assert DisplayEditorOutput is EvidenceEditorOutput
    assert EDITOR_VERSION == "evidence-display-editor-v3"
    assert (MAX_INPUT_BYTES, MAX_OUTPUT_TOKENS, MAX_SECONDS) == (16000, 1024, 30)
    assert "양 역할이 같은 동작과 보안 의미를 반복하면 하나로 묶는다" in INSTRUCTIONS
    assert "실제 성립 조건이 다르면 별도로" in INSTRUCTIONS
    assert "확실성 수준" not in INSTRUCTIONS and "confidence_score" not in INSTRUCTIONS


def test_different_role_and_issue_number_can_form_one_same_point_group():
    original = cross_role()
    before = deepcopy(original)
    document = build_editor_input(original)
    assert document["allowed_groups"] == [["e1", "e6"]]
    assert document["same_source_groups"] == [["e1", "e6"]]
    assert document["decision_issues"] == original["decision_issues"]
    assert document["role_decision_issues"]["verifier"] == original["verifier"]["output"]["decision_issues"]
    plan = build_display_plan(original, groups=[group("e1", "e6", representative="e6")], status="completed")
    assert plan["version"] == VERSION == "evidence-display-plan-v2"
    assert plan["groups"][0]["member_ids"] == ["e1", "e6"]
    assert plan["groups"][0]["representative_id"] == "e6"
    assert plan["metrics"]["merged_count"] == 1
    assert validate_display_plan(original, None, plan) == plan
    assert original == before


def test_confidence_score_difference_is_not_a_merge_criterion_or_source_binding():
    original = cross_role()
    binding = source_hash(original)
    original["primary"]["confidence_score"] = .01
    original["verifier"]["output"]["confidence_score"] = .99
    assert source_hash(original) == binding
    assert build_editor_input(original)["allowed_groups"] == [["e1", "e6"]]


@pytest.mark.parametrize("scope", ["root", "primary", "verifier"])
@pytest.mark.parametrize("field", ["point_ko", "missing_condition_ko"])
def test_all_root_and_role_issue_meanings_are_bound_without_using_numeric_membership_as_gate(scope, field):
    original = cross_role()
    plan = build_display_plan(original, groups=[group("e1", "e6")], status="completed")
    target = (original["decision_issues"] if scope == "root" else
              original["primary"]["decision_issues"] if scope == "primary" else
              original["verifier"]["output"]["decision_issues"])
    target[0][field] = "새로운 처리 조건이 추가되어 기존 원문 해석이 달라집니다."
    with pytest.raises(DisplayPlanError):
        validate_display_plan(original, None, plan)


@pytest.mark.parametrize("difference", ["position", "excerpt", "field", "empty_spans"])
def test_same_wording_without_exact_nonempty_physical_position_cannot_merge(difference):
    original = cross_role()
    second = original["evidence"][1]
    if difference == "position":
        second["source_spans"] = [{"start": 90, "end": 90 + len(second["excerpt"])}]
    elif difference == "excerpt":
        second["excerpt"] += " "
    elif difference == "field":
        second["field"] = "http_headers"
    else:
        second["source_spans"] = []
    assert build_editor_input(original) is None
    with pytest.raises(DisplayPlanError, match="display_source_mismatch"):
        build_display_plan(original, groups=[group("e1", "e6")], status="completed")


@pytest.mark.parametrize("support", ["normal_request", "context"])
def test_opposite_or_context_support_is_never_a_merge_candidate(support):
    original = cross_role()
    original["evidence"][1]["supports"] = support
    assert build_editor_input(original) is None
    with pytest.raises(DisplayPlanError, match="display_support_mismatch"):
        build_display_plan(original, groups=[group("e1", "e6")], status="completed")


def test_field_aliases_normalize_but_excerpt_and_position_do_not_change():
    original = cross_role()
    original["evidence"][1]["field"] = " event.raw_payload "
    assert build_editor_input(original)["allowed_groups"] == [["e1", "e6"]]
    plan = build_display_plan(original, groups=[group("e1", "e6")], status="completed")
    assert validate_display_plan(original, None, plan) == plan


def with_dependencies(*, difference=None):
    original = cross_role()
    first = evidence(2, supports="context", excerpt="Range: bytes=0-128", start=70,
                     interpretation="일부 범위의 파일 내용을 조회하는 보조 조건입니다.")
    second = evidence(7, supports="context", excerpt=first["excerpt"], start=70,
                      interpretation=first["interpretation_ko"])
    if difference == "interpretation":
        second["interpretation_ko"] = "이 범위의 값이 다른 실행 경로를 선택합니다."
    elif difference == "support":
        second["supports"] = "attack_request"
    elif difference == "missing_location":
        first["source_spans"] = second["source_spans"] = []
    original["evidence"].extend([first, second])
    return original, links(("primary", "e1", ("e2",)), ("verifier", "e6", ("e7",)))


def test_identical_required_contribution_can_use_different_role_local_ids():
    original, relations = with_dependencies()
    document = build_editor_input(original, relations)
    assert ["e1", "e6"] in document["allowed_groups"]
    plan = build_display_plan(original, relations,
        groups=[group("e1", "e6"), group("e2", "e7")], status="completed")
    assert plan["groups"][0]["related_ids"] == ["e2", "e7"]
    assert validate_display_plan(original, relations, plan) == plan


@pytest.mark.parametrize("difference", ["interpretation", "support", "missing_location"])
def test_different_or_unlocated_required_contribution_prevents_representative_substitution(difference):
    original, relations = with_dependencies(difference=difference)
    with pytest.raises(DisplayPlanError, match="display_dependency_mismatch"):
        build_display_plan(original, relations,
            groups=[group("e1", "e6"), group("e2"), group("e7")], status="completed")


def test_same_role_membership_rule_is_not_broadened():
    original = cross_role()
    original["evidence"][1]["origins"] = [{"role": "primary", "index": 1, "source_id": "c2"}]
    assert build_editor_input(original) is None
    with pytest.raises(DisplayPlanError, match="display_condition_mismatch"):
        build_display_plan(original, groups=[group("e1", "e6")], status="completed")


def test_same_excerpt_does_not_require_model_to_merge_different_interpretations():
    original = cross_role()
    plan = build_display_plan(original, groups=[group("e1"), group("e6")], status="completed")
    assert plan["reason"] == "no_reduction" and plan["metrics"]["merged_count"] == 0
    assert len(plan["groups"]) == 2


def test_exact_fallback_is_conservative_and_model_failure_keeps_all_sources():
    original = cross_role()
    assert build_display_plan(original)["metrics"]["group_count"] == 2
    original["evidence"][1]["interpretation_ko"] = original["evidence"][0]["interpretation_ko"]
    assert build_editor_input(original) is None
    assert build_display_plan(original)["metrics"]["group_count"] == 1
    fallback = build_display_plan(original, status="fallback", reason="display_call_failed")
    assert fallback["metrics"]["group_count"] == 2
    assert all(group["default_visible"] for group in fallback["groups"])


@pytest.mark.parametrize("role", ["primary", "verifier"])
@pytest.mark.parametrize("shared_membership", [False, True])
@pytest.mark.parametrize("status", ["skipped", "pending"])
def test_exact_subgroups_inside_cross_role_candidates_keep_single_role_condition_boundaries(
        role, shared_membership, status):
    original = cross_role()
    index = 0 if role == "primary" else 1
    duplicate = deepcopy(original["evidence"][index])
    identifier = "e2" if role == "primary" else "e7"
    duplicate.update(evidence_id=identifier,
        origins=[{"role": role, "index": 1, "source_id": "c2"}])
    original["evidence"].append(duplicate)
    if shared_membership:
        original["decision_issues"][index]["evidence_ids"].append(identifier)
    else:
        original["decision_issues"].append({**issue(identifier), "origins": [role]})
    assert build_editor_input(original) is not None
    before = deepcopy(original)
    plan = build_display_plan(original, status=status)
    assert validate_display_plan(original, None, plan) == plan
    assert len(plan["groups"]) == (2 if shared_membership else 3)
    assert original == before


@pytest.mark.parametrize("damage", ["version", "members", "representative", "source", "metrics"])
def test_hash_match_alone_cannot_authorize_a_forged_plan(damage):
    original = cross_role()
    plan = build_display_plan(original, groups=[group("e1", "e6")], status="completed")
    if damage == "version":
        plan["version"] = "evidence-display-v1"
    elif damage == "members":
        plan["groups"][0]["member_ids"] = ["e1"]
    elif damage == "representative":
        plan["groups"][0]["representative_id"] = "e99"
    elif damage == "source":
        plan["source_hash"] = "0" * 64
    else:
        plan["metrics"]["group_count"] = 999
    with pytest.raises(DisplayPlanError):
        validate_display_plan(original, None, plan)


def test_same_input_budget_rejects_whole_document_instead_of_dropping_context():
    original = cross_role()
    for item in original["evidence"]:
        item["interpretation_ko"] = "한" * 1990
    original["evidence"][1]["interpretation_ko"] += "둘"
    document = build_editor_input(original)
    before = deepcopy(document)
    assert not input_fits(SimpleNamespace(max_output_tokens=3072, context_window=1000),
                          INSTRUCTIONS, serialized_input(document))
    assert document == before
    assert source_document(original)["role_decision_issues"]["primary"]
