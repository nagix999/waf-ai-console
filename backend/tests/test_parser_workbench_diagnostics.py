"""Synthetic parser authoring and bounded diagnostics; all transport is mocked."""
from datetime import timedelta
import json
from types import SimpleNamespace

import httpx
import pytest
from app.models import ParserWorkbenchJob, VLLMProfile, utcnow
from app.services import parser_workbench_jobs as jobs
from app.services.parser_versions import ParserVersionError
from test_parser_workbench import PREFIX, create, discovery_stub, login, profile, proposal, run


pytestmark = pytest.mark.usefixtures("registered_vllm_target")
PRIVATE = "SYNTHETIC_PRIVATE_OUTPUT_OR_AUTH_MUST_NOT_LEAVE"


def confirmed_session(client, settings, monkeypatch):
    login(client)
    session, profile_id = create(client), profile(client)
    with monkeypatch.context() as setup:
        setup.setattr(jobs, "request_completion", discovery_stub([]))
        response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={
            "expected_revision": session["revision"], "profile_id": profile_id,
            "internal_data_confirmed": True})
        assert response.status_code == 202 and run(client, settings)
    session = client.get(PREFIX + f"/sessions/{session['id']}").json()
    groups = [{key: group[key] for key in ("group_id", "name", "waf_vendor", "case_ids")}
              for group in session["groups"]]
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={
        "expected_revision": session["revision"], "groups": groups})
    assert response.status_code == 200, response.text
    return response.json(), profile_id


def enqueue_generation(client, session, profile_id):
    response = client.post(PREFIX + f"/sessions/{session['id']}/generate", json={
        "expected_revision": session["revision"], "profile_id": profile_id,
        "internal_data_confirmed": True})
    assert response.status_code == 202, response.text
    return response.json()


def finish_generation(client, session, profile_id, settings):
    enqueue_generation(client, session, profile_id)
    assert run(client, settings)
    response = client.get(PREFIX + f"/sessions/{session['id']}/jobs")
    assert response.status_code == 200
    return response.json()["items"][-1]


def envelope(value=None, *, finish="stop", usage=None):
    return {"choices": [{"finish_reason": finish, "message": {
        "content": json.dumps(value if value is not None else proposal(), ensure_ascii=False)}}],
        "usage": usage or {"prompt_tokens": 51, "completion_tokens": 73, "total_tokens": 124},
        "private": PRIVATE}


def wire(monkeypatch, responder):
    original, calls = httpx.AsyncClient, []
    def handle(request):
        body = json.loads(request.content)
        document = json.loads(body["messages"][1]["content"])
        calls.append({"body": body, "document": document})
        response = responder(body, document, len(calls))
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)
    def mocked(**kwargs):
        assert kwargs["verify"] is True and kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(handle))
    monkeypatch.setattr(jobs.httpx, "AsyncClient", mocked)
    return calls


def diagnostics(job):
    value = job["summary"]["diagnostics"]
    assert isinstance(value["attempts"], list)
    assert value["attempt_count"] == len(value["attempts"])
    assert PRIVATE not in json.dumps(value, ensure_ascii=False)
    return value


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_unfinished_completion_preserves_its_cause_not_generic_schema_failure(
    client, settings, monkeypatch, finish,
):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    calls = wire(monkeypatch, lambda *_args: envelope({"private": PRIVATE}, finish=finish))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "failed"
    expected = ("parser_workbench_output_incomplete" if finish == "length"
                else "parser_workbench_completion_not_finished")
    assert job["error_code"] == expected
    assert 1 <= len(calls) <= job["summary"]["call_limit"] == 3
    detail = diagnostics(job)
    assert detail["last_error_code"] == expected
    assert detail["attempts"][-1]["code"] == expected


def test_incomplete_output_can_be_corrected_without_resetting_shared_budget(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    calls = wire(monkeypatch, lambda _body, _document, index:
                 envelope({"private": PRIVATE}, finish="length") if index == 1 else envelope())
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and job["error_code"] is None
    assert len(calls) == job["summary"]["call_count"] == 2
    detail = diagnostics(job)
    assert any(item["code"] == "parser_workbench_output_incomplete" for item in detail["attempts"])
    assert calls[1]["document"]["correction_code"] == "parser_workbench_output_incomplete"
    assert PRIVATE not in json.dumps(calls[1]["document"], ensure_ascii=False)


def test_schema_correction_receives_previous_rule_and_safe_field_issues(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    invalid = proposal()
    invalid["capture"]["line_endings"] = PRIVATE
    calls = wire(monkeypatch, lambda _body, _document, index: envelope(invalid if index == 1 else None))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and len(calls) == 2
    feedback = calls[1]["document"]["correction_feedback"]
    assert feedback["previous_rule"] == invalid
    assert feedback["issues"]
    assert any("line_endings" in item["field"] for item in feedback["issues"])
    assert all(set(item) <= {"field", "type"} for item in feedback["issues"])
    assert PRIVATE not in json.dumps(diagnostics(job), ensure_ascii=False)


def test_schema_failure_exposes_specific_safe_code_and_shared_attempt_limit(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    invalid = proposal()
    invalid["capture"]["line_endings"] = PRIVATE
    invalid[PRIVATE] = PRIVATE
    calls = wire(monkeypatch, lambda *_args: envelope(invalid))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "failed" and job["error_code"] == "parser_workbench_schema_invalid"
    assert len(calls) == job["summary"]["call_count"] == job["summary"]["call_limit"] == 3
    detail = diagnostics(job)
    assert detail["last_error_code"] == "parser_workbench_schema_invalid"
    assert all(PRIVATE not in json.dumps(item, ensure_ascii=False) for item in detail["attempts"])
    with client.app.state.session_factory() as db:
        saved = db.get(ParserWorkbenchJob, job["id"])
        assert saved.result_ciphertext is None
        assert PRIVATE not in json.dumps(saved.metadata_json, ensure_ascii=False)


def test_authoring_correction_identifies_aliases_and_excludes_holdout_sources(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    bad_rule = proposal()
    bad_rule["extract"] = {"kind": "json_string", "json_pointer": "/missing"}
    calls = wire(monkeypatch, lambda _body, _document, index: envelope(bad_rule if index == 1 else None))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and len(calls) == 2
    feedback = calls[1]["document"]["correction_feedback"]
    assert feedback["previous_rule"] == bad_rule
    ids = {item["case_id"] for item in calls[1]["document"]["samples"]}
    assert feedback["failed_sample_ids"] and set(feedback["failed_sample_ids"]) <= ids
    assert {"field": "extract", "type": "parser_json_pointer_missing"} in feedback["issues"]
    assert all(value.startswith("c") and len(value) == 4 for value in feedback["failed_sample_ids"])
    serialized = json.dumps(calls, ensure_ascii=False)
    assert all(case["case_id"] not in serialized for case in session["cases"] if case["role"] == "holdout")
    assert not any(name in serialized for name in ("expected_verdict", "reference_verdict", "expectations"))
    diagnostic = diagnostics(job)
    assert any(item["code"] == "parser_workbench_authoring_failed" for item in diagnostic["attempts"])


def test_authoring_failure_keeps_specific_code_and_does_not_save_partial_rule(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    bad_rule = proposal()
    bad_rule["extract"] = {"kind": "json_string", "json_pointer": "/missing"}
    calls = wire(monkeypatch, lambda *_args: envelope(bad_rule))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "failed" and job["error_code"] == "parser_workbench_authoring_failed"
    assert len(calls) == job["summary"]["call_limit"] == 3
    assert diagnostics(job)["last_error_code"] == "parser_workbench_authoring_failed"
    with client.app.state.session_factory() as db:
        assert db.get(ParserWorkbenchJob, job["id"]).result_ciphertext is None


def test_corrections_are_shared_across_groups_instead_of_reset_per_group(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    group = session["groups"][0]
    assert len(group["case_ids"]) >= 2
    groups = [{"group_id": "format1", "name": "First JSON format", "waf_vendor": group["waf_vendor"],
               "case_ids": group["case_ids"][:1]},
              {"group_id": "format2", "name": "Second JSON format", "waf_vendor": group["waf_vendor"],
               "case_ids": group["case_ids"][1:]}]
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={
        "expected_revision": session["revision"], "groups": groups})
    assert response.status_code == 200, response.text
    session = response.json()
    invalid = proposal()
    invalid["extract"] = {"kind": "json_string", "json_pointer": "/missing"}
    calls = wire(monkeypatch, lambda _body, _document, index: envelope(None if index == 3 else invalid))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "failed" and job["error_code"] == "parser_workbench_authoring_failed"
    assert len(calls) == job["summary"]["call_count"] == job["summary"]["call_limit"] == 4
    assert [call["document"]["group"]["group_id"] for call in calls] == ["format1"] * 3 + ["format2"]
    detail = diagnostics(job)
    assert [attempt["group_index"] for attempt in detail["attempts"]] == [1, 1, 1, 2]
    assert detail["correction_count"] == 2
    with client.app.state.session_factory() as db:
        assert db.get(ParserWorkbenchJob, job["id"]).result_ciphertext is None


def test_configured_output_budget_is_used_above_legacy_cap_without_expanding_it(monkeypatch):
    import asyncio
    from app.parser_rule_schemas import RuleProposalV1
    configured = SimpleNamespace(context_window=32768, max_output_tokens=4096, provider="vllm",
        model_name="synthetic", base_url="http://10.0.0.10:8000/v1", timeout_seconds=5)
    calls = wire(monkeypatch, lambda *_args: envelope())
    result = asyncio.run(jobs.request_completion(configured, None, "{}", jobs.RULE_INSTRUCTIONS,
                                                 RuleProposalV1, lambda: None))
    assert result is not None
    assert calls[0]["body"]["max_tokens"] == 4096
    assert calls[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert calls[0]["body"]["response_format"]["json_schema"]["strict"] is True


def test_context_budget_reserves_actual_configured_output_not_old_cap():
    configured = SimpleNamespace(context_window=10000, max_output_tokens=4096)
    # 7000 bytes fit only when the previous 1536-token cap is reserved.
    with pytest.raises(ParserVersionError, match="parser_workbench_context_limit"):
        jobs._budget(configured, "x" * 7000, "")


def test_feedback_omits_oversized_or_unencodable_proposals_without_losing_safe_issues():
    from app.services.parser_workbench_diagnostics import WorkbenchOutputError
    configured = SimpleNamespace(context_window=32768, max_output_tokens=3072)
    for previous in ({"private": "x" * 20000}, {"private": "\ud800"}):
        failure = WorkbenchOutputError("parser_workbench_schema_invalid", "schema",
            previous_rule=previous, issues=[{"field": "capture.line_endings", "type": "literal_error"}])
        value = json.loads(jobs._correction_payload(configured, {"samples": []}, jobs.RULE_INSTRUCTIONS, failure))
        assert value["correction_feedback"]["previous_rule_omitted"] is True
        assert "previous_rule" not in value["correction_feedback"]
        assert value["correction_feedback"]["issues"] == failure.issues


def test_feedback_drops_prior_rule_if_it_exceeds_the_remaining_input_budget():
    from app.services.parser_workbench_diagnostics import WorkbenchOutputError
    configured = SimpleNamespace(context_window=8192, max_output_tokens=3072)
    failure = WorkbenchOutputError("parser_workbench_schema_invalid", "schema",
                                   previous_rule={"private": "x" * 4000})
    value = json.loads(jobs._correction_payload(configured, {"samples": []}, "", failure))
    assert value["correction_feedback"]["previous_rule_omitted"] is True
    assert "previous_rule" not in value["correction_feedback"]


def test_json_failure_is_not_reclassified_as_rule_or_schema_failure(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    def respond(*_args):
        value = envelope()
        value["choices"][0]["message"]["content"] = '{"name":"one","name":"two"}'
        return value
    calls = wire(monkeypatch, respond)
    job = finish_generation(client, session, profile_id, settings)
    assert job["error_code"] == "parser_workbench_json_invalid"
    assert len(calls) == 3
    assert diagnostics(job)["attempts"][-1]["issues"] == [{"field": "$", "type": "json_duplicate_key"}]


def test_re2_rule_validation_failure_is_corrected_with_the_previous_rule(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    invalid = proposal()
    invalid["extract"] = {"kind": "regex_capture", "pattern": "(?P<wrong>.+)"}
    calls = wire(monkeypatch, lambda _body, _document, index: envelope(invalid if index == 1 else None))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and len(calls) == 2
    feedback = calls[1]["document"]["correction_feedback"]
    assert feedback["code"] == "parser_regex_capture_invalid"
    assert feedback["stage"] == "rule_validation" and feedback["previous_rule"] == invalid


def test_response_metadata_never_exposes_unknown_reason_values_or_invalid_usage():
    from app.services.parser_workbench_diagnostics import response_metadata
    assert response_metadata({"choices": [{"finish_reason": [PRIVATE]}], "usage": {
        "prompt_tokens": True, "completion_tokens": -1, "total_tokens": PRIVATE,
        "private": PRIVATE}}) == {}


def test_diagnostics_allow_only_numeric_usage_and_fixed_finish_reason(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    calls = wire(monkeypatch, lambda *_args: envelope(usage={
        "prompt_tokens": 51, "completion_tokens": 73, "total_tokens": 124,
        "private": PRIVATE, "completion_tokens_details": {"private": PRIVATE}}))
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "completed" and len(calls) == 1
    detail = diagnostics(job)
    attempt = detail["attempts"][-1]
    assert attempt["output_limit"] == 1536
    assert attempt["finish_reason"] == "stop"
    assert attempt["usage"]
    assert all(type(value) is int and value >= 0 for value in attempt["usage"].values())
    assert set(attempt["usage"]) <= {"prompt_tokens", "completion_tokens", "total_tokens",
                                     "input_tokens", "output_tokens"}
    assert "private" not in attempt["usage"]
    assert "raw_payload" not in json.dumps(detail)


def test_generation_profile_revoked_after_admission_never_receives_input(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    enqueue_generation(client, session, profile_id)
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).status = "disabled"
        db.commit()
    async def forbidden(*_args):
        pytest.fail("A revoked profile must not receive parser authoring inputs")
    monkeypatch.setattr(jobs, "request_completion", forbidden)
    assert run(client, settings)
    job = client.get(PREFIX + f"/sessions/{session['id']}/jobs").json()["items"][-1]
    assert job["status"] == "failed" and job["error_code"] == "parser_verified_profile_required"
    assert job["summary"]["call_count"] == 0


@pytest.mark.parametrize("mode", ["timeout", "http_error", "invalid_envelope"])
def test_transport_failures_do_not_leak_exception_or_provider_content(client, settings, monkeypatch, mode):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    def respond(*_args):
        if mode == "timeout":
            raise httpx.ReadTimeout(PRIVATE)
        if mode == "http_error":
            return httpx.Response(500, text=PRIVATE)
        return {"error": {"message": PRIVATE}, "choices": []}
    calls = wire(monkeypatch, respond)
    job = finish_generation(client, session, profile_id, settings)
    assert job["status"] == "failed" and calls
    assert PRIVATE not in json.dumps(job, ensure_ascii=False)
    assert job["error_code"] != "parser_workbench_invalid_rule"


def test_lost_lease_before_retry_never_dispatches_or_overwrites_new_owner(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    queued = enqueue_generation(client, session, profile_id)
    calls = []
    async def revoke(_profile, _key, _text, _instructions, _model, check):
        check()
        calls.append(True)
        with client.app.state.session_factory() as db:
            row = db.get(ParserWorkbenchJob, queued["id"])
            row.lease_owner = "synthetic-new-owner"
            row.lease_expires_at = utcnow() + timedelta(seconds=60)
            db.commit()
        raise ValueError(PRIVATE)
    monkeypatch.setattr(jobs, "request_completion", revoke)
    assert run(client, settings)
    with client.app.state.session_factory() as db:
        saved = db.get(ParserWorkbenchJob, queued["id"])
        assert saved.status == "running" and saved.lease_owner == "synthetic-new-owner"
        assert saved.error_code is None and saved.result_ciphertext is None
        assert saved.metadata_json["call_count"] == 1
        assert PRIVATE not in json.dumps(saved.metadata_json)
    assert calls == [True]


def test_expired_job_does_not_reset_existing_global_attempt_budget(client, settings, monkeypatch):
    session, profile_id = confirmed_session(client, settings, monkeypatch)
    queued = enqueue_generation(client, session, profile_id)
    assert jobs._claim(client.app.state.engine, "synthetic-expired-owner", 60) == queued["id"]
    with client.app.state.session_factory() as db:
        saved = db.get(ParserWorkbenchJob, queued["id"])
        saved.lease_expires_at = utcnow() - timedelta(seconds=1)
        saved.metadata_json = {**saved.metadata_json, "call_count": saved.metadata_json["call_limit"]}
        db.commit()
    async def forbidden(*_args):
        pytest.fail("Exhausted persisted budget must prevent dispatch")
    monkeypatch.setattr(jobs, "request_completion", forbidden)
    assert run(client, settings)
    detail = client.get(PREFIX + f"/sessions/{session['id']}/jobs").json()["items"][-1]
    assert detail["error_code"] == "parser_workbench_attempt_limit"
    assert detail["summary"]["call_count"] == detail["summary"]["call_limit"] == 3
