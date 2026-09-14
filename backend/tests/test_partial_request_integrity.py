"""Protocol-missing capture observations and legacy replay; no real LLM calls."""
import copy
import json

import pytest
from sqlalchemy import select

from agent_selection_helpers import model_output
from app import worker
from app.agent.executor import AgentCallResult
from app.agent.input_builder import build_agent_input
from app.agent.input_integrity import apply_integrity_guard
from app.models import AgentStep, Analysis, VLLMProfile
from app.services import prompt_snapshots
from app.services.http_parser import parse_http_payload
from app.services.request_integrity import (
    INTEGRITY_VERSION, LEGACY_INTEGRITY_VERSION, MAX_PAYLOAD_CHARS,
    assess_request_integrity, submitted_integrity,
)
from test_request_integrity import output, request


def partial(body="", headers="Content-Length: 80\r\nContent-Type: application/json\r\n"):
    return request(body, headers).replace(" HTTP/1.1\r\n", "\r\n", 1)


@pytest.mark.parametrize("body,headers,expected", [
    ("", "Content-Length: 20\r\n", "declared_body_not_captured"),
    ("abc", "Content-Length: 20\r\n", "body_shorter_than_content_length"),
    ("", "Content-Length: 0\r\n", None),
    ("abc", "Content-Length: 3\r\n", None),
    ("abc", "Content-Length: 2\r\n", None),
    ("abc", "Content-Length: 003, 3\r\n", None),
    ("abc", "Content-Length: 20\r\nContent-Length: 020\r\n", "body_shorter_than_content_length"),
    ("abc", "Content-Length: " + "9" * 100 + "\r\n", "body_shorter_than_content_length"),
    ("", "Content-Length: 20\r\nContent-Encoding: gzip\r\n", "declared_body_not_captured"),
])
def test_only_missing_protocol_does_not_erase_declared_body_observation(body, headers, expected):
    raw = partial(body, headers)
    parsed = parse_http_payload(raw)
    before = copy.deepcopy(parsed)
    result = assess_request_integrity(raw, parsed)
    assert parsed == before and parsed["parse_status"] == "partial"
    assert result["version"] == INTEGRITY_VERSION
    assert result["limitations"] == ["protocol_missing_body_check_only"]
    assert [item["code"] for item in result["issues"]] == ([expected] if expected else [])
    assert "verdict" not in result
    for finding in result["issues"]:
        assert finding["source_spans"] == [[0, len(raw)]]
        assert all(0 <= a <= b <= len(raw) for a, b in finding["affected_spans"])
    legacy = assess_request_integrity(raw, parsed, version=LEGACY_INTEGRITY_VERSION)
    assert legacy == {"version": LEGACY_INTEGRITY_VERSION, "status": "not_checked", "issues": [],
                      "limitations": ["http_structure_not_supported"]}


@pytest.mark.parametrize("raw", [
    partial("abc", ""), partial("abc", "Content-Length: 2, 4\r\n"),
    partial("abc", "Content-Length: -1\r\n"), partial("abc", "Content-Length: ４\r\n"),
    partial("abc", "Content-Length: " + "3," * 33 + "3\r\n"),
    partial("abc", "Content-Length: " + "9" * 129 + "\r\n"),
    partial("한글"), partial("abc", "Content-Length: 80\r\nContent-Encoding: gzip\r\n"),
    partial("", "Content-Length: 80\r\nTransfer-Encoding: chunked\r\n"),
    partial("", "Content-Length: 80\r\n malformed-header\r\n"),
    partial().replace("POST /submit?q=hello", "POST /submit?q=hello HTTP/9.9"),
    partial().replace("POST /submit?q=hello", "POST /submit?q=hello HTTP/"),
    partial().replace("\r\n", r"\r\n"),
    "POST /submit\r\nContent-Length: 80",  # No proven header/body boundary.
    "POST /submit\r\nContent-Length: 80\r\n",  # A single final line break isn't the boundary.
    json.dumps({"request": partial()}), "vendor=header Content-Length: 80",
    partial("a" * MAX_PAYLOAD_CHARS), partial("", "X-Pad: " + "a" * 33000 + "\r\nContent-Length: 80\r\n"),
])
def test_uncertain_headers_codings_and_bounds_never_manufacture_missing_body(raw):
    result = assess_request_integrity(raw, parse_http_payload(raw))
    assert result["status"] == "not_checked" and not result["issues"]
    assert result["limitations"]


@pytest.mark.parametrize("version", [LEGACY_INTEGRITY_VERSION, INTEGRITY_VERSION])
def test_submission_and_guard_keep_the_selected_version(version):
    raw = request("", "Content-Length: 80\r\n")
    parsed = parse_http_payload(raw)
    inspected = assess_request_integrity(raw, parsed, version=version)
    submitted = submitted_integrity(inspected, [(0, len(raw))], 3000)
    final, metadata = apply_integrity_guard(output("false_positive", "payload.headers.Content-Length", "Content-Length: 80"), submitted, raw, parsed)
    assert inspected["version"] == submitted["version"] == metadata["version"] == version
    assert metadata["downgraded_to_inconclusive"] and final.verdict.value == "inconclusive"
    if version == INTEGRITY_VERSION:
        assert "수집된 로그에는 본문이 없습니다" in final.summary_ko
    else:
        assert "본문 누락 가능성이나 구조·경계 문제가 있어" in final.summary_ko


@pytest.mark.parametrize("verdict", ["false_positive", "true_positive", "inconclusive"])
@pytest.mark.parametrize("field,excerpt,overlap", [
    ("payload.headers.Content-Length", "Content-Length: 80", True),
    ("payload.headers.Content-Type", "Content-Type: application/json", True),
    ("payload.query", "q=hello", False), ("payload.headers.Host", "Host: example.invalid", False),
])
def test_missing_body_only_limits_normal_evidence_about_that_body(verdict, field, excerpt, overlap):
    raw = partial()
    parsed = parse_http_payload(raw)
    assessed = assess_request_integrity(raw, parsed)
    built = build_agent_input({}, raw, parsed, 32768, 3072, evidence_selection=True, request_integrity=assessed)
    initial = output(verdict, field, excerpt)
    before = initial.model_dump(mode="json")
    final, meta = apply_integrity_guard(initial, built.request_integrity, raw, parsed)
    assert meta["downgraded_to_inconclusive"] == (verdict == "false_positive" and overlap)
    assert final.evidence == initial.evidence and initial.model_dump(mode="json") == before
    assert json.loads(built.text)["event"]["payload"] == raw
    # A proof omitted for either budget or source coverage cannot limit a verdict.
    for submitted in (submitted_integrity(assessed, [(0, 10)], 3000), submitted_integrity(assessed, [(0, len(raw))], 230)):
        assert submitted["omitted_issues"] and not submitted["issues"]
        assert not apply_integrity_guard(initial, submitted, raw, parsed)[1]["downgraded_to_inconclusive"]


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("revision,limited", [("2.11", False), ("2.12", True)])
def test_worker_partial_snapshot_selects_version_for_both_roles_and_all_history(
    client, event_payload, service_headers, monkeypatch, revision, limited,
):
    raw = partial()
    event_payload.update(payload=raw, waf_action="A")
    with monkeypatch.context() as pinned:
        pinned.setattr(prompt_snapshots, "FIXED_RULES_VERSION", f"waf-system-v{revision}")
        pinned.setattr(prompt_snapshots, "PROMPT_VERSION", f"waf-judgment-v{revision}")
        created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        normal = output("false_positive", "payload.headers.Content-Type", "Content-Type: application/json")
        return AgentCallResult(model_output(normal, kwargs), "synthetic-partial", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    version = INTEGRITY_VERSION if limited else LEGACY_INTEGRITY_VERSION
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-partial", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production"))
        db.commit()
        row = db.get(Analysis, created["id"])
        before = (row.prompt_snapshot_ciphertext, row.payload_ciphertext, row.event_fingerprint)
        worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        assert (row.prompt_snapshot_ciphertext, row.payload_ciphertext, row.event_fingerprint) == before
        assert row.verdict == ("inconclusive" if limited else "false_positive")
        assert row.result_json["primary"]["verdict"] == row.result_json["verifier"]["output"]["verdict"] == "false_positive"
        assert row.result_json["agent"]["preprocessors"][-1] == version
        metadata = row.result_json["diagnostics"]["request_integrity"]
        assert metadata["version"] == version and metadata["downgraded_to_inconclusive"] == limited
        steps = db.scalars(select(AgentStep)).all()
        parser = next(step for step in steps if step.step_type == "parser")
        assert parser.metadata_json["request_integrity"]["version"] == version
        stored = json.loads(client.app.state.crypto.decrypt_text(parser.output_ciphertext))
        assert stored["parse_status"] == "partial" and stored["request_integrity"]["version"] == version
    assert len(calls) == 2 and calls[0]["user_input"] == calls[1]["user_input"]
    assert json.loads(calls[0]["user_input"])["request_integrity"]["version"] == version
