"""Deterministic exclusion, input bounds and exact original snapshot preservation."""
from copy import deepcopy
import json

import pytest

from app.agent import evidence_decision_review_v231 as helper
from app.agent.contracts_v231 import SecurityAnalysisOutputV231
from app.agent.evidence import EvidenceSourceResolver
from app.agent.evidence_candidates import resolve_selection
from test_decision_review_worker_v231 import case, BAD_CLAIM, BAD_SUMMARY, DEPENDENT_CLAIM


def source_state(data):
    original = SecurityAnalysisOutputV231.model_validate(data["value"])
    selection = original.to_selection(input_truncated=False).legacy_bridge()
    sources = EvidenceSourceResolver(data["raw"], data["event"], data["parsed"],
        submitted_payload_spans=data["built"].retained_payload_spans,
        submitted_event=data["document"]["event"])
    catalog = data["document"]["evidence_candidates"]
    _, _, resolutions = resolve_selection(selection, catalog, sources)
    return original, selection, resolutions, catalog


def telemetry(first=1):
    return [{"output_validation_retry": {"attempt_count": count, "attempts": [{} for _ in range(count)]}}
            for count in (first, 1)]


@pytest.mark.parametrize("first", [1, 2, 3, 4])
def test_budget_counts_existing_logical_attempts_without_granting_extra_calls(first):
    assert helper.logical_attempts_used(telemetry(first)) == first + 1
    assert helper.MAX_LOGICAL_ATTEMPTS == 5


@pytest.mark.parametrize("bad", [None, [], [{"output_validation_retry": {}}],
    [{"output_validation_retry": {"attempt_count": True, "attempts": [{}]}}, telemetry()[1]],
    [{"output_validation_retry": {"attempt_count": 1, "attempts": [None]}}, telemetry()[1]],
    telemetry() + telemetry(),
    [{"output_validation_retry": {"attempt_count": 2, "attempts": [{}]}}, telemetry()[1]]])
def test_unknown_or_malformed_attempt_accounting_cannot_enable_a_review(bad):
    assert helper.logical_attempts_used(bad) is None


def test_excluded_claim_and_its_dependencies_do_not_contaminate_neutral_decision_base():
    data = case()
    original, selection, resolutions, catalog = source_state(data)
    before = deepcopy((original.model_dump(mode="json"), selection.model_dump(mode="json"), resolutions, catalog))
    base, blocked = helper.review_base(original, selection, resolutions, catalog, {1})
    assert blocked == {1, 2}
    assert [item.evidence_id for item in base.evidence] == ["e1"]
    assert base.evidence[0].model_dump(mode="json") == data["good"]
    assert base.verdict.value == "inconclusive"
    assert base.threat_analysis.model_dump(mode="json") == helper.NEUTRAL_THREAT
    assert base.decision_issue is None and base.interpretation_conflict is False
    assert BAD_CLAIM not in base.model_dump_json() and DEPENDENT_CLAIM not in base.model_dump_json()
    assert BAD_SUMMARY not in base.model_dump_json()
    assert (original.model_dump(mode="json"), selection.model_dump(mode="json"), resolutions, catalog) == before


def test_clear_duplicate_of_a_discarded_claim_cannot_become_independent_support():
    data = case()
    alias = deepcopy(data["value"]["evidence"][0])
    alias["evidence_id"] = "e4"
    data["value"]["evidence"].append(alias)
    original, selection, resolutions, catalog = source_state(data)
    base, blocked = helper.review_base(original, selection, resolutions, catalog, {0, 1})
    assert base is None and blocked == {0, 1, 2, 3}


@pytest.mark.parametrize("budget", [None, True, 100, 1023, "65536"])
def test_missing_or_small_input_budget_skips_instead_of_truncating_interpretations(budget):
    data = case()
    original, selection, resolutions, catalog = source_state(data)
    base, _ = helper.review_base(original, selection, resolutions, catalog, {1})
    assert helper.review_input(base, catalog, data["document"], budget) is None


def test_review_input_has_exact_budget_boundary_only_original_safe_snippets_and_no_mutation():
    data = case()
    original, selection, resolutions, catalog = source_state(data)
    base, _ = helper.review_base(original, selection, resolutions, catalog, {1})
    before = deepcopy((base.model_dump(mode="json"), catalog, data["document"]))
    text = helper.review_input(base, catalog, data["document"], 65536)
    assert helper.review_input(base, catalog, data["document"], len(text)) == text
    assert helper.review_input(base, catalog, data["document"], len(text) - 1) is None
    assert data["raw"] not in text
    envelope = json.loads(text)["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e1"]
    assert all(item in catalog["items"] for item in envelope["source_catalog"]["items"])
    assert (base.model_dump(mode="json"), catalog, data["document"]) == before


def test_hold_explanation_identifies_citation_error_instead_of_claiming_payload_is_unreadable():
    history = [{"rejected_evidence": [{"index": 1, "reason": "signature_support_not_allowed"}]}]
    assert "시그니처" in helper.hold_summary(history)
    assert "재검토를 완료하지 못" in helper.hold_summary(history, review_failed=True)
    assert "최종 판정을 뒷받침" in helper.hold_summary([], context_only=True)
