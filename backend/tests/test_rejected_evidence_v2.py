"""New encrypted diagnostics do not reinterpret links or expose extra fields."""
import json

import pytest

from app.agent.rejected_evidence_v2 import MAX_SNAPSHOT_BYTES, rejected_evidence_snapshot
from test_contracts_v237 import wire


def test_exact_new_memberships_are_kept_even_when_their_direction_is_invalid():
    rows = wire()["evidence"]
    rows[1]["supports"] = "normal_request"
    result = rejected_evidence_snapshot({"evidence": rows})
    assert result["version"] == "rejected-evidence-v2"
    assert result["evidence"] == [{"position": index, **row} for index, row in enumerate(rows)]
    assert result["truncated"] is False


def test_legacy_graph_and_unknown_private_fields_are_not_copied():
    row = wire()["evidence"][0]
    row.update(related_evidence_ids=["e5"], private_reasoning="PRIVATE", excerpt="PRIVATE")
    result = rejected_evidence_snapshot({"evidence": [row], "private_reasoning": "PRIVATE_ROOT"})
    encoded = json.dumps(result)
    assert "PRIVATE" not in encoded and "related_evidence_ids" not in encoded


@pytest.mark.parametrize("text", ["가" * 5000, "😀" * 5000, "\n" * 5000, "\\" * 5000])
def test_display_snapshot_remains_bounded_without_becoming_model_repair_input(text):
    row = wire()["evidence"][0]
    row["interpretation_ko"] = text
    result = rejected_evidence_snapshot({"evidence": [row] * 8})
    assert result["truncated"] and result["omitted_evidence_count"] == 3
    assert len(result["evidence"]) == 5
    for item in result["evidence"]:
        assert text.startswith(item["interpretation_ko"])
        assert len(item["interpretation_ko"]) <= 2000 and item["interpretation_truncated"]
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= MAX_SNAPSHOT_BYTES


def test_invalid_unicode_nested_values_and_unapproved_ids_are_omitted():
    row = wire()["evidence"][0]
    row.update(interpretation_ko="\ud800", source_id={"nested": "PRIVATE"},
               contributes_to_evidence_ids=["e1", {}, "PRIVATE", "e2", "e3"])
    result = rejected_evidence_snapshot({"evidence": [row]})
    item = result["evidence"][0]
    assert item["contributes_to_evidence_ids"] == ["e1", "e2"]
    assert "interpretation_ko" in item["omitted_fields"] and "source_id" in item["omitted_fields"]
    assert "PRIVATE" not in json.dumps(result)
    json.dumps(result, ensure_ascii=False).encode("utf-8")
