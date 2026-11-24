"""Core projection keeps trusted meaning groups, not guessed text similarity."""
from copy import deepcopy

import pytest

from app.result_contracts import AnalysisResultV3, legacy_result_projection, validate_new_result
from app.services.core_evidence_v3 import build_core_result
from test_core_evidence_v3 import (
    assert_raw_unchanged, dense_result, group, issue, links, semantic_view,
)


def two_role_same_excerpt():
    value = dense_result(4, exact=True)
    descriptions = [
        "페이지 입력이 목록 조회 범위를 지정하므로 정상적인 조회 요청입니다.",
        "목록에서 조회할 범위를 전달하는 값이므로 정상적인 페이지 이동에 해당합니다.",
        "해당 값은 페이지별 조회 범위를 지정하는 정상 기능 입력입니다.",
        "페이지 이동에 필요한 조회 범위 값이 전달되는 요청입니다.",
    ]
    for index, (item, description) in enumerate(zip(value["evidence"], descriptions, strict=True)):
        item["interpretation_ko"] = description
        if index >= 2:
            item["origins"] = [{"role": "verifier", "index": index - 2, "source_id": f"c{index - 1}"}]
    value["primary"]["evidence_ids"] = ["e1", "e2"]
    verifier = deepcopy(value["primary"])
    verifier["evidence_ids"] = ["e3", "e4"]
    value["verifier"].update(executed=True, agreement=True, output=verifier, reasons=["synthetic_verification"])
    value["diagnostics"]["roles"]["primary"]["resolved_source_count"] = 2
    value["diagnostics"]["roles"]["verifier"] = deepcopy(value["diagnostics"]["roles"]["primary"])
    return validate_new_result(value)


def test_two_interpretations_per_role_become_one_existing_representative_for_one_trusted_meaning():
    original = two_role_same_excerpt()
    before = deepcopy(original)
    schema = deepcopy(AnalysisResultV3.model_json_schema())
    view = semantic_view(original, [group("e1", "e2", "e3", "e4", representative="e3")])
    projected = build_core_result(original, display_view=view)["result"]
    assert projected["evidence"] == [original["evidence"][2]]
    assert projected["primary"]["evidence_ids"] == []
    assert projected["verifier"]["output"]["evidence_ids"] == ["e3"]
    # Do not manufacture Primary provenance on the selected Verifier item.
    assert projected["evidence"][0]["origins"] == original["evidence"][2]["origins"]
    assert projected["policy"]["decision_evidence_ids"] == ["e3"]
    assert set(projected) == set(original)
    assert AnalysisResultV3.model_json_schema() == schema
    assert original == before
    assert_raw_unchanged(original, projected)
    assert len(legacy_result_projection(projected)["analyst_assessment"]["evidence"]) == 1


def test_semantic_representative_cannot_replace_actual_policy_anchor_from_another_role():
    original = two_role_same_excerpt()
    original["policy"]["decision_evidence_ids"] = ["e1"]
    original = validate_new_result(original)
    view = semantic_view(original, [group("e1", "e2", "e3", "e4", representative="e4")])
    projected = build_core_result(original, display_view=view)["result"]
    assert projected["evidence"] == [original["evidence"][0]]
    assert projected["policy"]["decision_evidence_ids"] == ["e1"]
    assert projected["verifier"]["output"]["evidence_ids"] == []
    assert_raw_unchanged(original, projected)


def test_singleton_plan_never_implies_equal_meaning_from_equal_excerpt():
    original = two_role_same_excerpt()
    original["evidence"][0]["interpretation_ko"] = "입력값은 목록의 조회 범위를 지정합니다."
    original["evidence"][1]["interpretation_ko"] = "클라이언트가 지정한 정렬 방식은 조회 결과 표시 순서를 정합니다."
    original["evidence"][2]["interpretation_ko"] = "데이터 항목은 검사 예시로 전달되고 실행 대상으로 사용되지 않습니다."
    original["evidence"][3]["interpretation_ko"] = "지정한 범위는 응답 데이터의 개수를 제한합니다."
    original["decision_issues"] = [issue("e1", "e2"), issue("e3", "e4", origins=["verifier"])]
    original = validate_new_result(original)
    view = semantic_view(original, [group(item["evidence_id"]) for item in original["evidence"]])
    projected = build_core_result(original, display_view=view)["result"]
    assert len(projected["evidence"]) == 4  # References are mandatory, not a three-item quota.
    assert projected["decision_issues"] == original["decision_issues"]
    assert_raw_unchanged(original, projected)


def test_root_issue_can_require_multiple_members_without_silently_replacing_its_ids():
    original = dense_result(2)
    original["decision_issues"] = [issue("e1", "e2")]
    original = validate_new_result(original)
    view = semantic_view(original, [group("e1", "e2", representative="e2")])
    projected = build_core_result(original, display_view=view)["result"]
    assert {item["evidence_id"] for item in projected["evidence"]} == {"e1", "e2"}
    assert projected["decision_issues"] == original["decision_issues"]
    assert_raw_unchanged(original, projected)


@pytest.mark.parametrize("difference", ["interpretation", "support"])
def test_exact_fallback_never_equates_distinct_contributions_at_the_same_raw_location(difference):
    original = dense_result(4, exact=True,
        supports=["normal_request", "normal_request", "context", "context"])
    if difference == "interpretation":
        original["evidence"][2]["interpretation_ko"] = "첫 번째 필수 문맥에서는 입력이 검사 데이터로 취급됩니다."
        original["evidence"][3]["interpretation_ko"] = "두 번째 필수 문맥에서는 해당 값이 별도의 처리 조건을 지정합니다."
    else:
        original["evidence"][2]["supports"] = "normal_request"
    original["policy"]["decision_evidence_ids"] = ["e1", "e2"]
    original = validate_new_result(original)
    relations = links(("e1", ("e3",)), ("e2", ("e4",)))
    before = deepcopy((original, relations))
    projected = build_core_result(original, relations=relations, display_view={"status": "fallback"})["result"]
    assert {item["evidence_id"] for item in projected["evidence"]} == {"e1", "e2", "e3", "e4"}
    assert (original, relations) == before
    assert_raw_unchanged(original, projected)


def test_opposing_interpretation_of_same_excerpt_remains_beside_the_single_matching_group():
    original = two_role_same_excerpt()
    original["evidence"][3]["supports"] = "attack_request"
    original["evidence"][3]["interpretation_ko"] = "요청 입력은 별도 처리 조건에서 서버 동작을 바꾸려는 의미로 해석됩니다."
    original["policy"]["decision_evidence_ids"] = ["e1", "e2", "e3"]
    original = validate_new_result(original)
    view = semantic_view(original, [group("e1", "e2", "e3", representative="e2"), group("e4")])
    projected = build_core_result(original, display_view=view)["result"]
    assert [item["evidence_id"] for item in projected["evidence"]] == ["e2", "e4"]
    assert {item["supports"] for item in projected["evidence"]} == {"attack_request", "normal_request"}
    assert_raw_unchanged(original, projected)
