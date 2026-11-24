"""Synthetic guidance and same-source hints; no model/network calls."""
from copy import deepcopy
from difflib import ndiff
import hashlib
from pathlib import Path

import pytest

from app.agent import evidence_display as old_editor, evidence_display_v2 as editor
from app.agent import legacy_prompts_v231 as previous, prompts
from app.agent.contracts_v231 import SecurityAnalysisOutputV231
from app.agent.contracts_v232 import SecurityAnalysisOutputV232
from app.services.evidence_display_plan import build_display_plan
from test_core_evidence_meaning_groups import two_role_same_excerpt
from test_core_evidence_v3 import dense_result, group, issue, links


def test_previous_guidance_is_preserved_byte_for_byte_and_roles_stay_independent():
    raw = Path(previous.__file__).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "9498b2ad35533d1bf2228e4341904839a1eb7a3d3ed22793c90ede974d105a88"
    assert prompts.PROMPT_VERSION == "waf-judgment-v2.32"
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.32"
    assert prompts.DEFAULT_POLICY_TEXT == previous.DEFAULT_POLICY_TEXT
    assert prompts.PRIMARY_ROLE == previous.PRIMARY_ROLE
    assert prompts.VERIFIER_ROLE == previous.VERIFIER_ROLE
    assert "다른 Agent의 답변은 제공되지 않는다" in prompts.VERIFIER_ROLE


def test_only_two_common_evidence_lines_change_without_a_hard_count_or_unique_source_rule():
    changes = list(ndiff(previous.FIXED_INSTRUCTIONS.splitlines(), prompts.FIXED_INSTRUCTIONS.splitlines()))
    assert len([line for line in changes if line.startswith("- ")]) == 2
    assert len([line for line in changes if line.startswith("+ ")]) == 2
    assert len(prompts.FIXED_INSTRUCTIONS) <= len(previous.FIXED_INSTRUCTIONS) + 150
    assert "관측, 의도와 조건부 영향을 하나의 논점" in prompts.FIXED_INSTRUCTIONS
    assert "다른 동작, 성립 조건이나 필수 원문" in prompts.FIXED_INSTRUCTIONS
    assert "반대 해석은 별도 evidence_id" in prompts.FIXED_INSTRUCTIONS
    assert "핵심 해석 2~3개" not in prompts.FIXED_INSTRUCTIONS


def test_initial_output_schema_has_no_new_fields_or_validation_constraints():
    before, after = SecurityAnalysisOutputV231.model_json_schema(), SecurityAnalysisOutputV232.model_json_schema()
    before.pop("title", None)
    after.pop("title", None)
    assert before == after


def test_editor_keeps_existing_id_only_wire_limits_and_does_not_generate_prose():
    assert editor.DisplayEditorOutput is old_editor.DisplayEditorOutput
    assert (editor.MAX_INPUT_BYTES, editor.MAX_OUTPUT_TOKENS, editor.MAX_SECONDS) == (
        old_editor.MAX_INPUT_BYTES, old_editor.MAX_OUTPUT_TOKENS, old_editor.MAX_SECONDS)
    assert editor.VERSION == "evidence-display-editor-v2"
    assert "Primary 우선이나 문장 길이로 고르지 않는다" in editor.INSTRUCTIONS
    assert "같은 논점이 하나면 한 묶음으로 충분하다" in editor.INSTRUCTIONS
    assert "새로 작성하거나 수정하지 않는다" in editor.INSTRUCTIONS


def test_same_source_hints_include_both_roles_without_copying_or_changing_evidence():
    original = two_role_same_excerpt()
    before = deepcopy(original)
    document = editor.build_editor_input(original)
    assert document["same_source_groups"] == [["e1", "e2", "e3", "e4"]]
    assert document["evidence"] == original["evidence"]
    assert set(document) == {"evidence", "allowed_groups", "relations", "decision_issues", "verdict",
                             "required_source_ids", "same_source_groups"}
    assert all(isinstance(identifier, str) for group in document["same_source_groups"] for identifier in group)
    assert original == before
    # The model may choose an existing Verifier explanation as representative.
    output = editor.DisplayEditorOutput(groups=[group("e1", "e2", "e3", "e4", representative="e3")])
    plan = build_display_plan(original, group_output=output, status="completed")
    assert len(plan["groups"]) == 1 and plan["groups"][0]["representative_id"] == "e3"


@pytest.mark.parametrize("difference", ["position", "field", "support", "missing_positions", "condition", "dependency"])
def test_same_text_hint_never_crosses_source_direction_condition_or_dependency_boundaries(difference):
    original = dense_result(4, exact=True, supports=["normal_request", "normal_request", "context", "context"])
    if difference == "position":
        original["evidence"][1]["source_spans"] = [{"start": 500, "end": 508}]
    elif difference == "field":
        original["evidence"][1]["field"] = "extra_fields.request"
    elif difference == "support":
        original["evidence"][1]["supports"] = "attack_request"
    elif difference == "missing_positions":
        original["evidence"][0]["source_spans"] = []
        original["evidence"][1]["source_spans"] = []
    elif difference == "condition":
        original["decision_issues"] = [issue("e1")]
    relation = None
    if difference == "dependency":
        original["evidence"][3]["excerpt"] = "different context"
        relation = links(("e1", ("e3",)), ("e2", ("e4",)))
    document = editor.build_editor_input(original, relation)
    groups = document["same_source_groups"] if document is not None else []
    assert not any({"e1", "e2"} <= set(ids) for ids in groups)


def test_same_source_hint_is_not_authority_to_merge_opposing_interpretations():
    original = two_role_same_excerpt()
    original["evidence"][3]["supports"] = "attack_request"
    document = editor.build_editor_input(original)
    assert document["same_source_groups"] == [["e1", "e2", "e3"]]
    with pytest.raises(ValueError, match="display_support_mismatch"):
        build_display_plan(original, groups=[group("e1", "e2", "e3", "e4")], status="completed")
