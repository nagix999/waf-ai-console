"""Synthetic decision review preserves every server-verified evidence field."""
from copy import deepcopy

from pydantic import ValidationError
import pytest

from app.agent.contracts_v230 import SecurityAnalysisOutputV230
from app.agent.contracts_v231 import EvidenceDecisionReviewOutput, SecurityAnalysisOutputV231
from app.agent.output_schema_v226 import compact_strict_schema
from test_flat_evidence_v228 import catalog, wire


def neutral_base():
    value = wire()
    value.update(verdict="inconclusive", confidence_score=0, decision_issue=None,
                 summary_ko="원문 검증을 통과한 요청 근거를 다시 검토합니다.")
    value["threat_analysis"]["severity"] = "UNKNOWN"
    return value


def review_wire():
    value = wire()
    return {"retained_evidence_ids": ["e2", "e5"], "verdict": value["verdict"],
            "confidence_score": value["confidence_score"], "summary_ko": value["summary_ko"],
            "threat_analysis": deepcopy(value["threat_analysis"]), "decision_issue": None,
            "interpretation_conflict": False}


def review_context(base=None, candidates=None, eligible=None):
    base = neutral_base() if base is None else base
    return {"source_catalog": catalog() if candidates is None else candidates,
            "review_original_output": base,
            "review_eligible_evidence_ids": [entry["evidence_id"] for entry in base["evidence"]]
            if eligible is None else eligible}


def validate(value, *, context=None):
    return EvidenceDecisionReviewOutput.model_validate(value, context=review_context() if context is None else context)


def issue_codes(failure):
    return {issue["type"] for issue in failure.value.errors()}


def test_review_updates_only_decision_fields_and_preserves_original_evidence_order():
    context, value = review_context(), review_wire()
    value["retained_evidence_ids"] = ["e5", "e2"]
    value["decision_issue"] = {"point_ko": "입력 위치와 실행 의미를 함께 확인했습니다.",
                               "evidence_ids": ["e5", "e2"], "missing_condition_ko": None}
    before_context, before_value = deepcopy(context), deepcopy(value)
    reviewed = validate(value, context=context)
    analysis = reviewed.to_analysis()
    assert isinstance(analysis, SecurityAnalysisOutputV231)
    assert analysis.evidence[0].evidence_id == "e2"
    assert analysis.model_dump(mode="json")["evidence"] == context["review_original_output"]["evidence"]
    for key in ("signature_assessment", "capture_assessment"):
        assert analysis.model_dump(mode="json")[key] == context["review_original_output"][key]
    for key in value.keys() - {"retained_evidence_ids"}:
        assert analysis.model_dump(mode="json")[key] == value[key]
    assert context == before_context and value == before_value
    assert reviewed.model_dump(mode="json") == value
    assert "_analysis" not in reviewed.model_dump(mode="json")


def test_analysis_result_is_an_independent_copy_of_the_validated_private_result():
    reviewed = validate(review_wire())
    first = reviewed.to_analysis()
    first.evidence[0].interpretation_ko = "수정된 값"
    assert reviewed.to_analysis().evidence[0].interpretation_ko != "수정된 값"


def test_independent_counterevidence_cannot_be_omitted_to_make_a_decisive_result_easier():
    base, value = neutral_base(), review_wire()
    base["evidence"].append({"evidence_id": "e1", "source_id": "c1", "supports": "normal_request",
                             "interpretation_ko": "별도 요청의 정상 기능 문맥을 뒷받침하는 해석입니다.", "related_evidence_ids": []})
    context = review_context(base)
    with pytest.raises(ValidationError) as failure:
        validate(value, context=context)
    assert issue_codes(failure) == {"review_evidence_omitted"}
    value["retained_evidence_ids"].append("e1")
    analysis = validate(value, context=context).to_analysis()
    assert analysis.evidence[-1].supports == "normal_request"
    assert analysis.model_dump(mode="json")["evidence"] == base["evidence"]


@pytest.mark.parametrize("retained, expected", [
    (["e2"], "review_evidence_omitted"), (["e5"], "review_evidence_omitted"),
    ([], "review_evidence_omitted"), (["e2", "e5", "e2"], "review_evidence_repeated"),
    (["e2", "e1"], "review_evidence_not_eligible"), (["e6"], "literal_error"),
    (["e2"] * 6, "too_long"),
])
def test_unknown_duplicate_missing_and_oversized_selection_is_rejected(retained, expected):
    value = review_wire()
    value["retained_evidence_ids"] = retained
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert expected in issue_codes(failure)


@pytest.mark.parametrize("eligible, expected", [
    (["e2"], "review_evidence_dependency_missing"),
    (["e5"], "decision_review_context_invalid"),
    (["e2", "e2", "e5"], "decision_review_context_invalid"),
    (["e2", "e1"], "decision_review_context_invalid"),
])
def test_context_cannot_remove_a_dependency_or_hide_surviving_evidence(eligible, expected):
    with pytest.raises(ValidationError) as failure:
        validate(review_wire(), context=review_context(eligible=eligible))
    assert issue_codes(failure) == {expected}


@pytest.mark.parametrize("mutate", [
    lambda context: context.pop("review_original_output"),
    lambda context: context.update(source_catalog=None),
    lambda context: context.update(review_eligible_evidence_ids="e2"),
    lambda context: context["review_original_output"]["evidence"][0].update(source_id="c999"),
    lambda context: context["review_original_output"]["evidence"][0].update(source_id="c3"),
    lambda context: context["review_original_output"]["evidence"][0].update(related_evidence_ids=["e1"]),
])
def test_invalid_or_unverified_review_metadata_never_creates_a_result(mutate):
    context = review_context()
    mutate(context)
    context["defer_source_support_validation"] = True  # Never inherited by the strict review.
    with pytest.raises(ValidationError) as failure:
        validate(review_wire(), context=context)
    assert issue_codes(failure) == {"decision_review_context_invalid"}


@pytest.mark.parametrize("key, value", [
    ("evidence", []), ("supports", "normal_request"), ("source_id", "c3"),
    ("signature_assessment", {}), ("capture_assessment", {}),
])
def test_reviewer_cannot_generate_or_rewrite_sources_interpretations_or_capture(key, value):
    document = review_wire()
    document[key] = value
    with pytest.raises(ValidationError) as failure:
        validate(document)
    assert "extra_forbidden" in issue_codes(failure)


@pytest.mark.parametrize("mutate, expected", [
    (lambda value: value["threat_analysis"].update(severity="NONE"), "true_positive_requires_threat_severity"),
    (lambda value: value.update(summary_ko="정탐입니다. 요청 정보를 확인했습니다."), "summary_must_describe_request"),
    (lambda value: value.update(verdict="false_positive"), "decisive_verdict_requires_directional_content"),
    (lambda value: value.update(decision_issue={"point_ko": "미확인 조건입니다.", "evidence_ids": ["e2"], "missing_condition_ko": "누락 조건"}), "decisive_verdict_disallows_missing_condition"),
    (lambda value: value.update(decision_issue={"point_ko": "판단 조건입니다.", "evidence_ids": ["e1"], "missing_condition_ko": None}), "decision_evidence_unselected"),
])
def test_reconstructed_full_analysis_must_pass_the_existing_domain_contract(mutate, expected):
    value = review_wire()
    mutate(value)
    with pytest.raises(ValidationError) as failure:
        validate(value)
    assert expected in issue_codes(failure)


def test_cyclic_dependencies_remain_intact_when_reviewer_reorders_ids():
    base, value = neutral_base(), review_wire()
    base["evidence"][1]["related_evidence_ids"] = ["e2"]
    value["retained_evidence_ids"].reverse()
    analysis = validate(value, context=review_context(base)).to_analysis()
    assert analysis.expanded_evidence()[1] == [
        {"representative_index": 0, "related_indexes": [1]},
        {"representative_index": 1, "related_indexes": [0]},
    ]


def test_generation_schema_adds_only_the_small_review_contract_and_keeps_initial_contract_unchanged():
    previous = compact_strict_schema(SecurityAnalysisOutputV230.model_json_schema())
    current = compact_strict_schema(SecurityAnalysisOutputV231.model_json_schema())
    previous.pop("title")
    current.pop("title")
    assert current == previous
    schema = compact_strict_schema(EvidenceDecisionReviewOutput.model_json_schema())
    assert set(schema["properties"]) == {"retained_evidence_ids", "verdict", "confidence_score", "summary_ko",
                                         "threat_analysis", "decision_issue", "interpretation_conflict"}
    assert schema["properties"]["retained_evidence_ids"]["maxItems"] == 5
    assert "FlatEvidenceInterpretation" not in schema.get("$defs", {})
