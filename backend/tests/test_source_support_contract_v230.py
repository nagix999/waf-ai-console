"""Synthetic source-repair staging is opt-in and never skips domain checks."""
from copy import deepcopy

from pydantic import ValidationError
import pytest

from app.agent.contracts_v229 import SecurityAnalysisOutputV229
from app.agent.contracts_v230 import SecurityAnalysisOutputV230
from app.agent.output_schema_v226 import compact_strict_schema
from test_flat_evidence_v228 import catalog, wire


def forbidden(*, normal=False, source="c3"):
    value = wire()
    value["evidence"][0]["source_id"] = source
    if normal:
        value["evidence"][0]["supports"] = "normal_request"
        value["verdict"] = "false_positive"
        value["threat_analysis"]["severity"] = "NONE"
    return value


def validate(value, *, defer=False, candidates=None):
    return SecurityAnalysisOutputV230.model_validate(value, context={
        "source_catalog": catalog() if candidates is None else candidates,
        "defer_source_support_validation": defer,
    })


def codes(failure):
    return {issue["type"] for issue in failure.value.errors()}


@pytest.mark.parametrize("source", ["c3", "c5"])
@pytest.mark.parametrize("normal", [False, True])
def test_reference_support_violation_is_strict_by_default_and_can_only_be_staged_explicitly(source, normal):
    value = forbidden(source=source, normal=normal)
    before = deepcopy(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert codes(failure) == {"evidence_source_support_forbidden"}
    staged = validate(value, defer=True)
    assert staged.model_dump(mode="json") == before
    assert staged.to_selection(input_truncated=False).evidence[0].supports == value["evidence"][0]["supports"]
    assert value == before


@pytest.mark.parametrize("flag", [False, None, 0, 1, "true", "False", {}])
def test_only_literal_true_in_trusted_validation_context_enables_staging(flag):
    with pytest.raises(ValidationError) as failure:
        validate(forbidden(), defer=flag)
    assert "evidence_source_support_forbidden" in codes(failure)


def test_allowed_supports_on_real_payload_is_also_staged_without_relabeling():
    candidates, value = catalog(), wire()
    candidates["items"][1]["allowed_supports"] = ["context"]
    with pytest.raises(ValidationError) as failure:
        validate(value, candidates=candidates)
    assert codes(failure) == {"evidence_source_support_forbidden"}
    assert validate(value, defer=True, candidates=candidates).model_dump(mode="json") == value


@pytest.mark.parametrize("reverse", [False, True])
def test_unknown_source_is_not_hidden_by_an_earlier_forbidden_selection(reverse):
    value = forbidden()
    value["evidence"][1]["source_id"] = "c999"
    if reverse:
        value["evidence"].reverse()
    with pytest.raises(ValidationError) as failure:
        validate(value, defer=True)
    assert codes(failure) == {"evidence_source_unavailable"}


def test_metadata_only_decisive_output_still_requires_request_content_when_no_support_violation_exists():
    value = forbidden(source="c4")  # WAF action has no declared support restriction.
    with pytest.raises(ValidationError) as failure:
        validate(value, defer=True)
    assert codes(failure) == {"decisive_verdict_requires_directional_content"}


def test_an_unrelated_forbidden_opposite_interpretation_cannot_waive_metadata_only_decisive_anchor():
    value = wire()
    value["evidence"][0].update(source_id="c4", related_evidence_ids=[])
    value["evidence"][1].update(source_id="c3", supports="normal_request")
    with pytest.raises(ValidationError) as failure:
        validate(value, defer=True)
    assert codes(failure) == {"decisive_verdict_requires_directional_content"}


@pytest.mark.parametrize("mutate, expected", [
    (lambda value: value["evidence"][0].update(related_evidence_ids=["e2"]), "evidence_link_self"),
    (lambda value: value["evidence"][0].update(related_evidence_ids=["e1"]), "evidence_link_unknown"),
    (lambda value: value["evidence"][1].update(evidence_id="e2"), "evidence_id_repeated"),
    (lambda value: value["evidence"][1].update(supports="normal_request"), "evidence_support_conflict"),
    (lambda value: value["evidence"][0].update(interpretation_ko=" "), "evidence_text_required"),
    (lambda value: value["threat_analysis"].update(severity="NONE"), "true_positive_requires_threat_severity"),
    (lambda value: value.update(summary_ko="정탐입니다. 요청 정보를 확인했습니다."), "summary_must_describe_request"),
    (lambda value: value.update(decision_issue={"point_ko": "실제 판정 조건입니다.", "evidence_ids": ["e2"], "missing_condition_ko": "추가 조건"}), "decisive_verdict_disallows_missing_condition"),
    (lambda value: value.update(extra_field="not-allowed"), "extra_forbidden"),
])
def test_deferring_source_support_never_bypasses_other_domain_or_link_checks(mutate, expected):
    value = forbidden()
    mutate(value)
    with pytest.raises(ValidationError) as failure:
        validate(value, defer=True)
    assert expected in codes(failure)


def test_decisive_verdict_without_a_matching_direction_is_not_repaired_by_deferral():
    value = forbidden()
    value["evidence"][0]["supports"] = "normal_request"
    with pytest.raises(ValidationError) as failure:
        validate(value, defer=True)
    assert "decisive_verdict_requires_directional_content" in codes(failure)


def test_independent_opposite_source_violation_can_be_staged_without_merging_it_into_attack_claim():
    value = wire()
    value["evidence"][0]["related_evidence_ids"] = []
    value["evidence"][1].update(source_id="c3", supports="normal_request")
    assert validate(value, defer=True).model_dump(mode="json") == value


def test_frozen_v229_remains_strict_and_generation_schema_is_unchanged():
    with pytest.raises(ValidationError) as failure:
        SecurityAnalysisOutputV229.model_validate(forbidden(), context={
            "source_catalog": catalog(), "defer_source_support_validation": True})
    assert "evidence_source_support_forbidden" in codes(failure)
    current = compact_strict_schema(SecurityAnalysisOutputV230.model_json_schema())
    previous = compact_strict_schema(SecurityAnalysisOutputV229.model_json_schema())
    current.pop("title")
    previous.pop("title")
    assert current == previous
