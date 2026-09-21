"""Offline synthetic evidence, temporary DB, and mocked model responses only."""
import asyncio
import copy
import json

import httpx
import pytest
from sqlalchemy import select

from app import worker
from app.agent.evidence_editor import EvidenceEditorOutput, VERSION, editor_input, validate_groups
from app.agent.result_editor import ResultEditorOutput
from app.agent.executor import AgentCallResult, execute_structured_agent
from app.models import AgentStep, Analysis, TestRun as NamedRun, VLLMProfile
from app.services.analysis_retries import load_execution_snapshot
from app.services.analyst_presentation import assessment_view
from app.services.analysis_exports import build_report, render_pdf, render_xlsx
from test_agent_configuration import URL, assign
from test_agent_openai import completion, install_http, openai_profile
from test_model_profiles import login_admin
from test_model_test_role import create_verified, named
from test_moduagent_worker import primary_output
from agent_selection_helpers import model_output

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def assessment():
    return {"version": "analyst-assessment-v1", "evidence": [
        {"evidence_id": "e1", "field": "payload.query", "excerpt": "q=test", "supports": "false_positive", "interpretation_ko": "일반 검색 값입니다."},
        {"evidence_id": "e2", "field": "payload.query", "excerpt": "q=test", "supports": "false_positive", "interpretation_ko": "검색어 값이며 실행 구문은 없습니다."},
        {"evidence_id": "e3", "field": "payload.query", "excerpt": "q=test", "supports": "true_positive", "interpretation_ko": "다른 의미의 관찰은 보존합니다."}],
        "decision_issues": [{"point_ko": "처리 방식을 확인해야 합니다.", "evidence_ids": ["e1", "e2", "e3"], "missing_condition_ko": "검색어 처리 규칙"}]}


GROUPS = [{"member_ids": ["e1", "e2"], "representative_id": "e2"}, {"member_ids": ["e3"], "representative_id": "e3"}]


def test_id_only_grouping_keeps_sources_opposite_direction_and_issue_links():
    original = assessment()
    before = copy.deepcopy(original)
    assert editor_input(original)["allowed_groups"] == [["e1", "e2"], ["e3"]]
    assert validate_groups(original, {"groups": GROUPS}) == GROUPS
    result = {"verdict": "inconclusive", "analyst_assessment": original,
              "evidence_presentation": {"version": VERSION, "status": "completed", "groups": GROUPS}}
    view = assessment_view(result)
    assert len(view["evidence"]) == 2
    assert view["evidence"][0]["interpretation_ko"] == original["evidence"][1]["interpretation_ko"]
    assert view["issues"][0]["evidence_numbers"] == [1, 2]
    assert view["issues"][0]["missing_condition_ko"] == "검색어 처리 규칙"
    assert result["verdict"] == "inconclusive" and original == before
    report = build_report({"status": "completed", "result": result})
    assert "일반 검색 값입니다." not in str(report)
    assert "검색어 값이며 실행 구문은 없습니다." in str(report)
    assert render_pdf(report).startswith(b"%PDF") and render_xlsx(report).startswith(b"PK")


@pytest.mark.parametrize("groups", [
    GROUPS[:1],  # Missing evidence.
    [{"member_ids": ["e1", "e2", "e3"], "representative_id": "e1"}],  # Opposing directions.
    [{"member_ids": ["e1", "e1", "e2"], "representative_id": "e1"}, GROUPS[1]],
    [{"member_ids": ["e1", "e2"], "representative_id": "e3"}, GROUPS[1]],
    [{"member_ids": ["unknown", "e2"], "representative_id": "e2"}, GROUPS[1]],
    [{**GROUPS[0], "explanation": "invented"}, GROUPS[1]],
])
def test_invalid_mapping_falls_back_without_hiding_evidence(groups):
    original = assessment()
    with pytest.raises(ValueError):
        validate_groups(original, {"groups": groups})
    result = {"analyst_assessment": original, "evidence_presentation": {"version": VERSION, "status": "completed", "groups": groups}}
    assert len(assessment_view(result)["evidence"]) == 3


def test_same_words_at_different_source_locations_not_grouped():
    original = assessment()
    original["evidence"][0]["source_spans"] = [{"start": 1, "end": 7}]
    original["evidence"][1]["source_spans"] = [{"start": 11, "end": 17}]
    assert editor_input(original) is None
    with pytest.raises(ValueError):
        validate_groups(original, {"groups": GROUPS})


def configure(client, primary, editor, *, enabled=True, purpose="production", acknowledge=False):
    current = client.get(URL).json()
    if purpose == "production":
        # Pre-existing Production state for isolated worker/recovery tests.
        from legacy_state_helpers import seed_production_roles
        seed_production_roles(client, primary["id"], current["assignments"]["production"]["verifier_profile_id"],
            editor=editor["id"] if editor else None, editor_enabled=enabled)
        return client.get(URL)
    draft = current["assignments"]
    draft[purpose].update(primary_profile_id=primary["id"], evidence_editor_enabled=enabled,
                          evidence_editor_profile_id=editor["id"] if editor else None)
    return client.put(URL, json={"expected_state_token": current["state_token"], **draft,
                                 "external_transfer_acknowledged": acknowledge})


def test_editor_assignment_verification_ack_and_old_client_preservation(client):
    login_admin(client)
    primary = create_verified(client, "editor-primary")
    editor = create_verified(client, "editor-remote", provider="openai")
    assert configure(client, primary, editor, purpose="test").status_code == 422
    result = configure(client, primary, editor, purpose="test", acknowledge=True)
    assert result.status_code == 200, result.text
    assert "test.evidence_editor" in next(p for p in result.json()["profiles"] if p["id"] == editor["id"])["agent_roles"]
    assert client.post(f"/api/v1/model-profiles/{editor['id']}/disable").status_code == 409
    # An older settings form cannot silently disable or replace the editor.
    response = assign(client, None, test_primary=primary["id"], acknowledge=True)
    assert response.status_code == 200
    assert response.json()["assignments"]["test"]["evidence_editor_profile_id"] == editor["id"]
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, editor["id"]).max_output_tokens += 1
        db.commit()
    assert configure(client, primary, editor, purpose="test", acknowledge=True).status_code == 409


def install_calls(monkeypatch, mode="valid"):
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        if kwargs["output_model"] in {EvidenceEditorOutput, ResultEditorOutput}:
            if mode == "raise":
                raise TimeoutError("SENSITIVE_PROVIDER_BODY")
            if mode == "lost":
                raise worker.WorkerExecutionError("analysis_lease_lost")
            data = json.loads(kwargs["user_input"])
            ids = [item["evidence_id"] for item in data["evidence"]]
            groups = [{"member_ids": ids, "representative_id": ids[-1]}]
            if mode == "invalid":
                groups[0]["member_ids"] = [ids[0]]
            output = (ResultEditorOutput(groups=groups, check_groups=[{"member_ids": [item["check_id"]], "representative_id": item["check_id"]} for item in data["checks"]])
                      if kwargs["output_model"] is ResultEditorOutput else EvidenceEditorOutput(groups=groups))
        else:
            output = primary_output("q=test")
            output.confidence_score = .6  # Exercise both decision roles.
            output.evidence[0].interpretation_ko = "검색 값에서 확인된 일반 문자열입니다." if kwargs["agent_name"] == "waf-primary" else "검색어 test에는 실행 구문이 없습니다."
            output = model_output(output, kwargs)
        return AgentCallResult(output, "synthetic-run", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    return calls


@pytest.mark.parametrize("mode", ["valid", "invalid", "raise", "revoked"])
def test_optional_editor_distinct_profile_original_result_and_encrypted_history(client, event_payload, monkeypatch, mode):
    login_admin(client)
    primary, editor = create_verified(client, "editor-primary"), create_verified(client, "editor-other")
    assert configure(client, primary, editor).status_code == 200
    created = client.post("/api/v1/analyses", json=event_payload).json()
    calls = install_calls(monkeypatch, mode)
    if mode == "revoked":
        with client.app.state.session_factory() as db:
            db.get(VLLMProfile, editor["id"]).status = "disabled"
            db.commit()
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        assert analysis.status == "completed"
        result = analysis.result_json
        assert result["verdict"] == result["primary"]["verdict"]
        assert len(result["analyst_assessment"]["evidence"]) == 2
        assert result["evidence_presentation"]["status"] == ("completed" if mode == "valid" else "fallback")
        assert len(assessment_view(result)["evidence"]) == (1 if mode == "valid" else 2)
        snapshot = load_execution_snapshot(analysis, client.app.state.crypto)
        assert snapshot.evidence_editor.profile_id == editor["id"]
        step = db.scalar(select(AgentStep).where(AgentStep.step_type == "llm_evidence_editor"))
        assert step.metadata_json["duration_ms"] >= 0
        assert "q=test" not in json.dumps(step.metadata_json)
        assert "SENSITIVE_PROVIDER_BODY" not in json.dumps(step.metadata_json)
        assert "q=test" not in step.input_ciphertext
        assert "q=test" in client.app.state.crypto.decrypt_text(step.input_ciphertext)
    assert calls[0]["user_input"] == calls[1]["user_input"]
    assert len(calls) == (2 if mode == "revoked" else 3)
    if mode != "revoked":
        assert calls[-1]["profile"].id == editor["id"]
        assert calls[-1]["output_validation_max_attempts"] == 1
        assert "concurrency_engine" in calls[-1]
        assert "expected_verdict" not in calls[-1]["user_input"]


def test_named_test_pins_editor_before_setting_change(client, event_payload, monkeypatch):
    login_admin(client)
    client.app.state.settings.agent_mode = "moduagent"
    primary, editor = create_verified(client, "editor-primary"), create_verified(client, "editor-other")
    assert configure(client, primary, editor, purpose="test").status_code == 200
    run = named(client, event_payload)
    assert configure(client, primary, None, enabled=False, purpose="test").status_code == 200
    calls = install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        saved = db.get(NamedRun, run["id"])
        assert editor["id"] in client.app.state.crypto.decrypt_text(saved.evidence_editor_snapshot_ciphertext)
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
    assert calls[-1]["profile"].id == editor["id"]


def test_old_named_test_does_not_acquire_new_editor(client, event_payload, monkeypatch):
    login_admin(client)
    client.app.state.settings.agent_mode = "moduagent"
    primary = create_verified(client, "old-named-primary")
    assert configure(client, primary, None, enabled=False, purpose="test").status_code == 200
    run = named(client, event_payload)
    assert configure(client, primary, None, enabled=True, purpose="test").status_code == 200
    calls = install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        assert "evidence_presentation" not in analysis.result_json
        assert load_execution_snapshot(analysis, client.app.state.crypto).evidence_editor is None
    assert len(calls) == 2


def test_retry_retains_original_editor_after_current_assignment_disabled(client, event_payload, monkeypatch):
    from test_analysis_retries_keys import retry
    login_admin(client)
    client.app.state.settings.agent_mode = "moduagent"
    primary, editor = create_verified(client, "retry-primary"), create_verified(client, "retry-editor")
    assert configure(client, primary, editor).status_code == 200
    created = client.post("/api/v1/analyses", json=event_payload).json()
    async def fail(**kwargs):
        return AgentCallResult(None, "synthetic-failure", None, "error", None, "output_validation_failed", {})
    monkeypatch.setattr(worker, "execute_structured_agent", fail)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["id"])
        with pytest.raises(worker.WorkerExecutionError) as error:
            worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        worker.mark_failed(db, analysis, error.value)
    assert configure(client, primary, None, enabled=False).status_code == 200
    eligibility = client.get(f"/api/v1/analyses/{created['id']}/retry-eligibility").json()
    assert eligibility["allowed"] and eligibility["evidence_editor_enabled"]
    assert eligibility["evidence_editor_model_profile"] == editor["name"]
    response = retry(client, created["id"])
    assert response.status_code == 202
    calls = install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        worker.process_moduagent(db, client.app.state.crypto, db.get(Analysis, response.json()["analysis_id"]), "", .75)
        assert db.get(Analysis, created["id"]).status == "failed"
    assert calls[-1]["profile"].id == editor["id"]


def test_editor_never_swallows_lost_lease(client, event_payload, monkeypatch):
    login_admin(client)
    primary = create_verified(client, "lease-editor-primary")
    assert configure(client, primary, None).status_code == 200
    created = client.post("/api/v1/analyses", json=event_payload).json()
    install_calls(monkeypatch, "lost")
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["id"])
        with pytest.raises(worker.WorkerExecutionError, match="analysis_lease_lost"):
            worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        assert analysis.status != "completed"
        assert "evidence_presentation" not in analysis.result_json


@pytest.mark.parametrize("provider,success", [("vllm", True), ("openai", True), ("vllm", False), ("openai", False)])
def test_editor_real_framework_single_http_attempt_and_bounded_output(monkeypatch, provider, success):
    def response(request, number):
        return httpx.Response(200, json=completion(json.dumps({"groups": GROUPS}))) if success else httpx.Response(500)
    requests, _ = install_http(monkeypatch, response)
    profile = openai_profile(provider=provider, base_url="https://api.openai.com/v1" if provider == "openai" else "http://10.0.0.10:8000/v1")
    result = asyncio.run(execute_structured_agent(profile=profile, api_key="synthetic-key", instructions="Synthetic instruction",
        user_input="Synthetic evidence", session_id="test-editor", agent_name="waf-evidence-editor", egress_check=lambda: None,
        output_model=EvidenceEditorOutput, output_validation_max_attempts=1))
    assert result.succeeded == success
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["max_completion_tokens" if provider == "openai" else "max_tokens"] == 1024
    assert profile.max_output_tokens == 3072
    assert ("chat_template_kwargs" in body) == (provider == "vllm")
