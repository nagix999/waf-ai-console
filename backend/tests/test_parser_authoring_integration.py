"""Authoring-only guidance and coverage use mocked, approved internal transport."""
import json
from types import SimpleNamespace

import pytest

from app.models import ParserConfiguration, ParserWorkbenchJob
from app.services import parser_workbench_jobs as jobs
from app.services.parser_versions import BASELINE_ID, ParserVersionError
from app.services.parser_workbench_diagnostics import WorkbenchOutputError
from test_parser_workbench import PREFIX, proposal
from test_parser_workbench_diagnostics import (
    confirmed_session, diagnostics, envelope, finish_generation, wire,
)


pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def test_authoring_instructions_keep_context_cost_below_previous_prompt():
    # The previous mandatory prompt was 1,087 chars / 2,147 UTF-8 bytes.
    # Optional observations must not make the mandatory context larger.
    assert len(jobs.RULE_INSTRUCTIONS) <= 1087
    assert len(jobs.RULE_INSTRUCTIONS.encode("utf-8")) <= 2147
    assert all(name in jobs.RULE_INSTRUCTIONS for name in (
        "raw_payload", "starts_with", "regex_match", "format_observations", "match_coverage",
        "previous_rule", "failed_sample_ids", "limitations_ko", "source span"))


def mismatched_prefix_rule():
    rule = proposal()
    rule["match"] = {"all": [
        {"kind": "starts_with", "value": "GET "},
        {"kind": "json_string_exists", "json_pointer": "/request"},
    ]}
    return rule


def test_first_request_and_correction_receive_only_group_authoring_observations(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    bad = mismatched_prefix_rule()
    calls = wire(monkeypatch, lambda _body, _document, index: envelope(bad if index == 1 else None))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and len(calls) == 2
    first, correction = [call["document"] for call in calls]
    observations = first["format_observations"]
    count = len(first["samples"])
    assert observations["sample_count"] == observations["inspected_sample_count"] == count
    assert observations["json_document_count"] == count
    assert observations["json_request_paths"][0]["json_pointer"] == "/request"
    assert observations["json_request_paths"][0]["observed_sample_count"] == count
    assert correction["format_observations"] == observations
    assert correction["samples"] == first["samples"]
    feedback = correction["correction_feedback"]
    assert feedback["previous_rule"] == bad
    coverage = feedback["match_coverage"]
    assert coverage["sample_count"] == coverage["checked_sample_count"] == count
    assert coverage["truncated"] is False
    assert [(item["matched_count"], item["error_count"]) for item in coverage["conditions"]] == [
        (0, 0), (count, 0)]
    assert feedback["match_checks"]
    assert all(item["condition_index"] == 0 and not item["matched"] for item in feedback["match_checks"])
    assert diagnostics(job)["attempts"][0]["match_coverage"] == coverage

    wire_text = json.dumps(calls, ensure_ascii=False)
    for case in session["cases"]:
        assert case["case_id"] not in wire_text
    assert not any(key in wire_text for key in ("expected_verdict", "reference_verdict", "expectations", "prior_result"))
    with client.app.state.session_factory() as db:
        metadata_text = json.dumps(db.get(ParserWorkbenchJob, job["id"]).metadata_json, ensure_ascii=False)
        assert "format_observations" not in metadata_text
        assert "json_pointer" not in metadata_text
        assert "example.invalid" not in metadata_text and "q=synthetic" not in metadata_text
        assert db.get(ParserConfiguration, 1).active_version_id == BASELINE_ID


def test_multiple_groups_do_not_mix_samples_or_reset_extra_call_budget(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    group = session["groups"][0]
    assert len(group["case_ids"]) >= 2
    groups = [
        {"group_id": "format1", "name": "First structure", "waf_vendor": group["waf_vendor"],
         "case_ids": group["case_ids"][:1]},
        {"group_id": "format2", "name": "Second structure", "waf_vendor": group["waf_vendor"],
         "case_ids": group["case_ids"][1:]},
    ]
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={
        "expected_revision": session["revision"], "groups": groups})
    assert response.status_code == 200, response.text
    session = response.json()
    calls = wire(monkeypatch, lambda _body, _document, index:
                 envelope(mismatched_prefix_rule() if index == 1 else None))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed"
    assert len(calls) == job["summary"]["call_count"] == 3
    assert job["summary"]["call_limit"] == len(groups) + 2 == 4
    documents = [call["document"] for call in calls]
    assert [item["group"]["group_id"] for item in documents] == ["format1", "format1", "format2"]
    first_ids = {sample["case_id"] for sample in documents[0]["samples"]}
    second_ids = {sample["case_id"] for sample in documents[2]["samples"]}
    assert first_ids and second_ids and first_ids.isdisjoint(second_ids)
    assert documents[0]["samples"] == documents[1]["samples"]
    assert documents[0]["format_observations"]["sample_count"] == len(first_ids)
    assert documents[2]["format_observations"]["sample_count"] == len(second_ids)
    assert documents[1]["correction_feedback"]["match_coverage"]["sample_count"] == len(first_ids)
    assert "correction_feedback" not in documents[2]


@pytest.mark.parametrize("helper", ["authoring_structure_hints", "summarize_rule_match"])
def test_optional_helper_failure_does_not_create_model_job_failure(client, settings, monkeypatch, helper):
    session, profile_id = confirmed_session(client, settings, monkeypatch)

    def unavailable(*_args):
        raise RuntimeError("synthetic-private-exception-not-for-diagnostics")

    monkeypatch.setattr(jobs, helper, unavailable)
    calls = wire(monkeypatch, lambda _body, _document, index:
                 envelope(mismatched_prefix_rule() if index == 1 else None))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and len(calls) == 2
    documents = [call["document"] for call in calls]
    if helper == "authoring_structure_hints":
        assert all("format_observations" not in item for item in documents)
    else:
        assert "match_coverage" not in documents[1]["correction_feedback"]
        assert "match_coverage" not in diagnostics(job)["attempts"][0]
    assert "synthetic-private-exception" not in json.dumps(job)


def test_optional_hint_is_omitted_if_it_does_not_fit_without_removing_original_samples(monkeypatch):
    configured = SimpleNamespace(context_window=8192, max_output_tokens=3072)
    samples = [{"case_id": "c001", "waf_vendor": "Synthetic", "raw_payload": "synthetic-private-original"}]
    observations = {"version": "parser-authoring-structure-v1", "json_request_paths": [{"json_pointer": "/" + "x" * 4000}]}
    payload = {"group": {"group_id": "format1"}, "samples": samples, "format_observations": observations}
    seen = []

    def budget(_profile, text, _instructions):
        value = json.loads(text)
        seen.append(value)
        assert value["samples"] == samples
        if "format_observations" in value:
            raise ParserVersionError("parser_workbench_context_limit", 413)

    monkeypatch.setattr(jobs, "_budget", budget)
    value = json.loads(jobs._correction_payload(configured, payload, ""))
    assert len(seen) == 2
    assert "format_observations" not in value
    assert value["samples"] == samples and value["correction_code"] is None
    assert payload["format_observations"] == observations


def test_correction_omits_optional_coverage_and_hint_before_losing_original_samples(monkeypatch):
    configured = SimpleNamespace(context_window=8192, max_output_tokens=3072)
    samples = [{"case_id": "c001", "waf_vendor": "Synthetic", "raw_payload": "synthetic-private-original"}]
    payload = {"samples": samples, "format_observations": {"version": "parser-authoring-structure-v1"}}
    coverage = {"sample_count": 1, "checked_sample_count": 1, "truncated": False, "conditions": [
        {"condition_index": 0, "field": "match.all.0", "kind": "starts_with", "matched_count": 0, "error_count": 0}]}
    failure = WorkbenchOutputError("parser_workbench_authoring_failed", "authoring",
        previous_rule=mismatched_prefix_rule(), failed_sample_ids=["c001"],
        issues=[{"field": "match", "type": "parser_rule_not_matched"}], match_coverage=coverage)
    seen = []

    def budget(_profile, text, _instructions):
        value = json.loads(text)
        seen.append(value)
        assert value["samples"] == samples
        feedback = value["correction_feedback"]
        if "format_observations" in value or "match_coverage" in feedback:
            raise ParserVersionError("parser_workbench_context_limit", 413)

    monkeypatch.setattr(jobs, "_budget", budget)
    value = json.loads(jobs._correction_payload(configured, payload, "", failure))
    assert len(seen) == 3
    assert "format_observations" not in seen[1] and "match_coverage" in seen[1]["correction_feedback"]
    assert "match_coverage" not in value["correction_feedback"]
    assert value["correction_feedback"]["match_coverage_omitted"] is True
    assert value["correction_feedback"]["previous_rule"] == mismatched_prefix_rule()
    assert value["samples"] == samples


def test_matching_failure_still_fails_after_same_shared_budget_without_saving_rule(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    bad = mismatched_prefix_rule()
    calls = wire(monkeypatch, lambda *_args: envelope(bad))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "failed" and job["error_code"] == "parser_workbench_authoring_failed"
    assert len(calls) == job["summary"]["call_count"] == job["summary"]["call_limit"] == 3
    attempts = diagnostics(job)["attempts"]
    assert len(attempts) == 3
    assert all(item["match_coverage"]["conditions"][0]["matched_count"] == 0 for item in attempts)
    assert all(call["document"]["format_observations"] for call in calls)
    with client.app.state.session_factory() as db:
        assert db.get(ParserWorkbenchJob, job["id"]).result_ciphertext is None
        assert db.get(ParserConfiguration, 1).active_version_id == BASELINE_ID
    state = client.get(PREFIX + f"/sessions/{session['id']}").json()
    assert all(not group.get("rule") for group in state["groups"])


def test_coverage_distinguishes_passed_match_from_extractor_failure(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    bad = proposal()
    bad["extract"] = {"kind": "json_string", "json_pointer": "/missing"}
    calls = wire(monkeypatch, lambda _body, _document, index: envelope(bad if index == 1 else None))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and len(calls) == 2
    feedback = calls[1]["document"]["correction_feedback"]
    assert feedback["match_coverage"]["conditions"][0]["matched_count"] == len(calls[0]["document"]["samples"])
    assert {"field": "extract", "type": "parser_json_pointer_missing"} in feedback["issues"]
    assert not feedback.get("match_checks")
