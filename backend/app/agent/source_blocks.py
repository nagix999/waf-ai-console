"""Versioned, single-copy source blocks for v2.20 inputs only.

The catalog is an exact raw-source index, not an attack detector. Every retained
payload character appears in exactly one block. Offsets use Python characters,
not UTF-8 bytes, and never bridge an omitted part of the capture.
"""

import hashlib
import json
import re
from bisect import bisect_right
from itertools import islice
from pathlib import Path
from typing import Any


SOURCE_BLOCKS_VERSION = "evidence-selection-v3"
INPUT_POLICY_VERSION = "agent-input-v2"
FIXED_RULES_VERSION = "waf-system-v2.20"
MAX_BLOCK_CHARS = 300
MAX_SOURCE_ITEMS = 999
MAX_METADATA_ITEMS = 100
MAX_METADATA_DEPTH = 8
IDENTIFIER_FIELDS = frozenset({"event_id", "source_system", "company_name", "waf_vendor"})


def source_blocks_artifact() -> dict[str, Any]:
    """Safe content identifiers for an encrypted execution/configuration pin."""
    files = {}
    for name in ("source_blocks.py", "input_builder.py"):
        files[name] = hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    serialized = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return {
        "version": INPUT_POLICY_VERSION,
        "source_catalog_version": SOURCE_BLOCKS_VERSION,
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "files": files,
    }


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def _metadata_scalars(value: Any, path: str = "", depth: int = 0):
    if depth > MAX_METADATA_DEPTH:
        return
    if isinstance(value, dict):
        for key, child in islice(value.items(), MAX_METADATA_ITEMS):
            if not path and key in IDENTIFIER_FIELDS | {"payload"}:
                continue
            if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
                yield from _metadata_scalars(child, f"{path}.{key}" if path else key, depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(islice(value, MAX_METADATA_ITEMS)):
            yield from _metadata_scalars(child, f"{path}.{index}", depth + 1)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        yield path, str(value)


def metadata_candidates(event: dict, original_event: dict, parsed: dict, raw: str,
                        budget: int) -> tuple[list[dict], bool]:
    """Select only submitted, original metadata scalars, with bounded traversal."""
    from .evidence import EvidenceSourceResolver

    resolver = EvidenceSourceResolver(raw, original_event, parsed, submitted_event=event)
    items, omitted = [], False
    for index, (field, value) in enumerate(_metadata_scalars(event)):
        if index >= MAX_METADATA_ITEMS:
            omitted = True
            break
        excerpt = value[:MAX_BLOCK_CHARS]
        if not excerpt.strip():
            continue
        if len(field) > 120 or not resolver.matches(field, excerpt):
            omitted = True
            continue
        # Reserve the largest possible source id so fitting does not depend on
        # how many payload blocks are later selected.
        item = {"source_id": "c999", "field": field, "excerpt": excerpt,
                "start": None, "end": None}
        if _size([*items, item]) > budget:
            omitted = True
            continue
        items.append(item)
    return items, omitted


def _parser_boundaries(raw: str, parsed: dict | None) -> tuple[int, ...]:
    """Use only already recognized, exact original request/body boundaries."""
    if not isinstance(parsed, dict) or parsed.get("parse_status") == "failed":
        return ()
    ranges = parsed.get("raw_source_spans")
    if not isinstance(ranges, dict):
        return ()
    boundaries = set()
    for field in ("request_line", "body"):
        value, locations = parsed.get(field), ranges.get(field)
        if not isinstance(value, str) or not isinstance(locations, (list, tuple)):
            continue
        for pair in islice(locations, 2):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            start, end = pair
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start <= end <= len(raw) or raw[start:end] != value):
                continue
            boundary = end if field == "request_line" else start
            if 0 < boundary < len(raw) and raw[boundary - 1:boundary + 1] != "\r\n":
                boundaries.add(boundary)
    return tuple(sorted(boundaries))


def payload_blocks(raw: str, spans: list[tuple[int, int]], *, limit: int = MAX_SOURCE_ITEMS,
                   structural_boundaries: tuple[int, ...] = ()):
    """Yield adjacent blocks, preferring nearby line boundaries without gaps."""
    count, previous_end = 0, -1
    for start, end in spans:
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= len(raw) or start < previous_end):
            raise ValueError("source_blocks_invalid")
        previous_end = end
        cursor = start
        while cursor < end:
            if count >= limit:
                raise ValueError("source_blocks_limit_exceeded")
            boundary_index = bisect_right(structural_boundaries, cursor)
            region_end = min(end, structural_boundaries[boundary_index]) if boundary_index < len(structural_boundaries) else end
            stop = min(cursor + MAX_BLOCK_CHARS, region_end)
            if stop < region_end:
                # Only consider the latter half, otherwise a stream of short
                # lines would waste both IDs and the serialized input budget.
                boundary = max(raw.rfind("\n", cursor + MAX_BLOCK_CHARS // 2, stop),
                               raw.rfind("\r", cursor + MAX_BLOCK_CHARS // 2, stop))
                if boundary >= cursor:
                    stop = boundary + 1
                if raw[stop - 1:stop + 1] == "\r\n":
                    # Keep CRLF atomic when it would exceed the block limit.
                    stop = stop + 1 if stop - cursor < MAX_BLOCK_CHARS else stop - 1
            yield {"source_id": f"c{count + 1}", "field": "payload",
                   "excerpt": raw[cursor:stop], "start": cursor, "end": stop}
            cursor = stop
            count += 1


def build_source_catalog(raw: str, spans: list[tuple[int, int]], metadata: list[dict],
                         metadata_omitted: bool, *, decoding: dict | None = None,
                         parsed: dict | None = None) -> dict:
    items = list(payload_blocks(raw, spans, limit=MAX_SOURCE_ITEMS - len(metadata),
                                structural_boundaries=_parser_boundaries(raw, parsed)))
    if decoding is not None:
        # These are indices in the *submitted* (already filtered) hints, not
        # indices in the decoder's original output. Link every intersecting
        # block, including an origin longer than one 300-character source.
        valid_hints = []
        for index, hint in enumerate(decoding.get("items", [])):
            start, end = hint.get("start"), hint.get("end")
            if (type(start) is int and type(end) is int
                    and 0 <= start < end <= len(raw)
                    and raw[start:end] == hint.get("original")
                    and any(a <= start < end <= b for a, b in spans)):
                valid_hints.append((index, start, end))
        for item in items:
            indexes = [index for index, start, end in valid_hints
                       if start < item["end"] and item["start"] < end]
            if indexes:
                item["decoded_hint_indexes"] = indexes
    for item in metadata:
        items.append({**item, "source_id": f"c{len(items) + 1}"})
    return {"version": SOURCE_BLOCKS_VERSION, "items": items, "omitted": metadata_omitted,
            "omitted_scope": "event_metadata_only"}
