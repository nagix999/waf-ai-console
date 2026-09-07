"""Remove exact repetition after source grounding, not semantically alike facts.

This helper does not validate evidence and never changes its source, excerpt or
interpretation. Its comparison aliases mirror EvidenceSourceResolver; bare event
fields must not be confused with HTTP payload subfields. Different interpretations
of one source/excerpt remain separate evidence entries, including opposing ones.
"""

import re

from .contracts import EvidenceItem


MAX_FINAL_EVIDENCE = 5
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_ARRAY_INDEX = re.compile(r"\[(0|[1-9][0-9]*)\]")


def canonical_evidence_field(field: str) -> str:
    """Return only aliases guaranteed equivalent by the source resolver.

    This key is for comparison, never written back to an evidence item. Do not
    normalize query names, excerpt whitespace, URL encoding, or bare metadata
    fields such as 'query' and 'body'. HTTP query names can literally contain
    brackets, so array index aliases only apply to non-payload event paths.
    """
    canonical = field.strip().removeprefix("event.")
    if canonical == "raw_payload":
        return "payload"
    if canonical == "payload.request_target":
        return "payload.uri"
    if canonical.startswith("payload.headers."):
        name = canonical[len("payload.headers."):]
        if _HEADER_NAME.fullmatch(name):
            return "payload.headers." + name.lower()
        return canonical
    if canonical.startswith("payload."):
        return canonical
    return _ARRAY_INDEX.sub(r".\1", canonical)


def deduplicate_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Keep the first five distinct, already grounded exact evidence triples.

    Callers pass Primary first, then Verifier, retaining the existing selection
    order and v2 maximum. No similarity/substring matching, text joining or text
    normalization is performed. Copies protect the retained role snapshots.
    """
    retained: list[EvidenceItem] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (canonical_evidence_field(item.field), item.excerpt, item.interpretation_ko)
        if key in seen:
            continue
        seen.add(key)
        retained.append(item.model_copy(deep=True))
        if len(retained) == MAX_FINAL_EVIDENCE:
            break
    return retained
