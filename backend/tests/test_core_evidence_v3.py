"""Synthetic-only strict v3 core projections; no model/network/DB invocation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.result_contracts import AnalysisResultV3, legacy_result_projection, validate_new_result
from app.services.core_evidence_v3 import CoreEvidenceError, build_core_result
from app.services.evidence_display_plan import build_display_plan
from test_result_contract_v3 import result_fixture


def dense_result(count=10, *, verdict="false_positive", supports=None, exact=False):
    """A reusable, fully closed canonical fixture for API/export integration."""
    value = result_fixture()
    support = {"true_positive": "attack_request", "false_positive": "normal_request", "inconclusive": "context"}[verdict]
    supports = supports if supports is not None else [support] * count
    source = deepcopy(value["evidence"][0])
    value["evidence"] = []
    for index in range(count):
        start = 5 if exact else 100 * index + 5
        excerpt = "q=sample" if exact else f"q=synthetic-{index + 1}"
        item = {**deepcopy(source), "evidence_id": f"e{index + 1}", "excerpt": excerpt,
                "interpretation_ko": "같은 요청 구간에 대한 동일한 설명입니다." if exact else f"입력 항목 {index + 1}의 동작을 확인했습니다.",
                "supports": supports[index],
                "origins": [{"role": "primary" if index < 5 else "verifier", "index": index % 5,
                             "source_id": f"c{index + 1}"}],
                "source_spans": [{"start": start, "end": start + len(excerpt)}]}
        value["evidence"].append(item)
    severity = {"true_positive": "HIGH", "false_positive": "NONE", "inconclusive": "UNKNOWN"}[verdict]
    value["verdict"], value["threat_analysis"]["severity"] = verdict, severity
    value["primary"]["verdict"], value["primary"]["threat_analysis"]["severity"] = verdict, severity
    value["primary"]["evidence_ids"] = [f"e{i}" for i in range(1, min(count, 5) + 1)]
    value["diagnostics"]["roles"]["primary"].update(initial_model_verdict=verdict, validated_verdict=verdict,
                                                   resolved_source_count=min(count, 5))
    if count > 5:
        secondary = deepcopy(value["primary"])
        secondary["evidence_ids"] = [f"e{i}" for i in range(6, count + 1)]
        value["verifier"].update(executed=True, agreement=True, output=secondary, reasons=["synthetic_verification"])
        value["diagnostics"]["roles"]["verifier"] = {**deepcopy(value["diagnostics"]["roles"]["primary"]),
                                                    "resolved_source_count": count - 5}
    matching = [item["evidence_id"] for item in value["evidence"] if item["supports"] == support]
    value["policy"]["decision_evidence_ids"] = matching[:5] if verdict != "inconclusive" else []
    return validate_new_result(value)


def group(*ids, representative=None):
    return {"member_ids": list(ids), "representative_id": representative or ids[0]}


def issue(*ids, origins=None):
    return {"point_ko": "입력 처리 조건이 판정에 영향을 줍니다.", "evidence_ids": list(ids),
            "missing_condition_ko": None, "origins": origins or ["primary"]}


def links(*groups):
    return {"version": "evidence-relations-v1", "groups": [
        {"role": "primary", "representative_evidence_id": representative,
         "related_evidence_ids": list(related)} for representative, related in groups]}


def semantic_view(value, groups, *, relations=None):
    relation = relations or {"version": "evidence-relations-v1", "groups": []}
    plan = build_display_plan(value, relation, groups=groups, status="completed")
    return {"version": "evidence-display-view-v1", "status": "completed", "plan": plan,
            "relations": relation, "metadata": {}}


def assert_raw_unchanged(original, projected):
    indexed = {item["evidence_id"]: item for item in original["evidence"]}
    for item in projected["evidence"]:
        assert item == indexed[item["evidence_id"]]
        assert item is not indexed[item["evidence_id"]]
    selected = {item["evidence_id"] for item in projected["evidence"]}
    for role, old_role in ((projected["primary"], original["primary"]),
                           (projected["verifier"]["output"], original["verifier"]["output"])):
        if old_role is not None:
            assert role["evidence_ids"] == [identifier for identifier in old_role["evidence_ids"] if identifier in selected]
    assert projected["decision_issues"] == original["decision_issues"]
    assert projected["policy"]["decision_evidence_ids"] == [identifier for identifier in original["policy"]["decision_evidence_ids"]
                                                             if identifier in selected]
    assert validate_new_result(projected) == projected


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
def test_all_policy_ids_do_not_force_five_or_ten_core_items(verdict):
    original = dense_result(verdict=verdict)
    before = deepcopy(original)
    output = build_core_result(original)
    projected = output["result"]
    assert [item["evidence_id"] for item in projected["evidence"]] == ["e1", "e2", "e3"]
    assert output["metadata"] == {"version": "core-evidence-v1", "status": "skipped", "original_count": 10, "returned_count": 3}
    assert original == before
    assert_raw_unchanged(original, projected)


def test_semantic_single_group_is_not_refilled_with_duplicate_meaning():
    original = dense_result(5)
    view = semantic_view(original, [group("e1", "e2", "e3", "e4", "e5", representative="e3")])
    output = build_core_result(original, display_view=view)
    assert [item["evidence_id"] for item in output["result"]["evidence"]] == ["e3"]
    assert output["metadata"]["status"] == "completed"
    assert_raw_unchanged(original, output["result"])


def test_another_role_representative_is_not_fabricated_into_primary():
    original = dense_result()
    original["policy"]["decision_evidence_ids"] = ["e6"]
    original = validate_new_result(original)
    view = semantic_view(original, [group(*(f"e{i}" for i in range(1, 11)), representative="e6")])
    projected = build_core_result(original, display_view=view)["result"]
    assert [item["evidence_id"] for item in projected["evidence"]] == ["e6"]
    assert projected["primary"]["evidence_ids"] == []
    assert projected["verifier"]["output"]["evidence_ids"] == ["e6"]
    assert projected["evidence"][0]["origins"] == original["evidence"][5]["origins"]
    assert projected["primary"]["verdict"] == original["primary"]["verdict"]
    assert_raw_unchanged(original, projected)


def test_model_representative_outside_actual_policy_cannot_replace_anchor_id():
    original = dense_result()
    original["policy"]["decision_evidence_ids"] = ["e1"]
    original = validate_new_result(original)
    view = semantic_view(original, [group(*(f"e{i}" for i in range(1, 11)), representative="e6")])
    projected = build_core_result(original, display_view=view)["result"]
    assert [item["evidence_id"] for item in projected["evidence"]] == ["e1"]
    assert projected["policy"]["decision_evidence_ids"] == ["e1"]
    assert projected["verifier"]["output"]["evidence_ids"] == []


def test_saved_group_rank_not_default_visible_controls_core_selection():
    original = dense_result()
    ordered = ["e5", "e7", "e9", "e1", "e2", "e3", "e4", "e6", "e8", "e10"]
    view = semantic_view(original, [group(identifier) for identifier in ordered])
    # A display plan's mandatory counter/issue/all-fallback visibility is not
    # the core count contract. The pure projection reads grouping/ranking only.
    projected = build_core_result(original, display_view=view)["result"]
    assert [item["evidence_id"] for item in projected["evidence"]] == ["e5", "e7", "e9"]


def test_every_root_issue_reference_and_transitive_dependency_is_mandatory():
    original = dense_result(5, supports=["normal_request", "normal_request", "normal_request", "context", "context"])
    original["policy"]["decision_evidence_ids"] = ["e2"]
    original["decision_issues"] = [issue("e3")]
    original = validate_new_result(original)
    relations = links(("e2", ("e4",)), ("e4", ("e5",)))
    projected = build_core_result(original, relations=relations)["result"]
    assert {item["evidence_id"] for item in projected["evidence"]} == {"e2", "e3", "e4", "e5"}
    assert projected["decision_issues"] == original["decision_issues"]
    assert len(projected["evidence"]) == 4  # Soft target, never a source limit.
    assert_raw_unchanged(original, projected)


def test_required_context_not_forced_by_unselected_representatives():
    original = dense_result(5, supports=["normal_request", "normal_request", "normal_request", "normal_request", "context"])
    original["policy"]["decision_evidence_ids"] = ["e1"]
    original = validate_new_result(original)
    projected = build_core_result(original, relations=links(("e4", ("e5",))))["result"]
    assert {item["evidence_id"] for item in projected["evidence"]} == {"e1", "e2", "e3"}


def test_exact_fallback_never_merges_different_required_dependencies():
    original = dense_result(4, exact=True, supports=["normal_request", "normal_request", "context", "context"])
    original["evidence"][3]["excerpt"] = "a different required context"
    original["evidence"][3]["source_spans"] = [{"start": 100, "end": 128}]
    original = validate_new_result(original)
    relation = links(("e1", ("e3",)), ("e2", ("e4",)))
    projected = build_core_result(original, relations=relation, display_view={"status": "fallback"})["result"]
    assert {item["evidence_id"] for item in projected["evidence"]} == {"e1", "e2", "e3", "e4"}
    assert_raw_unchanged(original, projected)


def test_exact_fallback_distinguishes_root_issue_memberships():
    original = dense_result(4, exact=True)
    original["decision_issues"] = [issue("e2")]
    original["policy"]["decision_evidence_ids"] = ["e1"]
    original = validate_new_result(original)
    projected = build_core_result(original, display_view={"status": "fallback"})["result"]
    assert {item["evidence_id"] for item in projected["evidence"]} == {"e1", "e2"}


def test_many_issue_references_can_preserve_all_ten_without_changing_ids():
    original = dense_result()
    original["decision_issues"] = [issue(*(f"e{i}" for i in range(1, 6))),
                                   issue(*(f"e{i}" for i in range(6, 11)), origins=["verifier"])]
    original = validate_new_result(original)
    projected = build_core_result(original)["result"]
    assert len(projected["evidence"]) == 10
    assert_raw_unchanged(original, projected)


def test_counter_direction_keeps_one_representative_not_every_counter_item():
    supports = ["normal_request"] * 5 + ["attack_request"] * 5
    original = dense_result(supports=supports)
    projected = build_core_result(original)["result"]
    assert len(projected["evidence"]) == 3
    assert sum(item["supports"] == "attack_request" for item in projected["evidence"]) == 1
    assert any(item["evidence_id"] == "e1" for item in projected["evidence"])


def test_inconclusive_keeps_both_directional_representatives_not_all():
    supports = ["attack_request"] * 5 + ["normal_request"] * 5
    original = dense_result(verdict="inconclusive", supports=supports)
    output = build_core_result(original)
    assert len(output["result"]["evidence"]) == 3
    assert {item["supports"] for item in output["result"]["evidence"]} == {"attack_request", "normal_request"}
    assert output["result"]["verdict"] == "inconclusive"


def test_context_does_not_fill_space_when_directional_evidence_exists():
    original = dense_result(5, supports=["normal_request", "context", "context", "context", "context"])
    projected = build_core_result(original)["result"]
    assert [item["evidence_id"] for item in projected["evidence"]] == ["e1"]


def test_all_context_can_be_shown_only_for_inconclusive_not_decisive():
    original = dense_result(5, verdict="inconclusive")
    assert len(build_core_result(original)["result"]["evidence"]) == 3
    original = dense_result(5)
    for item in original["evidence"]:
        item["supports"] = "context"
    original = validate_new_result(original)
    with pytest.raises(CoreEvidenceError, match="^core_evidence_decision_evidence_missing$"):
        build_core_result(original)


@pytest.mark.parametrize("verdict,direction", [("true_positive", "attack_request"), ("false_positive", "normal_request")])
def test_decisive_anchor_must_be_in_actual_policy_not_just_somewhere_in_result(verdict, direction):
    original = dense_result(5, verdict=verdict, supports=["context", direction, direction, direction, direction])
    original["policy"]["decision_evidence_ids"] = ["e1"]
    original = validate_new_result(original)
    with pytest.raises(CoreEvidenceError, match="^core_evidence_decision_evidence_missing$"):
        build_core_result(original)


@pytest.mark.parametrize("status", ["fallback", "pending", "running"])
def test_model_failure_or_unready_plan_uses_bounded_code_fallback_not_all(status):
    original = dense_result()
    plan = build_display_plan(original, status="fallback", reason="editor_call_failed")
    assert all(group["default_visible"] for group in plan["groups"])
    output = build_core_result(original, display_view={"status": status, "plan": plan})
    assert output["metadata"]["status"] == "fallback"
    assert len(output["result"]["evidence"]) == 3


def test_exact_duplicates_reduce_even_in_failure_without_semantic_guesses():
    original = dense_result(exact=True)
    output = build_core_result(original, display_view={"status": "fallback", "plan": None})
    assert output["metadata"]["returned_count"] == 1
    assert [item["evidence_id"] for item in output["result"]["evidence"]] == ["e1"]
    changed = deepcopy(original)
    changed["evidence"][1]["interpretation_ko"] = "같은 원문을 다른 조건에서 해석한 별도 설명입니다."
    assert build_core_result(changed, display_view={"status": "fallback"})["metadata"]["returned_count"] == 2


def test_code_fallback_respects_original_policy_order():
    original = dense_result()
    original["policy"]["decision_evidence_ids"] = ["e4", "e2", "e1", "e5", "e3"]
    original = validate_new_result(original)
    output = build_core_result(original, display_view={"status": "fallback"})
    assert [item["evidence_id"] for item in output["result"]["evidence"]] == ["e4", "e2", "e1"]


def test_role_issues_are_kept_only_when_every_original_reference_survives():
    original = dense_result(5)
    keep, remove = issue("e1"), issue("e4", "e5")
    original["primary"]["decision_issues"] = [keep, remove]
    original = validate_new_result(original)
    projected = build_core_result(original)["result"]
    assert projected["primary"]["decision_issues"] == [keep]
    assert original["primary"]["decision_issues"] == [keep, remove]


def test_old_complete_evidence_presentation_map_is_not_returned():
    original = dense_result(5, exact=True)
    original["evidence_presentation"] = {"version": "evidence-editor-v1", "status": "completed", "reason": None,
        "groups": [group(*(f"e{i}" for i in range(1, 6)))]}
    original = validate_new_result(original)
    projected = build_core_result(original)["result"]
    assert "evidence_presentation" not in projected
    assert "evidence_presentation" in original


def test_tampered_plan_changes_only_fallback_status_not_original_or_verdict():
    original = dense_result()
    view = semantic_view(original, [group(*(f"e{i}" for i in range(1, 11)))])
    view["plan"]["source_hash"] = "0" * 64
    before = deepcopy(original)
    output = build_core_result(original, display_view=view)
    assert output["metadata"]["status"] == "fallback" and output["metadata"]["returned_count"] == 3
    assert output["result"]["verdict"] == original["verdict"]
    assert original == before


def test_invalid_relation_data_is_not_silently_ignored():
    original = dense_result()
    with pytest.raises(CoreEvidenceError, match="^core_evidence_source_invalid$"):
        build_core_result(original, relations=links(("e1", ("e99",))))


@pytest.mark.parametrize("field", ["unexpected", "summary_ko"])
def test_invalid_canonical_result_never_falls_through_to_legacy(field):
    original = dense_result()
    original[field] = {"synthetic-private-payload": "not valid"}
    with pytest.raises(CoreEvidenceError, match="^core_evidence_source_invalid$") as error:
        build_core_result(original)
    assert "synthetic-private" not in str(error.value)


def test_v3_shape_and_v2_projection_contain_only_selected_original_evidence():
    original = dense_result()
    schema_before = deepcopy(AnalysisResultV3.model_json_schema())
    projected = build_core_result(original)["result"]
    assert set(projected) == set(original)
    assert AnalysisResultV3.model_json_schema() == schema_before
    assert not {"core_evidence", "core_evidence_version", "display_plan", "metadata"}.intersection(projected)
    legacy = legacy_result_projection(projected)
    assert len(legacy["analyst_assessment"]["evidence"]) == 3
    assert len(legacy["evidence"]) == 3
    assert legacy["verifier"]["output"]["evidence"] == []


def test_every_repeated_read_is_deterministic_and_input_is_immutable():
    original = dense_result()
    view = semantic_view(original, [group("e4", "e5", representative="e4"), group("e1", "e2", "e3"),
                                   group("e6", "e7", "e8", "e9", "e10", representative="e9")])
    before = deepcopy((original, view))
    first = build_core_result(original, display_view=view)
    assert first == build_core_result(original, display_view=view)
    assert (original, view) == before
    assert [item["evidence_id"] for item in first["result"]["evidence"]] == ["e4", "e1", "e9"]


def test_shared_core_golden_fixture():
    path = Path(__file__).with_name("fixtures") / "core_evidence_v3.json"
    cases = json.loads(path.read_text())
    for case in cases:
        before = deepcopy(case["result"])
        actual = build_core_result(case["result"], relations=case.get("relations"), display_view=case.get("display_view"))
        assert actual == case["expected"]
        assert case["result"] == before
