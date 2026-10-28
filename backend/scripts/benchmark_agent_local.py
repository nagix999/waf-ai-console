"""Bounded synthetic-only preprocessing benchmark; no model, network or database.

Run with the project's existing Python dependencies installed::

    cd backend
    python scripts/benchmark_agent_local.py --repetitions 5 --sizes 1024 262144 2097152

For before/after comparisons, run the SAME script in two isolated source trees
with the same Python/dependencies and host conditions. If the older source tree
has no benchmark, place this script in its backend/scripts directory first. Do
not reset the active worktree or use a live service/database. Compare checksums
before interpreting timing changes; a new contract/version can change them.

All inputs are generated here, never read from files or environment variables.
Sizes count Python characters, not UTF-8 bytes or model tokens. Stdout is one
JSON document containing only synthetic counts, versions, times and checksums.
Generation and hashing are outside the timed phases. The resolver phase repeats
one source check 200 times on the same execution-scoped resolver. Parser, decoder,
payload fitting and candidate catalog are measured separately; their medians
must not be summed and presented as end-to-end service latency. Queueing, DB,
encryption, HTTP, concurrency and LLM processing are not measured. These local
microbenchmarks do not predict production latency or model quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Callable, TypeVar


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.evidence import EvidenceSourceResolver
from app.agent.evidence_candidates import VERSION as CANDIDATE_VERSION, build_candidates
from app.agent.input_builder import _bounded_payload
from app.services.http_parser import parse_http_payload
from app.services.payload_decoding import DECODER_VERSION, decode_payload


T = TypeVar("T")
MAX_PAYLOAD_CHARS = 2_097_152
RESOLVER_CHECKS = 200
SIGNATURE = "SyntheticMarker"
FIT_BUDGET = 6_000
CATALOG_BUDGET = 8_192


def _positive_bounded(maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("must be an integer") from None
        if not 1 <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between 1 and {maximum}")
        return number

    return parse


def _synthetic_payload(size: int) -> str:
    header = (
        "POST /?q=%3Cscript%3E&q=normal HTTP/1.1\r\n"
        "Host: synthetic.invalid\r\nX-Test: first\r\nX-Test: second\r\n\r\n"
    )
    fragment = '한글\\"\tABC '
    body_chars = max(0, size // 2 - len(header))
    body = (fragment * (body_chars // len(fragment) + 1))[:body_chars]
    return (header + body + SIGNATURE + "Z" * size)[:size]


def _checksum(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("ascii")).hexdigest()


def _measure(function: Callable[[], T], repetitions: int) -> tuple[T, dict[str, float]]:
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        result = function()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return result, {
        "median": round(statistics.median(samples), 6),
        "min": round(min(samples), 6),
        "max": round(max(samples), 6),
    }


def benchmark_case(size: int, repetitions: int) -> dict:
    raw = _synthetic_payload(size)
    event = {"event_id": "synthetic-benchmark", "signature": SIGNATURE}
    parsed, parser_times = _measure(lambda: parse_http_payload(raw), repetitions)
    decoded, decoder_times = _measure(lambda: decode_payload(raw), repetitions)
    fitted, fitting_times = _measure(lambda: _bounded_payload(raw, FIT_BUDGET, SIGNATURE), repetitions)
    submitted, truncated, spans = fitted
    catalog, catalog_times = _measure(
        lambda: build_candidates(raw, event, parsed, spans, decoded, CATALOG_BUDGET), repetitions,
    )
    resolver = EvidenceSourceResolver(raw, event, parsed, submitted_payload_spans=tuple(spans))

    def resolve_batch() -> list[str | None]:
        return [resolver.rejection_reason("payload.body", SIGNATURE) for _ in range(RESOLVER_CHECKS)]

    resolved, resolver_times = _measure(resolve_batch, repetitions)
    return {
        "payload_chars": len(raw),
        "payload_utf8_bytes": len(raw.encode("utf-8")),
        "submitted_payload_chars": len(submitted),
        "input_truncated": truncated,
        "retained_span_count": len(spans),
        "decoder_item_count": len(decoded["items"]),
        "decoder_warning_count": len(decoded["warnings"]),
        "decoder_scan_truncated": decoded["scan_truncated"],
        "catalog_item_count": len(catalog["items"]),
        "catalog_omitted": catalog["omitted"],
        "resolver_checks_per_batch": RESOLVER_CHECKS,
        "resolver_accepted_count": sum(reason is None for reason in resolved),
        "durations_ms": {
            "parser": parser_times,
            "decoder": decoder_times,
            "payload_fit": fitting_times,
            "candidate_catalog": catalog_times,
            "resolver_batch": resolver_times,
        },
        "checksums_sha256": {
            "synthetic_payload": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "parsed": _checksum(parsed),
            "decoded": _checksum(decoded),
            "payload_fit": _checksum(fitted),
            "catalog": _checksum(catalog),
            "resolver_batch": _checksum(resolved),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repetitions", type=_positive_bounded(20), default=5)
    parser.add_argument("--sizes", type=_positive_bounded(MAX_PAYLOAD_CHARS), nargs="+", default=[1024, 262144, 2097152])
    args = parser.parse_args(argv)
    if len(args.sizes) > 10:
        parser.error("at most 10 sizes are allowed")
    output = {
        "benchmark_version": "agent-local-v1",
        "synthetic_only": True,
        "repetitions": args.repetitions,
        "decoder_version": DECODER_VERSION,
        "candidate_version": CANDIDATE_VERSION,
        "payload_fit_budget_chars": FIT_BUDGET,
        "candidate_budget_chars": CATALOG_BUDGET,
        "cases": [benchmark_case(size, args.repetitions) for size in args.sizes],
    }
    print(json.dumps(output, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
