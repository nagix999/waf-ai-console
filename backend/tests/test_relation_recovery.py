"""Synthetic duplicate recovery; structural validity is not semantic proof."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from app.agent.contracts import SecurityAnalysisOutputV220
from app.agent.relation_recovery import (
    GROUP_DUPLICATES, NORMALIZATION_CODES, RELATED_DUPLICATES,
    recover_relation_duplicates,
)
from app.agent.structured_json import normalize_structured_json
from test_evidence_relations import wire


def extended_wire():
    value = wire()
    value["evidence"].extend([
        {"source_id": "c3", "supports": "context", "interpretation_ko": "첫 번째 값의 처리 위치를 설명합니다."},
        {"source_id": "c4", "supports": "context", "interpretation_ko": "두 번째 값의 사용 문맥을 설명합니다."},
        {"source_id": "c5", "supports": "context", "interpretation_ko": "결합 해석에 필요한 별도의 입력입니다."},
    ])
    return value


def dependencies(value):
    result = {}
    for group in value["evidence_relations"]:
        result.setdefault(group["representative_index"], set()).update(group["related_indexes"])
    return result


@pytest.mark.parametrize("empty", [False, True])
def test_valid_output_is_an_unchanged_deepcopy(empty):
    value = wire()
    if empty:
        value["evidence_relations"] = []
    before = deepcopy(value)
    recovered, codes = recover_relation_duplicates(value)
    assert recovered == value == before and codes == []
    assert recovered is not value and recovered["evidence"] is not value["evidence"]
    recovered["evidence"][0]["interpretation_ko"] = "수정해도 원본은 그대로입니다."
    assert value == before
    SecurityAnalysisOutputV220.model_validate(value)


@pytest.mark.parametrize("related,group_count,expected_codes", [
    ([0, 0], 1, [RELATED_DUPLICATES]),
    ([0], 2, [GROUP_DUPLICATES]),
    ([0, 0], 2, [RELATED_DUPLICATES, GROUP_DUPLICATES]),
    ([0, 0, 0, 0, 0, 0], 6, [RELATED_DUPLICATES, GROUP_DUPLICATES]),
])
def test_only_exact_duplicates_are_removed_and_original_dependency_set_is_preserved(related, group_count, expected_codes):
    value = wire()
    value["evidence_relations"] = [
        {"representative_index": 1, "related_indexes": list(related)} for _ in range(group_count)
    ]
    before = deepcopy(value)
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV220.model_validate(value)
    recovered, codes = recover_relation_duplicates(value)
    assert codes == expected_codes and set(codes) <= NORMALIZATION_CODES
    assert recovered["evidence_relations"] == [{"representative_index": 1, "related_indexes": [0]}]
    assert dependencies(recovered) == dependencies(value)
    assert value == before
    assert {key: item for key, item in recovered.items() if key != "evidence_relations"} == {
        key: item for key, item in value.items() if key != "evidence_relations"}
    SecurityAnalysisOutputV220.model_validate(recovered)
    again, repeated_codes = recover_relation_duplicates(recovered)
    assert again == recovered and repeated_codes == []


def test_first_occurrence_order_and_exact_explanations_are_preserved():
    value = extended_wire()
    first = {"representative_index": 1, "related_indexes": [4, 2, 4, 0, 2]}
    second = {"representative_index": 3, "related_indexes": [0, 2, 0]}
    value["evidence_relations"] = [first, second, deepcopy(first)]
    before = deepcopy(value)
    recovered, codes = recover_relation_duplicates(value)
    assert codes == [RELATED_DUPLICATES, GROUP_DUPLICATES]
    assert recovered["evidence_relations"] == [
        {"representative_index": 1, "related_indexes": [4, 2, 0]},
        {"representative_index": 3, "related_indexes": [0, 2]},
    ]
    assert dependencies(recovered) == dependencies(value)
    assert recovered["evidence"] == value["evidence"] and value == before
    SecurityAnalysisOutputV220.model_validate(recovered)


@pytest.mark.parametrize("first,second", [
    ([0], [2]),
    ([0, 2], [2, 0]),
    ([0, 0], [0]),
    ([0, 2, 0], [0, 2]),
])
def test_same_representative_with_different_original_sequences_is_not_merged(first, second):
    value = extended_wire()
    value["evidence_relations"] = [
        {"representative_index": 1, "related_indexes": first},
        {"representative_index": 1, "related_indexes": second},
    ]
    before = deepcopy(value)
    recovered, codes = recover_relation_duplicates(value)
    assert recovered == value == before and codes == []
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV220.model_validate(recovered)


@pytest.mark.parametrize("bad_group", [
    {"representative_index": 1, "related_indexes": [1, 0, 0]},
    {"representative_index": 1, "related_indexes": [2, 2]},
    {"representative_index": 2, "related_indexes": [0, 0]},
    {"representative_index": -1, "related_indexes": [0, 0]},
    {"representative_index": True, "related_indexes": [0, 0]},
    {"representative_index": "1", "related_indexes": [0, 0]},
    {"representative_index": 1.0, "related_indexes": [0, 0]},
    {"representative_index": 1, "related_indexes": [False, False]},
    {"representative_index": 1, "related_indexes": ["0", "0"]},
    {"representative_index": 1, "related_indexes": [0.0, 0.0]},
    {"representative_index": 1, "related_indexes": [-1, -1]},
    {"representative_index": 1, "related_indexes": []},
    {"representative_index": 1, "related_indexes": None},
    {"representative_index": 1, "related_indexes": "0,0"},
    {"representative_index": 1},
    {"related_indexes": [0, 0]},
    {"representative_index": 1, "related_indexes": [0, 0], "private_extra": "synthetic-sensitive-value"},
    [1, [0, 0]],
    None,
])
def test_unsafe_relations_are_not_guessed_or_partly_repaired(bad_group):
    value = wire()
    value["evidence_relations"] = [
        {"representative_index": 1, "related_indexes": [0, 0]},
        bad_group,
    ]
    before = deepcopy(value)
    recovered, codes = recover_relation_duplicates(value)
    assert recovered == value == before and codes == []
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV220.model_validate(recovered)


def test_opposite_supports_are_not_made_valid_by_deduplication():
    value = wire()
    value["evidence"][0]["supports"] = "normal_request"
    value["evidence_relations"] *= 2
    recovered, codes = recover_relation_duplicates(value)
    assert recovered == value and codes == []
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV220.model_validate(recovered)


@pytest.mark.parametrize("change", [
    lambda value: value.update(private_extra="synthetic-sensitive-value"),
    lambda value: value.pop("summary_ko"),
    lambda value: value.update(verdict="false_positive"),
    lambda value: value["evidence"][0].update(supports="unknown"),
    lambda value: value["evidence"][0].update(interpretation_ko=""),
    lambda value: value["evidence"][0].update(private_extra="synthetic-sensitive-value"),
])
def test_remaining_contract_errors_cancel_the_whole_operation(change):
    value = wire()
    value["evidence_relations"] *= 2
    change(value)
    before = deepcopy(value)
    recovered, codes = recover_relation_duplicates(value)
    assert recovered == value == before and codes == []
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV220.model_validate(recovered)


@pytest.mark.parametrize("relations", [None, {}, "synthetic-private-text", 1, True])
def test_invalid_container_is_not_coerced(relations):
    value = wire()
    value["evidence_relations"] = relations
    recovered, codes = recover_relation_duplicates(value)
    assert recovered == value and codes == []


def test_one_based_indices_or_source_numbers_are_not_reinterpreted():
    value = wire()
    value["evidence_relations"] = [{"representative_index": 2, "related_indexes": [1, 1]}]
    recovered, codes = recover_relation_duplicates(value)
    assert recovered == value and codes == []
    with pytest.raises(ValidationError):
        SecurityAnalysisOutputV220.model_validate(recovered)


def test_only_complete_strictly_decoded_values_are_used_without_string_repair():
    value = wire()
    value["evidence_relations"] *= 2
    normalized = normalize_structured_json("```json\n" + json.dumps(value, ensure_ascii=False) + "\n```")
    recovered, codes = recover_relation_duplicates(json.loads(normalized.content))
    assert codes == [GROUP_DUPLICATES]
    SecurityAnalysisOutputV220.model_validate(recovered)


def test_deep_nested_sensitive_values_do_not_escape_to_logs_or_errors(caplog, capsys):
    value = wire()
    secret = "SYNTHETIC_COOKIE_TOKEN_NEVER_PRINT"
    nested = secret
    for _ in range(20):
        nested = {"sensitive": [nested]}
    value["evidence"][0]["interpretation_ko"] = nested
    value["evidence_relations"] *= 2
    recovered, codes = recover_relation_duplicates(value)
    assert recovered == value and codes == []
    recovered["evidence"][0]["interpretation_ko"]["sensitive"].append("new")
    assert recovered != value
    streams = capsys.readouterr()
    assert streams.out == streams.err == ""
    assert not caplog.records
    assert secret not in repr(codes)


@pytest.mark.parametrize("value", ["synthetic-sensitive-value", [], None, True])
def test_nonobject_input_raises_only_a_fixed_safe_code(value):
    with pytest.raises(ValueError, match="^relation_recovery_input_invalid$") as error:
        recover_relation_duplicates(value)
    assert str(error.value) == "relation_recovery_input_invalid"
