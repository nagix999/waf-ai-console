"""Synthetic-only candidate calls, overlap, failure fencing and stable v3."""
import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from types import SimpleNamespace

import pytest

from app import worker_v227 as worker
from app.agent.contracts_v227 import SecurityAnalysisOutputV227
from app.agent.executor import AgentCallResult
from app.models import AgentRun, Analysis, ConcurrencyConfiguration, VLLMProfile
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from app.services.worker_runtime_v227 import process_moduagent
from test_evidence_interpretations_v227 import wire

pytestmark = pytest.mark.usefixtures("legacy_v227_admissions")


def result(*, succeeded=True):
    return AgentCallResult(None, "synthetic-run", None, "completed" if succeeded else "error",
        None, None if succeeded else "synthetic_failure", {})


def test_parallel_tasks_overlap_and_record_each_completion_in_owner_task(monkeypatch):
    async def scenario():
        both_started = asyncio.Event()
        release_primary = asyncio.Event()
        started, recorded = [], []
        owner = asyncio.current_task()

        async def execute(**kwargs):
            role = kwargs["role"]
            started.append(role)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            if role == "primary":
                await release_primary.wait()
            return result()

        def finished(role, call):
            assert asyncio.current_task() is owner
            recorded.append(role)
            if role == "verifier":
                release_primary.set()

        monkeypatch.setattr(worker, "_execute_grounded_agent_async", execute)
        calls = await asyncio.wait_for(worker._run_role_tasks(
            {"primary": {"role": "primary"}, "verifier": {"role": "verifier"}}, finished), 2)
        assert set(calls) == {"primary", "verifier"}
        assert recorded == ["verifier", "primary"]

    asyncio.run(scenario())


def test_primary_failure_cancels_remaining_verifier_and_drains_tasks(monkeypatch):
    async def scenario():
        verifier_started, canceled = asyncio.Event(), asyncio.Event()

        async def execute(**kwargs):
            if kwargs["role"] == "primary":
                await verifier_started.wait()
                return result(succeeded=False)
            verifier_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()

        monkeypatch.setattr(worker, "_execute_grounded_agent_async", execute)
        finished = []
        calls = await asyncio.wait_for(worker._run_role_tasks(
            {role: {"role": role} for role in ("primary", "verifier")},
            lambda role, call: finished.append(role)), 2)
        assert not calls["primary"].succeeded and canceled.is_set()
        assert finished == ["primary"]

    asyncio.run(scenario())


def test_persistence_failure_cancels_other_tasks(monkeypatch):
    async def scenario():
        started, canceled = asyncio.Event(), asyncio.Event()

        async def execute(**kwargs):
            if kwargs["role"] == "primary":
                await started.wait()
                return result()
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()

        def fail(*_args):
            raise worker.WorkerExecutionError("analysis_lease_lost")

        monkeypatch.setattr(worker, "_execute_grounded_agent_async", execute)
        with pytest.raises(worker.WorkerExecutionError, match="analysis_lease_lost"):
            await asyncio.wait_for(worker._run_role_tasks(
                {role: {"role": role} for role in ("primary", "verifier")}, fail), 2)
        assert canceled.is_set()

    asyncio.run(scenario())


def test_coordinator_cancellation_drains_both_roles(monkeypatch):
    async def scenario():
        both_started = asyncio.Event()
        started, canceled = set(), set()

        async def execute(**kwargs):
            role = kwargs["role"]
            started.add(role)
            if len(started) == 2:
                both_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.add(role)

        monkeypatch.setattr(worker, "_execute_grounded_agent_async", execute)
        task = asyncio.create_task(worker._run_role_tasks(
            {role: {"role": role} for role in ("primary", "verifier")},
            lambda *_args: pytest.fail("Canceled role cannot publish a result")))
        await asyncio.wait_for(both_started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert canceled == {"primary", "verifier"}

    asyncio.run(scenario())


@pytest.mark.parametrize("omitted,shared,limit,expected", [
    (False, True, 2, False), (True, True, 1, False),
    (True, True, 2, True), (True, False, 1, True),
])
def test_parallel_requires_known_omission_and_existing_server_capacity(
        monkeypatch, omitted, shared, limit, expected):
    from app.services import concurrency
    monkeypatch.setattr(concurrency, "configured_server_limit", lambda *_args: limit)
    primary = SimpleNamespace(base_url="http://10.0.0.10:8000/v1")
    verifier = primary if shared else SimpleNamespace(base_url="http://10.0.0.11:8000/v1")
    assert worker._parallel_eligible(None, omitted, primary, verifier) is expected


def test_model_profile_is_a_detached_immutable_snapshot():
    source = SimpleNamespace(id="synthetic", provider="vllm", base_url="http://10.0.0.10:8000/v1",
        model_name="synthetic-model", timeout_seconds=30, max_output_tokens=3072,
        tls_verify=True, external_data_approved=False)
    snapshot = worker.RoleProfile.capture(source)
    source.model_name = "changed-after-capture"
    assert snapshot.model_name == "synthetic-model"
    with pytest.raises(FrozenInstanceError):
        snapshot.model_name = "not-allowed"


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("large,server_limit,disagree,fail_primary,fail_verifier", [
    (False, 1, False, False, False),
    (True, 1, False, False, False),
    (True, 2, False, False, False),
    (True, 2, True, False, False),
    (True, 2, False, True, False),
    (True, 2, False, False, True),
])
def test_complete_candidate_keeps_independence_history_and_final_policy(
        client, event_payload, service_headers, monkeypatch,
        large, server_limit, disagree, fail_primary, fail_verifier):
    event = {**event_payload, "payload": "POST /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n"
             + ("synthetic body " * (12000 if large else 1))}
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202, response.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-v227", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="production"))
        row = db.get(Analysis, response.json()["id"])
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.27"
        row.prompt_version, row.prompt_policy_version_id = prompt.prompt_version, prompt.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(prompt.model_dump_json())
        config = db.get(ConcurrencyConfiguration, 1)
        if config is None:
            config = ConcurrencyConfiguration(id=1, production=1, test=1)
            db.add(config)
        config.server_limits = {"10.0.0.10:8000": server_limit}
        db.commit()

    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        is_verifier = kwargs["agent_name"] == "waf-verifier"
        if (not is_verifier and fail_primary) or (is_verifier and fail_verifier):
            return replace(result(succeeded=False), failure_id="synthetic-verifier-failure" if is_verifier else None)
        await asyncio.sleep(0)
        document = json.loads(kwargs["user_input"])
        assert not {"primary", "primary_output", "expected_verdict"} & document.keys()
        assert document["capture_context"]["payload_omitted"] is large
        source = next(item for item in document["evidence_candidates"]["items"] if item["field"] == "payload")
        value = wire()
        value["signature_assessment"].update(relation="exact", uncertainty_ko=None,
            matched_points=["합성 탐지 설명과 요청을 비교했습니다."])
        value["evidence"] = [{"source_id": source["source_id"],
            "supports": "normal_request" if is_verifier and disagree else "attack_request",
            "interpretation_ko": "원문의 요청 동작과 구체적인 입력 의미를 함께 해석했습니다."}]
        value["evidence"][0]["related_sources"] = []
        if large:
            related = next(item for item in document["evidence_candidates"]["items"]
                           if item["field"] == "payload" and item["source_id"] != source["source_id"])
            value["evidence"][0]["related_sources"] = [{"source_id": related["source_id"],
                "contribution_ko": "같은 요청에 포함된 데이터 범위를 보여줍니다.", "supports": "context"}]
        value["confidence_score"] = .9
        value["capture_assessment"] = {"status": "possible", "reason_ko": "합성 문맥에서 누락 가능성을 추정했습니다."}
        if is_verifier and disagree:
            value["verdict"], value["threat_analysis"]["severity"] = "false_positive", "NONE"
        return AgentCallResult(SecurityAnalysisOutputV227.model_validate(value),
            "synthetic-v227", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, response.json()["id"])
        original_payload, original_extra = row.payload_ciphertext, deepcopy(row.extra_fields)
        if fail_primary:
            with pytest.raises(worker.WorkerExecutionError, match="primary_agent_failed"):
                process_moduagent(db, crypto, row, "", .75)
            assert row.result_json is None
            return
        process_moduagent(db, crypto, row, "", .75)
        assert row.status == "completed"
        assert row.verdict == ("inconclusive" if disagree or fail_verifier else "true_positive")
        assert row.payload_ciphertext == original_payload and row.extra_fields == original_extra
        assert row.result_json["schema_version"] == "waf-analysis-v3"
        if disagree:
            assert row.result_json["summary_ko"] == wire()["summary_ko"]
        assert "capture_assessment" not in json.dumps(row.result_json)
        assert "related_sources" not in json.dumps(row.result_json)
        run = db.query(AgentRun).filter_by(analysis_id=row.id).one()
        if fail_verifier:
            assert run.failure_id == "synthetic-verifier-failure"
        primary_step = next(step for step in run.steps if step.step_type == "llm_primary")
        decrypted = json.loads(crypto.decrypt_text(primary_step.output_ciphertext))
        assert "capture_assessment" in json.dumps(decrypted)
        assert primary_step.metadata_json["role_schedule"] == (
            "parallel_known_required" if large and server_limit >= 2 else "sequential")
        if large:
            from app.services.core_evidence_v3 import build_core_result
            final_step = next(step for step in run.steps if step.step_type == "finalize")
            stored = json.loads(crypto.decrypt_text(final_step.output_ciphertext))
            links = stored["evidence_relations"]
            assert links["groups"]
            core = build_core_result(row.result_json, relations=links)["result"]
            ids = {item["evidence_id"] for item in core["evidence"]}
            for group in links["groups"]:
                if group["representative_evidence_id"] in ids:
                    assert set(group["related_evidence_ids"]) <= ids
            verifier_step = next(step for step in run.steps if step.step_type == "llm_verifier")
            if server_limit >= 2:
                assert verifier_step.started_at <= primary_step.completed_at
                assert primary_step.started_at <= verifier_step.completed_at
            else:
                assert primary_step.completed_at <= verifier_step.started_at
        policy_step = next(step for step in run.steps if step.step_type == "policy")
        assert policy_step.metadata_json["execute_verifier"] is large
    expected_count = 2 if large else 1
    assert len(calls) == expected_count
    if large:
        assert calls[0]["user_input"] == calls[1]["user_input"]
        assert calls[0]["instructions"] != calls[1]["instructions"]
        assert calls[0]["profile"].id == calls[1]["profile"].id
