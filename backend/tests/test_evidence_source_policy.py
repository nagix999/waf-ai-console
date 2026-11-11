"""Synthetic provenance policy checks; no provider, DB or operating data."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agent.contracts import AssessedEvidenceSelection, RequestEvidenceSelection
from app.agent.evidence_source_policy import (
    SIGNATURE_SUPPORT_ERROR, catalog_for_source_policy, is_signature_source_field, signature_support_issues,
)


RULES = "waf-system-v2.21"
EXCERPT = "Synthetic detection name"


def selected(source_id="c1", supports="attack_request", *, bridge=False):
    model = AssessedEvidenceSelection if bridge else RequestEvidenceSelection
    item = model(source_id=source_id, supports=supports,
                 interpretation_ko="합성 요청에서 관찰한 값과 의미를 설명합니다.")
    return SimpleNamespace(evidence=[item])


def catalog(field="signature", *, source_id="c1"):
    return {"version": "evidence-selection-v3", "items": [{
        "source_id": source_id, "field": field, "excerpt": EXCERPT,
        "start": None, "end": None,
    }], "omitted": False}


@pytest.mark.parametrize("field", ["signature", "event.signature", " signature ", " event.signature\n"])
def test_only_canonical_signature_field_and_explicit_alias_are_metadata(field):
    assert is_signature_source_field(field)


@pytest.mark.parametrize("field", [
    None, 1, {}, "", "Signature", "event.event.signature", "signature.value",
    "payload", "payload.signature", "payload.body.signature", "payload.query.signature",
    "extra_fields.signature", "extra_fields.event.signature", "event.extra_fields.signature",
    "extra_fields.items.0.signature", "extra_fields.items[0].signature", "signature_assessment",
])
def test_same_named_nested_request_and_extension_fields_are_not_blocked(field):
    assert not is_signature_source_field(field)


@pytest.mark.parametrize("field", ["signature", "event.signature"])
@pytest.mark.parametrize("supports,bridge", [
    ("attack_request", False), ("normal_request", False),
    ("true_positive", True), ("false_positive", True),
])
def test_both_wire_and_bridge_directions_cannot_use_signature(field, supports, bridge):
    output, sources = selected(supports=supports, bridge=bridge), catalog(field)
    before = output.evidence[0].model_dump(mode="json"), deepcopy(sources)
    assert signature_support_issues(output, sources, fixed_rules_version=RULES) == [
        {"index": 0, "reason": SIGNATURE_SUPPORT_ERROR},
    ]
    assert (output.evidence[0].model_dump(mode="json"), sources) == before


@pytest.mark.parametrize("bridge", [False, True])
def test_signature_context_remains_available_for_signature_comparison_and_relations(bridge):
    output = selected(supports="context", bridge=bridge)
    assert not signature_support_issues(output, catalog(), fixed_rules_version=RULES)


@pytest.mark.parametrize("field", [
    "payload", "payload.body", "payload.query.signature", "extra_fields.signature",
    "event.extra_fields.signature", "extra_fields.event.signature",
])
@pytest.mark.parametrize("supports", ["attack_request", "normal_request", "context"])
def test_equal_payload_or_extension_text_does_not_inherit_signature_provenance(field, supports):
    assert not signature_support_issues(selected(supports=supports), catalog(field),
                                        fixed_rules_version=RULES)


@pytest.mark.parametrize("revision", [
    None, "waf-system-v2.15", "waf-system-v2.19", "waf-system-v2.20", "waf-system-v2.22",
])
def test_legacy_unknown_and_future_pinned_runs_do_not_silently_change(revision):
    assert not signature_support_issues(selected(), catalog(), fixed_rules_version=revision)


def test_selected_source_id_not_model_claimed_field_determines_provenance():
    output = selected()
    # These extra attributes simulate an untrusted claimed path. Real output
    # schemas forbid them, and this source policy must not trust them either.
    forged = SimpleNamespace(source_id="c1", supports="attack_request", field="payload", excerpt="other")
    output.evidence = [forged]
    assert signature_support_issues(output, catalog(), fixed_rules_version=RULES) == [
        {"index": 0, "reason": SIGNATURE_SUPPORT_ERROR},
    ]


def test_missing_source_is_left_to_exact_citation_validation():
    assert not signature_support_issues(selected("c999"), catalog(), fixed_rules_version=RULES)


def test_duplicate_id_cannot_hide_signature_source_by_last_write():
    sources = catalog()
    sources["items"].append(catalog("payload")["items"][0])
    assert signature_support_issues(selected(), sources, fixed_rules_version=RULES) == [
        {"index": 0, "reason": SIGNATURE_SUPPORT_ERROR},
    ]
    sources["items"].reverse()
    assert signature_support_issues(selected(), sources, fixed_rules_version=RULES) == [
        {"index": 0, "reason": SIGNATURE_SUPPORT_ERROR},
    ]


def test_original_evidence_indexes_and_order_survive_mixed_valid_sources():
    output = SimpleNamespace(evidence=[
        selected("c2").evidence[0], selected("c1", supports="context").evidence[0],
        selected("c1", supports="normal_request").evidence[0], selected("c1").evidence[0],
        selected("c3").evidence[0],
    ])
    sources = catalog()
    sources["items"].extend([catalog("payload", source_id="c2")["items"][0],
                             catalog("extra_fields.signature", source_id="c3")["items"][0]])
    assert signature_support_issues(output, sources, fixed_rules_version=RULES) == [
        {"index": 2, "reason": SIGNATURE_SUPPORT_ERROR},
        {"index": 3, "reason": SIGNATURE_SUPPORT_ERROR},
    ]


def test_after_citation_only_correction_policy_is_rechecked_without_reclassifying():
    original = selected()
    original_value = original.evidence[0].model_dump(mode="json")
    sources = catalog()
    sources["items"].extend([catalog("event.signature", source_id="c2")["items"][0],
                             catalog("payload", source_id="c3")["items"][0]])
    to_alias = SimpleNamespace(evidence=[original.evidence[0].model_copy(update={"source_id": "c2"})])
    to_payload = SimpleNamespace(evidence=[original.evidence[0].model_copy(update={"source_id": "c3"})])
    assert signature_support_issues(to_alias, sources, fixed_rules_version=RULES)
    assert not signature_support_issues(to_payload, sources, fixed_rules_version=RULES)
    assert to_payload.evidence[0].supports == original.evidence[0].supports
    assert to_payload.evidence[0].interpretation_ko == original.evidence[0].interpretation_ko
    assert original.evidence[0].model_dump(mode="json") == original_value


def test_diagnostics_never_contain_catalog_excerpts_or_interpretations():
    result = signature_support_issues(selected(), catalog(), fixed_rules_version=RULES)
    assert EXCERPT not in str(result)
    assert "합성 요청" not in str(result)
    assert set(result[0]) == {"index", "reason"}


@pytest.mark.parametrize("sources", [None, [], {}, {"items": []}, {"items": [None, 1, {}]}])
def test_missing_or_non_metadata_catalogs_are_not_invented(sources):
    assert not signature_support_issues(selected(), sources, fixed_rules_version=RULES)


def test_v221_catalog_marks_signature_context_only_without_mutating_or_dropping_input():
    sources = catalog()
    sources["items"].extend([catalog("event.signature", source_id="c2")["items"][0],
                             catalog("payload", source_id="c3")["items"][0],
                             catalog("extra_fields.signature", source_id="c4")["items"][0]])
    sources["omitted_scope"] = "event_metadata_only"
    before = deepcopy(sources)
    result = catalog_for_source_policy(sources, fixed_rules_version=RULES)
    assert result is not sources and sources == before
    assert result["version"] == "evidence-selection-v4"
    assert result["omitted_scope"] == "event_metadata_only"
    assert result["omitted"] == sources["omitted"]
    for old, new in zip(sources["items"], result["items"], strict=True):
        expected = {**old, **({"allowed_supports": ["context"]}
                             if is_signature_source_field(old["field"]) else {})}
        assert new == expected and new is not old
    result["items"][0]["allowed_supports"].append("attack_request")
    assert result["items"][1]["allowed_supports"] == ["context"]
    assert sources == before


@pytest.mark.parametrize("revision", [None, "waf-system-v2.19", "waf-system-v2.20", "waf-system-v2.22"])
def test_catalog_of_pinned_legacy_or_unknown_revision_is_byte_for_byte_unchanged(revision):
    sources = catalog()
    before = deepcopy(sources)
    assert catalog_for_source_policy(sources, fixed_rules_version=revision) == before
    assert sources == before


def test_empty_new_catalog_changes_protocol_only_without_inventing_sources():
    sources = {"version": "evidence-selection-v3", "items": [], "omitted": False}
    assert catalog_for_source_policy(sources, fixed_rules_version=RULES) == {
        "version": "evidence-selection-v4", "items": [], "omitted": False,
    }
