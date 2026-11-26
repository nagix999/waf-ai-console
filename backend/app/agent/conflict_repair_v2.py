"""Whole bounded prior contribution drafts for an existing structured retry.

Domain-invalid membership and direction must remain visible to the model as
untrusted data. This collector checks shape and selected source identities,
not semantic validity, and never substitutes a display-truncated snapshot.
"""
from copy import deepcopy
import json
import re

VERSION = "evidence-contribution-correction-v1"
ENVELOPE = "evidence_contribution_correction"
MAX_CONTEXT_CHARS = 16000
MAX_CONTEXT_BYTES = 32768
REPAIR_CODES = frozenset({
    "evidence_contribution_support_invalid", "evidence_contribution_target_not_root",
    "evidence_link_unknown", "evidence_link_self", "evidence_link_repeated",
})
_IDS = frozenset({"e1", "e2", "e3", "e4", "e5"})
_SOURCE = re.compile(r"c[1-9][0-9]{0,2}\Z", re.ASCII)
_FIELDS = frozenset({"evidence_id", "source_id", "interpretation_ko", "supports", "contributes_to_evidence_ids"})
_ROOT_FIELDS = frozenset({"evidence", "signature_assessment", "capture_assessment",
    "interpretation_conflict", "summary_ko", "verdict", "confidence_score",
    "threat_analysis", "decision_issue"})


def conflict_draft(document, issues, *, selected_source_ids):
    """Allow rejected classifications/edges, not malformed or ambiguous data."""
    if (type(document) is not dict or set(document) != _ROOT_FIELDS or not selected_source_ids
            or not any(type(issue) is dict and issue.get("type") in REPAIR_CODES for issue in issues)):
        return None
    values, verdict = document.get("evidence"), document.get("verdict")
    if (type(values) is not list or not 1 <= len(values) <= 5
            or type(verdict) is not str
            or verdict not in {"true_positive", "false_positive", "inconclusive"}):
        return None
    evidence, seen = [], set()
    for item in values:
        if type(item) is not dict or set(item) != _FIELDS:
            return None
        key, source, text, support, targets = (item[name] for name in
            ("evidence_id", "source_id", "interpretation_ko", "supports", "contributes_to_evidence_ids"))
        if (type(key) is not str or key not in _IDS or key in seen
                or type(source) is not str or _SOURCE.fullmatch(source) is None or source not in selected_source_ids
                or type(text) is not str or not 1 <= len(text) <= 2000 or not text.strip()
                or type(support) is not str or support not in {"attack_request", "normal_request", "context"}
                or type(targets) is not list or len(targets) > 4
                or any(type(target) is not str or target not in _IDS for target in targets)):
            return None
        seen.add(key)
        evidence.append(deepcopy(item))
    return {"version": VERSION, "previous_verdict": verdict, "evidence": evidence}


def correction_input(user_input, draft, *, previous_attempt, char_budget):
    """Keep all original input and all prior interpretations, or add nothing."""
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
