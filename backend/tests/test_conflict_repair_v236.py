"""Bounded, complete, source-verified correction context only."""
from copy import deepcopy
import json

import pytest

from app.agent.conflict_repair_v1 import conflict_draft, correction_input, ENVELOPE
from test_contracts_v234 import conflict, diagnostic
from test_evidence_interpretations_v227 import request_input


def draft(value=None):
    value = conflict() if value is None else value
    paths = diagnostic(value)["ctx"]["conflict_paths"]
    return conflict_draft(value, [{"type": "evidence_support_conflict", "conflict_paths": paths}],
        selected_source_ids={"c1", "c2", "c3", "c4", "c5", "c6"})


def test_full_envelope_fits_exact_budget_without_mutating_any_original():
    value = draft()
    original = deepcopy(value)
    text, envelope = correction_input(request_input(), value, previous_attempt=1, char_budget=20000)
    again, same = correction_input(request_input(), value, previous_attempt=1, char_budget=len(text))
    assert (again, same) == (text, envelope)
    fallback, absent = correction_input(request_input(), value, previous_attempt=1, char_budget=len(text)-1)
    assert fallback == request_input() and absent is None
    assert value == original
    assert json.loads(text).pop(ENVELOPE) == envelope


def test_oversized_complete_context_is_not_truncated_even_with_large_input_budget():
    value = draft()
    value["evidence"] *= 500
    text, envelope = correction_input(request_input(), value, previous_attempt=1, char_budget=1000000)
    assert text == request_input() and envelope is None


def test_existing_untrusted_envelope_is_not_overwritten_or_merged():
    original = json.dumps({**json.loads(request_input()), ENVELOPE: {"untrusted": "synthetic"}})
    text, envelope = correction_input(original, draft(), previous_attempt=1, char_budget=20000)
    assert text == original and envelope is None


def test_forged_or_mismatched_conflict_path_cannot_select_previous_interpretations():
    value = conflict()
    paths = diagnostic(value)["ctx"]["conflict_paths"]
    paths[1][-1]["source_id"] = "c2"
    assert conflict_draft(value, [{"type": "evidence_support_conflict", "conflict_paths": paths}],
        selected_source_ids={"c1", "c2"}) is None


@pytest.mark.parametrize("attempt", [0, 4, True, "1"])
def test_only_actual_existing_next_retry_is_eligible(attempt):
    assert correction_input(request_input(), draft(), previous_attempt=attempt,
        char_budget=20000) == (request_input(), None)
