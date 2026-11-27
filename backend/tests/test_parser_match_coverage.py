"""Synthetic authoring-only coverage, without changing runtime predicates."""
from copy import deepcopy
import json

import pytest

from app.services import parser_match_diagnostics as diagnostics
from app.services import parser_rule_engine as engine
from test_parser_workbench import proposal


PRIVATE = "SYNTHETIC_PRIVATE_SOURCE_OR_PREDICATE_DO_NOT_PERSIST"


def summarize(payloads, predicates, *, mode="all"):
    definition = proposal()
    definition["match"] = {mode: predicates}
    return diagnostics.summarize_rule_match(
        [{"case_id": f"c{index + 1:03d}", "raw_payload": raw}
         for index, raw in enumerate(payloads)], definition)


@pytest.mark.parametrize("mode", ["all", "any"])
def test_condition_coverage_is_not_the_rules_all_or_any_result(mode):
    predicates = [{"kind": "starts_with", "value": "GET "},
                  {"kind": "starts_with", "value": "POST "}]
    value = summarize(["GET /one", "POST /two", "GET /three"], predicates, mode=mode)
    assert value == {
        "sample_count": 3, "checked_sample_count": 3, "truncated": False,
        "conditions": [
            {"condition_index": 0, "field": f"match.{mode}.0", "kind": "starts_with",
             "matched_count": 2, "error_count": 0},
            {"condition_index": 1, "field": f"match.{mode}.1", "kind": "starts_with",
             "matched_count": 1, "error_count": 0},
        ],
    }
    assert not any(key in value for key in ("qualified", "passed", "rule_matched", "approved"))


def test_fixed_sample_value_is_observable_as_partial_coverage():
    value = summarize([
        '{"source_ip":"192.0.2.1","request":"GET /one"}',
        '{"source_ip":"192.0.2.2","request":"POST /two"}',
        '{"source_ip":"192.0.2.3","request":"GET /three"}',
    ], [{"kind": "json_equals", "json_pointer": "/source_ip", "value": "192.0.2.1"},
        {"kind": "json_string_exists", "json_pointer": "/request"}])
    assert [item["matched_count"] for item in value["conditions"]] == [1, 3]
    assert [item["error_count"] for item in value["conditions"]] == [0, 0]


def test_all_matching_values_are_observations_not_an_approval_or_invariance_claim():
    value = summarize(['{"source_ip":"192.0.2.1"}'] * 2,
                      [{"kind": "json_equals", "json_pointer": "/source_ip", "value": "192.0.2.1"}])
    assert value["conditions"][0]["matched_count"] == 2
    assert set(value) == {"sample_count", "checked_sample_count", "truncated", "conditions"}
    assert set(value["conditions"][0]) == {
        "condition_index", "field", "kind", "matched_count", "error_count"}


def test_json_type_missing_key_and_invalid_json_errors_are_distinct_from_false():
    value = summarize(['{"enabled":true}', '{"enabled":1}', '{}', 'not JSON'],
                      [{"kind": "json_equals", "json_pointer": "/enabled", "value": True}])
    condition = value["conditions"][0]
    assert value["checked_sample_count"] == 4
    assert condition["matched_count"] == 1
    assert condition["error_count"] == 2
    assert value["checked_sample_count"] - condition["matched_count"] - condition["error_count"] == 1


def test_json_string_exists_non_string_or_empty_is_false_not_a_parse_error():
    value = summarize(['{"request":"GET /one"}', '{"request":""}', '{"request":42}', '{}'],
                      [{"kind": "json_string_exists", "json_pointer": "/request"}])
    assert value["conditions"][0]["matched_count"] == 1
    assert value["conditions"][0]["error_count"] == 1


def test_literal_prefix_keeps_case_whitespace_and_actual_escape_representation():
    value = summarize(["GET /one", "get /two", " GET /three", r"GET\n/four", "GET\n/five"],
                      [{"kind": "starts_with", "value": "GET "},
                       {"kind": "starts_with", "value": r"GET\n"},
                       {"kind": "starts_with", "value": "GET\n"}])
    assert [item["matched_count"] for item in value["conditions"]] == [1, 1, 1]


def test_json_wrapped_http_does_not_match_an_inner_request_prefix():
    value = summarize(['{"request":"GET /one"}', '{"request":"GET /two"}'],
                      [{"kind": "starts_with", "value": "GET "}])
    assert value["conditions"][0]["matched_count"] == 0
    assert value["conditions"][0]["error_count"] == 0


def test_re2_uses_search_and_does_not_return_patterns():
    if engine._re2 is None:
        pytest.skip("Native RE2 is unavailable")
    value = summarize(["wrapper GET /one", "POST /two", "GET /three"],
                      [{"kind": "regex_match", "pattern": r"GET /[^ ]+"}])
    assert value["conditions"][0]["matched_count"] == 2
    assert value["conditions"][0]["error_count"] == 0
    assert "pattern" not in json.dumps(value)


def test_marker_presence_counts_missing_or_ambiguous_as_errors():
    value = summarize(["request=GET /one;end", "prefix request=GET /two;end", "request=GET /three"],
                      [{"kind": "marker_pair_exists", "start_marker": "request=",
                        "end_marker": ";end", "start_anchor": "payload_start"}])
    assert value["conditions"][0]["matched_count"] == 1
    assert value["conditions"][0]["error_count"] == 2


def test_unexpected_predicate_error_is_counted_without_exception_text(monkeypatch):
    def broken(*_args):
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(diagnostics, "_matches", broken)
    value = summarize([PRIVATE, PRIVATE], [{"kind": "starts_with", "value": PRIVATE}])
    assert value["checked_sample_count"] == 2
    assert value["conditions"][0]["matched_count"] == 0
    assert value["conditions"][0]["error_count"] == 2
    assert PRIVATE not in json.dumps(value)


def test_partial_sample_is_not_added_to_any_condition_denominator(monkeypatch):
    class Budget:
        def __init__(self):
            self.calls = 0
        def check(self):
            self.calls += 1
            if self.calls > 8:
                raise engine.ParserRuleError("parser_rule_time_limit")
    monkeypatch.setattr(diagnostics, "_Budget", Budget)
    value = summarize(["GET /one", "POST /two", "GET /three"],
                      [{"kind": "starts_with", "value": "GET "},
                       {"kind": "starts_with", "value": "POST "}])
    assert value["sample_count"] == 3
    assert value["checked_sample_count"] == 1
    assert value["truncated"] is True
    assert [item["matched_count"] for item in value["conditions"]] == [1, 0]
    assert [item["error_count"] for item in value["conditions"]] == [0, 0]


def test_shared_budget_is_used_for_all_samples_and_condition_contexts(monkeypatch):
    budgets, observed = [], []
    class Budget:
        def __init__(self):
            budgets.append(self)
        def check(self):
            pass
    original = diagnostics._matches
    def traced(predicate, context):
        observed.append(context.budget)
        return original(predicate, context)
    monkeypatch.setattr(diagnostics, "_Budget", Budget)
    monkeypatch.setattr(diagnostics, "_matches", traced)
    value = summarize(["GET /one", "GET /two"], [{"kind": "starts_with", "value": "GET "}])
    assert value["checked_sample_count"] == 2
    assert len(budgets) == 1 and observed == [budgets[0], budgets[0]]


def test_source_and_predicate_literals_are_not_in_counts_or_changed():
    definition = proposal()
    definition["match"] = {"all": [{"kind": "starts_with", "value": PRIVATE}]}
    definition["extract"] = {"kind": "json_string", "json_pointer": "/missing"}
    samples = [{"case_id": PRIVATE, "raw_payload": PRIVATE}]
    before = deepcopy((samples, definition))
    value = diagnostics.summarize_rule_match(samples, definition)
    assert PRIVATE not in json.dumps(value)
    assert (samples, definition) == before
    assert value["conditions"][0]["matched_count"] == 1
    assert not any(key in value for key in ("extract", "qualified", "rule_matched"))


@pytest.mark.parametrize("raw", [None, 1, True, "\ud800", "x" * 65537, "가" * 21846])
def test_invalid_source_is_rejected_with_only_a_fixed_safe_code(raw):
    with pytest.raises(engine.ParserRuleError, match="^parser_source_invalid$") as failure:
        diagnostics.summarize_rule_match([{"raw_payload": raw}], proposal())
    assert str(failure.value) == "parser_source_invalid"


def test_source_at_byte_limit_and_empty_sample_collection_are_supported():
    value = summarize(["x" * 65536], [{"kind": "starts_with", "value": "x"}])
    assert value["checked_sample_count"] == 1 and value["truncated"] is False
    empty = diagnostics.summarize_rule_match([], proposal())
    assert empty["sample_count"] == empty["checked_sample_count"] == 0
    assert empty["truncated"] is False


@pytest.mark.parametrize("samples", [None, {}, tuple(), [{"raw_payload": "GET /one"}] * 101])
def test_sample_collection_bound_is_explicit_without_silent_count_clipping(samples):
    with pytest.raises(engine.ParserRuleError, match="^parser_source_invalid$"):
        diagnostics.summarize_rule_match(samples, proposal())


def test_maximum_100_samples_and_eight_conditions_are_schema_bounded():
    value = summarize(["GET /one"] * 100, [{"kind": "starts_with", "value": "GET "}] * 8)
    assert value["sample_count"] == value["checked_sample_count"] == 100
    assert value["truncated"] is False
    assert len(value["conditions"]) == 8
    assert all(item["matched_count"] == 100 for item in value["conditions"])
    definition = proposal()
    definition["match"] = {"all": [{"kind": "starts_with", "value": "GET "}] * 9}
    with pytest.raises(engine.ParserRuleError, match="^parser_rule_bundle_invalid$"):
        diagnostics.summarize_rule_match([], definition)


def test_malformed_rule_does_not_expose_an_unknown_field_or_literal():
    definition = proposal()
    definition[PRIVATE] = PRIVATE
    with pytest.raises(engine.ParserRuleError, match="^parser_rule_bundle_invalid$") as failure:
        diagnostics.summarize_rule_match([{"raw_payload": PRIVATE}], definition)
    assert PRIVATE not in repr(failure.value)
