"""Synthetic-only v2.19 candidate filtering; no DB, worker or model execution."""

from copy import deepcopy
import json

import pytest

from app.agent.evidence import EvidenceSourceResolver
from app.agent.evidence_candidates import MAX_CANDIDATES, MAX_EXCERPT, build_candidates
from app.agent.input_builder import build_agent_input
from app.services.http_parser import parse_http_payload


REVISION = "waf-system-v2.19"
IDENTIFIERS = {
    "source_system": "synthetic-source",
    "event_id": "synthetic-event",
    "company_name": "Synthetic Company",
    "waf_vendor": "Synthetic Vendor",
}
RAW = (
    "POST /lookup?event_id=request-value HTTP/1.1\r\n"
    "Host: example.invalid\r\nContent-Type: application/json\r\n\r\n"
    '{"company_name":"request-company","waf_vendor":"request-vendor",'
    '"source_system":"request-source","id":"request-id"}'
)


def candidate_catalog(event, *, raw=RAW, revision=REVISION, budget=8192):
    return build_candidates(raw, event, parse_http_payload(raw), [(0, len(raw))],
        None, budget, original_event=event, fixed_rules_version=revision)


def agent_input(event, *, raw=RAW, revision=REVISION):
    return build_agent_input(event, raw, parse_http_payload(raw), 32768, 3072,
        evidence_selection=True, fixed_rules_version=revision)


def fields(catalog):
    return {item["field"] for item in catalog["items"]}


@pytest.mark.parametrize("field", list(IDENTIFIERS))
def test_v219_excludes_only_the_four_top_level_identifier_fields(field):
    event = {**IDENTIFIERS, "extra_fields": {field: "nested-request-value"}}
    catalog = candidate_catalog(event)
    assert catalog["version"] == "evidence-selection-v2"
    assert field not in fields(catalog)
    assert f"extra_fields.{field}" in fields(catalog)
    assert not set(IDENTIFIERS) & fields(catalog)


def test_nested_identifier_names_and_other_id_fields_keep_existing_eligibility():
    event = {**IDENTIFIERS, "extra_fields": {
        **{field: "nested-" + field for field in IDENTIFIERS},
        "request_id": "request-id-value", "user_id": "user-id-value",
        "id": "ordinary-id-value", "request": {"query": {"id": "query-value"}},
        "items": [{"event_id": "list-event-id"}],
    }}
    catalog = candidate_catalog(event)
    assert {"extra_fields." + field for field in IDENTIFIERS} <= fields(catalog)
    assert {
        "extra_fields.request_id", "extra_fields.user_id", "extra_fields.id",
        "extra_fields.request.query.id", "extra_fields.items.0.event_id",
    } <= fields(catalog)


def test_detection_and_network_context_candidates_are_not_removed():
    event = {**IDENTIFIERS,
        "signature": "Synthetic Signature", "event_name": "Synthetic Detection",
        "waf_action": "D", "src_ip": "192.0.2.10", "dest_ip": "198.51.100.20",
        "src_port": 43120, "dest_port": 443,
    }
    catalog = candidate_catalog(event)
    assert {"signature", "event_name", "waf_action", "src_ip", "dest_ip", "src_port", "dest_port"} <= fields(catalog)
    assert not set(IDENTIFIERS) & fields(catalog)
    values = {item["field"]: item["excerpt"] for item in catalog["items"]}
    assert values["src_port"] == "43120" and values["dest_port"] == "443"


def test_identifier_names_and_values_in_payload_are_not_sanitized():
    body = json.dumps(IDENTIFIERS, ensure_ascii=False, separators=(",", ":"))
    raw = "POST /event_id HTTP/1.1\r\nHost: example.invalid\r\n\r\n" + body
    assert len(raw) < MAX_EXCERPT
    catalog = candidate_catalog(IDENTIFIERS, raw=raw)
    whole = next(item for item in catalog["items"] if item["field"] == "payload" and item["excerpt"] == raw)
    assert whole["start"] == 0 and whole["end"] == len(raw)
    for field, value in IDENTIFIERS.items():
        assert field in whole["excerpt"] and value in whole["excerpt"]
    assert not set(IDENTIFIERS) & fields(catalog)


def test_filtering_does_not_mutate_input_event_or_remove_model_context():
    event = {**IDENTIFIERS, "signature": "Synthetic Signature",
        "extra_fields": {"request_id": "synthetic-extra-id"}}
    before = deepcopy(event)
    candidate_catalog(event)
    built = agent_input(event)
    document = json.loads(built.text)
    assert event == before
    assert document["event"] == {**before, "payload": RAW}
    assert document["evidence_candidates"]["version"] == "evidence-selection-v2"
    assert not set(IDENTIFIERS) & fields(document["evidence_candidates"])
    assert not built.input_truncated
    assert built.retained_payload_spans == ((0, len(RAW)),)


def test_intentional_identifier_exclusion_is_not_reported_as_missing_evidence():
    # The entire one-character source is covered, and there are no eligible
    # metadata values to omit. Filtering is policy, not a truncation signal.
    catalog = candidate_catalog(IDENTIFIERS, raw="X")
    assert catalog == {
        "version": "evidence-selection-v2", "omitted": False,
        "items": [{"source_id": "c1", "field": "payload", "excerpt": "X",
                   "start": 0, "end": 1, "decoded_hint_indexes": []}],
    }
    built = agent_input(IDENTIFIERS, raw="X")
    document = json.loads(built.text)
    assert document["event"] == {**IDENTIFIERS, "payload": "X"}
    assert not document["evidence_candidates"]["omitted"]
    assert not built.input_truncated
    assert document["input_truncation"] == {"payload": False, "event_metadata": False, "parser_hints": False}


@pytest.mark.parametrize("revision", [None, "waf-system-v2.7", "waf-system-v2.16",
    "waf-system-v2.17", "waf-system-v2.18", "unknown-rules", "waf-system-v2.190",
    "waf-system-v2.19/policy-1", ""])
def test_older_missing_and_unknown_revisions_preserve_exact_legacy_catalog(revision):
    expected = {
        "version": "evidence-selection-v1", "omitted": False,
        "items": [{"source_id": "c1", "field": "payload", "excerpt": "X",
                   "start": 0, "end": 1, "decoded_hint_indexes": []}],
    }
    for field, value in IDENTIFIERS.items():
        expected["items"].append({"source_id": f"c{len(expected['items']) + 1}",
            "field": field, "excerpt": value, "start": None, "end": None})
    assert candidate_catalog(IDENTIFIERS, raw="X", revision=revision) == expected
    # Omitting the new keyword must also retain the old wire catalog exactly.
    assert build_candidates("X", IDENTIFIERS, parse_http_payload("X"), [(0, 1)],
        None, 8192, original_event=IDENTIFIERS) == expected


@pytest.mark.parametrize("revision", [None, "waf-system-v2.18", "unknown-rules"])
def test_input_builder_preserves_legacy_catalog_for_non_v219_revisions(revision):
    built = agent_input(IDENTIFIERS, revision=revision)
    document = json.loads(built.text)
    assert document["evidence_candidates"]["version"] == "evidence-selection-v1"
    assert set(IDENTIFIERS) <= fields(document["evidence_candidates"])
    previous = build_agent_input(IDENTIFIERS, RAW, parse_http_payload(RAW), 32768, 3072,
        evidence_selection=True)
    assert built == previous


def test_filtering_does_not_reallocate_reserved_candidate_budget_to_raw_payload():
    raw = "POST /large HTTP/1.1\r\nHost: example.invalid\r\n\r\n" + "request-data-0123456789" * 5000
    old = agent_input(IDENTIFIERS, raw=raw, revision="waf-system-v2.18")
    new = agent_input(IDENTIFIERS, raw=raw)
    old_document, new_document = json.loads(old.text), json.loads(new.text)
    assert old.input_truncated and new.input_truncated
    assert old.retained_payload_spans == new.retained_payload_spans
    assert old.submitted_payload_chars == new.submitted_payload_chars
    assert old.estimated_input_token_budget == new.estimated_input_token_budget
    assert old_document["event"] == new_document["event"]
    assert old_document["event"]["payload"] == new_document["event"]["payload"]


@pytest.mark.parametrize("budget", [256, 768, 2048, 8192])
def test_v219_keeps_candidate_count_excerpt_size_and_serialized_budget_limits(budget):
    raw = "POST /bounded HTTP/1.1\r\nHost: example.invalid\r\n\r\n" + (
        "value=" + "synthetic-data-" * 25 + "\n") * 100
    catalog = candidate_catalog(IDENTIFIERS, raw=raw, budget=budget)
    assert catalog["version"] == "evidence-selection-v2"
    assert len(catalog["items"]) <= MAX_CANDIDATES
    assert len(json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))) <= budget
    assert catalog["omitted"]
    assert not set(IDENTIFIERS) & fields(catalog)
    resolver = EvidenceSourceResolver(raw, IDENTIFIERS, parse_http_payload(raw),
        submitted_payload_spans=((0, len(raw)),), submitted_event=IDENTIFIERS)
    for item in catalog["items"]:
        assert len(item["excerpt"]) <= MAX_EXCERPT
        assert resolver.matches(item["field"], item["excerpt"])
        assert raw[item["start"]:item["end"]] == item["excerpt"]
