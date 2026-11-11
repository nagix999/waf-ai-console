"""Synthetic-only grouping/priority safety; no model, network or DB is used."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.evidence_display import (DisplayEditorOutput, INSTRUCTIONS, VERSION as EDITOR_VERSION,
    build_editor_input, input_fits, instructions_hash, serialized_input)
from app.agent.evidence_editor import EvidenceEditorOutput
from app.services.evidence_display_plan import (
    DisplayPlanError, build_display_plan, source_document, source_hash, validate_display_plan,
)


def evidence(index, *, supports="attack_request", excerpt=None, interpretation=None, start=None):
    start = index * 100 if start is None else start
    excerpt = f"synthetic source {index}" if excerpt is None else excerpt
    return {"evidence_id": f"e{index}", "field": "payload", "excerpt": excerpt,
            "interpretation_ko": interpretation or f"기존 근거 {index}의 해석입니다.", "supports": supports,
            "origins": [{"role": "primary" if index <= 5 else "verifier", "index": (index - 1) % 5,
                         "source_id": f"c{index}"}],
            "source_spans": [{"start": start, "end": start + len(excerpt)}]}


def result(*items, verdict="true_positive", issues=None, decision_ids=None):
    return {"schema_version": "waf-analysis-v3", "verdict": verdict, "evidence": list(items),
            "decision_issues": issues or [], "policy": {"decision_evidence_ids": decision_ids or []}}


def issue(*ids, missing="처리 조건이 확인되지 않았습니다."):
    return {"point_ko": "입력의 처리 조건을 구분해야 합니다.", "evidence_ids": list(ids),
            "missing_condition_ko": missing, "origins": ["primary"]}


def relations(*groups):
    return {"version": "evidence-relations-v1", "groups": [
        {"role": "primary", "representative_evidence_id": representative, "related_evidence_ids": list(related)}
        for representative, related in groups]}


def group(*ids, representative=None):
    return {"member_ids": list(ids), "representative_id": representative or ids[0]}


def test_wire_is_existing_strict_model_not_executor_allowlist_extension():
    assert DisplayEditorOutput is EvidenceEditorOutput
    assert EDITOR_VERSION == "evidence-display-editor-v1"
    assert len(instructions_hash()) == 64
    assert "성립 조건" in INSTRUCTIONS and "새로 작성하거나 수정하지 않는다" in INSTRUCTIONS


def test_semantic_groups_can_join_different_sources_without_rewriting_canonical():
    original = result(evidence(1), evidence(2), evidence(3, supports="normal_request"))
    before = deepcopy(original)
    model_output = DisplayEditorOutput(groups=[group("e2", "e1", representative="e2"), group("e3")])
    plan = build_display_plan(original, group_output=model_output, status="completed")
    assert original == before
    assert plan["groups"][0] == {"group_id": "g2", "representative_id": "e2", "member_ids": ["e1", "e2"],
        "related_ids": [], "supports": "attack_request", "number": 2, "default_visible": True,
        "visibility_reasons": ["rank_top"]}
    assert plan["metrics"] == {"original_evidence_count": 3, "group_count": 2, "core_group_count": 2, "merged_count": 1}
    assert plan == validate_display_plan(original, None, plan)
    assert all(not any(key in item for key in ("summary_ko", "explanation_ko", "excerpt")) for item in plan["groups"])


def test_rank_order_and_canonical_numbers_are_independent():
    original = result(*(evidence(index) for index in range(1, 6)))
    plan = build_display_plan(original, groups=[group("e5"), group("e3"), group("e2"), group("e1"), group("e4")], status="completed")
    assert [item["number"] for item in plan["groups"]] == [5, 3, 2, 1, 4]
    assert [item["default_visible"] for item in plan["groups"]] == [True, True, True, False, False]
    assert plan["reason"] == "no_reduction" and plan["metrics"]["merged_count"] == 0


def test_all_issues_and_counter_evidence_protected_beyond_three_groups():
    original = result(*(evidence(index, supports="normal_request" if index == 5 else "attack_request")
                        for index in range(1, 7)), issues=[issue("e4", missing=None), issue("e6")])
    plan = build_display_plan(original, groups=[group(f"e{i}") for i in range(1, 7)], status="completed")
    assert all(item["default_visible"] for item in plan["groups"])
    assert plan["groups"][3]["visibility_reasons"] == ["decision_issue"]
    assert plan["groups"][4]["visibility_reasons"] == ["counter_evidence"]
    assert plan["metrics"]["core_group_count"] == 6


@pytest.mark.parametrize("verdict,opposite", [("true_positive", "normal_request"), ("false_positive", "attack_request")])
def test_opposite_request_evidence_cannot_be_hidden_by_ranking(verdict, opposite):
    matching = "attack_request" if verdict == "true_positive" else "normal_request"
    original = result(*(evidence(i, supports=opposite if i == 5 else matching) for i in range(1, 6)), verdict=verdict)
    plan = build_display_plan(original)
    assert plan["groups"][-1]["default_visible"]
    assert plan["groups"][-1]["visibility_reasons"] == ["counter_evidence"]


def test_inconclusive_protects_both_directions_but_context_is_auxiliary():
    original = result(*(evidence(i, supports="context" if i == 6 else "normal_request" if i == 5 else "attack_request")
                        for i in range(1, 7)), verdict="inconclusive")
    plan = build_display_plan(original)
    assert all(item["default_visible"] for item in plan["groups"][:5])
    assert not plan["groups"][-1]["default_visible"]
    assert plan["groups"][-1]["visibility_reasons"] == ["context_auxiliary"]


def test_context_does_not_spend_primary_rank_and_remains_a_partition_member():
    original = result(evidence(1, supports="context"), evidence(2), evidence(3), evidence(4), evidence(5))
    plan = build_display_plan(original, relations(("e2", ("e1",))))
    assert [item["default_visible"] for item in plan["groups"]] == [False, True, True, True, False]
    assert plan["groups"][1]["related_ids"] == ["e1"]
    assert plan["groups"][0]["member_ids"] == ["e1"]
    assert sorted(identifier for item in plan["groups"] for identifier in item["member_ids"]) == ["e1", "e2", "e3", "e4", "e5"]


def test_context_referenced_by_issue_keeps_visible_independent_group():
    original = result(evidence(1, supports="context"), evidence(2), issues=[issue("e1")])
    plan = build_display_plan(original, relations(("e2", ("e1",))))
    assert plan["groups"][0]["default_visible"]
    assert plan["groups"][0]["visibility_reasons"] == ["decision_issue"]


@pytest.mark.parametrize("groups", [
    [group("e1")], [group("e1", "e1"), group("e2")], [group("e1", "e2"), group("e2")],
    [group("e1", "missing")], [group("e1", "e2", representative="missing")],
    [{**group("e1", "e2"), "explanation_ko": "새 문장"}], [],
])
def test_invalid_partition_extra_text_or_representative_rejected_as_a_whole(groups):
    with pytest.raises(DisplayPlanError, match="^display_groups_invalid$"):
        build_display_plan(result(evidence(1), evidence(2)), groups=groups, status="completed")


@pytest.mark.parametrize("second", ["normal_request", "context"])
def test_cross_support_merges_rejected(second):
    with pytest.raises(DisplayPlanError, match="^display_support_mismatch$"):
        build_display_plan(result(evidence(1), evidence(2, supports=second)), groups=[group("e1", "e2")], status="completed")


@pytest.mark.parametrize("missing", [None, "명시된 미확인 조건입니다."])
def test_issue_connected_to_only_one_member_cannot_be_hidden_by_merge(missing):
    original = result(evidence(1), evidence(2), issues=[issue("e1", missing=missing)])
    with pytest.raises(DisplayPlanError, match="^display_condition_mismatch$"):
        build_display_plan(original, groups=[group("e1", "e2")], status="completed")
    assert build_editor_input(original) is None


def test_same_issue_memberships_may_merge_but_group_remains_visible():
    original = result(evidence(1), evidence(2), issues=[issue("e1", "e2")])
    plan = build_display_plan(original, groups=[group("e1", "e2")], status="completed")
    assert plan["groups"][0]["visibility_reasons"] == ["rank_top", "decision_issue"]


def test_different_required_relations_cannot_be_merged():
    original = result(evidence(1), evidence(2), evidence(3, supports="context"), evidence(4, supports="context"))
    links = relations(("e1", ("e3",)), ("e2", ("e4",)))
    with pytest.raises(DisplayPlanError, match="^display_dependency_mismatch$"):
        build_display_plan(original, links, groups=[group("e1", "e2"), group("e3"), group("e4")], status="completed")
    assert build_editor_input(original, links)["allowed_groups"] == [["e1"], ["e2"], ["e3", "e4"]]


def test_equivalent_physical_dependencies_preserve_every_original_related_id():
    first, second = evidence(3, supports="context"), evidence(4, supports="context")
    second.update(field=first["field"], excerpt=first["excerpt"], source_spans=deepcopy(first["source_spans"]))
    original = result(evidence(1), evidence(2), first, second)
    links = relations(("e1", ("e3",)), ("e2", ("e4",)))
    plan = build_display_plan(original, links, groups=[group("e1", "e2"), group("e3"), group("e4")], status="completed")
    assert plan["groups"][0]["related_ids"] == ["e3", "e4"]


def test_nested_and_cyclic_relations_are_bounded_and_complete():
    original = result(evidence(1), evidence(2, supports="context"), evidence(3, supports="context"))
    links = relations(("e1", ("e2",)), ("e2", ("e3",)), ("e3", ("e2",)))
    plan = build_display_plan(original, links)
    assert plan["groups"][0]["related_ids"] == ["e2", "e3"]
    assert plan["groups"][1]["related_ids"] == ["e3"]
    assert plan["groups"][2]["related_ids"] == ["e2"]


def test_exact_duplicate_code_path_does_not_need_a_model_call():
    first, second = evidence(1), evidence(2)
    second.update(field=first["field"], excerpt=first["excerpt"], source_spans=deepcopy(first["source_spans"]),
                  interpretation_ko=first["interpretation_ko"])
    original = result(first, second)
    assert build_editor_input(original) is None
    plan = build_display_plan(original)
    assert plan["groups"][0]["member_ids"] == ["e1", "e2"]
    assert plan["metrics"]["merged_count"] == 1
    fallback = build_display_plan(original, status="fallback", reason="editor_call_failed")
    assert len(fallback["groups"]) == 2 and all(group["default_visible"] for group in fallback["groups"])
    assert all(group["visibility_reasons"] == ["fallback_original"] for group in fallback["groups"])


def test_long_valid_input_is_not_secretly_truncated():
    original = result(*(evidence(i, interpretation=str(i) + "한" * 1990) for i in range(1, 11)))
    document = build_editor_input(original)
    text = serialized_input(document)
    assert len(document["evidence"]) == 10 and len(text.encode()) > 16000
    assert not input_fits(SimpleNamespace(max_output_tokens=3072, context_window=32768), INSTRUCTIONS, text)
    assert document["evidence"] == original["evidence"]


def test_empty_evidence_has_a_valid_zero_metrics_plan():
    original = result(verdict="inconclusive")
    plan = build_display_plan(original)
    assert plan["groups"] == [] and set(plan["metrics"].values()) == {0}
    assert build_editor_input(original) is None
    assert validate_display_plan(original, None, plan) == plan


def test_legacy_editor_combined_with_relations_keeps_grouping_and_raw_references():
    first, second = evidence(1), evidence(2)
    second.update(field=first["field"], excerpt=first["excerpt"], source_spans=deepcopy(first["source_spans"]))
    original = result(first, second, evidence(3, supports="context"))
    original["evidence_presentation"] = {"version": "evidence-editor-v1", "status": "completed",
        "groups": [group("e1", "e2", representative="e2"), group("e3")]}
    links = relations(("e1", ("e3",)), ("e2", ("e3",)))
    plan = build_display_plan(original, links, reason="editor_disabled")
    assert plan["groups"][0]["representative_id"] == "e2"
    assert plan["groups"][0]["related_ids"] == ["e3"]
    assert plan["metrics"]["merged_count"] == 1


def test_legacy_merge_cannot_remove_a_different_required_condition():
    first, second = evidence(1), evidence(2)
    second.update(field=first["field"], excerpt=first["excerpt"], source_spans=deepcopy(first["source_spans"]))
    original = result(first, second, evidence(3, supports="context"))
    original["evidence_presentation"] = {"version": "evidence-editor-v1", "status": "completed",
        "groups": [group("e1", "e2"), group("e3")]}
    plan = build_display_plan(original, relations(("e1", ("e3",))))
    assert plan["metrics"]["group_count"] == 3


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(source_hash="0" * 64),
    lambda p: p.update(extra="untrusted"),
    lambda p: p["metrics"].update(group_count=999),
    lambda p: p["groups"][0].update(number=999),
    lambda p: p["groups"][0].update(group_id="g999"),
    lambda p: p["groups"][0].update(supports="normal_request"),
    lambda p: p["groups"][0].update(default_visible=False),
    lambda p: p["groups"][0].update(visibility_reasons=[]),
    lambda p: p["groups"][0].update(related_ids=["e2"]),
    lambda p: p["groups"][0].update(member_ids=[]),
    lambda p: p.update(reason="synthetic-private-provider-output"),
])
def test_matching_hash_does_not_authorize_tampered_plan(mutate):
    original = result(evidence(1), evidence(2))
    plan = build_display_plan(original)
    mutate(plan)
    with pytest.raises(DisplayPlanError, match="^display_plan_invalid$"):
        validate_display_plan(original, None, plan)


@pytest.mark.parametrize("mutate", [
    lambda r: r["evidence"][0].update(interpretation_ko="변경된 설명입니다."),
    lambda r: r["evidence"][0].update(supports="normal_request"),
    lambda r: r["evidence"][0]["source_spans"][0].update(end=999),
    lambda r: r["evidence"][0]["origins"][0].update(source_id="c999"),
    lambda r: r.update(verdict="inconclusive"),
    lambda r: r["policy"].update(decision_evidence_ids=["e2"]),
    lambda r: r.update(decision_issues=[issue("e2")]),
])
def test_every_material_display_source_change_invalidates_existing_plan(mutate):
    original = result(evidence(1), evidence(2))
    plan = build_display_plan(original)
    mutate(original)
    with pytest.raises(DisplayPlanError):
        validate_display_plan(original, None, plan)


def test_metadata_not_used_for_display_does_not_change_source_hash():
    original = result(evidence(1), evidence(2))
    expected = source_hash(original)
    original.update(summary_ko="표시 계획과 무관한 요약입니다.", agent={"model_name": "synthetic-model"})
    assert source_hash(original) == expected


@pytest.mark.parametrize("reason", ["editor_disabled", "display_record_invalid", "display_lease_expired", "analysis_not_completed"])
def test_job_public_reason_codes_are_supported_without_exposing_text(reason):
    plan = build_display_plan(result(evidence(1)), reason=reason)
    assert plan["reason"] == reason


def test_common_frontend_parity_fixture():
    fixture_path = Path(__file__).with_name("fixtures") / "evidence_display_plan.json"
    if not fixture_path.exists():
        pytest.fail("The shared, synthetic frontend/backend parity fixture must be present")
    cases = json.loads(fixture_path.read_text())
    for case in cases:
        plan = build_display_plan(case["result"], case["relations"], groups=case.get("model_groups"),
                                  status=case["status"], reason=case.get("reason"))
        assert plan == case["expected"]
        assert source_document(case["result"], case["relations"]) == case["source_document"]
        assert validate_display_plan(case["result"], case["relations"], plan) == plan
