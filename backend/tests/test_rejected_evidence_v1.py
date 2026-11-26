"""Synthetic bounded snapshots; never copy provider/raw/private fields."""
import json

import pytest

from app.agent.rejected_evidence_v1 import (
    MAX_SNAPSHOT_BYTES, rejected_evidence_snapshot,
)


def item(**changes):
    return {"evidence_id": "e1", "source_id": "c1", "supports": "attack_request",
            "interpretation_ko": "합성 요청의 동작과 보안 의미를 설명합니다.",
            "related_evidence_ids": ["e2"], **changes}


def test_preserves_literal_interpretations_ids_direction_and_links_without_repair():
    first = item()
    second = item(evidence_id="e2", supports="normal_request", related_evidence_ids=["e1"])
    value = rejected_evidence_snapshot({"evidence": [first, second]})
    assert value["evidence"] == [{"position": index, **row} for index, row in enumerate([first, second])]
    assert not value["truncated"] and value["omitted_evidence_count"] == 0


def test_unknown_root_and_evidence_fields_are_never_copied_or_stringified():
    value = rejected_evidence_snapshot({"private_reasoning": "PRIVATE_ROOT",
        "evidence": [item(private_reasoning="PRIVATE_ITEM", excerpt="PRIVATE_SOURCE",
                          provider_response={"secret": "PRIVATE_PROVIDER"})]})
    assert "PRIVATE_" not in json.dumps(value)
    assert set(value["evidence"][0]) == {"position", "evidence_id", "source_id", "supports",
                                        "interpretation_ko", "related_evidence_ids"}


@pytest.mark.parametrize("text", ["가" * 5_000, "😀" * 5_000, "\n" * 5_000, "\\" * 5_000])
def test_limits_literal_text_by_characters_and_encoded_json_bytes_with_explicit_flags(text):
    value = rejected_evidence_snapshot({"evidence": [item(interpretation_ko=text)] * 8})
    assert value["truncated"] and value["omitted_evidence_count"] == 3
    assert len(value["evidence"]) == 5
    for row in value["evidence"]:
        assert len(row["interpretation_ko"]) <= 2_000
        assert text.startswith(row["interpretation_ko"])
        assert row["interpretation_truncated"] is True
    assert len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) <= MAX_SNAPSHOT_BYTES


def test_bad_nested_and_missing_values_are_omitted_not_guessed():
    value = rejected_evidence_snapshot({"evidence": [None, item(evidence_id="e1\nPRIVATE_ID",
        source_id={"bad": "PRIVATE_SOURCE"}, supports=["attack_request"],
        interpretation_ko={"nested": "PRIVATE_TEXT"}, related_evidence_ids=["e1", "PRIVATE_LINK", {}, "e2", "e3"])]})
    assert value["truncated"] and value["omitted_evidence_count"] == 1
    row = value["evidence"][0]
    assert row["position"] == 1 and row["related_evidence_ids"] == ["e1", "e2"]
    assert set(row["omitted_fields"]) == {"evidence_id", "source_id", "supports", "interpretation_ko", "related_evidence_ids"}
    assert "PRIVATE_" not in json.dumps(value)


def test_unpaired_unicode_cannot_break_the_execution_output_encryption():
    value = rejected_evidence_snapshot({"evidence": [item(interpretation_ko="\ud800")]})
    assert "interpretation_ko" not in value["evidence"][0]
    assert "interpretation_ko" in value["evidence"][0]["omitted_fields"]
    json.dumps(value, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("document", [None, [], "PRIVATE", {}, {"evidence": {}}, {"evidence": []}])
def test_missing_evidence_does_not_invent_a_diagnostic(document):
    assert rejected_evidence_snapshot(document) is None
