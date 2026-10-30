"""Synthetic-only local parser management and isolated suggestion jobs."""
import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models import AccessAudit, Analysis, ParserJob, VLLMProfile, VLLMTestRun, utcnow
from app.parser_schemas import ParserSuggestionOutput
from app.services import parser_jobs
from app.services.parser_versions import BASELINE_ID
from app.services.vllm_profiles import profile_fingerprint

PREFIX = "/api/v1/admin/parsers"
RAW = "GET /search?q=fixture HTTP/1.1\\r\\nHost: example.internal\\r\\nCookie: private-fixture\\r\\n\\r\\n"


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def analysis(client, raw=RAW, *, vendor="generic"):
    with client.app.state.session_factory() as db:
        row = Analysis(source_system="synthetic-parser", event_id="synthetic-" + str(utcnow()), company_name="Example",
            src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor=vendor, waf_action="D",
            payload_ciphertext=client.app.state.crypto.encrypt_text(raw), encryption_key_version="test")
        db.add(row)
        db.commit()
        return row.id


def candidate(client):
    response = client.post(PREFIX, json={"name": "Escaped capture", "description": "private-note-fixture",
        "engine_version": "http-capture-v1", "parent_id": BASELINE_ID,
        "options": {"line_endings": "escaped_crlf", "request_line_whitespace": "strict", "skip_leading_blank_lines": False}})
    assert response.status_code == 201, response.text
    return response.json()


def profile(client, provider="vllm"):
    with client.app.state.session_factory() as db:
        row = VLLMProfile(name="Synthetic parser model", provider=provider, base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="verified", timeout_seconds=5, context_window=32768, max_output_tokens=1536)
        db.add(row)
        db.flush()
        db.add(VLLMTestRun(profile_id=row.id, mode="full", status="passed", profile_fingerprint=profile_fingerprint(row)))
        db.commit()
        return row.id


def suggestion(client, identifier, profile_id):
    return client.post(PREFIX + "/suggestions", json={"baseline_version_id": BASELINE_ID, "profile_id": profile_id,
        "analysis_ids": [identifier], "internal_data_confirmed": True})


def test_admin_only_routes_and_no_cache(client, service_headers):
    assert client.get(PREFIX).status_code == 401
    assert client.get(PREFIX, headers=service_headers).status_code == 403
    login(client)
    response = client.get(PREFIX)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["active_version_id"] == BASELINE_ID


def test_version_is_immutable_no_direct_activation(client):
    login(client)
    created = candidate(client)
    assert created["description"] == "private-note-fixture"
    listed = client.get(PREFIX).json()
    assert "private-note-fixture" not in json.dumps(listed)
    assert listed["active_version_id"] == BASELINE_ID
    assert client.patch(PREFIX + "/" + created["id"], json={"name": "replace"}).status_code == 405
    assert client.post(PREFIX + "/" + created["id"] + "/activate").json()["detail"] == "production_promotion_required"
    with client.app.state.session_factory() as db:
        from app.models import ParserVersion
        assert "private-note-fixture" not in db.get(ParserVersion, created["id"]).description_ciphertext


@pytest.mark.parametrize("changes", [{"engine_version": "python"}, {"options": {"regex": ".*"}},
    {"script": "print('fixture')"}, {"options": {"line_endings": "actual", "request_line_whitespace": "strict", "skip_leading_blank_lines": "false"}}])
def test_executable_or_untyped_options_rejected(client, changes):
    login(client)
    assert client.post(PREFIX, json={"name": "Invalid", **changes}).status_code == 422


def test_comparison_preserves_raw_audits_and_encrypts(client):
    login(client)
    identifier, version = analysis(client), candidate(client)
    response = client.post(PREFIX + "/comparisons", json={"baseline_version_id": BASELINE_ID,
        "candidate_version_id": version["id"], "analysis_ids": [identifier, identifier]})
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["kind"] == "comparison" and job["status"] == "completed"
    item = job["result"]["items"][0]
    assert item["raw_payload"] == RAW
    assert item["candidate"]["parse_status"] == "partial"
    assert item["candidate"]["method"] == "GET"
    assert "capture_serialization_unverified" in item["candidate"]["warnings"]
    assert item["baseline"]["parse_status"] != "success"
    assert item["valid_source_spans"] is True
    assert job["summary"]["sample_count"] == 1
    assert "private-fixture" not in client.get(PREFIX + "/jobs").text
    reviewed = client.post(PREFIX + "/jobs/" + job["id"] + "/review", json={"confirmed": True})
    assert reviewed.status_code == 200
    assert reviewed.json()["summary"]["structure_reviewed"] is True
    assert client.get(PREFIX).json()["active_version_id"] == BASELINE_ID
    with client.app.state.session_factory() as db:
        row = db.get(ParserJob, job["id"])
        assert RAW not in row.request_ciphertext and "private-fixture" not in row.result_ciphertext
        audits = list(db.scalars(select(AccessAudit.action)))
        assert "read_raw_for_parser" in audits and "view_parser_job" in audits and "review_parser_comparison" in audits


def test_selection_and_bytes_fail_closed(client):
    login(client)
    version = candidate(client)
    base = {"baseline_version_id": BASELINE_ID, "candidate_version_id": version["id"]}
    assert client.post(PREFIX + "/comparisons", json=base).status_code == 422
    assert client.post(PREFIX + "/comparisons", json={**base, "analysis_ids": ["missing"]}).status_code == 404
    huge = analysis(client, "x" * (65536 + 1))
    response = client.post(PREFIX + "/comparisons", json={**base, "analysis_ids": [huge]})
    assert response.status_code == 413
    assert response.json()["detail"] == "parser_source_size_limit"


def test_suggestion_requires_explicit_confirmation_and_internal_profile(client, registered_vllm_target):
    login(client)
    identifier = analysis(client)
    external = profile(client, "openai")
    assert suggestion(client, identifier, external).json()["detail"] == "parser_internal_vllm_required"
    assert client.post(PREFIX + "/suggestions", json={"baseline_version_id": BASELINE_ID,
        "profile_id": external, "analysis_ids": [identifier]}).status_code == 422


def test_suggestion_queued_not_called_by_api_and_raw_excluded_from_list(client, registered_vllm_target, monkeypatch):
    login(client)
    identifier, profile_id = analysis(client), profile(client)
    async def forbidden(*args, **kwargs):
        pytest.fail("API must not call a model")
    monkeypatch.setattr(parser_jobs, "request_suggestion", forbidden)
    response = suggestion(client, identifier, profile_id)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"
    assert "private-fixture" not in response.text
    assert "private-fixture" not in client.get(PREFIX + "/jobs").text
    review = client.post(PREFIX + "/jobs/" + response.json()["id"] + "/review", json={"confirmed": True})
    assert review.status_code == 409


def test_suggestion_worker_mocked_result_encrypted_no_autoapply(client, registered_vllm_target, monkeypatch, settings):
    login(client)
    identifier, profile_id = analysis(client), profile(client)
    job = suggestion(client, identifier, profile_id).json()
    calls = []
    async def fake(profile, key, text, check):
        check()
        calls.append(json.loads(text))
        return ParserSuggestionOutput(engine_version="http-capture-v1", options={"line_endings": "escaped_crlf"},
            reason_ko="직렬화된 줄바꿈이 확인됩니다.", limitations_ko=["원문 경계와 여러 형식의 혼합 여부는 직접 확인해야 합니다."])
    monkeypatch.setattr(parser_jobs, "request_suggestion", fake)
    assert asyncio.run(parser_jobs.process_pending_parser_job(settings, client.app.state.engine, client.app.state.crypto)) is True
    assert asyncio.run(parser_jobs.process_pending_parser_job(settings, client.app.state.engine, client.app.state.crypto)) is False
    detail = client.get(PREFIX + "/jobs/" + job["id"]).json()
    assert detail["status"] == "completed" and detail["result"]["automatically_applied"] is False
    assert calls[0]["samples"][0]["raw_payload"] == RAW
    assert "expected_verdict" not in json.dumps(calls)
    assert client.get(PREFIX).json()["active_version_id"] == BASELINE_ID
    assert len(client.get(PREFIX).json()["items"]) == 1


def test_profile_revoked_before_dispatch_fails_without_call(client, registered_vllm_target, monkeypatch, settings):
    login(client)
    identifier, profile_id = analysis(client), profile(client)
    job = suggestion(client, identifier, profile_id).json()
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).status = "disabled"
        db.commit()
    async def forbidden(*args, **kwargs):
        pytest.fail("Revoked model must not receive data")
    monkeypatch.setattr(parser_jobs, "request_suggestion", forbidden)
    asyncio.run(parser_jobs.process_pending_parser_job(settings, client.app.state.engine, client.app.state.crypto))
    detail = client.get(PREFIX + "/jobs/" + job["id"]).json()
    assert detail["status"] == "failed" and detail["error_code"] == "parser_verified_profile_required"


def test_expired_job_reclaimed_and_live_owner_not_overwritten(client, registered_vllm_target):
    login(client)
    job = suggestion(client, analysis(client), profile(client)).json()
    engine = client.app.state.engine
    assert parser_jobs.claim_job(engine, "first", 60) == job["id"]
    assert parser_jobs.claim_job(engine, "second", 60) is None
    with client.app.state.session_factory() as db:
        db.get(ParserJob, job["id"]).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert parser_jobs.claim_job(engine, "second", 60) == job["id"]
    with pytest.raises(ValueError, match="parser_job_lease_lost"):
        parser_jobs.request_check(engine, job["id"], "first", job["profile_id"], None)()


@pytest.mark.parametrize("output", [
    {"engine_version": "python", "options": {}, "reason_ko": "fixture", "limitations_ko": []},
    {"engine_version": "http-capture-v1", "options": {"regex": ".*"}, "reason_ko": "fixture", "limitations_ko": []},
    {"engine_version": "http-capture-v1", "options": {}, "reason_ko": "fixture", "limitations_ko": [], "script": "fixture"},
])
def test_model_cannot_add_executable_fields(output):
    with pytest.raises(ValueError):
        ParserSuggestionOutput.model_validate(output)


def test_failure_diagnostics_never_echo_provider_text():
    assert parser_jobs.safe_failure(ValueError("private-fixture")) == "parser_suggestion_invalid_output"
    assert parser_jobs.safe_failure(RuntimeError("private-fixture")) == "parser_suggestion_failed"
    assert "private-fixture" not in parser_jobs.safe_failure(httpx.ConnectError("private-fixture"))


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_confirmations_are_explicit_booleans(client, value):
    login(client)
    response = client.post(PREFIX + "/suggestions", json={"baseline_version_id": BASELINE_ID,
        "profile_id": "fixture", "analysis_ids": ["fixture"], "internal_data_confirmed": value})
    assert response.status_code == 422
    assert client.post(PREFIX + "/jobs/fixture/review", json={"confirmed": value}).status_code == 422


def test_test_run_selection_is_bounded_and_explicitly_reported(client, event_payload):
    from test_test_runs import upload
    login(client)
    run = upload(client, [{**event_payload, "event_id": f"fixture-{index}", "payload": RAW} for index in range(3)])
    version = candidate(client)
    response = client.post(PREFIX + "/comparisons", json={"baseline_version_id": BASELINE_ID,
        "candidate_version_id": version["id"], "test_run_id": run["id"], "max_cases": 2})
    assert response.status_code == 201, response.text
    summary = response.json()["summary"]
    assert summary["source_total"] == 3 and summary["sample_count"] == 2 and summary["selection_limited"] is True
    assert len(response.json()["result"]["items"]) == 2


@pytest.mark.parametrize("provider_body,error", [
    ({"choices": [{"finish_reason": "length", "message": {"content": "private-fixture"}}]}, "vllm_output_incomplete"),
    ({"choices": [{"finish_reason": "stop", "message": {"content": "private-fixture", "refusal": "private-fixture"}}]}, "vllm_refusal"),
    ({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}, "parser_suggestion_invalid_output"),
])
def test_transport_rejects_incomplete_refused_and_invalid_output(monkeypatch, provider_body, error):
    from types import SimpleNamespace
    original_client = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=provider_body)
    def mock_client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False and kwargs["verify"] is True
        return original_client(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(parser_jobs.httpx, "AsyncClient", mock_client)
    profile_value = SimpleNamespace(model_name="fixture", provider="vllm", max_output_tokens=2048,
                                    base_url="http://10.0.0.10:8000/v1", timeout_seconds=5)
    checks = []
    with pytest.raises(Exception) as caught:
        asyncio.run(parser_jobs.request_suggestion(profile_value, None, "synthetic", lambda: checks.append(True)))
    assert parser_jobs.safe_failure(caught.value) == error
    assert checks == [True]
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert calls[0]["max_tokens"] == 1536
    schema = calls[0]["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_worker_invalid_result_is_fixed_error_not_provider_text(client, registered_vllm_target, monkeypatch, settings):
    login(client)
    job = suggestion(client, analysis(client), profile(client)).json()
    async def fake(*args, **kwargs):
        raise ValueError("private-fixture")
    monkeypatch.setattr(parser_jobs, "request_suggestion", fake)
    asyncio.run(parser_jobs.process_pending_parser_job(settings, client.app.state.engine, client.app.state.crypto))
    response = client.get(PREFIX + "/jobs/" + job["id"])
    assert response.json()["error_code"] == "parser_suggestion_invalid_output"
    assert "private-fixture" not in response.text


def test_structure_review_rejects_all_failed_candidate(client):
    login(client)
    version = candidate(client)
    response = client.post(PREFIX + "/comparisons", json={"baseline_version_id": BASELINE_ID,
        "candidate_version_id": version["id"], "analysis_ids": [analysis(client, "not a request")]})
    assert response.status_code == 201
    assert client.post(PREFIX + "/jobs/" + response.json()["id"] + "/review", json={"confirmed": True}).status_code == 409


def test_list_does_not_hydrate_encrypted_source_or_result(client, monkeypatch):
    from sqlalchemy import event
    login(client)
    version = candidate(client)
    response = client.post(PREFIX + "/comparisons", json={"baseline_version_id": BASELINE_ID,
        "candidate_version_id": version["id"], "analysis_ids": [analysis(client)]})
    assert response.status_code == 201
    columns = []
    def inspect(_connection, cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and "parser_jobs" in statement:
            columns.extend(item[0] for item in cursor.description or [])
    event.listen(client.app.state.engine, "after_cursor_execute", inspect)
    try:
        assert client.get(PREFIX + "/jobs").status_code == 200
    finally:
        event.remove(client.app.state.engine, "after_cursor_execute", inspect)
    assert "request_ciphertext" not in columns and "result_ciphertext" not in columns


def capture_profile(name, vendor, mode="actual", **changes):
    return {"name": name, "waf_vendor": vendor, "line_endings": mode,
            "request_line_whitespace": "strict", "skip_leading_blank_lines": False, **changes}


def test_unconfigured_profiles_do_not_change_old_serialized_contract():
    from app.parser_schemas import ParserOptions, ParserVersionCreate
    from app.services.parser_engine import DEFAULT_OPTIONS
    assert ParserOptions().model_dump() == DEFAULT_OPTIONS
    assert ParserOptions(profiles=None).model_dump(mode="json") == DEFAULT_OPTIONS
    assert ParserVersionCreate(name="Fixture").model_dump(mode="json")["options"] == DEFAULT_OPTIONS


@pytest.mark.parametrize("profiles", [
    [capture_profile("Same", "Vendor A"), capture_profile("Same too", " vendor a ")],
    [capture_profile("Invalid", "", "actual")],
    [capture_profile("Unsafe", "Vendor A", regex=".*")],
    [capture_profile(str(index), f"Vendor {index}") for index in range(21)],
])
def test_vendor_profiles_are_typed_bounded_and_unambiguous(client, profiles):
    login(client)
    from app.services.parser_engine import DEFAULT_OPTIONS
    response = client.post(PREFIX, json={"name": "Invalid bundle", "options": {**DEFAULT_OPTIONS, "profiles": profiles}})
    assert response.status_code == 422


def test_suggestion_cannot_define_vendor_profiles():
    with pytest.raises(ValueError):
        ParserSuggestionOutput.model_validate({"engine_version": "http-capture-v1", "options": {
            "line_endings": "actual", "request_line_whitespace": "strict", "skip_leading_blank_lines": False,
            "profiles": [capture_profile("Unreviewed", "Vendor A")]}, "reason_ko": "fixture", "limitations_ko": []})


def test_multi_vendor_format_profiles_are_compared_on_frozen_metadata(client):
    login(client)
    from app.services.parser_engine import DEFAULT_OPTIONS
    profiles = [capture_profile("A native", "Vendor A", request_line_whitespace="flexible"),
                capture_profile("A serialized", "Vendor A", "escaped_crlf"),
                capture_profile("B leading blank", "Vendor B", skip_leading_blank_lines=True)]
    response = client.post(PREFIX, json={"name": "Vendor capture bundle", "options": {**DEFAULT_OPTIONS, "profiles": profiles}})
    assert response.status_code == 201, response.text
    version = response.json()
    assert version["options"]["profiles"] == profiles
    raw_a = "GET\t\t/a HTTP/1.1\r\nHost: example.internal\r\n\r\n"
    raw_b = "\r\nGET /b HTTP/1.1\r\nHost: example.internal\r\n\r\n"
    ids = [analysis(client, raw_a, vendor="vendor a"), analysis(client, RAW, vendor="Vendor A"),
           analysis(client, raw_b, vendor="Vendor B"), analysis(client, raw_a, vendor="Unknown vendor")]
    response = client.post(PREFIX + "/comparisons", json={"baseline_version_id": BASELINE_ID,
        "candidate_version_id": version["id"], "analysis_ids": ids})
    assert response.status_code == 201, response.text
    job = response.json()
    items = job["result"]["items"]
    assert [item["waf_vendor"] for item in items] == ["vendor a", "Vendor A", "Vendor B", "Unknown vendor"]
    assert [item["candidate"]["capture_selection"]["status"] for item in items] == ["matched", "matched", "matched", "generic_fallback"]
    assert [item["candidate"]["capture_selection"].get("profile_name") for item in items[:3]] == ["A native", "A serialized", "B leading blank"]
    assert [item["candidate"]["parse_status"] for item in items] == ["success", "partial", "success", "failed"]
    assert items[-1]["candidate"]["method"] == items[-1]["baseline"]["method"] is None
    assert job["summary"]["all_valid_source_spans"] is True
    assert client.get(PREFIX).json()["active_version_id"] == BASELINE_ID


def test_payload_profiles_do_not_extract_a_vendor_envelope(client):
    login(client)
    version = candidate(client)
    raw = json.dumps({"request": RAW, "vendor": "Fixture"})
    response = client.post(PREFIX + "/comparisons", json={"baseline_version_id": BASELINE_ID,
        "candidate_version_id": version["id"], "analysis_ids": [analysis(client, raw)]})
    assert response.status_code == 201
    item = response.json()["result"]["items"][0]
    assert item["raw_payload"] == raw
    assert item["candidate"]["parse_status"] == "failed"
    assert item["candidate"]["method"] is None


def test_active_parser_remains_available_outside_recent_versions(client):
    from app.services.parser_versions import create_version
    login(client)
    assert client.get(PREFIX).json()["active_version"]["id"] == BASELINE_ID
    with client.app.state.session_factory() as db:
        for index in range(201):
            create_version(db, client.app.state.crypto, name=f"Synthetic saved version {index}")
        db.commit()
    response = client.get(PREFIX)
    assert response.status_code == 200
    document = response.json()
    assert document["total"] == 202 and len(document["items"]) == document["recent_limit"] == 200
    assert document["limited"] is True
    assert BASELINE_ID not in {item["id"] for item in document["items"]}
    assert document["active_version"]["id"] == document["active_version_id"] == BASELINE_ID


def test_job_history_has_real_server_pagination(client):
    login(client)
    version = candidate(client)
    ids = []
    for _ in range(3):
        response = client.post(PREFIX + "/comparisons", json={"baseline_version_id": BASELINE_ID,
            "candidate_version_id": version["id"], "analysis_ids": [analysis(client)]})
        assert response.status_code == 201
        ids.append(response.json()["id"])
    first = client.get(PREFIX + "/jobs", params={"limit": 2, "offset": 0}).json()
    second = client.get(PREFIX + "/jobs", params={"limit": 2, "offset": 2}).json()
    assert first["total"] == second["total"] == 3
    assert first["limit"] == second["limit"] == 2 and second["offset"] == 2
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert {item["id"] for item in first["items"] + second["items"]} == set(ids)
