"""Synthetic profiles and events; no live LLM or operational database access."""
import json
from agent_selection_helpers import model_output

import pytest
from sqlalchemy import select

from app import worker
from app.agent.executor import AgentCallResult
from app.agent.contracts import EvidenceCorrectionOutput
from app.models import AgentConfiguration, Analysis, TestRun as NamedRun, VLLMProfile, VLLMTestRun
from app.services.analysis_retries import load_execution_snapshot
from app.services.vllm_profiles import profile_fingerprint
from test_model_profiles import login_admin, profile_payload
from test_model_test_role import create_verified, named, role
from test_moduagent_worker import primary_output

pytestmark = pytest.mark.usefixtures("registered_vllm_target")
URL = "/api/v1/admin/agent-settings"


def assign(client, primary, verifier=None, *, test_primary=None, test_verifier=None, acknowledge=False, token=None):
    catalog = client.get(URL).json()
    return client.put(URL, json={"expected_state_token": token or catalog["state_token"],
        "production": {"primary_profile_id": primary, "verifier_profile_id": verifier},
        "test": {"primary_profile_id": test_primary, "verifier_profile_id": test_verifier},
        "external_transfer_acknowledged": acknowledge})


def test_admin_only_and_no_implicit_model_calls(client, service_headers):
    for suffix in ("", "/diagnostics"):
        assert client.get(URL + suffix).status_code == 401
        assert client.get(URL + suffix, headers=service_headers).status_code == 403
    login_admin(client)
    result = client.get(URL)
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["assignments"] == {purpose: {"primary_profile_id": None, "verifier_profile_id": None,
        "evidence_editor_enabled": False, "evidence_editor_profile_id": None}
                                            for purpose in ("production", "test")}
    with client.app.state.session_factory() as db:
        assert list(db.scalars(select(VLLMTestRun))) == []


def test_legacy_roles_preserved_until_explicit_atomic_assignment(client):
    login_admin(client)
    first, second = create_verified(client, "fixture-first"), create_verified(client, "fixture-second")
    role(client, first, "promote")
    role(client, first, "assign-test")
    before = client.get(URL).json()
    assert before["assignments"]["production"] == {"primary_profile_id": first["id"], "verifier_profile_id": None,
        "evidence_editor_enabled": False, "evidence_editor_profile_id": None}
    result = assign(client, second["id"], first["id"], test_primary=first["id"], test_verifier=second["id"])
    assert result.status_code == 200, result.text
    profiles = {item["id"]: item for item in result.json()["profiles"]}
    assert profiles[first["id"]]["is_test"] and profiles[second["id"]]["status"] == "production"
    assert profiles[first["id"]]["agent_roles"] == ["production.verifier"]
    assert profiles[first["id"]]["profile_fingerprint"] == first["profile_fingerprint"]
    assert assign(client, first["id"], token=before["state_token"]).status_code == 409
    assert client.put(f"/api/v1/model-profiles/{first['id']}", json={"name": "blocked"}).json()["detail"] == "agent_profile_is_assigned"
    assert client.post(f"/api/v1/model-profiles/{second['id']}/disable").status_code == 409


@pytest.mark.parametrize("invalid", ["draft", "quick", "changed", "disabled"])
def test_verifier_requires_matching_full_verification(client, invalid):
    login_admin(client)
    primary, verifier = create_verified(client, "fixture-primary"), create_verified(client, "fixture-verifier")
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, verifier["id"])
        if invalid in {"draft", "disabled"}:
            profile.status = invalid
        elif invalid == "changed":
            profile.max_output_tokens += 1
        else:
            db.scalar(select(VLLMTestRun).where(VLLMTestRun.profile_id == profile.id)).mode = "quick"
        db.commit()
    response = assign(client, primary["id"], verifier["id"])
    assert response.status_code == 409, response.text
    assert client.get(URL).json()["assignments"]["production"]["primary_profile_id"] is None


def test_stale_legacy_primary_assignment_invalidates_confirmation(client):
    login_admin(client)
    profile = create_verified(client, "fixture-profile")
    before = client.get(URL).json()
    role(client, profile, "promote")
    assert assign(client, profile["id"], token=before["state_token"]).status_code == 409


def test_cross_provider_requires_explicit_acknowledgement(client):
    login_admin(client)
    primary = create_verified(client, "fixture-local")
    verifier = create_verified(client, "fixture-external", provider="openai")
    assert assign(client, primary["id"], verifier["id"]).status_code == 422
    assert assign(client, primary["id"], verifier["id"], acknowledge=True).status_code == 200


def install_calls(monkeypatch, *, repair=False):
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        output = primary_output("q=test")
        output.confidence_score = 0.6
        output.summary_ko = "PRIMARY-ONLY-FIXTURE" if kwargs["agent_name"].startswith("waf-primary") else "VERIFIER-ONLY-FIXTURE"
        if repair and kwargs["agent_name"] == "waf-primary":
            output.evidence[0].field = "payload.body"
        if "-evidence-repair" in kwargs["agent_name"]:
            output = EvidenceCorrectionOutput(corrections=[{"index": 0, "field": "payload.query", "excerpt": "q=test"}],
                                              requires_reanalysis=False)
        return AgentCallResult(model_output(output, kwargs), f"fixture-run-{len(calls)}", "fixture-fingerprint", "completed", None, None,
                               {"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    return calls


def test_distinct_roles_same_input_bounded_repair_encrypted_history(client, event_payload, monkeypatch):
    login_admin(client)
    primary = create_verified(client, "fixture-local")
    verifier = create_verified(client, "fixture-external", provider="openai")
    with client.app.state.session_factory() as db:
        row = db.get(VLLMProfile, verifier["id"])
        row.context_window = 18000
        db.scalar(select(VLLMTestRun).where(VLLMTestRun.profile_id == row.id)).profile_fingerprint = profile_fingerprint(row)
        db.commit()
    assert assign(client, primary["id"], verifier["id"], acknowledge=True).status_code == 200
    created = client.post("/api/v1/analyses", json=event_payload).json()
    calls = install_calls(monkeypatch, repair=True)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        snapshot = load_execution_snapshot(analysis, client.app.state.crypto)
        assert snapshot.verifier_profile_id == verifier["id"]
        assert snapshot.schema_version == 3
        assert analysis.result_json["diagnostics"]["roles"]["primary"]["repair_recovered"]
        assert analysis.result_json["agent"]["role_profiles"]["verifier"]["model_profile_id"] == verifier["id"]
    assert [call["agent_name"] for call in calls] == ["waf-primary", "waf-primary-evidence-repair", "waf-verifier"]
    assert [call["profile"].id for call in calls] == [primary["id"], primary["id"], verifier["id"]]
    assert calls[0]["user_input"] == calls[2]["user_input"]
    assert "PRIMARY-ONLY-FIXTURE" not in calls[2]["user_input"]
    assert "evidence_correction" not in calls[2]["user_input"]
    assert calls[1]["output_validation_max_attempts"] == 1
    runs = client.get(f"/api/v1/analyses/{created['id']}/agent-runs").json()
    step = next(item for item in runs[0]["steps"] if item["step_type"] == "llm_primary")
    output = json.loads(step["output"])
    assert output["grounding_history"][0]["output"]["evidence"][0]["source_id"] == "c999"
    assert "q=test" in output["validated_output"]["evidence"][0]["excerpt"]
    assert output["validated_output"]["evidence"][0]["field"].startswith("payload")
    assert "q=test" not in json.dumps(step["metadata"])
    assert "PRIMARY-ONLY-FIXTURE" not in json.dumps(step["metadata"])
    counts = client.get(URL + "/diagnostics").json()["counts"]
    assert counts["measured"] == 1 and counts["primary_repair_recovered"] == 1


def test_named_test_pins_both_roles_before_assignment_change(client, event_payload, monkeypatch):
    login_admin(client)
    client.app.state.settings.agent_mode = "moduagent"
    first, second = create_verified(client, "fixture-first"), create_verified(client, "fixture-second")
    assert assign(client, first["id"], test_primary=first["id"], test_verifier=second["id"]).status_code == 200
    run = named(client, event_payload)
    assert run["profile_metadata"]["verifier_profile"]["model_profile_id"] == second["id"]
    assert assign(client, second["id"], test_primary=second["id"], test_verifier=first["id"]).status_code == 200
    calls = install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
    assert [call["profile"].id for call in calls] == [first["id"], second["id"]]


def test_unmeasured_history_not_reported_as_zero_abstention(client, event_payload):
    login_admin(client)
    result = client.get(URL + "/diagnostics").json()
    assert result["inconclusive_rate"] is None and result["counts"]["measured"] == 0
    assert client.get(URL + "/diagnostics?days=91").status_code == 422
    assert client.get(URL + "/diagnostics?purpose=unknown").status_code == 422
    assert assign(client, None, "missing-profile").status_code == 422


def test_diagnostics_count_old_and_new_versions_without_rewriting_history(client, event_payload):
    login_admin(client)
    fixtures = [("evidence-repair-v1", "completed", "true_positive"),
                ("evidence-repair-v2", "completed", "inconclusive"),
                ("unrecognized-version", "completed", "inconclusive"),
                ("evidence-repair-v2", "failed", None)]
    originals = {}
    for index, (version, status, verdict) in enumerate(fixtures):
        identifier = client.post("/api/v1/analyses", json={**event_payload, "event_id": f"diagnostic-fixture-{index}"}).json()["id"]
        document = {"diagnostics": {"version": version, "inconclusive_reasons": ["primary_evidence_rejected"],
                     "roles": {"primary": {"repair_attempted": True, "repair_recovered": False}}}}
        with client.app.state.session_factory() as db:
            row = db.get(Analysis, identifier)
            row.status, row.verdict, row.result_json = status, verdict, document
            db.commit()
        originals[identifier] = document
    result = client.get(URL + "/diagnostics").json()
    assert result["counts"]["completed"] == 3 and result["counts"]["failed"] == 1
    assert result["counts"]["measured"] == 2 and result["counts"]["inconclusive"] == 1
    assert result["counts"]["primary_repair_attempted"] == 2
    assert result["unmeasured_completed"] == 1 and result["inconclusive_rate"] == .5
    with client.app.state.session_factory() as db:
        assert {identifier: db.get(Analysis, identifier).result_json for identifier in originals} == originals


def test_verified_profile_still_needs_space_for_common_instructions_and_repair(client):
    login_admin(client)
    profile = create_verified(client, "fixture-small-context")
    with client.app.state.session_factory() as db:
        row = db.get(VLLMProfile, profile["id"])
        row.context_window = 8192
        db.scalar(select(VLLMTestRun).where(VLLMTestRun.profile_id == row.id)).profile_fingerprint = profile_fingerprint(row)
        db.commit()
    response = assign(client, profile["id"])
    assert response.status_code == 422
    assert response.json()["detail"] == "agent_context_budget_too_small"


def test_failed_retry_keeps_both_original_roles_after_reassignment(client, event_payload, monkeypatch):
    from test_analysis_retries_keys import retry

    login_admin(client)
    client.app.state.settings.agent_mode = "moduagent"
    primary, verifier = create_verified(client, "fixture-primary"), create_verified(client, "fixture-verifier")
    replacement = create_verified(client, "fixture-replacement")
    assert assign(client, primary["id"], verifier["id"]).status_code == 200
    created = client.post("/api/v1/analyses", json=event_payload).json()

    async def fail(**kwargs):
        kwargs["egress_check"]()
        return AgentCallResult(None, "fixture-failed-run", None, "error", None, None,
                               {"error_summary": {"code": "output_validation_failed"}})

    monkeypatch.setattr(worker, "execute_structured_agent", fail)
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, created["id"])
        with pytest.raises(worker.WorkerExecutionError) as failure:
            worker.process_moduagent(db, client.app.state.crypto, original, "", .75)
        worker.mark_failed(db, original, failure.value)
    assert client.get(URL + "/diagnostics").json()["llm_failure_counts"] == {"output_validation_failed": 1}
    assert assign(client, replacement["id"]).status_code == 200
    eligibility = client.get(f"/api/v1/analyses/{created['id']}/retry-eligibility").json()
    assert eligibility["allowed"] and eligibility["verifier_model_profile"] == verifier["name"]
    response = retry(client, created["id"])
    assert response.status_code == 202, response.text
    calls = install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        child = db.get(Analysis, response.json()["analysis_id"])
        worker.process_moduagent(db, client.app.state.crypto, child, "", .75)
        assert db.get(Analysis, created["id"]).status == "failed"
    assert [call["profile"].id for call in calls] == [primary["id"], verifier["id"]]


def test_revoked_verifier_is_blocked_before_transport_without_profile_fallback(client, event_payload, monkeypatch):
    login_admin(client)
    primary, verifier = create_verified(client, "fixture-primary"), create_verified(client, "fixture-verifier")
    assert assign(client, primary["id"], verifier["id"]).status_code == 200
    created = client.post("/api/v1/analyses", json=event_payload).json()
    transported = []

    async def execute(**kwargs):
        kwargs["egress_check"]()
        transported.append(kwargs["profile"].id)
        with client.app.state.session_factory() as latest:
            latest.get(VLLMProfile, verifier["id"]).status = "disabled"
            latest.commit()
        output = primary_output("q=test")
        output.confidence_score = .6
        return AgentCallResult(model_output(output, kwargs), "fixture-run", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        assert analysis.verdict == "inconclusive"
        assert analysis.result_json["diagnostics"]["inconclusive_reasons"] == ["verifier_failed"]
    assert transported == [primary["id"]]
    assert client.get(URL + "/diagnostics").json()["llm_failure_counts"] == {"invocation_failed": 1}
