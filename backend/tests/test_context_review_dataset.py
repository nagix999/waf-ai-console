"""Offline data/protocol regressions, not LLM verdict-accuracy measurements."""
from collections import Counter
import importlib.util
import ipaddress
import json
from pathlib import Path

import pytest

from app.agent.evidence_candidates import resolve_selection
from app.agent.prompts import FIXED_RULES_VERSION
from app.models import AgentRun, Analysis
from app.services.http_parser import parse_http_payload
from app.services.request_integrity import assess_request_integrity
from app.services.uploads import extract_test_upload_row
from app.worker import _event_document
from test_evidence_selection import build, resolver, selected
from test_test_runs import login, upload


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "samples/waf-context-review-v1"
EVENTS = json.loads((DIRECTORY / "context_review_18.json").read_text())
ANSWERS = json.loads((DIRECTORY / "reference/answers_18.json").read_text())
REFERENCE_FIELDS = {"expected_verdict", "rationale_ko", "group", "title_ko", "missing_condition_ko"}


def test_review_files_are_reproducible_balanced_and_do_not_relabel_the_benchmark():
    spec = importlib.util.spec_from_file_location("context_review_fixtures", ROOT / "scripts/build_context_review_samples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, content in module.documents().items():
        assert (ROOT / path).read_text() == content
    assert len(EVENTS) == len({row["event_id"] for row in EVENTS}) == len({row["payload"] for row in EVENTS}) == 18
    assert Counter(row["expected_verdict"] for row in EVENTS) == {
        "true_positive": 6, "false_positive": 6, "inconclusive": 6,
    }
    for group in ("html", "sql", "shell", "path", "lookup", "template"):
        assert Counter(row["expected_verdict"] for row in ANSWERS if row["group"] == group) == {
            "true_positive": 1, "false_positive": 1,
        }
    previous_ids = set()
    for path in ("waf-dummy-v1/all_150.json", "waf-parser-stress-v1/parser_stress_50.json"):
        previous_ids.update(row["event_id"] for row in json.loads((ROOT / "samples" / path).read_text()))
    assert previous_ids.isdisjoint(row["event_id"] for row in EVENTS)


@pytest.mark.parametrize("event,answer", zip(EVENTS, ANSWERS), ids=[row["event_id"] for row in EVENTS])
def test_review_input_preserves_the_whole_request_but_not_reference_rationale(event, answer):
    detached, expected = extract_test_upload_row(event)
    assert event["event_id"] == answer["event_id"]
    assert expected == answer["expected_verdict"]
    assert bool(answer["missing_condition_ko"]) == (expected == "inconclusive")
    assert answer["rationale_ko"]
    assert ipaddress.ip_address(event["src_ip"]) in ipaddress.ip_network("192.0.2.0/24")
    assert ipaddress.ip_address(event["dest_ip"]) in ipaddress.ip_network("198.51.100.0/24")
    # Neutral envelopes are shared across classes; no affirmative test/safe cues.
    sample_input = json.dumps(detached, ensure_ascii=False).lower()
    for marker in ("synthetic", "fixture", "canary", "waf-syn-", "context-review"):
        assert marker not in sample_input
    raw = event["payload"]
    parsed = parse_http_payload(raw)
    assert parsed["parse_status"] == "success"
    headers, _, body = raw.partition("\r\n\r\n")
    if "Content-Length:" in headers:
        length = next(line.split(":", 1)[1] for line in headers.split("\r\n") if line.startswith("Content-Length:"))
        assert int(length) == len(body.encode())
        assert isinstance(json.loads(body), dict)
    integrity = assess_request_integrity(raw, parsed)
    assert not integrity["issues"]  # No accidental parser-boundary task in this set.
    built = build(raw, detached, request_integrity=integrity)
    document = json.loads(built.text)
    assert document["event"]["payload"] == raw and not built.input_truncated
    assert not REFERENCE_FIELDS.intersection(document["event"])
    assert answer["rationale_ko"] not in built.text
    assert "verdict" not in document
    # Validate the actual source-selection protocol, without fabricated semantic
    # test passes: the selected interpretation is an explicit mock, not an LLM.
    candidates = document["evidence_candidates"]["items"]
    assert any(item["field"].startswith("payload.") for item in candidates)
    for item in candidates:
        output, issues, _ = resolve_selection(selected(item["source_id"]), document["evidence_candidates"], resolver(raw, built, detached))
        assert not issues and output.evidence[0].excerpt == item["excerpt"]
        if item["start"] is not None:
            assert raw[item["start"]:item["end"]] == item["excerpt"]


def test_all_review_labels_stay_outside_real_upload_and_worker_input(client):
    login(client)
    result = upload(client, EVENTS, key="context-review-offline-upload")
    assert (result["accepted"], result["rejected"], result["duplicates"]) == (18, 0, 0)
    details = client.get(f"/api/v1/test-runs/{result['id']}?limit=200").json()
    answers = {answer["event_id"]: answer for answer in ANSWERS}
    with client.app.state.session_factory() as db:
        assert db.query(AgentRun).count() == 0  # Upload test cannot call a model.
        assert len(details["items"]) == 18
        for item in details["items"]:
            row = db.get(Analysis, item["analysis_id"])
            answer = answers[row.event_id]
            assert not REFERENCE_FIELDS.intersection(row.extra_fields)
            assert item["evaluation"]["reference_label"]["verdict"] == answer["expected_verdict"]
            raw = client.app.state.crypto.decrypt_text(row.payload_ciphertext)
            built = build(raw, _event_document(row))
            assert not REFERENCE_FIELDS.intersection(json.loads(built.text)["event"])
            assert answer["rationale_ko"] not in built.text
            assert "expected_verdict" not in built.text


def test_new_shared_prompt_keeps_each_existing_protocol_enabled():
    from app.agent.analyst_assessment import RULES_VERSIONS
    from app.agent.evidence_candidates import SELECTION_RULES_VERSIONS
    from app.services.request_integrity import INTEGRITY_RULES_VERSIONS

    for revision in ("waf-system-v2.10", FIXED_RULES_VERSION):
        assert revision in RULES_VERSIONS & SELECTION_RULES_VERSIONS & INTEGRITY_RULES_VERSIONS
