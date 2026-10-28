"""Candidate-only JSON observations; no live data, model calls or verdict rules."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from app.agent import legacy_prompts_v215, prompts
from app.agent.input_integrity import apply_integrity_guard
from app.services.http_parser import parse_http_payload
from app.services.json_duplicate_values import SAME_CODE, DIFFERENT_CODE, UNCOMPARABLE_CODE
from app.services.request_integrity import (
    INTEGRITY_VERSION, LEGACY_INTEGRITY_VERSION, INTEGRITY_V3_VERSION,
    NORMAL_GUARD_CODES, assess_request_integrity, integrity_version_for_rules, submitted_integrity,
)


def request(body, headers="", *, media="application/json"):
    return f"POST /items HTTP/1.1\r\nHost: example.invalid\r\nContent-Type: {media}\r\n{headers}\r\n{body}"


def assess(raw, version=INTEGRITY_V3_VERSION):
    return assess_request_integrity(raw, parse_http_payload(raw), version=version)


@pytest.mark.parametrize("body,code", [
    ('{"page":2,"page":2}', SAME_CODE),
    ('{"page":2,"page":3}', DIFFERENT_CODE),
    ('{"page":2,"page":2.0}', UNCOMPARABLE_CODE),
    ('{"page":true,"page":1}', DIFFERENT_CODE),
    ('{"page":{},"page":{}}', UNCOMPARABLE_CODE),
    ('{"outer":{"page":2,"page":2}}', SAME_CODE),
])
def test_only_candidate_gets_scalar_observation_with_full_proof(body, code):
    raw = request(body)
    parsed = parse_http_payload(raw)
    before = deepcopy(parsed)
    old = assess_request_integrity(raw, parsed, version=INTEGRITY_VERSION)
    new = assess_request_integrity(raw, parsed, version=INTEGRITY_V3_VERSION)
    assert parsed == before
    assert [item["code"] for item in old["issues"]] == ["json_duplicate_keys"]
    assert [item["code"] for item in new["issues"]] == ["json_duplicate_keys", code]
    added = new["issues"][-1]
    assert added["source_spans"] == [[0, len(raw)]]
    assert all(0 <= start < end <= len(raw) for start, end in added["affected_spans"])
    assert code not in NORMAL_GUARD_CODES
    assert not {"verdict", "keys", "values", "path"} & added.keys()
    assert set(added) == {"code", "scope", "source_spans", "affected_spans"}


@pytest.mark.parametrize("raw", [
    request('{"x":1,"x":1}', media="text/plain"),
    request('{"x":1,"x":1}', "Content-Encoding: gzip\r\n"),
    request('{"x":1,"x":1}', "Transfer-Encoding: chunked\r\n"),
    request('{"x":1,"x":1}', "Content-Length: 99\r\n"),
    request('{"x":1,"x":1}', "Content-Length: 1\r\nContent-Length: 99\r\n"),
    request('{"x":1,"x":1'),
    request('{"x":1,"x":1} trailing'),
    request('{"x":NaN,"x":NaN}'),
    request('{"x":"가","x":"가"}', "Content-Length: 999\r\n"),
    request('{"x":1,"x":1}', "Content-Length: " + "9" * 129 + "\r\n"),
    request('{"x":1,"x":1}', "Content-Length: " + "13," * 33 + "13\r\n"),
    request('[{"x":1},{"x":1}]'),
])
def test_unsupported_partial_or_invalid_body_never_claims_scalar_equality(raw):
    result = assess(raw)
    assert not any(item["code"] in {SAME_CODE, DIFFERENT_CODE} for item in result["issues"])


def test_candidate_facts_are_omitted_if_complete_source_not_submitted():
    raw = request('{"page":2,"page":2}')
    result = assess(raw)
    body_start = raw.index('{')
    limited = submitted_integrity(result, [(body_start, len(raw))], 10_000)
    assert limited["issues"] == [] and limited["omitted_issues"]
    whole = submitted_integrity(result, [(0, len(raw))], 10_000)
    assert [item["code"] for item in whole["issues"]] == ["json_duplicate_keys", SAME_CODE]


def test_new_observations_do_not_change_normal_verdict_guard():
    from test_request_integrity import output
    raw = request('{"page":2,"page":2}')
    parsed = parse_http_payload(raw)
    value = output("false_positive", "payload.body", '"page":2')
    assessment = submitted_integrity(assess(raw), [(0, len(raw))], 10_000)
    guarded, metadata = apply_integrity_guard(value, assessment, raw, parsed)
    assert guarded is value and guarded.verdict == "false_positive"
    assert not metadata["downgraded_to_inconclusive"]


def test_candidate_preserves_existing_missing_body_guard():
    from test_request_integrity import output
    raw = request('{"page":2', "Content-Length: 90\r\n")
    parsed = parse_http_payload(raw)
    value = output("false_positive", "payload.body", '"page":2')
    results = []
    for version in (INTEGRITY_VERSION, INTEGRITY_V3_VERSION):
        assessment = submitted_integrity(assess(raw, version), [(0, len(raw))], 10_000)
        guarded, metadata = apply_integrity_guard(value, assessment, raw, parsed)
        assert guarded.verdict == "inconclusive" and metadata["downgraded_to_inconclusive"]
        results.append(guarded.model_dump(mode="json"))
    assert results[0] == results[1]


def test_repeated_keys_in_different_objects_are_not_mixed():
    result = assess(request('[{"page":2},{"page":3}]'))
    assert result["issues"] == []


@pytest.mark.parametrize("version,expected", [
    ("waf-system-v2.9", LEGACY_INTEGRITY_VERSION),
    ("waf-system-v2.11", LEGACY_INTEGRITY_VERSION),
    ("waf-system-v2.12", INTEGRITY_VERSION),
    ("waf-system-v2.14", INTEGRITY_VERSION),
    ("waf-system-v2.15", INTEGRITY_VERSION),
    ("waf-system-v2.16", INTEGRITY_V3_VERSION),
])
def test_integrity_version_selection_preserves_old_execution(version, expected):
    assert integrity_version_for_rules(version) == expected


def test_candidate_observation_count_is_bounded_without_inventing_absence():
    body = "{" + ",".join(f'"k{index}":1,"k{index}":1' for index in range(9)) + "}"
    result = assess(request(body))
    assert len(result["issues"]) == 9  # original duplicate finding plus eight observations
    assert "json_duplicate_observation_limit" in result["limitations"]


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("rules,version", [(legacy_prompts_v215, INTEGRITY_VERSION), (prompts, INTEGRITY_V3_VERSION)])
def test_worker_keeps_pinned_v215_input_and_candidate_uses_new_observation(
    client, event_payload, service_headers, monkeypatch, rules, version,
):
    from app import worker
    from app.agent.executor import AgentCallResult
    from app.models import Analysis, VLLMProfile
    from app.services import prompt_snapshots
    from test_security_analysis_v3 import output
    original = prompt_snapshots._build_snapshot
    def snapshot(policy, crypto, *, origin="enqueue", rules=None):
        return original(policy, crypto, origin=origin, rules=rules or selected)
    selected = rules
    event_payload["payload"] = request('{"page":2,"page":2}')
    with patch.object(prompt_snapshots, "_build_snapshot", snapshot):
        created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload)
    assert created.status_code == 202, created.text
    inputs = []
    async def fake(**kwargs):
        document = json.loads(kwargs["user_input"])
        inputs.append(document)
        candidate = next(item for item in document["evidence_candidates"]["items"] if item["field"] == "payload.body")
        value = output("normal_request", verdict="false_positive", source=candidate["source_id"])
        value.summary_ko = "요청 본문의 페이지 번호가 같은 값으로 반복되어 있습니다."
        value = kwargs["output_model"].model_validate(value.model_dump(mode="json"))
        return AgentCallResult(value, "synthetic-duplicate", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", fake)
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-duplicate", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production"))
        db.commit()
        row = db.get(Analysis, created.json()["id"])
        worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        assert row.result_json["policy"]["fixed_rules_version"] == rules.FIXED_RULES_VERSION
        assert row.result_json["diagnostics"]["request_integrity"]["version"] == version
    assert len(inputs) == 2 and inputs[0] == inputs[1]
    observed = {item["code"] for item in inputs[0]["request_integrity"]["issues"]}
    assert (SAME_CODE in observed) == (version == INTEGRITY_V3_VERSION)
