"""Synthetic essential comparisons, immutable v3 and frozen v1 rejection."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.agent import evidence_relations as old
from app.agent import evidence_relations_v238 as links
from app.agent.executor import AgentCallResult
from app.core_result_contracts import revalidate_core_result
from app.result_contracts import AnalysisResultV3, validate_new_result
from app.services import evidence_display_jobs_v3 as old_jobs
from app.services import evidence_display_jobs_v4 as jobs
from app.services.core_evidence_v4 import build_core_result
from app.services.evidence_display_plan_v3 import (
    DisplayPlanError, build_display_plan, build_editor_input, validate_display_plan,
)
from test_agent_openai import openai_profile
from test_core_evidence_v3 import dense_result, group
from test_core_evidence_meaning_groups import two_role_same_excerpt


def result():
    value = dense_result(5, verdict="true_positive", supports=[
        "attack_request", "normal_request", "context", "context", "context"])
    value["policy"]["fixed_rules_version"] = "waf-system-v2.38"
    value["policy"]["prompt_version"] = "waf-judgment-v2.38/policy-1"
    value["policy"]["decision_evidence_ids"] = ["e1"]
    return validate_new_result(value)


def relations(*values):
    return {"version": links.VERSION, "groups": [{"role": role,
        "representative_evidence_id": key, "related_evidence_ids": list(related)}
        for role, key, related in (values or [("primary", "e1", ["e2"])])]}


def call_for(value, mapping=None):
    selected = [{"source_id": item["origins"][0]["source_id"],
                 "supports": item["supports"]} for item in value["evidence"]]
    refs = mapping or [{"representative_index": 0, "related_indexes": [1]}]
    expanded = {"evidence": selected, "evidence_relations": refs}
    history = [{"output": {"native": True}, "expanded_output": expanded,
        "assessment": expanded, "source_resolutions": [{"index": index,
            "source_id": item["source_id"]} for index, item in enumerate(selected)]}]
    return AgentCallResult(object(), "synthetic", None, "completed", None, None, {}, history)


def view(value, references, groups=None, status="skipped"):
    plan = build_display_plan(value, references, groups=groups, status=status)
    return {"version": jobs.VIEW_VERSION, "status": status, "plan": plan,
            "relations": references, "metadata": {}}


def test_essential_opposite_reference_is_accepted_without_reclassification_or_conflict():
    value, refs = result(), relations()
    before = deepcopy(value)
    assert links.validate_evidence_relations(refs, value["evidence"]) == refs
    assert links.build_evidence_relations(call_for(value), evidence=value["evidence"]) == refs
    assert value == before and value["interpretation_conflict"] is False
    with pytest.raises(old.EvidenceRelationsError, match="conflicting_supports"):
        old.validate_evidence_relations({**refs, "version": old.VERSION}, value["evidence"])
    assessment = call_for(value).grounding_history[0]["assessment"]
    sources = call_for(value).grounding_history[0]["source_resolutions"]
    assert links.assert_relation_sources(assessment, sources) == {i: f"c{i + 1}" for i in range(5)}
    with pytest.raises(old.EvidenceRelationsError, match="conflicting_supports"):
        old.assert_relation_sources(assessment, sources)


def test_shared_browser_parity_fixture_binds_role_issues_and_mixed_references():
    from app.services.evidence_display_plan_v3 import source_document
    fixture = json.loads((Path(__file__).parent / "fixtures/evidence_display_plan_v3.json").read_text())
    assert source_document(fixture["result"], fixture["relations"]) == fixture["source_document"]
    assert validate_display_plan(fixture["result"], fixture["relations"], fixture["view"]["plan"]) == fixture["view"]["plan"]
    changed = deepcopy(fixture["result"])
    changed["primary"]["decision_issues"][0]["point_ko"] = "다른 역할별 판단 조건입니다."
    with pytest.raises(DisplayPlanError):
        validate_display_plan(changed, fixture["relations"], fixture["view"]["plan"])


@pytest.mark.parametrize("damage", ["missing", "role", "self", "duplicate", "version", "origin"])
def test_new_relation_validation_keeps_every_nonsemantic_fence(damage):
    value, refs = result(), relations()
    if damage == "missing": refs["groups"][0]["related_evidence_ids"] = ["e99"]
    if damage == "role": refs["groups"][0]["role"] = "verifier"
    if damage == "self": refs["groups"][0]["related_evidence_ids"] = ["e1"]
    if damage == "duplicate": refs["groups"][0]["related_evidence_ids"] = ["e2", "e2"]
    if damage == "version": refs["version"] = old.VERSION
    if damage == "origin": value["evidence"][1]["origins"][0]["index"] = 0
    with pytest.raises(old.EvidenceRelationsError):
        links.validate_evidence_relations(refs, value["evidence"])


def test_missing_or_mismatched_resolved_source_is_never_a_partial_success():
    value, call = result(), call_for(result())
    call.grounding_history[0]["source_resolutions"][1]["source_id"] = "c99"
    with pytest.raises(old.EvidenceRelationsError):
        links.build_evidence_relations(call, evidence=value["evidence"])


def test_only_validated_final_review_can_replace_original_relation_slots():
    value = result()
    call = call_for(value)
    final = deepcopy(call.grounding_history[0])
    final.update(kind="decision_review", decision_review_validated=True)
    final["expanded_output"]["evidence_relations"] = [{"representative_index": 0, "related_indexes": [2]}]
    call = replace(call, grounding_history=[*call.grounding_history, final],
                   telemetry={"evidence_decision_review": {"recovered": True}})
    assert links.build_evidence_relations(call, evidence=value["evidence"])["groups"][0]["related_evidence_ids"] == ["e3"]
    final["decision_review_validated"] = False
    with pytest.raises(old.EvidenceRelationsError):
        links.build_evidence_relations(call, evidence=value["evidence"])


@pytest.mark.parametrize("state", ["skipped", "fallback", "missing"])
def test_core_keeps_essential_counter_source_and_unchanged_verdict_even_without_editor(state):
    value = result()
    refs = relations(("primary", "e1", ["e2"]), ("primary", "e2", ["e4"]),
                     ("primary", "e4", ["e1"]))
    before, schema = deepcopy(value), AnalysisResultV3.model_json_schema()
    display = view(value, refs) if state == "skipped" else {"status": state}
    projected = build_core_result(value, relations=refs, display_view=display)["result"]
    assert {item.evidence_id for item in projected.evidence} >= {"e1", "e2", "e4"}
    assert projected.verdict.value == "true_positive" and not projected.interpretation_conflict
    assert projected.verifier.executed is False and projected.verifier.reasons == []
    indexed = {item["evidence_id"]: item for item in value["evidence"]}
    assert all(item.model_dump(mode="json") == indexed[item.evidence_id] for item in projected.evidence)
    assert revalidate_core_result(projected).model_dump(mode="json") == projected.model_dump(mode="json")
    assert value == before and AnalysisResultV3.model_json_schema() == schema


def test_directional_policy_anchor_is_not_waived_by_allowing_mixed_dependencies():
    value = result()
    value["policy"]["decision_evidence_ids"] = ["e2"]
    with pytest.raises(ValueError, match="decision_evidence_missing"):
        build_core_result(value, relations=relations(), display_view=view(value, relations()))


def test_unlocated_equal_text_cannot_break_or_authorize_a_fallback_merge():
    value = result()
    for item in value["evidence"][:2]:
        item.update(source_spans=[], excerpt="same synthetic bytes", interpretation_ko="같은 입력을 참조한 해석입니다.", supports="attack_request")
    projected = build_core_result(value, relations=relations(), display_view={"status": "fallback"})["result"]
    assert {item.evidence_id for item in projected.evidence} >= {"e1", "e2"}


@pytest.mark.parametrize("damage", ["support", "different_excerpt", "different_dependencies"])
def test_editor_still_rejects_unsafe_merges_with_valid_mixed_references(damage):
    value = result()
    if damage == "different_excerpt": value["evidence"][1]["supports"] = "attack_request"
    if damage == "different_dependencies":
        value["evidence"][1].update(supports="attack_request",
            excerpt=value["evidence"][0]["excerpt"], source_spans=value["evidence"][0]["source_spans"])
    refs = relations()
    groups = [group("e1", "e2"), group("e3"), group("e4"), group("e5")]
    with pytest.raises(DisplayPlanError):
        build_display_plan(value, refs, groups=groups, status="completed")


def test_safe_same_point_cross_role_merge_preserves_separate_counterinterpretation():
    value = two_role_same_excerpt()
    for index in (1, 3):
        value["evidence"][index].update(supports="attack_request", excerpt="synthetic=constraint",
            source_spans=[{"start": 30, "end": 50}], interpretation_ko="처리 조건에 따라 다른 보안 의미가 성립하는 반대 정황입니다.")
    value["policy"]["fixed_rules_version"] = "waf-system-v2.38"
    value["policy"]["decision_evidence_ids"] = ["e1", "e3"]
    refs = relations(("primary", "e1", ["e2"]), ("verifier", "e3", ["e4"]))
    plan = view(value, refs, [group("e1", "e3", representative="e3"), group("e2", "e4")], "completed")
    assert build_editor_input(value, refs) is not None
    assert validate_display_plan(value, refs, plan["plan"]) == plan["plan"]
    projected = build_core_result(value, relations=refs, display_view=plan)["result"].model_dump(mode="json")
    assert len(projected["evidence"]) == 2
    assert {item["supports"] for item in projected["evidence"]} == {"attack_request", "normal_request"}
    assert projected["evidence"][0]["origins"] == value["evidence"][2]["origins"]


@pytest.mark.parametrize("version", ["evidence-display-editor-v1", "evidence-display-editor-v2", old_jobs.VERSION, jobs.VERSION])
def test_snapshot_versions_and_artifacts_preserve_old_editor_engines(version, settings):
    from app.services.crypto import CryptoService
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    profile = openai_profile(id="synthetic-v4-display", provider="vllm", base_url="http://10.0.0.10:8000/v1")
    captured = jobs.capture_display_profile(profile, version=version)
    assert jobs.load_snapshot(crypto, crypto.encrypt_text(captured.model_dump_json())) == captured
    if version != jobs.VERSION:
        assert captured.model_dump() == old_jobs.capture_display_profile(profile, version=version).model_dump()
    before, after = old_jobs.implementation_artifact(), jobs.implementation_artifact()
    assert all(after["files"][key] == digest for key, digest in before["files"].items())
    assert after["version"] == "evidence-display-editor-v4"
