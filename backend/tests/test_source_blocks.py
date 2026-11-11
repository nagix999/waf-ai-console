"""Synthetic source-block coverage tests, not model quality evaluation."""

import copy
import hashlib
import json
import re

import pytest

from app.agent.evidence import EvidenceSourceResolver
from app.agent.evidence_candidates import resolve_selection
from app.agent.input_builder import build_agent_input
from app.agent.source_blocks import (
    MAX_SOURCE_ITEMS, SOURCE_BLOCKS_VERSION, build_source_catalog,
    metadata_candidates, payload_blocks, source_blocks_artifact,
)
from app.services.http_parser import parse_http_payload
from app.services.payload_decoding import decode_payload
from app.services.request_integrity import assess_request_integrity


def build(raw, event=None, *, context_window=32768, max_output_tokens=3072, **kwargs):
    return build_agent_input(
        event or {}, raw, parse_http_payload(raw), context_window, max_output_tokens,
        evidence_selection=True, fixed_rules_version="waf-system-v2.20", **kwargs,
    )


def assert_complete_coverage(raw, built):
    document = json.loads(built.text)
    catalog = document["evidence_candidates"]
    assert catalog["version"] == SOURCE_BLOCKS_VERSION
    assert "payload" not in document["event"]
    assert len(built.text) <= built.estimated_input_token_budget * 3
    assert len(catalog["items"]) <= MAX_SOURCE_ITEMS
    assert len({item["source_id"] for item in catalog["items"]}) == len(catalog["items"])
    blocks = [item for item in catalog["items"] if item["field"] == "payload"]
    assert all(re.fullmatch(r"c[1-9][0-9]{0,2}", item["source_id"]) for item in catalog["items"])
    for item in blocks:
        assert 0 < len(item["excerpt"]) <= 300
        assert raw[item["start"]:item["end"]] == item["excerpt"]
        assert any(a <= item["start"] < item["end"] <= b for a, b in built.retained_payload_spans)
    for start, end in built.retained_payload_spans:
        selected = [item for item in blocks if start <= item["start"] < item["end"] <= end]
        cursor = start
        for item in selected:
            assert item["start"] == cursor
            cursor = item["end"]
        assert cursor == end
        assert "".join(item["excerpt"] for item in selected) == raw[start:end]
    assert built.submitted_payload_chars == sum(b - a for a, b in built.retained_payload_spans)
    assert built.submitted_payload_chars == sum(len(item["excerpt"]) for item in blocks)
    assert document["input_truncation"]["payload"] == (built.submitted_payload_chars != len(raw))
    return document, blocks


@pytest.mark.parametrize("raw", [
    "GET /fixture HTTP/1.1\r\nCookie: synthetic=unchanged\r\n\r\n",
    "GET /fixture HTTP/1.1\nX-Fixture: value\n\n",
    r"GET /fixture HTTP/1.1\r\nCookie: synthetic=unchanged\r\n\r\n",
    "POST /fixture HTTP/1.1\r\n\r\n" + "😀한글é" * 900,
    "POST /fixture HTTP/1.1\r\n\r\n" + '\\"\x00\t' * 900,
    "fixture=" + "x" * 36000,
    "fixture=value\r\n" * 1800,
    " " * 3000,
    "",
])
def test_all_submitted_characters_have_exact_bounded_source_blocks(raw):
    document, blocks = assert_complete_coverage(raw, build(raw))
    assert "request_line" not in document["generic_http_parser_hints"]
    assert "uri" not in document["generic_http_parser_hints"]
    assert not document["evidence_candidates"]["omitted"]


def test_payload_is_not_duplicated_in_event_or_parser_hints():
    raw = "POST /unique-request-target HTTP/1.1\r\n\r\nUNIQUE_BODY_SOURCE"
    built = build(raw)
    document, blocks = assert_complete_coverage(raw, built)
    assert "".join(item["excerpt"] for item in blocks) == raw
    assert built.text.count("UNIQUE_BODY_SOURCE") == 1
    assert built.text.count("/unique-request-target") == 1
    assert not built.input_truncated


def test_recognized_request_line_and_body_are_separate_without_masking_cookie():
    raw = ("POST /fixture?q=hello HTTP/1.1\r\nHost: fixture.invalid\r\n"
           "Cookie: SYNTHETIC_COOKIE_CANARY\r\nContent-Length: 10\r\n\r\nnote=hello")
    parsed = parse_http_payload(raw)
    document, blocks = assert_complete_coverage(raw, build(raw))
    assert blocks[0]["excerpt"] == parsed["request_line"]
    assert blocks[-1]["excerpt"] == "note=hello"
    assert all("SYNTHETIC_COOKIE_CANARY" not in item["excerpt"]
               for item in blocks if "note=hello" in item["excerpt"] or "q=hello" in item["excerpt"])
    assert "SYNTHETIC_COOKIE_CANARY" in "".join(item["excerpt"] for item in blocks)
    assert not document["input_truncation"]["payload"]


def test_normal_request_line_block_does_not_overlap_body_integrity_issue():
    raw = "POST /fixture?q=hello HTTP/1.1\r\nContent-Length: 80\r\n\r\nnote=hello"
    parsed = parse_http_payload(raw)
    _, blocks = assert_complete_coverage(raw, build(raw))
    request_block = next(item for item in blocks if "q=hello" in item["excerpt"])
    body_start = parsed["raw_source_spans"]["body"][0][0]
    assert request_block["end"] <= body_start
    assert "note=hello" not in request_block["excerpt"]


@pytest.mark.parametrize("mutate", ["failed", "missing_ranges", "bad_offset", "wrong_body", "missing_body"])
def test_unknown_or_invalid_body_boundaries_are_not_invented(mutate):
    raw = "POST /fixture HTTP/1.1\r\nHost: fixture.invalid\r\n\r\nnote=hello"
    parsed = parse_http_payload(raw)
    if mutate == "failed":
        parsed["parse_status"] = "failed"
    elif mutate == "missing_ranges":
        parsed.pop("raw_source_spans")
    elif mutate == "bad_offset":
        parsed["raw_source_spans"]["body"] = [[-1, len(raw)]]
    elif mutate == "wrong_body":
        parsed["body"] = "invented-body"
    else:
        parsed["raw_source_spans"].pop("body")
    catalog = build_source_catalog(raw, [(0, len(raw))], [], False, parsed=parsed)
    body_start = raw.index("note=hello")
    assert not any(item["start"] == body_start for item in catalog["items"])
    assert "".join(item["excerpt"] for item in catalog["items"]) == raw


def test_decoder_origin_crossing_existing_request_line_boundary_links_both_blocks():
    raw = "POST /fixture HTTP/1.1\r\nHost: fixture.invalid\r\n\r\nnote=hello"
    line_end = len("POST /fixture HTTP/1.1")
    decoding = decoder_fixture(raw, [(line_end - 5, line_end + 8)])
    built = build(raw, decoding=decoding)
    document, blocks = assert_complete_coverage(raw, built)
    assert_decoder_links(document)
    linked = [item for item in blocks if item.get("decoded_hint_indexes") == [0]]
    assert len(linked) == 2 and linked[0]["end"] == linked[1]["start"] == line_end


@pytest.mark.parametrize("context_window", [5632, 8192, 32768])
def test_structural_boundaries_and_decoder_links_remain_inside_serialized_budget(context_window):
    raw = ("POST /fixture HTTP/1.1\r\nCookie: SYNTHETIC_COOKIE_CANARY\r\n"
           "Content-Type: text/plain\r\n\r\n" + "body%3Cscript%3E" * 5000)
    body_start = raw.index("body%3C")
    decoding = decoder_fixture(raw, [(body_start - 3, body_start + 15), (len(raw) - 30, len(raw))])
    built = build(raw, decoding=decoding, context_window=context_window, max_output_tokens=1024)
    document, blocks = assert_complete_coverage(raw, built)
    assert_decoder_links(document)
    assert blocks[0]["excerpt"] == "POST /fixture HTTP/1.1"
    for item in blocks:
        if item["start"] >= body_start:
            assert "SYNTHETIC_COOKIE_CANARY" not in item["excerpt"]


def test_middle_code_is_not_lost_after_forty_eight_candidate_limit():
    marker = "<svg onload=syntheticFunction()>"
    raw = "x" * 18000 + marker + "y" * 18000
    built = build(raw)
    _, blocks = assert_complete_coverage(raw, built)
    assert len(blocks) > 48
    assert not built.input_truncated
    assert marker in "".join(item["excerpt"] for item in blocks)


@pytest.mark.parametrize("unit", ["x", "😀", "한글", '\\"\x00\t\n'])
@pytest.mark.parametrize("context_window", [5632, 8192, 32768])
def test_budget_truncation_preserves_only_real_ranges_and_covers_each(unit, context_window):
    raw = "HEAD_MARK\r\n" + unit * 100000 + "\r\nTAIL_MARK"
    built = build(raw, context_window=context_window, max_output_tokens=1024)
    document, blocks = assert_complete_coverage(raw, built)
    assert built.input_truncated
    assert document["input_truncation"]["payload"]
    assert blocks[0]["excerpt"].startswith("HEAD_MARK")
    assert blocks[-1]["excerpt"].endswith("TAIL_MARK")
    assert "WAF_PAYLOAD_TRUNCATED" not in "".join(item["excerpt"] for item in blocks)
    assert len(built.retained_payload_spans) >= 2


def test_large_context_is_still_bounded_by_source_id_space():
    raw = "fixture-line\r\n" * 50000
    built = build(raw, context_window=1000000)
    _, blocks = assert_complete_coverage(raw, built)
    assert built.input_truncated
    assert 48 < len(blocks) <= 999


@pytest.mark.parametrize("line_ending", ["\r\n", "\n", "\r"])
@pytest.mark.parametrize("padding", [149, 150, 298, 299, 300])
def test_block_boundaries_preserve_exact_line_endings(line_ending, padding):
    raw = ("x" * padding + line_ending) * 8
    blocks = list(payload_blocks(raw, [(0, len(raw))]))
    assert "".join(item["excerpt"] for item in blocks) == raw
    assert all(len(item["excerpt"]) <= 300 for item in blocks)
    if line_ending == "\r\n":
        assert not any(left["excerpt"].endswith("\r") and right["excerpt"].startswith("\n")
                       for left, right in zip(blocks, blocks[1:]))


def test_catalog_does_not_join_omitted_middle_or_duplicate_occurrences():
    raw = "SAME" + "x" * 1000 + "SAME"
    spans = [(0, 4), (1004, 1008)]
    catalog = build_source_catalog(raw, spans, [], False)
    assert [(item["excerpt"], item["start"], item["end"]) for item in catalog["items"]] == [
        ("SAME", 0, 4), ("SAME", 1004, 1008),
    ]
    assert catalog["items"][0]["source_id"] != catalog["items"][1]["source_id"]


@pytest.mark.parametrize("spans", [[(-1, 3)], [(0, 100)], [(3, 2)], [(0, 3), (2, 4)]])
def test_invalid_source_offsets_are_rejected(spans):
    with pytest.raises(ValueError, match="^source_blocks_invalid$"):
        list(payload_blocks("fixture", spans))


def test_only_four_top_level_identifiers_are_excluded_and_originals_unchanged():
    raw = "GET /?event_id=payload-identifier HTTP/1.1\r\n\r\n"
    event = {
        "event_id": "synthetic-id", "source_system": "synthetic-source",
        "company_name": "synthetic-company", "waf_vendor": "synthetic-vendor",
        "signature": "synthetic-signature", "event_name": "synthetic-event",
        "waf_action": "D", "src_ip": "192.0.2.2", "src_port": 1234,
        "payload": raw,
        "extra_fields": {"event_id": "nested-id", "company_name": "nested-company",
                         "source_system": "nested-source", "waf_vendor": "nested-vendor"},
    }
    original = copy.deepcopy(event)
    document, _ = assert_complete_coverage(raw, build(raw, event))
    fields = {item["field"] for item in document["evidence_candidates"]["items"]}
    assert not fields & {"event_id", "source_system", "company_name", "waf_vendor"}
    assert {"signature", "event_name", "waf_action", "src_ip", "src_port"} <= fields
    assert {"extra_fields.event_id", "extra_fields.company_name",
            "extra_fields.source_system", "extra_fields.waf_vendor"} <= fields
    assert document["event"]["event_id"] == "synthetic-id"
    assert event == original


def test_metadata_and_nested_traversal_are_bounded():
    nested = {"value": "synthetic"}
    for _ in range(1000):
        nested = {"child": nested}
    event = {"extra_fields": {"deep": nested, "many": ["fixture"] * 10000}}
    raw = "GET /fixture HTTP/1.1\r\n\r\n"
    document, _ = assert_complete_coverage(raw, build(raw, event))
    assert document["input_truncation"]["event_metadata"]
    assert len([item for item in document["evidence_candidates"]["items"] if item["field"] != "payload"]) <= 100


def test_clipped_metadata_annotation_is_not_an_original_source():
    raw = "GET /fixture HTTP/1.1\r\n\r\n"
    submitted = {"extra_fields": {"value": "x" * 10 + "…"}}
    original = {"extra_fields": {"value": "x" * 400}}
    items, omitted = metadata_candidates(submitted, original, parse_http_payload(raw), raw, 8192)
    assert omitted and not items


def test_selected_blocks_use_existing_exact_source_resolver():
    from app.agent.contracts import EvidenceSelectionOutput
    from test_moduagent_worker import primary_output

    raw = "POST /fixture HTTP/1.1\r\n\r\n" + "block-data-" * 300
    built = build(raw)
    document, blocks = assert_complete_coverage(raw, built)
    resolver = EvidenceSourceResolver(raw, {}, parse_http_payload(raw),
        submitted_payload_spans=built.retained_payload_spans, submitted_event=document["event"])
    value = primary_output().model_dump(mode="json")
    for item in blocks:
        value["evidence"] = [{"source_id": item["source_id"], "interpretation_ko": "합성 원문 해석"}]
        output = EvidenceSelectionOutput.model_validate(value)
        resolved, issues, resolutions = resolve_selection(output, document["evidence_candidates"], resolver)
        if not item["excerpt"].strip():
            # Separating recognized structure can expose a standalone CRLF
            # separator. Retain it verbatim but keep the existing empty-content
            # grounding rejection instead of treating whitespace as evidence.
            assert issues == [{"index": 0, "reason": "empty_excerpt"}]
            assert not resolutions and not resolved.evidence
            continue
        assert not issues and resolutions
        assert resolved.evidence[0].excerpt == item["excerpt"]


def test_decoder_and_integrity_hints_are_retained_within_same_budget():
    raw = "POST /fixture HTTP/1.1\r\nContent-Length: 22\r\n\r\nvalue=%3Cscript%3E"
    parsed = parse_http_payload(raw)
    built = build(raw, decoding=decode_payload(raw), request_integrity=assess_request_integrity(raw, parsed))
    document, _ = assert_complete_coverage(raw, built)
    assert document["request_integrity"] == built.request_integrity
    assert document["decoded_payload_hints"]["items"]
    for hint in document["decoded_payload_hints"]["items"]:
        assert raw[hint["start"]:hint["end"]] == hint["original"]
        assert any(a <= hint["start"] < hint["end"] <= b for a, b in built.retained_payload_spans)
    assert_decoder_links(document)


def decoder_fixture(raw, spans):
    return {"decoder_version": "synthetic-decoder", "items": [
        {"start": start, "end": end, "original": raw[start:end],
         "decoded": "synthetic-derived-value", "steps": [], "warnings": []}
        for start, end in spans
    ], "scan_truncated": False, "warnings": []}


def assert_decoder_links(document):
    hints = document["decoded_payload_hints"]["items"]
    blocks = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
    for index, hint in enumerate(hints):
        matching = [item for item in blocks if hint["start"] < item["end"] and item["start"] < hint["end"]]
        assert matching
        assert all(index in item.get("decoded_hint_indexes", []) for item in matching)
    for item in blocks:
        assert item.get("decoded_hint_indexes", []) == [
            index for index, hint in enumerate(hints)
            if hint["start"] < item["end"] and item["start"] < hint["end"]
        ]


@pytest.mark.parametrize("span", [(20, 40), (280, 340), (20, 940)])
def test_decoder_links_all_intersecting_blocks_for_short_and_long_origins(span):
    raw = "fixture-" * 200
    decoding = decoder_fixture(raw, [span])
    original = copy.deepcopy(decoding)
    built = build(raw, decoding=decoding)
    document, _ = assert_complete_coverage(raw, built)
    assert_decoder_links(document)
    assert len(document["decoded_payload_hints"]["items"]) == 1
    assert decoding == original


def test_filtered_decoder_hints_use_submitted_local_indexes_not_original_indexes():
    raw = "HEAD" + "x" * 100000 + "TAIL"
    decoding = decoder_fixture(raw, [(50000, 50020), (len(raw) - 4, len(raw))])
    built = build(raw, decoding=decoding, context_window=8192, max_output_tokens=1024)
    document, _ = assert_complete_coverage(raw, built)
    assert len(document["decoded_payload_hints"]["items"]) == 1
    assert document["decoded_payload_hints"]["items"][0]["original"] == "TAIL"
    assert document["decoded_payload_hints"]["omitted_items"]
    assert_decoder_links(document)
    assert any(item.get("decoded_hint_indexes") == [0] for item in document["evidence_candidates"]["items"])


def test_decoder_origin_crossing_omitted_gap_is_not_submitted_or_linked():
    raw = "x" * 100000
    decoding = decoder_fixture(raw, [(10, len(raw) - 10)])
    built = build(raw, decoding=decoding, context_window=8192, max_output_tokens=1024)
    document, _ = assert_complete_coverage(raw, built)
    assert not document["decoded_payload_hints"]["items"]
    assert document["decoded_payload_hints"]["omitted_items"]
    assert not any(item.get("decoded_hint_indexes") for item in document["evidence_candidates"]["items"])


@pytest.mark.parametrize("context_window", [5632, 8192, 32768])
def test_source_links_and_decoder_hint_text_are_included_in_serialized_budget(context_window):
    raw = "fixture-%3Cscript%3E-" * 5000
    decoding = decoder_fixture(raw, [(index * 400, index * 400 + 390) for index in range(20)])
    built = build(raw, decoding=decoding, context_window=context_window, max_output_tokens=1024)
    document, _ = assert_complete_coverage(raw, built)
    assert_decoder_links(document)
    assert len(built.text) <= built.estimated_input_token_budget * 3


@pytest.mark.parametrize("mutate", ["missing_original", "wrong_original", "wrong_offset", "non_submitted"])
def test_catalog_never_links_invalid_or_unsubmitted_decoder_origins(mutate):
    raw = "fixture" * 300
    decoding = decoder_fixture(raw, [(10, 30)])
    hint = decoding["items"][0]
    spans = [(0, 300)]
    if mutate == "missing_original":
        hint.pop("original")
    elif mutate == "wrong_original":
        hint["original"] = "fabricated"
    elif mutate == "wrong_offset":
        hint["start"] = -1
    else:
        hint.update(start=1000, end=1020, original=raw[1000:1020])
    catalog = build_source_catalog(raw, spans, [], False, decoding=decoding)
    assert not any(item.get("decoded_hint_indexes") for item in catalog["items"])


def test_omitted_scope_does_not_claim_any_submitted_payload_has_no_candidate():
    raw = "fixture" * 1000
    catalog = build_source_catalog(raw, [(0, len(raw))], [], True)
    assert catalog["omitted"]
    assert catalog["omitted_scope"] == "event_metadata_only"
    assert "".join(item["excerpt"] for item in catalog["items"]) == raw


@pytest.mark.parametrize("version,expected", [
    (None, "46a78b8e77a7d3b26fa39af044eb5e687a2b6a9011bec2b6e44a7db75b700439"),
    ("waf-system-v2.18", "46a78b8e77a7d3b26fa39af044eb5e687a2b6a9011bec2b6e44a7db75b700439"),
    ("waf-system-v2.19", "36c696968bcf7283b85391e0c85c14a87f82dc3c985fc8dca1b033497a6cf5e8"),
    ("unknown-version", "46a78b8e77a7d3b26fa39af044eb5e687a2b6a9011bec2b6e44a7db75b700439"),
])
def test_legacy_input_documents_keep_exact_serialized_bytes(version, expected):
    raw = "POST /synthetic?q=1 HTTP/1.1\r\nCookie: fixture=safe\r\n\r\n" + "x" * 2000 + "끝"
    event = {"event_id": "synthetic-id", "source_system": "fixture-source", "company_name": "fixture-co",
             "waf_vendor": "fixture-vendor", "signature": "fixture-signature", "waf_action": "D",
             "extra_fields": {"event_id": "nested-visible", "value": "synthetic"}}
    built = build_agent_input(event, raw, parse_http_payload(raw), 8192, 1024,
                             evidence_selection=True, fixed_rules_version=version)
    assert hashlib.sha256(built.text.encode()).hexdigest() == expected


def test_version_does_not_silently_change_legacy_non_selection_callers():
    raw = "GET /fixture HTTP/1.1\r\n\r\n"
    legacy = build_agent_input({}, raw, parse_http_payload(raw), 8192, 1024)
    pinned = build_agent_input({}, raw, parse_http_payload(raw), 8192, 1024,
                               fixed_rules_version="waf-system-v2.20")
    assert pinned == legacy


def test_artifact_hash_covers_both_implementation_files_and_is_safe():
    artifact = source_blocks_artifact()
    assert artifact["version"] == "agent-input-v2"
    assert artifact["source_catalog_version"] == "evidence-selection-v3"
    assert set(artifact["files"]) == {"input_builder.py", "source_blocks.py"}
    assert all(re.fullmatch("[0-9a-f]{64}", value) for value in artifact["files"].values())
    serialized = json.dumps(artifact["files"], sort_keys=True, separators=(",", ":"))
    assert artifact["sha256"] == hashlib.sha256(serialized.encode()).hexdigest()
