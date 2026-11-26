"""Frozen same-role review, shared source membership and exclusion closure."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from app.agent.contracts_v237 import EvidenceDecisionReviewOutput, SecurityAnalysisOutputV237
from app.agent.evidence_decision_review_v237 import review_base, review_input
from test_contracts_v237 import wire, validate, opposite_roots
from test_flat_evidence_v228 import catalog


def neutral_base(value=None):
    value = wire() if value is None else deepcopy(value)
    value.update(verdict="inconclusive", confidence_score=0, decision_issue=None,
                 summary_ko="남은 요청 원문을 기준으로 판정을 재검토합니다.")
    value["threat_analysis"]["severity"] = "UNKNOWN"
    return value


def review_wire(value=None):
    original = wire() if value is None else value
    return {"retained_evidence_ids": [item["evidence_id"] for item in original["evidence"]],
        **{key: deepcopy(original[key]) for key in ("verdict", "confidence_score", "summary_ko",
            "threat_analysis", "decision_issue", "interpretation_conflict")}}


def context(base=None, eligible=None):
    base = neutral_base() if base is None else base
    return {"source_catalog": catalog(), "review_original_output": base,
        "review_eligible_evidence_ids": [item["evidence_id"] for item in base["evidence"]] if eligible is None else eligible}


def test_review_freezes_all_native_fields_memberships_and_original_slot_order():
    ctx, result = context(), review_wire()
    original = deepcopy(ctx)
    result["retained_evidence_ids"].reverse()
    reviewed = EvidenceDecisionReviewOutput.model_validate(result, context=ctx)
    output = reviewed.to_analysis()
    assert isinstance(output, SecurityAnalysisOutputV237)
    assert output.model_dump(mode="json")["evidence"] == ctx["review_original_output"]["evidence"]
    assert output.evidence[1].contributes_to_evidence_ids == ["e2"]
    assert ctx == original
    output.evidence[1].contributes_to_evidence_ids.clear()
    assert reviewed.to_analysis().evidence[1].contributes_to_evidence_ids == ["e2"]
    assert "_analysis" not in reviewed.model_dump(mode="json")


@pytest.mark.parametrize("eligible,code", [
    (["e2"], "review_evidence_dependency_missing"),
    (["e5"], "decision_review_context_invalid"),
    (["e2", "e2"], "decision_review_context_invalid"),
])
def test_review_cannot_hide_a_required_contribution_or_surviving_root(eligible, code):
    with pytest.raises(ValidationError) as failure:
        EvidenceDecisionReviewOutput.model_validate(review_wire(), context=context(eligible=eligible))
    assert failure.value.errors()[0]["type"] == code


def test_independent_opposite_root_cannot_be_omitted_or_changed_by_decision_review():
    base = neutral_base(opposite_roots())
    result = review_wire()
    with pytest.raises(ValidationError) as failure:
        EvidenceDecisionReviewOutput.model_validate(result, context=context(base))
    assert failure.value.errors()[0]["type"] == "review_evidence_omitted"
    result["retained_evidence_ids"].append("e1")
    output = EvidenceDecisionReviewOutput.model_validate(result, context=context(base)).to_analysis()
    assert output.evidence[2].supports == "normal_request"
    assert output.model_dump(mode="json")["evidence"] == base["evidence"]
    result["evidence"] = []
    with pytest.raises(ValidationError) as failure:
        EvidenceDecisionReviewOutput.model_validate(result, context=context(base))
    assert failure.value.errors()[0]["type"] == "extra_forbidden"


def base_for(original, *, missing=(), excluded=(), corrected=None):
    selection = original.to_selection(input_truncated=False)
    if corrected:
        selection = selection.model_copy(update={"evidence": [item.model_copy(update={"source_id": corrected.get(index, item.source_id)})
            for index, item in enumerate(selection.evidence)]})
    resolutions = [{"index": index, "source_id": item.source_id} for index, item in enumerate(selection.evidence) if index not in missing]
    return review_base(original, selection, resolutions, catalog(), set(excluded))


def test_lost_shared_contribution_invalidates_its_roots_but_not_independent_counterevidence():
    value = opposite_roots()
    value["evidence"][1]["contributes_to_evidence_ids"] = ["e2"]
    original = validate(value)
    before = original.model_dump(mode="json")
    base, blocked = base_for(original, missing={1})
    assert blocked == {0, 1}
    assert [item.evidence_id for item in base.evidence] == ["e1"]
    assert base.evidence[0].supports == "normal_request"
    assert base.to_selection(input_truncated=False).evidence[0].source_id == "c6"
    assert original.model_dump(mode="json") == before


def test_lost_one_root_keeps_shared_contribution_and_other_root_with_only_membership_pruned():
    original = validate(opposite_roots())
    before = original.model_dump(mode="json")
    base, blocked = base_for(original, excluded={0})
    assert blocked == {0}
    assert [item.evidence_id for item in base.evidence] == ["e5", "e1"]
    assert base.evidence[0].contributes_to_evidence_ids == ["e1"]
    assert base.evidence[0].supports == "context"
    assert base.evidence[1].supports == "normal_request"
    assert base.expanded_evidence()[1] == [{"representative_index": 1, "related_indexes": [0]}]
    assert [item.source_id for item in base.to_selection(input_truncated=False).evidence] == ["c1", "c6"]
    assert original.model_dump(mode="json") == before


def test_orphaned_contribution_is_removed_not_promoted_to_new_root():
    value = opposite_roots()
    value["evidence"][1]["contributes_to_evidence_ids"] = ["e2"]
    base, blocked = base_for(validate(value), excluded={0})
    assert blocked == {0, 1}
    assert [item.evidence_id for item in base.evidence] == ["e1"]
    assert base.evidence[0].contributes_to_evidence_ids == []


def test_lost_common_contribution_blocks_every_declared_root():
    base, blocked = base_for(validate(opposite_roots()), missing={1})
    assert base is None
    assert blocked == {0, 1, 2}


def test_source_only_correction_preserves_memberships_text_and_support():
    original = validate(wire())
    before = original.model_dump(mode="json")
    base, blocked = base_for(original, corrected={0: "c6"})
    assert blocked == set()
    assert base.evidence[0].source_id == "c6"
    assert base.evidence[0].interpretation_ko == original.evidence[0].interpretation_ko
    assert base.evidence[0].supports == original.evidence[0].supports
    assert base.evidence[1].contributes_to_evidence_ids == ["e2"]
    assert original.model_dump(mode="json") == before


def test_review_input_uses_only_surviving_original_candidates_and_whole_bounded_wire():
    base = validate(neutral_base())
    text = review_input(base, catalog(), {"capture_context": {"payload_omitted": False}}, 20000)
    document = json.loads(text)
    assert document["evidence_decision_review"]["original_output"] == base.model_dump(mode="json")
    assert {item["source_id"] for item in document["evidence_decision_review"]["source_catalog"]["items"]} == {"c1", "c2"}
    assert document["capture_context"] == {"payload_omitted": False}
    assert review_input(base, catalog(), {}, 1024) is None
