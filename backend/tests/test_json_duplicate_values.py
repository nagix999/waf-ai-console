"""Synthetic-only bounded scalar comparison; no existing pipeline changes."""

import json

import pytest

from app.services import json_duplicate_values as inspection


def inspect(body, **kwargs):
    return inspection.inspect_json_duplicate_values(body, **kwargs)


def codes(report):
    return [item["code"] for item in report["observations"]]


@pytest.mark.parametrize("value", [
    "2", "-2", "0", "-0", "2.0", "2e0", "1e999999", "true", "false", "null",
    '"normal"', '"한글"', r'"\u0061"', r'"escaped\\value"', r'"\uD83D\uDE00"',
])
def test_exact_same_scalar_values_are_not_reported_as_conflicting(value):
    body = '{"page":' + value + ',"page":' + value + "}"
    report = inspect(body)
    assert report["status"] == "complete"
    assert codes(report) == [inspection.SAME_CODE]
    assert report["limitations"] == []


@pytest.mark.parametrize("first,second", [
    ("2", "3"), ("-2", "2"), ("true", "false"), ("true", "1"), ("false", "0"),
    ("null", '"null"'), ("2", '"2"'), ('"normal"', '"different"'),
    ('"é"', r'"e\u0301"'),
])
def test_different_scalar_values_preserve_types_without_normalization(first, second):
    report = inspect('{"page":' + first + ',"page":' + second + "}")
    assert codes(report) == [inspection.DIFFERENT_CODE]


@pytest.mark.parametrize("first,second", [
    ("2", "2.0"), ("2", "2e0"), ("2e0", "2E0"), ("0", "-0"),
    ("2.1", "2.2"), ("1e999999", "2e999999"),
    ('{"value":2}', '{"value":2}'), ("[2]", "[2]"), ("[2]", "2"),
    (r'"\uD800"', r'"\uD800"'),
])
def test_numeric_spellings_containers_and_unpaired_surrogates_remain_uncomparable(first, second):
    report = inspect('{"page":' + first + ',"page":' + second + "}")
    assert report["status"] == "complete"
    assert codes(report) == [inspection.UNCOMPARABLE_CODE]


def test_escaped_keys_and_scalar_strings_use_json_escaping_only():
    body = r'{"pa\u0067e":"a","page":"\u0061"}'
    report = inspect(body)
    assert codes(report) == [inspection.SAME_CODE]
    item = report["observations"][0]
    assert [body[a:b] for a, b in item["key_spans"]] == ['"pa\\u0067e"', '"page"']
    assert [body[a:b] for a, b in item["value_spans"]] == ['"a"', '"\\u0061"']


@pytest.mark.parametrize("body", [
    '[{"page":2},{"page":3}]', '{"a":{"page":2},"b":{"page":3}}',
    '{"page":{"page":2},"other":[{"page":2},{"page":2}]}',
])
def test_identical_names_in_separate_objects_never_form_duplicate_groups(body):
    report = inspect(body)
    assert report["status"] == "complete"
    assert report["observations"] == []


def test_nested_and_array_object_duplicates_are_scoped_and_sorted_by_original_position():
    body = '{"page":2,"a":[{"page":2,"page":3},{"page":"x","page":"x"}],"page":2}'
    report = inspect(body)
    assert codes(report) == [inspection.SAME_CODE, inspection.DIFFERENT_CODE, inspection.SAME_CODE]
    assert [[body[a:b] for a, b in item["value_spans"]] for item in report["observations"]] == [
        ["2", "2"], ["2", "3"], ['"x"', '"x"'],
    ]


def test_three_occurrences_are_compared_as_one_group():
    report = inspect('{"page":2,"page":2,"page":3}')
    assert codes(report) == [inspection.DIFFERENT_CODE]
    assert len(report["observations"][0]["value_spans"]) == 3


def test_spans_are_original_character_offsets_not_utf8_bytes():
    body = ' {"한글":"가","한글":"가"} '
    offset = 81
    report = inspect(body, source_start=offset)
    assert codes(report) == [inspection.SAME_CODE]
    for observation in report["observations"]:
        assert [body[a - offset:b - offset] for a, b in observation["key_spans"]] == ['"한글"', '"한글"']
        assert [body[a - offset:b - offset] for a, b in observation["value_spans"]] == ['"가"', '"가"']


@pytest.mark.parametrize("suffix", [",", ",}", "", " extra", " true", "\x00"])
def test_later_syntax_failure_discards_all_prior_comparison_claims(suffix):
    body = '{"ok":{"page":2,"page":2},"next":null' + suffix
    if suffix in {" extra", " true", "\x00"}:
        body = '{"page":2,"page":2}' + suffix
    report = inspect(body)
    assert report["status"] == "uncomparable"
    assert report["observations"] == []
    assert report["limitations"] == ["json_syntax_invalid"]


@pytest.mark.parametrize("body", [
    '{"page":2,"page":2,}', '{"page":2,"page":02}', '{"page":2,"page":+2}',
    '{"page":2,"page":2.}', '{"page":2,"page":2e}', '{"page":NaN,"page":NaN}',
    '{"page":Infinity,"page":Infinity}', '{"page":truex,"page":truex}',
    r'{"page":"\q","page":"\q"}', '{"page":"raw\nnewline","page":"raw\nnewline"}',
    '{"page":2,"page":2]', '[{"page":2,"page":2},]', "", "\ufeff{}",
])
def test_invalid_json_cannot_make_equal_claims(body):
    report = inspect(body)
    assert report["status"] == "uncomparable"
    assert report["observations"] == []
    assert report["limitations"] == ["json_syntax_invalid"]


def test_valid_prefix_with_incomplete_capture_never_compares_values():
    report = inspect('{"page":2,"page":2}', complete=False)
    assert report["status"] == "uncomparable"
    assert report["observations"] == []
    assert report["limitations"] == ["json_capture_incomplete"]


@pytest.mark.parametrize("value", [None, b"{}", 1, True, [], {}])
def test_non_text_input_has_fixed_failure(value):
    assert inspect(value)["limitations"] == ["json_inspection_input_invalid"]


@pytest.mark.parametrize("offset", [True, False, 0.0, "1", None, -1, inspection.MAX_SOURCE_OFFSET])
def test_source_offset_requires_bounded_nonnegative_integer(offset):
    report = inspect("{}", source_start=offset)
    assert report["status"] == "uncomparable"
    assert report["observations"] == []
    assert report["limitations"][0] in {"json_inspection_input_invalid", "json_source_offset_invalid"}


@pytest.mark.parametrize("complete", [None, 0, 1, "true"])
def test_capture_flag_requires_boolean(complete):
    assert inspect("{}", complete=complete)["limitations"] == ["json_inspection_input_invalid"]


def test_character_bound_precedes_encoding_unbounded_source():
    class NoEncoding(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("oversized input must not be encoded")

    report = inspect(NoEncoding("x" * (inspection.MAX_UTF8_BYTES + 1)))
    assert report["limitations"] == ["json_byte_limit"]


def test_utf8_byte_limit_is_not_only_character_limit():
    body = '"' + "한" * (inspection.MAX_UTF8_BYTES // 3 + 1) + '"'
    assert len(body) < inspection.MAX_UTF8_BYTES
    assert inspect(body)["limitations"] == ["json_byte_limit"]


def test_invalid_input_unicode_has_fixed_failure():
    assert inspect('"\ud800"')["limitations"] == ["json_input_unicode_uncomparable"]
    assert inspect(r'{"\uD800":2,"\uD800":2}')["limitations"] == ["json_key_unicode_uncomparable"]


def test_depth_is_bounded_without_recursive_parser():
    body = "[" * inspection.MAX_DEPTH + '{"page":2,"page":2}' + "]" * inspection.MAX_DEPTH
    report = inspect(body)
    assert report["limitations"] == ["json_depth_limit"]
    assert report["observations"] == []
    assert inspect("[" * inspection.MAX_DEPTH + "]" * inspection.MAX_DEPTH)["status"] == "complete"


def test_node_limit_discards_previously_completed_duplicate_groups():
    body = '[{"page":2,"page":2},' + ",".join("0" for _ in range(inspection.MAX_NODES)) + "]"
    report = inspect(body)
    assert report["limitations"] == ["json_node_limit"]
    assert report["observations"] == []


def test_string_and_number_limits_do_not_clip_and_compare():
    body = '{"page":2,"page":2,"other":"' + "x" * inspection.MAX_STRING_CHARS + '"}'
    report = inspect(body)
    assert report["limitations"] == ["json_string_limit"]
    assert report["observations"] == []
    body = '{"page":2,"page":2,"other":' + "9" * (inspection.MAX_NUMBER_CHARS + 1) + "}"
    assert inspect(body)["limitations"] == ["json_number_limit"]


def test_duplicate_group_and_member_limits_discard_partial_claims():
    body = "{" + ",".join(f'"k{i}":2,"k{i}":2' for i in range(inspection.MAX_DUPLICATE_GROUPS + 1)) + "}"
    report = inspect(body)
    assert report["limitations"] == ["json_duplicate_group_limit"]
    assert report["observations"] == []
    body = "{" + ",".join('"page":2' for _ in range(inspection.MAX_DUPLICATE_MEMBERS + 1)) + "}"
    assert inspect(body)["limitations"] == ["json_duplicate_member_limit"]


def test_only_fixed_codes_and_spans_leave_helper_or_reach_output(caplog, capsys):
    body = '{"SENSITIVE_CANARY_KEY":"SENSITIVE_CANARY_VALUE","SENSITIVE_CANARY_KEY":"OTHER_SECRET"}'
    report = inspect(body)
    serialized = json.dumps(report)
    assert "SENSITIVE_CANARY" not in serialized
    assert "OTHER_SECRET" not in serialized
    assert set(report) == {"version", "status", "observations", "limitations"}
    assert set(report["observations"][0]) == {"code", "key_spans", "value_spans"}
    assert caplog.text == ""
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("body", ["{}", "[]", "[1,2,3]", '"normal"', "true", "null", "2", "  {}\r\n"])
def test_valid_documents_without_duplicates_have_no_observations(body):
    report = inspect(body)
    assert report["status"] == "complete"
    assert report["observations"] == []
    assert report["limitations"] == []
