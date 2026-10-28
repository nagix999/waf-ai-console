"""Resolve evidence against its claimed raw source, without decoding payloads."""

import re
from collections.abc import Mapping
from ..services.http_parser import parse_http_payload


class EvidenceSourceResolver:
    """Validate location and exact text only; this does not validate its meaning."""

    def __init__(
        self, raw_payload: str, event: Mapping[str, object], parsed: dict | None = None,
        *, submitted_payload_spans: tuple[tuple[int, int], ...] | None = None,
        submitted_event: Mapping[str, object] | None = None,
        decoding_hints: dict | None = None,
    ) -> None:
        self.raw_payload = raw_payload
        self.event = event
        # Source ranges are a per-execution immutable snapshot, never a shared
        # raw-text cache. Preserve individual boundaries; do not merge ranges.
        self.spans = {field: tuple((start, end) for start, end in ranges)
                      for field, ranges in (parsed if parsed is not None else parse_http_payload(raw_payload))["raw_source_spans"].items()}
        self.submitted_spans = None if submitted_payload_spans is None else tuple(submitted_payload_spans)
        self._submitted_range_cache: dict[str, tuple[tuple[int, int], ...]] = {}
        self.submitted_event = submitted_event
        self.decoding_hints = decoding_hints or {}

    def matches(self, field: str, excerpt: str) -> bool:
        return self.rejection_reason(field, excerpt) is None

    def rejection_reason(self, field: str, excerpt: str) -> str | None:
        """Only fixed codes leave this function; never raw model/event text."""
        if not excerpt.strip():
            return "empty_excerpt"
        normalized = field.strip().removeprefix("event.")
        if normalized in {"payload", "raw_payload"} or normalized.startswith("payload."):
            ranges = self._source_ranges(normalized)
            if not ranges:
                return "source_not_found"
            if not any(self.raw_payload.find(excerpt, start, end) >= 0 for start, end in ranges):
                return "excerpt_not_in_source"
            submitted = self._submitted_ranges(normalized)
            if submitted is not None and not any(
                self.raw_payload.find(excerpt, start, end) >= 0 for start, end in submitted
            ):
                return "excerpt_not_submitted"
            return None
        sources = self.sources(field)
        if not sources:
            return "source_not_found"
        if not any(excerpt in source for source in sources):
            return "excerpt_not_in_source"
        submitted = self._submitted_sources(field)
        if submitted is not None and not any(excerpt in source for source in submitted):
            return "excerpt_not_submitted"
        return None

    def sources(self, field: str) -> list[str]:
        field = field.strip().removeprefix("event.")
        if field in {"payload", "raw_payload"}:
            return [self.raw_payload]
        if field.startswith("payload."):
            return self._payload_sources(field[8:])
        return self._event_sources(self.event, field)

    @staticmethod
    def _event_sources(event: Mapping[str, object], field: str) -> list[str]:
        value: object = event
        # Both extra_fields.items.0.value and items[0].value identify the same
        # concrete scalar. Container fields never search all their descendants.
        path = re.sub(r"\[(0|[1-9][0-9]*)\]", r".\1", field)
        for part in path.split("."):
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            elif isinstance(value, (list, tuple)) and part.isascii() and part.isdecimal():
                index = int(part)
                if index >= len(value):
                    return []
                value = value[index]
            else:
                return []
        if isinstance(value, bool) or value is None:
            return []
        if isinstance(value, (str, int, float)):
            return [str(value)]
        return []

    def _submitted_sources(self, field: str) -> list[str] | None:
        field = field.strip().removeprefix("event.")
        if field in {"payload", "raw_payload"} or field.startswith("payload."):
            ranges = self._submitted_ranges(field)
            return None if ranges is None else [self.raw_payload[start:end] for start, end in ranges]
        if self.submitted_event is None:
            return None
        return self._event_sources(self.submitted_event, field)

    def _source_ranges(self, field: str) -> tuple[tuple[int, int], ...]:
        if field in {"payload", "raw_payload"}:
            return ((0, len(self.raw_payload)),)
        return self._payload_ranges(field[8:])

    def _submitted_ranges(self, field: str) -> tuple[tuple[int, int], ...] | None:
        if self.submitted_spans is None:
            return None
        if field not in self._submitted_range_cache:
            self._submitted_range_cache[field] = tuple(
                (max(start, a), min(end, b))
                for start, end in self._source_ranges(field) for a, b in self.submitted_spans
                if max(start, a) < min(end, b)
            )
        return self._submitted_range_cache[field]

    def resolve_decoder_original(self, field: str, excerpt: str) -> dict | None:
        """Resolve only a submitted tool artifact's exact *original* occurrence.

        No decoded values, general field guessing, whitespace normalization or
        search of other fields. The caller records the mapping in encrypted I/O.
        """
        match = re.fullmatch(r"decoded_payload_hints\.items(?:\.(0|[1-9][0-9]*)|\[(0|[1-9][0-9]*)\])\.original", field.strip())
        if not match or not excerpt.strip() or self.submitted_spans is None:
            return None
        if self.decoding_hints.get("raw_source_field") != "payload":
            return None
        index = int(match[1] or match[2])
        items = self.decoding_hints.get("items", [])
        if index >= len(items):
            return None
        item = items[index]
        start, end, original = item.get("start"), item.get("end"), item.get("original")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(self.raw_payload)
                or not isinstance(original, str) or excerpt not in original
                or self.raw_payload[start:end] != original
                or not any(a <= start < end <= b for a, b in self.submitted_spans)):
            return None
        return {"field": "payload", "start": start, "end": end, "hint_index": index}

    def _payload_sources(self, field: str) -> list[str]:
        return [self.raw_payload[start:end] for start, end in self._payload_ranges(field)]

    def _payload_ranges(self, field: str) -> tuple[tuple[int, int], ...]:
        if field.startswith("headers."):
            field = field.lower()
        return self.spans.get(field, ())
