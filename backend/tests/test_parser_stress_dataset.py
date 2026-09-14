"""Checked-in synthetic fixtures only; no model/network/runtime DB access."""
from collections import Counter
import importlib.util
import json
from pathlib import Path

import pytest

from app.agent.evidence import EvidenceSourceResolver
from app.agent.input_builder import build_agent_input
from app.models import Analysis
from app.services.http_parser import parse_http_payload
from test_test_runs import login, upload

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "samples/waf-parser-stress-v1"
EVENTS = json.loads((DIRECTORY / "parser_stress_50.json").read_text())
ANSWERS = json.loads((DIRECTORY / "reference/answers_50.json").read_text())
META = {"expected_verdict", "difficulty", "test_category", "case_name"}


def test_dataset_is_deterministic_balanced_and_distinct():
    spec = importlib.util.spec_from_file_location("parser_fixtures", ROOT / "scripts/build_parser_stress_samples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, content in module.documents().items():
        assert (ROOT / path).read_text() == content
    assert len(EVENTS) == len({e["event_id"] for e in EVENTS}) == len({e["payload"] for e in EVENTS}) == 50
    assert Counter(e["expected_verdict"] for e in EVENTS) == {"true_positive": 20, "false_positive": 20, "inconclusive": 10}
    assert set(Counter(e["test_category"] for e in EVENTS).values()) == {5}
    old = json.loads((ROOT / "samples/waf-dummy-v1/hard_50.json").read_text())
    assert not {e["event_id"] for e in old} & {e["event_id"] for e in EVENTS}


@pytest.mark.parametrize("event,answer", zip(EVENTS, ANSWERS), ids=[e["event_id"] for e in EVENTS])
def test_expected_parser_and_exact_reference(event, answer):
    parsed = parse_http_payload(event["payload"])
    assert event["event_id"] == answer["event_id"]
    assert event["expected_verdict"] == answer["expected_verdict"]
    assert parsed["parse_status"] == answer["expected_parse_status"]
    resolver = EvidenceSourceResolver(event["payload"], event, parsed)
    assert all(resolver.matches(e["field"], e["excerpt"]) for e in answer["important_evidence"])


def test_long_input_keeps_exact_attack_tail():
    event = EVENTS[45]
    agent_input = build_agent_input({k: v for k, v in event.items() if k not in META}, event["payload"],
                                   parse_http_payload(event["payload"]), 32768, 3072)
    assert agent_input.input_truncated
    assert ANSWERS[45]["important_evidence"][0]["excerpt"] in agent_input.text
    assert not META.intersection(json.loads(agent_input.text).get("event", {}))


def test_normal_escaped_json_is_valid_at_its_actual_nesting_depth():
    windows_body = EVENTS[7]["payload"].split(r"\r\n\r\n", 1)[1]
    quoted_body = json.loads(EVENTS[13]["payload"])["request_text"].split("\r\n\r\n", 1)[1]
    assert json.loads(windows_body)["text"] == r"Save reports under C:\reports\monthly."
    assert json.loads(quoted_body)["format"] == "plain_text"


def test_upload_accepts_all_labels_without_leaking_metadata(client):
    login(client)
    run = upload(client, EVENTS, key="parser-stress-offline-upload")
    assert (run["accepted"], run["rejected"], run["duplicates"]) == (50, 0, 0)
    detail = client.get(f"/api/v1/test-runs/{run['id']}?limit=200").json()
    by_id = {e["event_id"]: e for e in EVENTS}
    with client.app.state.session_factory() as db:
        for item in detail["items"]:
            row = db.get(Analysis, item["analysis_id"])
            original = by_id[item["event_id"]]
            assert not META.intersection(row.extra_fields)
            assert client.app.state.crypto.decrypt_text(row.payload_ciphertext) == original["payload"]
            assert item["evaluation"]["reference_label"]["verdict"] == original["expected_verdict"]
    assert upload(client, EVENTS, key="parser-stress-offline-upload")["id"] == run["id"]
