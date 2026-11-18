"""Synthetic parser workbench coverage. No real model, raw logs or network."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.models import (AccessAudit, AgentRun, AgentStep, Analysis, ParserConfiguration, ParserWorkbench, ParserWorkbenchJob,
    ValidationDataset, ValidationDatasetItem, ValidationDatasetVersion, VLLMProfile, VLLMTestRun, utcnow)
from app.parser_rule_schemas import RuleProposalV1
from app.services import parser_workbench as service, parser_workbench_jobs as jobs
from app.services.parser_versions import BASELINE_ID, ParserVersionError
from app.services.vllm_profiles import profile_fingerprint

PREFIX = "/api/v1/admin/parser-workbench"


def proposal():
    return {"schema_version": "parser-rule-proposal-v1", "name": "JSON request",
        "match": {"all": [{"kind": "json_string_exists", "json_pointer": "/request"}]},
        "extract": {"kind": "json_string", "json_pointer": "/request"},
        "capture": {"line_endings": "actual", "request_line_whitespace": "strict", "skip_leading_blank_lines": False},
        "reason_ko": "request 키에서 HTTP 문자열을 읽습니다.", "limitations_ko": "한 요청 문자열만 지원합니다."}


def raw(path):
    return json.dumps({"request": f"GET {path}?q=synthetic HTTP/1.1\r\nHost: example.invalid\r\nX-Test: first\r\nX-Test: second\r\n\r\n"})


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def sources(client, paths=("/alpha", "/beta", "/gamma", "/delta"), *, purpose="test"):
    identifiers = []
    with client.app.state.session_factory() as db:
        for path in paths:
            row = Analysis(source_system="synthetic-workbench", event_id=str(uuid.uuid4()), company_name="Synthetic",
                src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="SyntheticVendor", waf_action="D",
                analysis_purpose=purpose, payload_ciphertext=client.app.state.crypto.encrypt_text(raw(path)),
                encryption_key_version="test")
            db.add(row)
            db.flush()
            identifiers.append(row.id)
        db.commit()
    return identifiers


def profile(client, provider="vllm"):
    with client.app.state.session_factory() as db:
        row = VLLMProfile(name="Synthetic workbench", provider=provider, base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="verified", timeout_seconds=5, context_window=32768, max_output_tokens=1536)
        db.add(row)
        db.flush()
        db.add(VLLMTestRun(profile_id=row.id, mode="full", status="passed", profile_fingerprint=profile_fingerprint(row)))
        db.commit()
        return row.id


def create(client, identifiers=None):
    response = client.post(PREFIX + "/sessions", json={"name": "Synthetic parser review",
        "source": {"analysis_ids": identifiers or sources(client)}})
    assert response.status_code == 201, response.text
    return response.json()


def discovery_stub(calls):
    async def complete(profile, key, text, instructions, output_model, check):
        check()
        document = json.loads(text)
        calls.append(document)
        if output_model is jobs.DiscoveryOutput:
            return jobs.DiscoveryOutput(groups=[{"group_id": "format1", "name": "JSON request", "waf_vendor": "SyntheticVendor",
                "case_ids": [sample["case_id"] for sample in document["samples"]]}])
        return RuleProposalV1.model_validate(proposal())
    return complete


def run(client, settings):
    return asyncio.run(jobs.process_pending_workbench_job(settings, client.app.state.engine, client.app.state.crypto))


def generated(client, settings, monkeypatch, profile_id):
    session = create(client)
    calls = []
    monkeypatch.setattr(jobs, "request_completion", discovery_stub(calls))
    job = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"],
        "profile_id": profile_id, "internal_data_confirmed": True})
    assert job.status_code == 202, job.text
    assert run(client, settings)
    session = client.get(PREFIX + f"/sessions/{session['id']}").json()
    assert session["status"] == "discovered"
    groups = [{key: group[key] for key in ("group_id", "name", "waf_vendor", "case_ids")} for group in session["groups"]]
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={"expected_revision": session["revision"], "groups": groups})
    assert response.status_code == 200, response.text
    session = response.json()
    response = client.post(PREFIX + f"/sessions/{session['id']}/generate", json={"expected_revision": session["revision"],
        "profile_id": profile_id, "internal_data_confirmed": True})
    assert response.status_code == 202, response.text
    assert run(client, settings)
    session = client.get(PREFIX + f"/sessions/{session['id']}").json()
    assert session["status"] == "generated", session["jobs"]
    return session, calls


def expectations(client, session, *, wrong=False, only_unknown=False):
    items = []
    for case in session["cases"]:
        if case.get("heldout_hidden"):
            continue
        request = json.loads(case["raw_payload"])["request"]
        path = request.split(" ")[1].split("?")[0]
        fields = {"method": {"state": "unknown"} if only_unknown else {"state": "present", "value": "GET"},
                  "uri": {"state": "unknown"} if only_unknown else {"state": "present", "value": "/wrong" if wrong else path}}
        items.append({"case_id": case["case_id"], "fields": fields})
    response = client.put(PREFIX + f"/sessions/{session['id']}/expectations", json={"expected_revision": session["revision"], "items": items})
    assert response.status_code == 200, response.text
    return response.json()


def validate(client, session):
    response = client.post(PREFIX + f"/sessions/{session['id']}/validate", json={"expected_revision": session["revision"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_only_no_store_and_safe_catalog(client, service_headers):
    assert client.get(PREFIX + "/catalog").status_code == 401
    assert client.get(PREFIX + "/catalog", headers=service_headers).status_code == 403
    login(client)
    response = client.get(PREFIX + "/catalog")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["active_version_id"] == BASELINE_ID
    assert response.json()["limits"]["max_cases"] == 100


def test_sources_frozen_encrypted_audited_and_production_internal_only(client):
    login(client)
    identifiers = sources(client, purpose="production")
    session = create(client, identifiers)
    assert all(case["internal_only"] for case in session["cases"])
    assert session["split_summary"]["authoring_count"] and session["split_summary"]["holdout_count"]
    with client.app.state.session_factory() as db:
        row = db.get(ParserWorkbench, session["id"])
        text = row.input_ciphertext + row.state_ciphertext + json.dumps(row.metadata_json)
        assert "example.invalid" not in text and "request" not in row.input_ciphertext
        db.get(Analysis, identifiers[0]).payload_ciphertext = client.app.state.crypto.encrypt_text("changed")
        db.commit()
    again = client.get(PREFIX + f"/sessions/{session['id']}").json()
    assert all("raw_payload" not in case and "baseline" not in case for case in again["cases"] if case["role"] == "holdout")
    assert all(case["raw_payload"] != "changed" for case in again["cases"] if case["role"] == "authoring")
    with client.app.state.session_factory() as db:
        actions = list(db.scalars(select(AccessAudit.action)))
        assert "read_raw_for_parser" in actions and "view_parser_workbench_raw" in actions


def test_family_split_has_no_near_duplicate_leakage_and_keeps_json_keys():
    one = raw("/item/123")
    two = raw("/item/456").replace("synthetic", "different")
    assert service._family(one, "V") == service._family(two, "V")
    assert service._family(raw("/one"), "V") != service._family(raw("/two"), "V")
    assert service._family(one, "V") != service._family(one.replace('"request":', '"other":'), "V")
    cases = [{"case_id": str(index), "raw_payload": value, "waf_vendor": "V", "internal_only": False}
             for index, value in enumerate([one, two, raw("/another")])]
    service.split_cases(cases)
    assert cases[0]["role"] == cases[1]["role"]
    with pytest.raises(ParserVersionError, match="distinct_families"):
        service.split_cases(cases[:2])


def test_pipeline_never_sends_holdout_labels_or_expectations_and_saves_candidate_only(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, calls = generated(client, settings, monkeypatch, profile(client))
    holdout_ids = {case["case_id"] for case in session["cases"] if case["role"] == "holdout"}
    serialized = json.dumps(calls)
    assert all(identifier not in serialized for identifier in holdout_ids)
    assert not any(key in serialized for key in ("reference_verdict", "expected_verdict", "expectations", "review", "verdict"))
    assert all(sample["case_id"].startswith("c") and len(sample["case_id"]) == 4 for call in calls for sample in call["samples"])
    assert all(len(identifier) == 36 for group in session["groups"] for identifier in group["case_ids"])
    assert session["groups_confirmed"] is True
    assert session["holdout_revealed"] is False
    session = validate(client, session)
    assert session["rules_locked"] and session["holdout_revealed"]
    frozen_revision = session["revision"]
    blocked = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": frozen_revision,
        "profile_id": session["jobs"][0]["profile_id"], "internal_data_confirmed": True})
    assert blocked.status_code == 409 and blocked.json()["detail"] == "parser_holdout_rules_locked"
    blocked = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={"expected_revision": frozen_revision,
        "groups": [{key: group[key] for key in ("group_id", "name", "waf_vendor", "case_ids", "rule")} for group in session["groups"]]})
    assert blocked.status_code == 409 and blocked.json()["detail"] == "parser_holdout_rules_locked"
    session = expectations(client, session)
    session = validate(client, session)
    assert session["validation_summary"]["ready"] is True, session["validation_summary"]
    response = client.post(PREFIX + f"/sessions/{session['id']}/save-candidate", json={"expected_revision": session["revision"], "name": "Reviewed candidate"})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["status"] == "saved" and saved["candidate_version_id"]
    with client.app.state.session_factory() as db:
        assert db.get(ParserConfiguration, 1).active_version_id == BASELINE_ID
        assert service.workbench_parser_review_ready(db, saved["candidate_version_id"], BASELINE_ID)
    assert client.get(PREFIX + "/catalog").json()["rules"][0]["method"] == "json_string"
    # Changing reviewed expectations invalidates eligibility, not the old version.
    edited = expectations(client, saved, wrong=True)
    with client.app.state.session_factory() as db:
        assert not service.workbench_parser_review_ready(db, saved["candidate_version_id"], BASELINE_ID)
    assert edited["validation_summary"] is None


@pytest.mark.parametrize("unknown", [True, False])
def test_wrong_or_unknown_expectations_never_qualify(client, registered_vllm_target, settings, monkeypatch, unknown):
    login(client)
    session, _ = generated(client, settings, monkeypatch, profile(client))
    session = validate(client, session)
    session = validate(client, expectations(client, session, wrong=not unknown, only_unknown=unknown))
    summary = session["validation_summary"]
    assert summary["ready"] is False
    assert summary["field_total"] == (0 if unknown else len(session["cases"]) * 2)
    response = client.post(PREFIX + f"/sessions/{session['id']}/save-candidate", json={"expected_revision": session["revision"], "name": "Not reviewed"})
    assert response.status_code == 409 and response.json()["detail"] == "parser_workbench_validation_required"


def test_optimistic_lock_busy_and_group_confirmation(client, registered_vllm_target):
    login(client)
    session, pid = create(client), profile(client)
    stale = client.put(PREFIX + f"/sessions/{session['id']}/expectations", json={"expected_revision": 99, "items": []})
    assert stale.status_code == 409
    direct = client.post(PREFIX + f"/sessions/{session['id']}/generate", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    assert direct.status_code == 409 and direct.json()["detail"] == "parser_group_confirmation_required"
    job = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True}).json()
    blocked = client.put(PREFIX + f"/sessions/{session['id']}/expectations", json={"expected_revision": job["session_revision"], "items": []})
    assert blocked.status_code == 409 and blocked.json()["detail"] == "parser_workbench_job_in_progress"


def test_holdout_cannot_be_used_in_discovered_group():
    cases = [{"case_id": "a", "waf_vendor": "V", "role": "authoring"}, {"case_id": "h", "waf_vendor": "V", "role": "holdout"}]
    with pytest.raises(ParserVersionError, match="membership_invalid"):
        service.validate_groups([{"group_id": "g", "name": "G", "waf_vendor": "V", "case_ids": ["a", "h"]}], cases)


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_confirmation_is_explicit_boolean(client, value):
    login(client)
    session = create(client)
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"],
        "profile_id": "missing", "internal_data_confirmed": value})
    assert response.status_code == 422


def test_external_provider_is_never_accepted(client, registered_vllm_target):
    login(client)
    session = create(client)
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"],
        "profile_id": profile(client, "openai"), "internal_data_confirmed": True})
    assert response.status_code == 409 and response.json()["detail"] == "parser_internal_vllm_required"


def test_profile_revocation_and_expired_lease_fail_closed(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, pid = create(client), profile(client)
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    job = response.json()
    assert jobs._claim(client.app.state.engine, "old-owner", 60) == job["id"]
    assert jobs._claim(client.app.state.engine, "other-owner", 60) is None
    with client.app.state.session_factory() as db:
        db.get(ParserWorkbenchJob, job["id"]).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.get(VLLMProfile, pid).status = "disabled"
        db.commit()
    async def forbidden(*args):
        pytest.fail("Revoked profile must never receive a payload")
    monkeypatch.setattr(jobs, "request_completion", forbidden)
    assert run(client, settings)
    detail = client.get(PREFIX + f"/sessions/{session['id']}/jobs").json()["items"][0]
    assert detail["status"] == "failed" and detail["error_code"] == "parser_verified_profile_required"


def test_invalid_model_output_repair_budget_and_safe_failure(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, pid = create(client), profile(client)
    client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    calls = []
    async def invalid(*args):
        calls.append(True)
        raise ValueError("synthetic-private-model-output")
    monkeypatch.setattr(jobs, "request_completion", invalid)
    assert run(client, settings)
    detail = client.get(PREFIX + f"/sessions/{session['id']}/jobs").json()["items"][0]
    assert len(calls) == 2 and detail["summary"]["call_count"] == 2
    assert detail["error_code"] == "parser_workbench_schema_invalid"
    assert "synthetic-private-model-output" not in json.dumps(detail)


def test_duplicate_headers_and_query_expectations_are_preserved():
    parsed = {"headers": {"x-test": ["one", "two"]}, "query": [("q", "one"), ("q", "two")]}
    pairs = [{"name": "q", "value": "one"}, {"name": "q", "value": "two"}]
    assert service.field_value(parsed, "query") == pairs
    assert len(service.field_value(parsed, "headers")) == 2
    compared = service._compare_fields(parsed, {"query": {"state": "present", "value": pairs}})
    assert compared[0]["status"] == "match"


def test_transport_is_internal_strict_bounded_and_no_duplicate_json(monkeypatch):
    original = httpx.AsyncClient
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"groups":[],"groups":[]}'}}]})
    def mock_client(**kwargs):
        assert kwargs == {"verify": True, "follow_redirects": False, "trust_env": False, "timeout": 5}
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(jobs.httpx, "AsyncClient", mock_client)
    configured = SimpleNamespace(context_window=32768, model_name="synthetic", provider="vllm", max_output_tokens=1536,
        base_url="http://10.0.0.10:8000/v1", timeout_seconds=5)
    with pytest.raises(ValueError):
        asyncio.run(jobs.request_completion(configured, None, "{}", jobs.DISCOVERY_INSTRUCTIONS, jobs.DiscoveryOutput, lambda: None))
    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert bodies[0]["max_tokens"] == 1536
    assert bodies[0]["response_format"]["json_schema"]["schema"]["additionalProperties"] is False


def test_dataset_revision_is_exact_and_never_copies_reference_fields(client):
    login(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        dataset = ValidationDataset(name="Synthetic frozen data", revision=2, created_by="admin")
        db.add(dataset)
        db.flush()
        items = []
        for index, path in enumerate(("/old-alpha", "/old-beta", "/old-gamma")):
            item = ValidationDatasetItem(dataset_id=dataset.id, item_id=str(uuid.uuid4()), revision=1,
                event_ciphertext=crypto.encrypt_text(json.dumps({"payload": raw(path), "waf_vendor": "SyntheticVendor",
                    "expected_verdict": "false_positive", "signature": "sensitive-signature-synthetic"})),
                schema_snapshot_ciphertext=crypto.encrypt_text("{}"), encryption_key_version="test", input_hash=str(index),
                reference_verdict="false_positive", review_status="draft", internal_only=index == 1, created_by="admin")
            db.add(item)
            db.flush()
            items.append(item.id)
        version = ValidationDatasetVersion(dataset_id=dataset.id, revision=1, name="Frozen revision one", description="",
            item_version_ids=items, created_by="admin")
        latest = ValidationDatasetVersion(dataset_id=dataset.id, revision=2, name="Unselected current revision", description="",
            item_version_ids=items[:1], created_by="admin")
        db.add_all([version, latest])
        db.commit()
        version_id = version.id
    response = client.post(PREFIX + "/sessions", json={"source": {"dataset_version_id": version_id}})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["source_summary"]["dataset_revision"] == 1
    assert len(result["cases"]) == 3 and result["split_summary"]["internal_only_count"] == 1
    with client.app.state.session_factory() as db:
        frozen, _ = service.documents(db.get(ParserWorkbench, result["id"]), crypto)
    content = json.dumps(frozen)
    assert "expected_verdict" not in content and "reference_verdict" not in content and "signature" not in content


def test_test_source_freezes_selected_latest_items_and_reports_limit(client, event_payload):
    from test_test_runs import upload
    login(client)
    run = upload(client, [{**event_payload, "event_id": f"workbench-{index}", "payload": raw(path),
                          "waf_vendor": "SyntheticVendor"} for index, path in enumerate(("/one", "/two", "/three"))])
    response = client.post(PREFIX + "/sessions", json={"source": {"test_run_id": run["id"]}, "max_cases": 2})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["source_summary"]["test_run_id"] == run["id"]
    assert result["source_summary"]["selection_limited"] is True
    assert len(result["cases"]) == 2


def test_group_specific_holdout_and_same_case_minimum_fields_required(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, _ = generated(client, settings, monkeypatch, profile(client))
    # Introduce a second proposal that never applies to any unseen sample. It
    # must not inherit another rule's global method/path review coverage.
    groups = [{key: group[key] for key in ("group_id", "name", "waf_vendor", "case_ids", "rule")} for group in session["groups"]]
    members = groups[0]["case_ids"]
    assert len(members) >= 2
    other = deepcopy(groups[0])
    other.update(group_id="format2", name="Missing format", case_ids=[members[-1]])
    groups[0]["case_ids"] = members[:-1]
    other["rule"]["match"] = {"all": [{"kind": "starts_with", "value": "missing-format:"}]}
    groups.append(other)
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={"expected_revision": session["revision"], "groups": groups})
    assert response.status_code == 200, response.text
    session = validate(client, response.json())
    session = validate(client, expectations(client, session))
    assert session["validation_summary"]["holdout_rule_count"] == 1
    assert session["validation_summary"]["required_rule_count"] == 2
    assert session["validation_summary"]["ready"] is False


def test_manual_rule_changes_cannot_remove_existing_vendor_rules(client, registered_vllm_target, settings, monkeypatch):
    from app.services.parser_rule_versions import create_rule_bundle_version, read_rule_bundle_definition
    login(client)
    crypto = client.app.state.crypto
    old_rule = {"rule_id": str(uuid.uuid4()), "rule_version": 1, "waf_vendor": "OtherVendor", "definition": proposal()}
    with client.app.state.session_factory() as db:
        version = create_rule_bundle_version(db, crypto, {"schema_version": "vendor-parser-bundle-v1",
            "selection_policy_version": "vendor-rule-selection-v1", "rules": [old_rule]}, name="Existing other vendor")
        db.commit()
        baseline_id = version.id
    response = client.post(PREFIX + "/sessions", json={"source": {"analysis_ids": sources(client)}, "baseline_version_id": baseline_id})
    assert response.status_code == 201, response.text
    with client.app.state.session_factory() as db:
        baseline = db.get(type(version), baseline_id)
        state = {"groups": [{"group_id": "new", "rule_id": str(uuid.uuid4()), "name": "New", "waf_vendor": "SyntheticVendor", "rule": proposal()}]}
        bundle = service.candidate_bundle(state, baseline, crypto)
        assert old_rule["rule_id"] in {rule["rule_id"] for rule in bundle["rules"]}
        assert len(bundle["rules"]) == 2


def test_source_size_and_single_family_refuse_without_model(client):
    login(client)
    ids = sources(client, ("/same/123", "/same/456"))
    response = client.post(PREFIX + "/sessions", json={"source": {"analysis_ids": ids}})
    assert response.status_code == 422 and response.json()["detail"] == "parser_split_requires_distinct_families"
    ids = sources(client)
    with client.app.state.session_factory() as db:
        db.get(Analysis, ids[0]).payload_ciphertext = client.app.state.crypto.encrypt_text("x" * 65537)
        db.commit()
    response = client.post(PREFIX + "/sessions", json={"source": {"analysis_ids": ids}})
    assert response.status_code == 413 and response.json()["detail"] == "parser_source_size_limit"


def test_expired_job_retains_global_call_budget(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, pid = create(client), profile(client)
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    identifier = response.json()["id"]
    assert jobs._claim(client.app.state.engine, "expired-owner", 60) == identifier
    with client.app.state.session_factory() as db:
        row = db.get(ParserWorkbenchJob, identifier)
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        row.metadata_json = {**row.metadata_json, "call_count": 2}
        db.commit()
    async def forbidden(*args):
        pytest.fail("Restart must not reset the persisted call budget")
    monkeypatch.setattr(jobs, "request_completion", forbidden)
    assert run(client, settings)
    detail = client.get(PREFIX + f"/sessions/{session['id']}/jobs").json()["items"][0]
    assert detail["status"] == "failed" and detail["error_code"] == "parser_workbench_attempt_limit"


@pytest.mark.parametrize("fields", [
    {"method": {"state": "present", "value": [{"name": "x", "value": "y"}]}},
    {"headers": {"state": "present", "value": "not-pairs"}},
    {"uri": {"state": "unknown", "value": "/invented"}},
    {"verdict": {"state": "present", "value": "false_positive"}},
])
def test_expected_fields_are_closed_and_typed(client, fields):
    login(client)
    session = create(client)
    response = client.put(PREFIX + f"/sessions/{session['id']}/expectations", json={"expected_revision": session["revision"],
        "items": [{"case_id": session["cases"][0]["case_id"], "fields": fields}]})
    assert response.status_code == 422


def test_preview_recommends_parser_failures_and_balances_vendor_without_raw(client):
    login(client)
    identifiers = sources(client)
    with client.app.state.session_factory() as db:
        for identifier, status in zip(identifiers, ("success", "failed", "partial")):
            run = AgentRun(analysis_id=identifier, status="completed")
            db.add(run)
            db.flush()
            db.add(AgentStep(run_id=run.id, sequence=1, step_type="parser", name="Parser", status="completed",
                metadata_json={"parse_status": status}, output_ciphertext="private-output-must-not-be-read"))
        db.commit()
    response = client.post(PREFIX + "/source-preview", json={"source": {"analysis_ids": identifiers}, "max_cases": 3,
        "limit": 2, "offset": 0})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["recommended_case_ids"] == [identifiers[1], identifiers[2], identifiers[0]]
    assert preview["recommendation"]["unknown_status_count"] == 1
    assert len(preview["items"]) == 2 and preview["total"] == 4
    assert not any(key in json.dumps(preview) for key in ("raw_payload", "payload", "private-output", "reference_verdict"))
    response = client.post(PREFIX + "/sessions", json={"source": {"analysis_ids": identifiers,
        "case_ids": [identifiers[2], identifiers[3]]}})
    assert response.status_code == 201, response.text
    session = response.json()
    assert len(session["cases"]) == 2 and session["source_summary"]["selection_strategy"] == "manual"
    with client.app.state.session_factory() as db:
        frozen, _ = service.documents(db.get(ParserWorkbench, session["id"]), client.app.state.crypto)
    assert {case["source_ref"] for case in frozen["cases"]} == {identifiers[2], identifiers[3]}


def test_manual_source_selection_rejects_outside_membership(client):
    login(client)
    identifiers = sources(client)
    response = client.post(PREFIX + "/sessions", json={"source": {"analysis_ids": identifiers[:2],
        "case_ids": [identifiers[0], identifiers[2]]}})
    assert response.status_code == 409 and response.json()["detail"] == "parser_source_membership_changed"


def test_explicit_rule_target_is_same_vendor_versioned_and_not_sent_to_model(client, registered_vllm_target, settings, monkeypatch):
    from app.models import ParserVersion
    from app.services.parser_rule_versions import create_rule_bundle_version, read_rule_bundle_definition
    login(client)
    crypto = client.app.state.crypto
    target = str(uuid.uuid4())
    with client.app.state.session_factory() as db:
        baseline = create_rule_bundle_version(db, crypto, {"schema_version": "vendor-parser-bundle-v1",
            "selection_policy_version": "vendor-rule-selection-v1", "rules": [{"rule_id": target, "rule_version": 5,
                "waf_vendor": "SyntheticVendor", "definition": proposal()}]}, name="Existing target")
        db.commit()
        baseline_id = baseline.id
    response = client.post(PREFIX + "/sessions", json={"source": {"analysis_ids": sources(client)}, "baseline_version_id": baseline_id})
    assert response.status_code == 201, response.text
    session, pid, calls = response.json(), profile(client), []
    monkeypatch.setattr(jobs, "request_completion", discovery_stub(calls))
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    assert response.status_code == 202
    assert run(client, settings)
    session = client.get(PREFIX + f"/sessions/{session['id']}").json()
    assert session["baseline_rules"][0]["rule_id"] == target
    group = {key: session["groups"][0][key] for key in ("group_id", "name", "waf_vendor", "case_ids")}
    group["target_rule_id"] = target
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={"expected_revision": session["revision"], "groups": [group]})
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["groups"][0]["rule_id"] == target and session["groups"][0]["rule_version"] == 6
    response = client.post(PREFIX + f"/sessions/{session['id']}/generate", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    assert response.status_code == 202
    assert run(client, settings)
    assert "target_rule_id" not in json.dumps(calls) and target not in json.dumps(calls)
    session = client.get(PREFIX + f"/sessions/{session['id']}").json()
    session = validate(client, session)
    session = validate(client, expectations(client, session))
    assert session["validation_summary"]["ready"] is True
    response = client.post(PREFIX + f"/sessions/{session['id']}/save-candidate", json={"expected_revision": session["revision"], "name": "Target revision six"})
    assert response.status_code == 201, response.text
    with client.app.state.session_factory() as db:
        bundle = read_rule_bundle_definition(db.get(ParserVersion, response.json()["candidate_version_id"]), crypto)
    assert len(bundle["rules"]) == 1
    assert bundle["rules"][0]["rule_id"] == target and bundle["rules"][0]["rule_version"] == 6


def test_target_rejects_missing_wrong_vendor_and_reused_target(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, _ = generated(client, settings, monkeypatch, profile(client))
    group = {key: session["groups"][0][key] for key in ("group_id", "name", "waf_vendor", "case_ids")}
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={"expected_revision": session["revision"],
        "groups": [{**group, "target_rule_id": str(uuid.uuid4())}]})
    assert response.status_code == 422 and response.json()["detail"] == "parser_rule_target_invalid"


def test_frozen_request_guard_blocks_accidental_holdout_or_reference_injection(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, pid = create(client), profile(client)
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    identifier = response.json()["id"]
    with client.app.state.session_factory() as db:
        job = db.get(ParserWorkbenchJob, identifier)
        frozen, _ = service.documents(db.get(ParserWorkbench, session["id"]), client.app.state.crypto)
        holdout = next(case for case in frozen["cases"] if case["role"] == "holdout")
        document = json.loads(client.app.state.crypto.decrypt_text(job.request_ciphertext))
        document["samples"].append({"case_id": "c999", "waf_vendor": holdout["waf_vendor"], "raw_payload": holdout["raw_payload"]})
        document["case_map"]["c999"] = holdout["case_id"]
        job.request_ciphertext = client.app.state.crypto.encrypt_text(json.dumps(document))
        db.commit()
    async def forbidden(*args):
        pytest.fail("A holdout must not become a model input")
    monkeypatch.setattr(jobs, "request_completion", forbidden)
    assert run(client, settings)
    status = client.get(PREFIX + f"/sessions/{session['id']}/jobs").json()["items"][0]
    assert status["error_code"] == "parser_workbench_frozen_request_mismatch"


@pytest.mark.parametrize("corrected", [True, False])
def test_generation_repairs_only_authoring_structure_with_shared_bound(client, registered_vllm_target, settings, monkeypatch, corrected):
    login(client)
    session = create(client)
    pid, calls = profile(client), []
    monkeypatch.setattr(jobs, "request_completion", discovery_stub(calls))
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    assert response.status_code == 202 and run(client, settings)
    session = client.get(PREFIX + f"/sessions/{session['id']}").json()
    group = {key: session["groups"][0][key] for key in ("group_id", "name", "waf_vendor", "case_ids")}
    response = client.post(PREFIX + f"/sessions/{session['id']}/groups", json={"expected_revision": session["revision"], "groups": [group]})
    assert response.status_code == 200
    session, attempts = response.json(), []
    async def completion(profile, key, text, instructions, output_model, check):
        check()
        document = json.loads(text)
        attempts.append(document)
        value = proposal()
        if not corrected or len(attempts) == 1:
            value["extract"] = {"kind": "json_string", "json_pointer": "/missing"}
        return RuleProposalV1.model_validate(value)
    monkeypatch.setattr(jobs, "request_completion", completion)
    response = client.post(PREFIX + f"/sessions/{session['id']}/generate", json={"expected_revision": session["revision"], "profile_id": pid, "internal_data_confirmed": True})
    assert response.status_code == 202 and run(client, settings)
    result = client.get(PREFIX + f"/sessions/{session['id']}").json()
    last = result["jobs"][-1]
    assert len(attempts) == (2 if corrected else 3)
    assert attempts[1]["correction_code"] == "parser_workbench_authoring_failed"
    assert {"field": "extract", "type": "parser_authoring_not_qualified"} in attempts[1]["correction_feedback"]["issues"]
    assert {"field": "extract", "type": "parser_json_pointer_missing"} in attempts[1]["correction_feedback"]["issues"]
    assert last["status"] == ("completed" if corrected else "failed")
    assert last["summary"]["call_count"] <= last["summary"]["call_limit"] == 3
    assert all(case["case_id"] not in json.dumps(attempts) for case in result["cases"] if case["role"] == "holdout")


def test_holdout_minimum_fields_must_be_confirmed_on_same_case(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, _ = generated(client, settings, monkeypatch, profile(client))
    session = validate(client, session)
    held = [case for case in session["cases"] if case["role"] == "holdout"]
    assert len(held) == 2
    method_case, uri_case = held
    path = json.loads(uri_case["raw_payload"])["request"].split(" ")[1].split("?")[0]
    response = client.put(PREFIX + f"/sessions/{session['id']}/expectations", json={"expected_revision": session["revision"],
        "items": [{"case_id": method_case["case_id"], "fields": {"method": {"state": "present", "value": "GET"}}},
                  {"case_id": uri_case["case_id"], "fields": {"uri": {"state": "present", "value": path}}}]})
    assert response.status_code == 200
    result = validate(client, response.json())
    assert result["validation_summary"]["holdout"]["field_total"] == 2
    assert result["validation_summary"]["minimum_holdout_fields_confirmed"] is False
    assert result["validation_summary"]["ready"] is False


def test_stale_owner_cannot_reserve_calls_or_overwrite_new_owner(client, registered_vllm_target):
    login(client)
    session, pid = create(client), profile(client)
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"],
        "profile_id": pid, "internal_data_confirmed": True})
    identifier, engine = response.json()["id"], client.app.state.engine
    assert jobs._claim(engine, "stale", 60) == identifier
    with client.app.state.session_factory() as db:
        db.get(ParserWorkbenchJob, identifier).lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert jobs._claim(engine, "current", 60) == identifier
    with pytest.raises(ParserVersionError, match="parser_workbench_lease_lost"):
        jobs._reserve_call(engine, identifier, "stale")
    jobs._reserve_call(engine, identifier, "current")
    with client.app.state.session_factory() as db:
        job = db.get(ParserWorkbenchJob, identifier)
        assert job.lease_owner == "current" and job.metadata_json["call_count"] == 1


def test_worker_does_not_finish_or_fail_job_after_lease_reassignment(client, registered_vllm_target, settings, monkeypatch):
    login(client)
    session, pid = create(client), profile(client)
    response = client.post(PREFIX + f"/sessions/{session['id']}/discover", json={"expected_revision": session["revision"],
        "profile_id": pid, "internal_data_confirmed": True})
    identifier = response.json()["id"]
    async def reassigned(profile, key, text, instructions, output_model, check):
        document = json.loads(text)
        with client.app.state.session_factory() as db:
            job = db.get(ParserWorkbenchJob, identifier)
            job.lease_owner = "replacement-owner"
            job.lease_expires_at = utcnow() + timedelta(seconds=60)
            db.commit()
        return jobs.DiscoveryOutput(groups=[{"group_id": "g", "name": "JSON", "waf_vendor": "SyntheticVendor",
            "case_ids": [sample["case_id"] for sample in document["samples"]]}])
    monkeypatch.setattr(jobs, "request_completion", reassigned)
    assert run(client, settings)
    with client.app.state.session_factory() as db:
        job, row = db.get(ParserWorkbenchJob, identifier), db.get(ParserWorkbench, session["id"])
        assert job.status == "running" and job.lease_owner == "replacement-owner"
        assert job.result_ciphertext is None and job.error_code is None
        assert row.revision == response.json()["session_revision"] and row.status == "draft"


@pytest.mark.parametrize("legacy_count", [1, 201])
def test_newer_legacy_parser_does_not_hide_latest_rule_bundle_in_catalog(client, legacy_count):
    from app.models import ParserVersion
    from app.services.parser_rule_versions import create_rule_bundle_version
    from app.services.parser_versions import create_version
    login(client)
    with client.app.state.session_factory() as db:
        bundle = create_rule_bundle_version(db, client.app.state.crypto, {
            "schema_version": "vendor-parser-bundle-v1", "selection_policy_version": "vendor-rule-selection-v1",
            "rules": [{"rule_id": str(uuid.uuid4()), "rule_version": 1, "waf_vendor": "SyntheticVendor", "definition": proposal()}]},
            name="Most recent vendor rules")
        later = create_version(db, client.app.state.crypto, name="Later legacy capture", engine_version="http-capture-v1",
            options={"line_endings": "actual", "request_line_whitespace": "strict", "skip_leading_blank_lines": False},
            parent_id=BASELINE_ID, actor="admin")
        later.created_at = bundle.created_at + timedelta(seconds=1)
        for index in range(legacy_count - 1):
            db.add(ParserVersion(name=f"More recent legacy {index}", engine_version=later.engine_version,
                options_json=deepcopy(later.options_json), content_hash=later.content_hash, parent_id=BASELINE_ID,
                created_by="admin", created_at=bundle.created_at + timedelta(seconds=index + 2)))
        db.commit()
        identifier = bundle.id
    response = client.get(PREFIX + "/catalog")
    assert response.status_code == 200, response.text
    assert response.json()["active_version_id"] == BASELINE_ID
    assert any(rule["version_id"] == identifier for rule in response.json()["rules"])
