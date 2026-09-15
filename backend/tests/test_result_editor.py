"""Synthetic follow-up tasks, isolated DBs and offline model transports only."""
import asyncio
import copy
import json

import httpx
import pytest
from sqlalchemy import select

from app import worker
from app.agent import evidence_editor as legacy
from app.agent import result_editor as editor
from app.agent.contracts import AnalystCheck
from app.agent.executor import AgentCallResult, execute_structured_agent
from app.models import AgentStep, Analysis, TestRun as NamedRun
from app.services.analysis_exports import build_report, render_pdf, render_xlsx
from app.services.analysis_retries import load_execution_snapshot
from app.services.evidence_editor import EditorSnapshot
from test_agent_openai import completion, install_http, openai_profile
from test_evidence_editor import assessment, configure, GROUPS, install_calls as install_evidence_calls
from test_model_profiles import login_admin
from test_model_test_role import create_verified, named
from test_moduagent_worker import primary_output
from agent_selection_helpers import model_output

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def checks():
    first = {"source_ko": "검색 API의 입력 처리 규격", "check_ko": "검색어를 문자열 값으로만 사용하는지 SQL 구문에 연결하는지 확인하세요.",
             "why_ko": "정상 검색 데이터와 SQL 조건 변경 시도를 구분하기 위한 확인입니다."}
    different = {"source_ko": first["source_ko"], "check_ko": "첨부 경로에 상위 디렉터리 이동 제한이 적용되는지 확인하세요.",
                 "why_ko": "검색어 처리와 별개인 파일 접근 범위를 확인하기 위한 작업입니다."}
    repeated = {**first, "check_ko": "검색 입력이 문자열 데이터인지 SQL의 일부로 해석되는지 확인하세요.",
                "why_ko": "검색 입력이 SQL 조건을 변경할 수 있는지 구분하는 데 필요합니다."}
    return [first, different, repeated]


CHECK_GROUPS = [{"member_ids": ["c1", "c3"], "representative_id": "c3"}, {"member_ids": ["c2"], "representative_id": "c2"}]


def presentation(values=None, groups=None):
    return {"version": editor.CHECK_VERSION, "status": "completed", "items": editor.check_items(values if values is not None else checks()),
            "groups": copy.deepcopy(CHECK_GROUPS if groups is None else groups)}


def test_combined_input_and_id_only_output_preserve_distinct_task_and_originals():
    original, tasks = assessment(), checks()
    before = copy.deepcopy((original, tasks))
    document = editor.editor_input(original, tasks)
    assert document["allowed_groups"] == [["e1", "e2"], ["e3"]]
    assert document["check_allowed_groups"] == [["c1", "c2", "c3"]]
    groups, check_groups = editor.validate_result(original, tasks, {"groups": GROUPS, "check_groups": CHECK_GROUPS})
    assert groups == GROUPS and check_groups == CHECK_GROUPS
    assert editor.present_checks(tasks, presentation()) == [tasks[2], tasks[1]]
    assert (original, tasks) == before
    for phrase in ["같은 자료·대상·처리 경로·확인 조건", "요청 수신 여부와 실제 실행 여부", "의미가 같은지 확실하지 않으면 각각 남긴다", "새로 작성하거나 바꾸지 않는다"]:
        assert phrase in editor.INSTRUCTIONS
    assert len(editor.INSTRUCTIONS) <= 4000


def test_followup_only_candidates_work_without_duplicate_evidence():
    tasks = checks()
    assert editor.editor_input(None, tasks)["evidence"] == []
    assert editor.validate_result(None, tasks, {"groups": [], "check_groups": CHECK_GROUPS}) == ([], CHECK_GROUPS)
    assert editor.editor_input(None, tasks[:1]) is None
    assert editor.editor_input(assessment(), [])["checks"] == []
    assert editor.validate_result(assessment(), [], {"groups": GROUPS, "check_groups": []}) == (GROUPS, [])
    with pytest.raises(ValueError):
        editor.validate_result(None, tasks, {"groups": GROUPS, "check_groups": CHECK_GROUPS})


@pytest.mark.parametrize("groups", [
    CHECK_GROUPS[:1], [],
    [{"member_ids": ["c1", "c3"], "representative_id": "c2"}, CHECK_GROUPS[1]],
    [{"member_ids": ["c1", "c1", "c3"], "representative_id": "c3"}, CHECK_GROUPS[1]],
    [{"member_ids": ["e1", "c3"], "representative_id": "c3"}, CHECK_GROUPS[1]],
    [{"member_ids": ["c1", "c3"], "representative_id": "c3", "explanation": "invented"}, CHECK_GROUPS[1]],
    [*CHECK_GROUPS, CHECK_GROUPS[1]], None,
])
def test_invalid_or_incomplete_mapping_keeps_all_original_tasks(groups):
    tasks = checks()
    with pytest.raises(ValueError):
        editor.validate_check_groups(tasks, groups)
    saved = presentation()
    saved["groups"] = groups
    assert editor.present_checks(tasks, saved) == tasks


def test_source_boundary_and_changed_originals_invalidate_display_mapping():
    tasks = checks()
    tasks[2]["source_ko"] = "다른 애플리케이션의 접근 기록"
    with pytest.raises(ValueError, match="source_mismatch"):
        editor.validate_check_groups(tasks, CHECK_GROUPS)
    assert editor.present_checks(tasks, presentation(tasks)) == tasks
    tasks = checks()
    saved = presentation(tasks)
    tasks[0]["why_ko"] = "원래 기록이 변경되었습니다."
    assert editor.present_checks(tasks, saved) == tasks
    for state in ["fallback", "skipped", "running"]:
        assert editor.present_checks(checks(), {**presentation(), "status": state}) == checks()
    assert editor.present_checks(checks(), {**presentation(), "version": "unknown"}) == checks()


def test_export_uses_same_representatives_and_does_not_write_original_result():
    tasks = checks()
    result = {"verdict": "inconclusive", "summary_ko": "요청 처리 규격을 확인해야 합니다.",
              "analyst_guidance": {"checks": tasks}, "follow_up_presentation": presentation()}
    before = copy.deepcopy(result)
    report = build_report({"status": "completed", "result": result})
    assert tasks[0]["check_ko"] not in str(report)
    assert tasks[1]["check_ko"] in str(report) and tasks[2]["check_ko"] in str(report)
    assert render_pdf(report).startswith(b"%PDF") and render_xlsx(report).startswith(b"PK")
    assert result == before


def install_calls(monkeypatch, mode="valid"):
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        if kwargs["output_model"] in {editor.ResultEditorOutput, legacy.EvidenceEditorOutput}:
            if mode == "raise":
                raise TimeoutError("SENSITIVE_UPSTREAM_RESPONSE")
            if mode == "lost":
                raise worker.WorkerExecutionError("analysis_lease_lost")
            document = json.loads(kwargs["user_input"])
            # Decision evidence is identical across the two roles; only the
            # follow-up tasks require presentation grouping in v2.
            groups = [{"member_ids": [item["evidence_id"]], "representative_id": item["evidence_id"]} for item in document["evidence"]]
            if kwargs["output_model"] is legacy.EvidenceEditorOutput:
                value = legacy.EvidenceEditorOutput(groups=groups)
            else:
                value = editor.ResultEditorOutput(groups=groups, check_groups=CHECK_GROUPS[:1] if mode == "invalid" else CHECK_GROUPS)
        else:
            value = primary_output("q=test")
            value.confidence_score = .6
            tasks = checks()
            value.analyst_checks = [AnalystCheck(**task) for task in ([tasks[0], tasks[1]] if kwargs["agent_name"] == "waf-primary" else [tasks[2], tasks[1]])]
            value = model_output(value, kwargs)
        return AgentCallResult(value, "synthetic-run", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    return calls


@pytest.mark.parametrize("mode", ["valid", "invalid", "raise", "budget"])
def test_worker_groups_followups_only_once_without_changing_verdict_or_originals(client, event_payload, monkeypatch, mode):
    login_admin(client)
    primary, other = create_verified(client, "followup-primary"), create_verified(client, "followup-editor")
    assert configure(client, primary, other).status_code == 200
    created = client.post("/api/v1/analyses", json=event_payload).json()
    calls = install_calls(monkeypatch, mode)
    if mode == "budget":
        monkeypatch.setattr(legacy, "input_fits", lambda *args: False)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        result = analysis.result_json
        assert analysis.status == "completed"
        assert result["verdict"] == result["primary"]["verdict"]
        original = result["analyst_guidance"]["checks"]
        assert original == [checks()[0], checks()[1], checks()[2]]
        saved = result["follow_up_presentation"]
        assert saved["status"] == ("completed" if mode == "valid" else "fallback")
        assert len(editor.present_checks(original, saved)) == (2 if mode == "valid" else 3)
        assert load_execution_snapshot(analysis, client.app.state.crypto).evidence_editor.version == editor.VERSION
        step = db.scalar(select(AgentStep).where(AgentStep.step_type == "llm_evidence_editor"))
        assert step.name == "근거·확인사항 정리"
        assert step.metadata_json["check_count"] == 3
        assert step.metadata_json["displayed_check_count"] == (2 if mode == "valid" else 3)
        assert checks()[0]["check_ko"] not in json.dumps(step.metadata_json, ensure_ascii=False)
        assert "SENSITIVE_UPSTREAM_RESPONSE" not in str(result) + str(step.metadata_json)
        assert checks()[0]["check_ko"] in client.app.state.crypto.decrypt_text(step.input_ciphertext)
    assert len(calls) == (2 if mode == "budget" else 3)
    assert calls[0]["user_input"] == calls[1]["user_input"]
    if mode != "budget":
        assert calls[-1]["profile"].id == other["id"]
        assert calls[-1]["output_validation_max_attempts"] == 1
        assert "expected_verdict" not in calls[-1]["user_input"]


@pytest.mark.parametrize("duplicate_evidence", [False, True])
def test_old_editor_snapshot_keeps_old_contract_and_scope(client, event_payload, monkeypatch, duplicate_evidence):
    login_admin(client)
    client.app.state.settings.agent_mode = "moduagent"
    primary = create_verified(client, "legacy-editor-primary")
    assert configure(client, primary, None, purpose="test").status_code == 200
    created = named(client, event_payload)
    with client.app.state.session_factory() as db:
        run = db.get(NamedRun, created["id"])
        saved = EditorSnapshot.model_validate_json(client.app.state.crypto.decrypt_text(run.evidence_editor_snapshot_ciphertext))
        old = saved.model_copy(update={"version": legacy.VERSION, "instructions": legacy.INSTRUCTIONS,
                                      "instructions_hash": legacy.instructions_hash(legacy.INSTRUCTIONS)})
        run.evidence_editor_snapshot_ciphertext = client.app.state.crypto.encrypt_text(old.model_dump_json())
        db.commit()
    calls = install_evidence_calls(monkeypatch) if duplicate_evidence else install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["items"][0]["analysis_id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        assert analysis.status == "completed"
        assert "follow_up_presentation" not in analysis.result_json
        assert load_execution_snapshot(analysis, client.app.state.crypto).evidence_editor.version == legacy.VERSION
        assert analysis.result_json["evidence_presentation"]["status"] == ("completed" if duplicate_evidence else "skipped")
    assert len(calls) == (3 if duplicate_evidence else 2)
    if duplicate_evidence:
        assert calls[-1]["output_model"] is legacy.EvidenceEditorOutput
        assert calls[-1]["instructions"] == legacy.INSTRUCTIONS
        assert "checks" not in json.loads(calls[-1]["user_input"])


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("success", [True, False])
def test_new_output_contract_keeps_single_attempt_and_token_limit(monkeypatch, provider, success):
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200, json=completion(json.dumps({"groups": [], "check_groups": CHECK_GROUPS}))) if success else httpx.Response(500))
    profile = openai_profile(provider=provider, base_url="https://api.openai.com/v1" if provider == "openai" else "http://10.0.0.10:8000/v1")
    result = asyncio.run(execute_structured_agent(profile=profile, api_key="synthetic-key", instructions="Synthetic editor instructions",
        user_input="Synthetic checks", session_id="test-result-editor", agent_name="waf-evidence-editor", egress_check=lambda: None,
        output_model=editor.ResultEditorOutput, output_validation_max_attempts=1))
    assert result.succeeded is success and len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["max_completion_tokens" if provider == "openai" else "max_tokens"] == 1024
    assert ("chat_template_kwargs" in body) is (provider == "vllm")
