"""Bounded domain-only diagnostics for encrypted admin execution output.

This is not result recovery: no rejected value is validated, repaired, sent to
another model or used for a verdict. Never put these records in telemetry.
"""
import json
import re

VERSION = "rejected-evidence-v1"
MAX_EVIDENCE = 5
MAX_LINKS = 4
MAX_INTERPRETATION_CHARS = 2_000
MAX_INTERPRETATION_JSON_BYTES = 6_000
MAX_SNAPSHOT_BYTES = 32_768
_EVIDENCE_ID = re.compile(r"e[1-5]\Z", re.ASCII)
_SOURCE_ID = re.compile(r"c[1-9][0-9]{0,2}\Z", re.ASCII)
_SUPPORTS = frozenset({"attack_request", "normal_request", "context"})


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8", errors="strict"))


def _interpretation(value):
    """Preserve a literal prefix, marking any character/encoded-byte limit."""
    if type(value) is not str:
        return None, False
    text = value[:MAX_INTERPRETATION_CHARS]
    try:
        if _json_size(text) > MAX_INTERPRETATION_JSON_BYTES:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if _json_size(text[:middle]) <= MAX_INTERPRETATION_JSON_BYTES:
                    low = middle
                else:
                    high = middle - 1
            text = text[:low]
    except UnicodeError:
        return None, False
    return text, len(text) < len(value)


def rejected_evidence_snapshot(document):
    """Select only five known fields from an already normalized JSON object.

    Invalid/nested values and unknown fields are not stringified or copied.
    Position is the original zero-based position, not a fabricated evidence ID.
    This collector deliberately cannot supply input to the result contract.
    """
    if type(document) is not dict or type(document.get("evidence")) is not list:
        return None
    original = document["evidence"]
    if not original:
        return None
    snapshot = {"version": VERSION, "evidence": [], "truncated": False,
                "omitted_evidence_count": max(0, len(original) - MAX_EVIDENCE)}
    for position, item in enumerate(original[:MAX_EVIDENCE]):
        if type(item) is not dict:
            snapshot["omitted_evidence_count"] += 1
            continue
        selected, omitted = {"position": position}, []
        for name, pattern in (("evidence_id", _EVIDENCE_ID), ("source_id", _SOURCE_ID)):
            value = item.get(name)
            if type(value) is str and pattern.fullmatch(value):
                selected[name] = value
            else:
                omitted.append(name)
        support = item.get("supports")
        if type(support) is str and support in _SUPPORTS:
            selected["supports"] = support
        else:
            omitted.append("supports")
        text, shortened = _interpretation(item.get("interpretation_ko"))
        if text is not None:
            selected["interpretation_ko"] = text
            if shortened:
                selected["interpretation_truncated"] = True
        else:
            omitted.append("interpretation_ko")
        links = item.get("related_evidence_ids")
        if type(links) is list:
            selected["related_evidence_ids"] = [key for key in links[:MAX_LINKS]
                if type(key) is str and _EVIDENCE_ID.fullmatch(key)]
            if len(selected["related_evidence_ids"]) != len(links):
                omitted.append("related_evidence_ids")
        else:
            omitted.append("related_evidence_ids")
        if omitted:
            selected["omitted_fields"] = omitted
        snapshot["truncated"] |= bool(omitted or shortened)
        snapshot["evidence"].append(selected)
    snapshot["truncated"] |= snapshot["omitted_evidence_count"] > 0
    # The per-text bound normally leaves room for all five rows. Keep this
    # final hard limit as a guard if future domain fields are added here.
    while _json_size(snapshot) > MAX_SNAPSHOT_BYTES and snapshot["evidence"]:
        snapshot["evidence"].pop()
        snapshot["omitted_evidence_count"] += 1
        snapshot["truncated"] = True
    return snapshot
