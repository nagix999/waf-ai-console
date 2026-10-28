"""Synthetic-only equivalence and boundary checks for local preprocessing."""

import json
from pathlib import Path
import runpy

import pytest

from app.agent import evidence_candidates, input_builder
from app.agent.evidence import EvidenceSourceResolver
from app.services import payload_decoding
from app.services.http_parser import parse_http_payload


def _legacy_bounded_payload(payload, budget, signature):
    if len(payload) + 2 <= budget and len(input_builder._serialize(payload)) <= budget:
        return payload, False, [(0, len(payload))]
    low, high = 0, min(len(payload), budget - 2)
    fitted, retained = "", []
    while low <= high:
        middle = (low + high) // 2
        candidate, spans = input_builder._select_payload(payload, middle, signature)
        if len(input_builder._serialize(candidate)) <= budget:
            fitted, retained, low = candidate, spans, middle + 1
        else:
            high = middle - 1
    return fitted, fitted != payload, retained


@pytest.mark.parametrize("fragment", ["abc", "한글İ", '\\"\t\x00', "İ" * 10])
@pytest.mark.parametrize("budget", [0, 1, 2, 33, 34, 100, 127, 1000, 30000])
def test_payload_fit_preserves_original_offsets_and_escaping(fragment, budget):
    raw = "HEAD" + fragment * 600 + "SyntheticMarker" + fragment * 600 + "TAIL"
    actual = input_builder._bounded_payload(raw, budget, "syntheticmarker")
    assert actual == _legacy_bounded_payload(raw, budget, "syntheticmarker")


def test_payload_signature_search_is_once_per_fit():
    class CountingPayload(str):
        lower_calls = 0

        def lower(self):
            self.lower_calls += 1
            return super().lower()

    raw = CountingPayload("head" + "İ" * 20000 + "syntheticmarker" + "z" * 20000)
    input_builder._bounded_payload(raw, 1000, "syntheticmarker")
    assert raw.lower_calls == 1


def _legacy_decoding_hints(decoding, raw, spans, budget):
    result = {
        "decoder_version": decoding["decoder_version"], "items": [],
        "omitted_items": False, "scan_truncated": bool(decoding.get("scan_truncated")),
        "warnings": list(decoding.get("warnings", [])),
        "interpretation": "derived_untrusted_text_not_application_execution",
        "raw_source_field": "payload",
        "source_offsets": "original_payload_characters_end_exclusive",
    }
    for item in decoding.get("items", []):
        start, end = item["start"], item["end"]
        if raw[start:end] != item["original"] or not any(a <= start < end <= b for a, b in spans):
            result["omitted_items"] = True
            continue
        if len(input_builder._serialize({**result, "items": [*result["items"], item]})) <= budget:
            result["items"].append(item)
        else:
            result["omitted_items"] = True
    return result


@pytest.mark.parametrize("budget", [0, 150, 350, 500, 900, 1200, 3000, 8192])
@pytest.mark.parametrize("omit_first", [False, True])
def test_hint_accounting_matches_full_json_including_omitted_flag_transition(budget, omit_first):
    raw = " ".join(["%EA%B0%80", "%00%22%5C", "%26lt%3B", "%252F"] * 4)
    decoding = payload_decoding.decode_payload(raw)
    spans = [(decoding["items"][0]["end"], len(raw))] if omit_first else [(0, len(raw))]
    assert input_builder._decoding_hints(decoding, raw, spans, budget) == _legacy_decoding_hints(
        decoding, raw, spans, budget,
    )


def _legacy_reason(resolver, field, excerpt):
    if not excerpt.strip():
        return "empty_excerpt"
    sources = resolver.sources(field)
    if not sources:
        return "source_not_found"
    if not any(excerpt in source for source in sources):
        return "excerpt_not_in_source"
    submitted = resolver._submitted_sources(field)
    if submitted is not None and not any(excerpt in source for source in submitted):
        return "excerpt_not_submitted"
    return None


@pytest.mark.parametrize("submitted", [None, (), ((0, 20),), ((0, 80), (90, 130)), ((0, 80), (80, 180))])
def test_range_search_matches_slices_without_joining_submitted_boundaries(submitted):
    raw = "POST /?q=한글&q=duplicate HTTP/1.1\r\nX-Test: duplicate\r\nX-Test: second\r\n\r\nbody-only한글duplicate"
    resolver = EvidenceSourceResolver(raw, {"signature": "metadata"}, submitted_payload_spans=submitted)
    for field in ("payload", " event.raw_payload ", "payload.query.q", "payload.query",
                  "payload.headers.X-TEST", "payload.headers", "payload.body", "payload.unknown", "signature"):
        for excerpt in ("duplicate", "한글", "second", "body-only", "metadata", "unknown", "\r\n", " "):
            assert resolver.rejection_reason(field, excerpt) == _legacy_reason(resolver, field, excerpt)


def test_submitted_intersections_never_join_adjacent_ranges_or_other_fields():
    raw = "GET /?q=ABCDEF&q=ABCDEF HTTP/1.1\r\nX-Test: ABCDEF\r\n\r\nABCDEF"
    first = raw.index("ABCDEF")
    second = raw.index("ABCDEF", first + 1)
    resolver = EvidenceSourceResolver(raw, {}, submitted_payload_spans=((first, first + 3), (first + 3, first + 6)))
    assert resolver.rejection_reason("payload.query.q", "ABCDEF") == "excerpt_not_submitted"
    resolver = EvidenceSourceResolver(raw, {}, submitted_payload_spans=((second, second + 6),))
    assert resolver.matches("payload.query.q", "ABCDEF")
    assert resolver.rejection_reason("payload.headers.X-Test", "ABCDEF") == "excerpt_not_submitted"


def test_resolver_ranges_are_snapshotted_and_intersections_reused():
    raw = "GET / HTTP/1.1\r\nX-Test: one\r\nX-Test: two\r\n\r\nbody"
    parsed = parse_http_payload(raw)
    resolver = EvidenceSourceResolver(raw, {}, parsed, submitted_payload_spans=((0, len(raw)),))
    before = resolver._submitted_ranges("payload.headers.X-Test")
    parsed["raw_source_spans"]["headers.x-test"].clear()
    assert resolver.matches("payload.headers.X-Test", "two")
    assert resolver._submitted_ranges("payload.headers.X-Test") is before
    assert isinstance(resolver._payload_ranges("headers.x-test"), tuple)


@pytest.mark.parametrize("budget", [0, 60, 150, 300, 500, 1024, 8192])
def test_candidate_accounting_preserves_limits_ids_and_escaped_text(budget, monkeypatch):
    raw = 'POST /?q=%EA%B0%80 HTTP/1.1\r\nX-Test: \\\"\r\n\r\n한글\t\x00' * 4
    parsed = parse_http_payload(raw)
    observed = []
    original_size = evidence_candidates._size

    def size(value):
        observed.append(value)
        return original_size(value)

    monkeypatch.setattr(evidence_candidates, "_size", size)
    catalog = evidence_candidates.build_candidates(raw, {"signature": "한글\t"}, parsed, ((0, len(raw)),), None, budget)
    assert [item["source_id"] for item in catalog["items"]] == [f"c{i + 1}" for i in range(len(catalog["items"]))]
    assert not catalog["items"] or original_size(catalog) <= budget
    # The initial envelope and, when nonempty, final verification only.
    assert sum("items" in value for value in observed) == (2 if catalog["items"] else 1)


@pytest.mark.parametrize("value,expected", [
    ("ordinary한글" * 1000, False), (r"\u003c", True), (r"\\u003c", False),
    (r"\\\u003c", True), (r"\x3c", True), (r"\U0000003c", False),
])
def test_unicode_marker_fast_negative_preserves_escape_pairing(value, expected):
    assert payload_decoding._unicode_marker(value) is expected


def test_decoder_accounting_keeps_default_ascii_and_space_size_bound(monkeypatch):
    sizes = []
    original_size = payload_decoding._serialized_size

    def size(value):
        sizes.append((len(value.get("items", [])), original_size(value)))
        return original_size(value)

    monkeypatch.setattr(payload_decoding, "_serialized_size", size)
    raw = " ".join(["%EA%B0%80" * 100] * 25)
    result = payload_decoding.decode_payload(raw)
    assert "serialized_output_limit_reached" in result["warnings"]
    assert original_size(result) == len(json.dumps(result, ensure_ascii=True).encode("ascii"))
    assert original_size(result) <= payload_decoding.MAX_SERIALIZED_BYTES
    assert sum(count > 0 for count, _ in sizes) == 1


def test_unmarked_base64_is_still_examined_without_unicode_markers():
    raw = "PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg=="
    result = payload_decoding.decode_payload(raw)
    assert result["items"][0]["decoded"] == "<script>alert(1)</script>"


def test_local_benchmark_emits_json_counts_without_payload(capsys):
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_agent_local.py"
    main = runpy.run_path(str(script))["main"]
    assert main(["--repetitions", "1", "--sizes", "1024"]) == 0
    stdout = capsys.readouterr().out
    result = json.loads(stdout)
    assert result["synthetic_only"] is True
    assert result["repetitions"] == 1
    case = result["cases"][0]
    assert case["payload_chars"] == 1024
    assert case["resolver_checks_per_batch"] == 200
    assert set(case["durations_ms"]) == {"parser", "decoder", "payload_fit", "candidate_catalog", "resolver_batch"}
    assert all(len(value) == 64 for value in case["checksums_sha256"].values())
    assert "synthetic.invalid" not in stdout
    assert "SyntheticMarker" not in stdout


@pytest.mark.parametrize("arguments", [
    ["--repetitions", "0"], ["--repetitions", "21"],
    ["--sizes", "0"], ["--sizes", "2097153"],
])
def test_local_benchmark_bounds_work(arguments):
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_agent_local.py"
    main = runpy.run_path(str(script))["main"]
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
