"""Bounded prior interpretations for the next existing structured retry only.

This is untrusted model data, not an instruction or an accepted result. Keep
complete explanations and all selected counterevidence, or omit the context
entirely. Never use the display-truncated rejected-evidence snapshot here.
"""
from copy import deepcopy
import json

from pydantic import ValidationError

from .contracts_v234 import FlatEvidenceInterpretation, verified_conflict_paths

VERSION = "evidence-conflict-correction-v1"
ENVELOPE = "evidence_conflict_correction"
MAX_CONTEXT_CHARS = 16000
MAX_CONTEXT_BYTES = 32768


def conflict_draft(document, issues, *, selected_source_ids):
    """Accept a complete, typed evidence graph with a verified conflict only."""
    if type(document) is not dict or not selected_source_ids:
        return None
    values = document.get("evidence")
    verdict = document.get("verdict")
    if (type(values) is not list or not 1 <= len(values) <= 5
            or type(verdict) is not str
            or verdict not in {"true_positive", "false_positive", "inconclusive"}):
        return None
    try:
        evidence = [FlatEvidenceInterpretation.model_validate(item).model_dump(mode="json")
                    for item in values]
    except (ValidationError, ValueError, TypeError):
        return None
    identifiers = {item["evidence_id"] for item in evidence}
    if (len(identifiers) != len(evidence)
            or any(item["source_id"] not in selected_source_ids
                   or not set(item["related_evidence_ids"]).issubset(identifiers)
                   for item in evidence)):
        return None
    paths = next((verified_conflict_paths(issue.get("conflict_paths"), evidence,
                         selected_source_ids=selected_source_ids)
                  for issue in issues if type(issue) is dict
                  and issue.get("type") == "evidence_support_conflict"), [])
    if not paths:
        return None
    return {"version": VERSION, "previous_verdict": verdict,
            "evidence": evidence, "conflict_paths": paths}


def correction_input(user_input, draft, *, previous_attempt, char_budget):
    """Return (actual input, encrypted audit envelope), or unchanged input.

    Budget is the worker's existing input allowance, not an extra allocation.
    The original event, catalog, hints and all evidence are retained. No
    partial repair explanation is sent when there is insufficient headroom.
    """
    if (type(draft) is not dict or type(previous_attempt) is not int
            or not 1 <= previous_attempt <= 3
            or type(char_budget) is not int or char_budget <= 0):
        return user_input, None
    try:
        document = json.loads(user_input)
        if type(document) is not dict or ENVELOPE in document:
            return user_input, None
        envelope = {**deepcopy(draft), "previous_attempt": previous_attempt}
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > MAX_CONTEXT_CHARS or len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
            return user_input, None
        text = json.dumps({**document, ENVELOPE: envelope}, ensure_ascii=False, separators=(",", ":"))
        if len(text) > char_budget:
            return user_input, None
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return user_input, None
    return text, envelope
