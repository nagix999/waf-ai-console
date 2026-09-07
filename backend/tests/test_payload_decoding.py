"""All decoder inputs are synthetic; no network, model, or production DB."""

import base64
import json

import pytest

from app.services import payload_decoding as decoder


def decode_one(raw):
    result = decoder.decode_payload(raw)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert raw[item["start"]:item["end"]] == item["original"]
    assert item["field"] == "payload"
    assert item["id"] == "decoded-1"
    assert item["steps"][0]["input"] == item["original"]
    assert item["steps"][-1]["output"] == item["decoded"]
    assert "application_decoding_unverified" in item["warnings"]
    return item


def test_empty_payload_has_json_safe_bounded_contract():
    assert decoder.decode_payload("") == {
        "decoder_version": "waf-text-decoder-v2",
        "items": [],
        "warnings": [],
        "scan_truncated": False,
        "scanned_chars": 0,
        "total_chars": 0,
    }


@pytest.mark.parametrize("raw,decoded", [
    ("%27%20OR%201%3D1%20--", "' OR 1=1 --"),
    ("hello+world%2B%20done", "hello+world+ done"),
    ("%ed%95%9c%ea%b8%80", "한글"),
    ("%F0%9F%98%80", "😀"),
    ("한글%20값", "한글 값"),
])
def test_url_decoding_is_strict_utf8_and_preserves_literal_plus(raw, decoded):
    item = decode_one(raw)
    assert item["decoded"] == decoded
    assert item["steps"] == [{"encoding": "url_percent", "input": raw, "output": decoded}]


def test_plus_without_explicit_encoding_is_not_changed_or_reported():
    assert decoder.decode_payload("a+b c++")["items"] == []


def test_independent_and_repeated_fields_have_distinct_exact_offsets():
    raw = "한글😀 GET /?q=%2527&q=%2527&x=%3Cscript%3E HTTP/1.1\r\nCookie: q=%41\r\n\r\n"
    result = decoder.decode_payload(raw)
    assert [item["decoded"] for item in result["items"]] == ["'", "'", "<script>", "A"]
    assert [item["id"] for item in result["items"]] == [f"decoded-{number}" for number in range(1, 5)]
    assert len({(item["start"], item["end"]) for item in result["items"]}) == 4
    for item in result["items"]:
        assert item["original"] == raw[item["start"]:item["end"]]
    assert result["scanned_chars"] == result["total_chars"] == len(raw)
    assert result["scan_truncated"] is False
    assert raw.startswith("한글😀")


def test_nested_url_layers_stay_in_one_item_without_duplicate_results():
    item = decode_one("%2527")
    assert item["original"] == "%2527"
    assert item["decoded"] == "'"
    assert item["steps"] == [
        {"encoding": "url_percent", "input": "%2527", "output": "%27"},
        {"encoding": "url_percent", "input": "%27", "output": "'"},
    ]


def test_nested_decoding_is_limited_to_three_transforms():
    item = decode_one("%25252527")
    assert len(item["steps"]) == decoder.MAX_DECODE_STEPS == 3
    assert item["decoded"] == "%27"
    assert "max_decode_steps_reached" in item["warnings"]


@pytest.mark.parametrize("raw,decoded", [
    ("&lt;script&gt;alert(1)&lt;/script&gt;", "<script>alert(1)</script>"),
    ("&#39;OR&#x20;1&#61;1", "'OR 1=1"),
    ("&#x1F600;", "😀"),
    ("&NotEqualTilde;", "≂̸"),
    ("&apos;", "'"),
])
def test_explicit_complete_html_entities_are_text_only(raw, decoded):
    item = decode_one(raw)
    assert item["decoded"] == decoded
    assert item["steps"][0]["encoding"] == "html_entity"


@pytest.mark.parametrize("raw", ["&lt", "&amp", "&#39", "&#x27"])
def test_incomplete_html_entities_are_not_repaired(raw):
    result = decoder.decode_payload(raw)
    assert result["items"] == []
    assert "incomplete_html_entity" in result["warnings"]


@pytest.mark.parametrize("raw,warning", [
    ("&syntheticUnknown;", "invalid_html_entity"),
    ("&#xZZ;", "invalid_html_codepoint"),
    ("&#0;", "invalid_html_codepoint"),
    ("&#128;", "invalid_html_codepoint"),
    ("&#xD800;", "invalid_html_codepoint"),
    ("&#1114112;", "invalid_html_codepoint"),
])
def test_html_does_not_apply_replacement_or_legacy_codepoint_repairs(raw, warning):
    result = decoder.decode_payload(raw)
    assert result["items"] == []
    assert warning in result["warnings"]


@pytest.mark.parametrize("raw,decoded", [
    (r"\u003cscript\u003e", "<script>"),
    (r"\uD55C\uAE00", "한글"),
    (r"\uD83D\uDE00", "😀"),
    (r"\xd7\x41", "×A"),
    (r"\x3cscript\x3e", "<script>"),
])
def test_unicode_and_hex_escapes_support_scalar_values_and_surrogate_pairs(raw, decoded):
    item = decode_one(raw)
    assert item["decoded"] == decoded
    assert item["steps"][0]["encoding"] == "unicode_escape"


def test_escaped_backslash_is_not_reinterpreted_as_unicode_escape():
    result = decoder.decode_payload(r"\\u003c \\x41")
    assert result["items"] == []


@pytest.mark.parametrize("raw,warning", [
    ("%2", "invalid_url_percent_escape"),
    ("%GG", "invalid_url_percent_escape"),
    ("%41%ZZ", "invalid_url_percent_escape"),
    ("%FF", "url_percent_not_utf8"),
    ("%E3%81", "url_percent_not_utf8"),
    (r"\u12", "invalid_unicode_escape"),
    (r"\uZZZZ", "invalid_unicode_escape"),
    (r"\xG1", "invalid_unicode_escape"),
    (r"\uD800", "unpaired_unicode_surrogate"),
    (r"\uDC00", "unpaired_unicode_surrogate"),
    (r"\uD800\u0041", "unpaired_unicode_surrogate"),
])
def test_malformed_or_non_utf8_candidates_are_not_partially_repaired(raw, warning):
    result = decoder.decode_payload(raw)
    assert result["items"] == []
    assert warning in result["warnings"]
    assert raw not in result["warnings"]


def test_invalid_later_layer_retains_only_prior_exact_transform():
    item = decode_one("%25FF")
    assert item["decoded"] == "%FF"
    assert len(item["steps"]) == 1
    assert "url_percent_not_utf8" in item["warnings"]


@pytest.mark.parametrize("text", ["Hello", "select(1)", "한글입니다", "<script>alert(1)</script>", "one\ntwo\tthree"])
def test_base64_is_canonical_utf8_text_and_always_labelled_as_candidate(text):
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    item = decode_one("q=" + encoded)
    assert item["original"] == encoded
    assert item["decoded"] == text
    assert item["steps"][0]["encoding"] == "base64"
    assert "base64_candidate" in item["warnings"]


@pytest.mark.parametrize("raw", [
    "test", "password", "administrator", "aabbccddeeff0011", "123456789012",
    "YWJj", "YWJjZGVmZ2hp", "SGVsbG9=", "SGVsbG8", "_SGVsbG8=",
])
def test_common_identifiers_short_unpadded_and_noncanonical_base64_are_not_reported(raw):
    assert decoder.decode_payload(raw)["items"] == []


@pytest.mark.parametrize("binary,warning", [
    (b"\xff" * 4, "base64_not_utf8"),
    (b"\x00" * 10, "base64_not_printable"),
    (b"\x1f\x8b\x08\x00synthetic compressed marker", "base64_not_utf8"),
])
def test_binary_base64_is_not_rendered_decompressed_or_repaired(binary, warning):
    raw = base64.b64encode(binary).decode("ascii")
    result = decoder.decode_payload(raw)
    assert result["items"] == []
    assert warning in result["warnings"]


def test_different_encoding_types_can_form_an_auditable_chain():
    item = decode_one("%26%23x5c%3Bu003c")
    assert item["decoded"] == "<"
    assert [step["encoding"] for step in item["steps"]] == ["url_percent", "html_entity", "unicode_escape"]
    for first, second in zip(item["steps"], item["steps"][1:]):
        assert first["output"] == second["input"]


def test_decoded_control_characters_are_explicitly_flagged_not_removed():
    item = decode_one("%00%0D%0A")
    assert item["decoded"] == "\x00\r\n"
    assert "decoded_control_characters" in item["warnings"]
    assert json.loads(json.dumps(item)) == item


def test_original_candidate_limit_skips_entire_fragment_and_keeps_next_valid_one():
    raw = "%41" * decoder.MAX_ORIGINAL_CHARS + " next=%42"
    result = decoder.decode_payload(raw)
    assert [item["decoded"] for item in result["items"]] == ["B"]
    assert "candidate_too_long" in result["warnings"]
    assert result["scan_truncated"] is False


def test_oversized_explicit_base64_candidate_is_skipped_with_static_warning():
    raw = base64.b64encode(b"a" * 1_025).decode("ascii")
    result = decoder.decode_payload(raw)
    assert result["items"] == []
    assert result["warnings"] == ["candidate_too_long"]


def test_scan_limit_never_decodes_a_partly_scanned_fragment():
    raw = " " * (decoder.MAX_SCAN_CHARS - 5) + "%4141" + "%42"
    result = decoder.decode_payload(raw)
    assert result["items"] == []
    assert result["scanned_chars"] == decoder.MAX_SCAN_CHARS
    assert result["total_chars"] == len(raw)
    assert result["scan_truncated"] is True
    assert "candidate_crosses_scan_boundary" in result["warnings"]


def test_scan_limit_ignores_payload_after_bounded_prefix():
    raw = " " * decoder.MAX_SCAN_CHARS + "%41" * 100_000
    result = decoder.decode_payload(raw)
    assert result["items"] == []
    assert result["scanned_chars"] == decoder.MAX_SCAN_CHARS
    assert result["scan_truncated"] is True
    assert result["warnings"] == ["scan_limit_reached"]


def test_item_count_is_bounded_and_repeated_occurrences_are_not_collapsed():
    raw = " ".join("%41" for _ in range(decoder.MAX_ITEMS + 10))
    result = decoder.decode_payload(raw)
    assert len(result["items"]) == decoder.MAX_ITEMS
    assert "item_limit_reached" in result["warnings"]
    assert result["scan_truncated"] is True
    assert result["scanned_chars"] < result["total_chars"]


def test_invalid_candidate_attempts_have_an_independent_work_limit(monkeypatch):
    calls = []
    original = decoder._decode_fragment

    def count(raw):
        calls.append(1)
        return original(raw)

    monkeypatch.setattr(decoder, "_decode_fragment", count)
    result = decoder.decode_payload("%GG " * (decoder.MAX_CANDIDATES + 100))
    assert len(calls) == decoder.MAX_CANDIDATES
    assert result["items"] == []
    assert result["scan_truncated"] is True
    assert "candidate_limit_reached" in result["warnings"]


def test_serialized_output_including_original_steps_and_unicode_is_bounded():
    raw = " ".join(("한" * 700 + "%41") for _ in range(30))
    result = decoder.decode_payload(raw)
    assert result["items"]
    assert len(json.dumps(result, ensure_ascii=True).encode("ascii")) <= decoder.MAX_SERIALIZED_BYTES
    assert "serialized_output_limit_reached" in result["warnings"]
    assert result["scan_truncated"] is True
    for item in result["items"]:
        assert len(item["original"]) <= decoder.MAX_ORIGINAL_CHARS
        assert len(item["decoded"]) <= decoder.MAX_DECODED_CHARS
        assert len(item["steps"]) <= decoder.MAX_DECODE_STEPS


def test_decoded_text_limit_is_enforced_before_saving_a_transform(monkeypatch):
    monkeypatch.setattr(decoder, "MAX_DECODED_CHARS", 2)
    result = decoder.decode_payload("%41%42%43")
    assert result["items"] == []
    assert result["warnings"] == ["decoded_text_too_long"]


def test_decoding_is_deterministic_and_does_not_log_or_echo_failed_input(caplog, capsys):
    raw = "synthetic-sensitive-%FF"
    before = raw
    first = decoder.decode_payload(raw)
    second = decoder.decode_payload(raw)
    assert first == second
    assert raw == before
    assert raw not in json.dumps(first)
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


def test_input_type_error_uses_only_a_static_message():
    with pytest.raises(ValueError, match="^payload_must_be_text$"):
        decoder.decode_payload({"synthetic_sensitive": "never echo"})
