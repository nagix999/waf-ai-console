"""Offline flat-contract parity: only direction mixtures cease to be errors."""
from copy import deepcopy

from pydantic import ValidationError
import pytest

from app.agent.contracts_v234 import SecurityAnalysisOutputV234
from app.agent.contracts_v238 import (
    EvidenceDecisionReviewOutput, SecurityAnalysisOutputV238, SecurityAnalysisSelectionV238,
)
from app.agent.evidence_decision_review_v238 import review_base
from app.agent import input_builder_v236, input_builder_v238
from app.agent.output_schema_v226 import compact_strict_schema
from app.services.http_parser import parse_http_payload
from test_decision_review_contract_v232 import review_context, review_wire
from test_flat_evidence_v228 import catalog, wire


def mixed(*, indirect=False, cycle=False, reverse=False):
    value = wire()
    value["evidence"][1]["supports"] = "normal_request"
    if reverse:
        value["evidence"][0]["supports"] = "normal_request"
        value["evidence"][1]["supports"] = "attack_request"
    if indirect:
        value["evidence"][0]["related_evidence_ids"] = ["e1"]
        value["evidence"].append({"evidence_id": "e1", "source_id": "c6",
            "interpretation_ko": "두 관측을 연결하는 실제 요청 문맥입니다.",
            "supports": "context", "related_evidence_ids": ["e5"]})
    if cycle:
        value["evidence"][1]["related_evidence_ids"] = ["e2"]
    return value


def validate(value):
    return SecurityAnalysisOutputV238.model_validate(value, context={"source_catalog": catalog()})


def test_wire_schema_is_identical_to_v236_model_without_new_roles_or_fields():
    previous = compact_strict_schema(SecurityAnalysisOutputV234.model_json_schema())
    current = compact_strict_schema(SecurityAnalysisOutputV238.model_json_schema())
    previous.pop("title")
    current.pop("title")
    assert current == previous


@pytest.mark.parametrize("indirect,cycle,reverse", [
    (False, False, False), (True, False, False), (False, True, False),
    (True, True, False), (False, False, True), (True, False, True),
])
def test_direct_transitive_and_cyclic_mixtures_preserve_every_field(indirect, cycle, reverse):
    value = mixed(indirect=indirect, cycle=cycle, reverse=reverse)
    before = deepcopy(value)
    output = validate(value)
    assert output.model_dump(mode="json") == before == value
    assert output.interpretation_conflict is False
    expanded = output.to_selection(input_truncated=False)
    assert type(expanded) is SecurityAnalysisSelectionV238
    assert expanded.legacy_bridge().verdict.value == before["verdict"]
    assert expanded.interpretation_conflict is False
    assert [item.supports.value for item in expanded.evidence] == [item["supports"] for item in before["evidence"]]
    assert len(expanded.evidence_relations) == sum(bool(item["related_evidence_ids"]) for item in before["evidence"])
    with pytest.raises(ValidationError) as failure:
        SecurityAnalysisOutputV234.model_validate(value, context={"source_catalog": catalog()})
    assert failure.value.errors()[0]["type"] == "evidence_support_conflict"


@pytest.mark.parametrize("mutate,code", [
    (lambda v: v["evidence"][0].update(source_id="c999"), "evidence_source_unavailable"),
    (lambda v: v["evidence"][0].update(source_id="c3"), "evidence_source_support_forbidden"),
    (lambda v: v["evidence"][0].update(source_id="c5"), "evidence_source_support_forbidden"),
    (lambda v: v["evidence"][0].update(source_id="c4"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(supports="context"), "decisive_verdict_requires_directional_content"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e1"]), "evidence_link_unknown"),
    (lambda v: v["evidence"][0].update(related_evidence_ids=["e2"]), "evidence_link_self"),
    (lambda v: v["evidence"][0].update(interpretation_ko="  "), "evidence_text_required"),
    (lambda v: v.update(verdict="inconclusive"), "inconclusive_requires_unknown_severity"),
    (lambda v: v["threat_analysis"].update(severity="NONE"), "true_positive_requires_threat_severity"),
    (lambda v: v.update(summary_ko="정탐입니다. 잘못된 요약입니다."), "summary_must_describe_request"),
    (lambda v: v.update(decision_issue={"point_ko": "조건", "evidence_ids": ["e2"],
        "missing_condition_ko": "확정 판정에 남길 수 없는 필수 미확인 조건"}), "decisive_verdict_disallows_missing_condition"),
    (lambda v: v.update(extra_private_field="SYNTHETIC_PRIVATE"), "extra_forbidden"),
])
def test_all_non_mixture_security_guards_remain_strict(mutate, code):
    value = mixed()
    mutate(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert code in {entry["type"] for entry in failure.value.errors()}


def test_source_only_deferral_does_not_skip_other_guards_or_decision_review_source_checks():
    value = mixed()
    value["evidence"][0]["source_id"] = "c3"
    output = SecurityAnalysisOutputV238.model_validate(value, context={"source_catalog": catalog(),
        "defer_source_support_validation": True})
    assert output.model_dump(mode="json") == value
    value["evidence"][0]["related_evidence_ids"] = ["e1"]
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV238.model_validate(value, context={"source_catalog": catalog(),
            "defer_source_support_validation": True})


def test_mixed_surviving_decision_review_keeps_all_sources_directions_and_dependencies():
    base = mixed()
    base.update(verdict="inconclusive", confidence_score=0, decision_issue=None)
    base["threat_analysis"]["severity"] = "UNKNOWN"
    context = review_context(base)
    before = deepcopy(context)
    output = EvidenceDecisionReviewOutput.model_validate(review_wire(), context=context).to_analysis()
    assert type(output) is SecurityAnalysisOutputV238
    assert output.model_dump(mode="json")["evidence"] == base["evidence"]
    assert context == before
    bad = review_wire()
    bad["retained_evidence_ids"] = ["e2"]
    with pytest.raises(ValidationError) as failure:
        EvidenceDecisionReviewOutput.model_validate(bad, context=context)
    assert failure.value.errors()[0]["type"] == "review_evidence_omitted"


def test_rejected_opposite_dependency_invalidates_root_not_independent_direction():
    value = mixed()
    value["evidence"].append({"evidence_id": "e1", "source_id": "c6", "supports": "normal_request",
        "interpretation_ko": "별도 요청 관측의 독립적인 정상 사용 해석입니다.", "related_evidence_ids": []})
    original = validate(value)
    selection = original.to_selection(input_truncated=False).legacy_bridge()
    resolutions = [{"index": 0, "source_id": "c2"}, {"index": 2, "source_id": "c6"}]
    before = original.model_dump(mode="json")
    base, blocked = review_base(original, selection, resolutions, catalog(), {1})
    assert blocked == {0, 1}
    assert [item.evidence_id for item in base.evidence] == ["e1"]
    assert base.evidence[0].supports.value == "normal_request"
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize("context_window", [8192, 32768])
def test_input_budget_source_ranges_and_literal_values_are_identical_to_v236(context_window):
    raw = "POST /synthetic HTTP/1.1\r\nHost: example.invalid\r\n\r\nvalue=/@/&padding=" + "A" * 12000
    event = {"payload": raw, "signature": "Synthetic detection", "waf_action": "D"}
    parsed = parse_http_payload(raw)
    old = input_builder_v236.build_agent_input(event, raw, parsed, context_window, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.36")
    new = input_builder_v238.build_agent_input(event, raw, parsed, context_window, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.38")
    assert new == old
