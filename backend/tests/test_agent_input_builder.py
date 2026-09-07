import copy
import json

import pytest

from app.agent.input_builder import TRUNCATION_MARKER, build_agent_input, truncate_payload
from app.services.http_parser import parse_http_payload


def build(payload, event=None, *, context_window=8192, max_output_tokens=1024):
    return build_agent_input(
        event or {"event_id": "synthetic-input", "signature": "SQL Injection"},
        payload,
        parse_http_payload(payload),
        context_window,
        max_output_tokens,
    )


def assert_bounded_document(built, context_window=8192, max_output_tokens=1024):
    # This is the public estimated character envelope, not a tokenizer assertion.
    remaining = context_window - max_output_tokens - 4096
    assert built.estimated_input_token_budget == remaining
    assert len(built.text) <= remaining * 3
    document = json.loads(built.text)
    assert built.submitted_payload_chars == len(document["event"]["payload"])
    assert document["input_truncated"] == built.input_truncated
    assert any(document["input_truncation"].values()) == built.input_truncated
    return document


def test_normal_input_preserves_payload_cookie_and_event_without_mutation():
    payload = "POST /login?next=%2Fadmin HTTP/1.1\r\nCookie: session=synthetic\r\n\r\n사용자=테스트"
    event = {
        "event_id": "synthetic-1",
        "company_name": "합성회사",
        "signature": "Generic Rule",
        "extra_fields": {"attributes": {"policy": "test-policy", "tags": ["합성", 42, True, None]}},
    }
    original = copy.deepcopy(event)
    built = build(payload, event)
    document = assert_bounded_document(built)
    assert built.input_truncated is False
    assert document["event"] == {**event, "payload": payload}
    assert document["generic_http_parser_hints"]["query_parameter_names"] == ["next"]
    assert document["generic_http_parser_hints"]["header_names"] == ["cookie"]
    assert event == original


@pytest.mark.parametrize("body_fragment", ["ascii", "한글보안분석", '\\"\x00\t\n'])
def test_entire_serialized_input_is_bounded_for_ascii_korean_and_json_escapes(body_fragment):
    payload = "POST /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n" + body_fragment * 20_000 + "TAIL_MARK"
    built = build(payload)
    document = assert_bounded_document(built)
    submitted = document["event"]["payload"]
    assert built.input_truncated is True
    assert document["input_truncation"]["payload"] is True
    assert submitted.startswith("POST /synthetic HTTP/1.1")
    assert submitted.endswith("TAIL_MARK")
    assert TRUNCATION_MARKER in submitted
    assert built.original_payload_chars == len(payload)


def test_long_uri_cannot_reenter_in_full_through_parser_hints():
    payload = "GET /search?q=" + "x" * 100_000 + " HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n"
    built = build(payload, context_window=32768, max_output_tokens=3072)
    document = assert_bounded_document(built, 32768, 3072)
    hints = document["generic_http_parser_hints"]
    assert len(hints["request_line"]) < 3000
    assert document["input_truncation"] == {"payload": True, "event_metadata": False, "parser_hints": True}


@pytest.mark.parametrize(
    "metadata",
    [
        {"large_text": "합성\\\"\x00" * 100_000},
        {"many_values": ["synthetic" + str(index) for index in range(10_000)]},
        {"field_" + str(index): "synthetic" for index in range(10_000)},
        {"oversized_key" * 1000: "synthetic"},
    ],
)
def test_oversized_metadata_is_bounded_without_losing_small_payload(metadata):
    payload = "GET /health HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n"
    event = {"event_id": "synthetic-metadata", "extra_fields": metadata}
    original = copy.deepcopy(event)
    built = build(payload, event)
    document = assert_bounded_document(built)
    assert document["event"]["payload"] == payload
    assert document["input_truncation"]["event_metadata"] is True
    assert document["input_truncation"]["payload"] is False
    assert event == original


def test_deep_metadata_does_not_recurse_through_the_entire_source():
    nested = {"value": "synthetic-leaf"}
    for _ in range(2000):
        nested = {"child": nested}
    built = build("GET /health HTTP/1.1\r\n\r\n", {"event_id": "synthetic-deep", "extra_fields": nested})
    document = assert_bounded_document(built)
    assert document["input_truncation"]["event_metadata"] is True
    cursor = nested
    for _ in range(2000):
        cursor = cursor["child"]
    assert cursor == {"value": "synthetic-leaf"}


def test_discarded_parser_items_set_truncation_even_with_small_payload():
    parsed = {
        "parse_status": "success",
        "headers": {"x-synthetic-" + str(index): ["v"] for index in range(200)},
        "query": [],
    }
    built = build_agent_input({"event_id": "synthetic-hints"}, "GET / HTTP/1.1", parsed, 32768, 3072)
    document = assert_bounded_document(built, 32768, 3072)
    assert document["input_truncation"] == {"payload": False, "event_metadata": False, "parser_hints": True}
    assert len(document["generic_http_parser_hints"]["header_names"]) <= 100


def test_small_positive_context_budget_does_not_expand_to_the_old_minimum():
    # 512 tokens remain, so the entire user document has at most 1,536 characters.
    payload = "GET /small HTTP/1.1\r\n\r\n" + "x" * 20_000 + "TAIL_MARK"
    built = build(payload, context_window=5632, max_output_tokens=1024)
    document = assert_bounded_document(built, 5632, 1024)
    assert built.input_truncated is True
    assert document["event"]["payload"].startswith("GET /small HTTP/1.1")
    assert document["event"]["payload"].endswith("TAIL_MARK")


@pytest.mark.parametrize("context_window,max_output_tokens", [(4096, 1024), (8192, 8000), (4400, 0), (8192, -1)])
def test_context_without_usable_input_room_fails_with_safe_error(context_window, max_output_tokens):
    with pytest.raises(ValueError, match="^agent_context_budget_too_small$"):
        build("GET /synthetic HTTP/1.1\r\n\r\n", context_window=context_window, max_output_tokens=max_output_tokens)


@pytest.mark.parametrize("budget", [0, 1, 10, len(TRUNCATION_MARKER), len(TRUNCATION_MARKER) + 1, 100])
def test_payload_truncation_never_exceeds_small_requested_character_budget(budget):
    submitted, truncated = truncate_payload("head" + "x" * 1000 + "tail", budget)
    assert len(submitted) <= budget
    assert truncated is True


def test_signature_span_head_and_tail_survive_payload_selection():
    payload = "HEAD_MARK" + "a" * 10_000 + "SYNTHETIC_SIGNATURE" + "b" * 10_000 + "TAIL_MARK"
    submitted, truncated = truncate_payload(payload, 2000, "SYNTHETIC_SIGNATURE")
    assert truncated is True
    assert submitted.startswith("HEAD_MARK")
    assert submitted.endswith("TAIL_MARK")
    assert "SYNTHETIC_SIGNATURE" in submitted
    assert len(submitted) <= 2000
