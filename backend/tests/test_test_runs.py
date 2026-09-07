"""Named test ingestion/worker invariants on synthetic isolated SQLite only."""
import json
import uuid

import pytest
from sqlalchemy import func, select

from app.models import Analysis, AnalysisLabel, TestRun as NamedRun, TestRunItem as RunItem, VLLMProfile, VLLMTestRun, utcnow
from app.services.test_runs import named_test_request_check
from app.services.vllm_profiles import TargetNotAllowedError
from app.services.vllm_profiles import profile_fingerprint
from app import worker


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def record_synthetic_full_pass(db, profile):
    db.flush()
    profile.is_test = True
    db.add(VLLMTestRun(profile_id=profile.id, mode="full", status="passed",
        profile_fingerprint=profile_fingerprint(profile), completed_at=utcnow()))


def direct(client, event, *, key=None, **metadata):
    response = client.post("/api/v1/test-runs", json={"name": "합성 판정 회귀",
        "idempotency_key": key or str(uuid.uuid4()), "event": event, **metadata})
    assert response.status_code == 202, response.text
    return response.json()


def upload(client, rows, *, key=None):
    response = client.post("/api/v1/test-runs/uploads", data={"name": "합성 파일 회귀",
        "idempotency_key": key or str(uuid.uuid4())}, files={"file": ("synthetic.json", json.dumps(rows).encode())})
    assert response.status_code == 202, response.text
    return response.json()


def test_named_run_replay_and_new_execution_do_not_collide(client, event_payload):
    login(client)
    key = str(uuid.uuid4())
    first = direct(client, event_payload, key=key)
    replay = direct(client, event_payload, key=key)
    second = direct(client, event_payload)
    assert replay["id"] == first["id"] != second["id"]
    assert first["items"][0]["analysis_id"] != second["items"][0]["analysis_id"]
    assert first["source_system"] != second["source_system"]
    conflict = client.post("/api/v1/test-runs", json={"name": "변경된 실행",
        "idempotency_key": key, "event": event_payload})
    assert conflict.status_code == 409
    assert client.get("/api/v1/test-runs").json()["total"] == 2


def test_metadata_and_reference_are_not_event_or_prompt_input(client, event_payload):
    login(client)
    run = direct(client, event_payload, expected_verdict="false_positive", difficulty="hard",
                 test_category="합성 분류", case_name="합성 시나리오")
    item = run["items"][0]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, item["analysis_id"])
        saved = db.get(NamedRun, run["id"])
        assert not {"difficulty", "case_name", "test_category", "expected_verdict"}.intersection(row.extra_fields)
        assert row.prompt_snapshot_ciphertext == saved.prompt_snapshot_ciphertext
        assert item["evaluation"]["reference_label"]["verdict"] == "false_positive"
        assert item["difficulty"] == "hard"
    production = client.post("/api/v1/analyses", json={**event_payload, "test_category": "not-model-data"})
    assert production.status_code == 422


def test_invalid_and_duplicate_rows_are_durable_but_not_duplicate_metrics(client, event_payload):
    login(client)
    row = {**event_payload, "expected_verdict": "false_positive", "difficulty": "easy"}
    run = upload(client, [row, row, {**row, "src_port": "not-a-port"}, {}])
    assert (run["total"], run["accepted"], run["duplicates"], run["rejected"]) == (4, 1, 1, 2)
    assert len(run["items"]) == 4
    assert run["evaluation_summary"]["total"] == 1
    assert run["items"][2]["error_code"] == "invalid_test_event"
    assert client.get(f"/api/v1/analyses?test_run_id={run['id']}").json()["total"] == 1


def test_fixed_reference_and_matrix_drilldown_keep_cohort(client, event_payload):
    login(client)
    run = upload(client, [{**event_payload, "expected_verdict": "false_positive", "difficulty": "easy"},
                         {**event_payload, "event_id": "second", "expected_verdict": "true_positive", "difficulty": "hard"}])
    with client.app.state.session_factory() as db:
        first = db.get(Analysis, run["items"][0]["analysis_id"])
        db.add(AnalysisLabel(analysis_id=first.id, revision=2, verdict="true_positive",
            source_kind="reference", source_ref="synthetic-correction", ai_visible=False,
            created_by="fixture", attachment_id=str(uuid.uuid4()), token_digest="a" * 64))
        for row in db.scalars(select(Analysis)):
            row.status = "completed"
            row.completed_at = utcnow()
            row.verdict = "true_positive"
            row.model_profile = "synthetic-model"
            row.result_json = {"verdict": row.verdict, "agent": {"framework": "moduagent"}}
        db.commit()
    detail = client.get(f"/api/v1/test-runs/{run['id']}").json()
    assert detail["items"][0]["evaluation"]["reference_label"]["verdict"] == "false_positive"
    general = client.get(f"/api/v1/analyses/{run['items'][0]['analysis_id']}").json()
    assert general["evaluation"]["reference_label"]["verdict"] == "true_positive"
    drilled = client.get(f"/api/v1/test-runs/{run['id']}?reference_verdict=false_positive&verdict=true_positive").json()
    assert drilled["total_items"] == 1
    assert drilled["evaluation_summary"]["total"] == 2
    grouped = client.get(f"/api/v1/test-runs/{run['id']}?difficulty=easy").json()
    assert grouped["evaluation_summary"]["total"] == grouped["total_items"] == 1


def test_test_run_admin_only_and_names_required(client, event_payload, service_headers):
    assert client.get("/api/v1/test-runs", headers=service_headers).status_code == 403
    assert client.post("/api/v1/test-runs", headers=service_headers, json={
        "name": "합성", "idempotency_key": str(uuid.uuid4()), "event": event_payload}).status_code == 403
    login(client)
    for name in ("", " ", "\n"):
        assert client.post("/api/v1/test-runs", json={"name": name, "idempotency_key": str(uuid.uuid4()),
            "event": event_payload}).status_code == 422
    assert client.post("/api/v1/test-analyses", json=event_payload).status_code == 422
    assert client.post("/api/v1/test-uploads", files={"file": ("synthetic.json", b"[]")}).status_code == 422


def test_moduagent_test_requires_profile_and_mode_mismatch_fails_closed(client, event_payload):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    rejected = client.post("/api/v1/test-runs", json={"name": "합성", "idempotency_key": str(uuid.uuid4()), "event": event_payload})
    assert rejected.status_code == 409
    client.app.state.settings.agent_mode = "stub"
    run = direct(client, event_payload)
    with client.app.state.session_factory() as db:
        row = worker.claim_next(db, "synthetic-worker", 300)
        with pytest.raises(worker.WorkerExecutionError, match="test_run_execution_mode_mismatch"):
            worker.process_moduagent(db, client.app.state.crypto, row, "", 0.75)


def test_run_profile_is_pinned_and_change_blocks_requests(client, registered_vllm_target, event_payload):
    login(client)
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(name="synthetic-v1", base_url="http://10.0.0.10:8000/v1", model_name="synthetic", status="production")
        db.add(profile)
        record_synthetic_full_pass(db, profile)
        db.commit()
        profile_id = profile.id
    client.app.state.settings.agent_mode = "moduagent"
    run = direct(client, event_payload)
    check = named_test_request_check(client.app.state.engine, run["id"])
    check()
    with client.app.state.session_factory() as db:
        original = db.get(VLLMProfile, profile_id)
        original.status = "verified"
        db.add(VLLMProfile(name="synthetic-v2", base_url="http://10.0.0.10:8000/v1", model_name="other", status="production"))
        db.commit()
    check()  # Promotion elsewhere does not change the selected profile.
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, profile_id).model_name = "edited"
        db.commit()
    with pytest.raises(TargetNotAllowedError, match="test_run_profile_changed"):
        check()


def test_non_finite_upload_is_safe_422_and_creates_no_run(client, event_payload):
    login(client)
    content = json.dumps([{**event_payload, "bad_number": float("nan")}]).encode()
    response = client.post("/api/v1/test-runs/uploads", data={"name": "합성", "idempotency_key": str(uuid.uuid4())},
        files={"file": ("synthetic.json", content)})
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(NamedRun)) == 0


def test_worker_uses_original_model_and_prompt_after_production_changes(client, registered_vllm_target, event_payload, monkeypatch):
    from app.agent.executor import AgentCallResult
    from test_prompt_snapshots import create_and_activate, fake_output
    login(client)
    with client.app.state.session_factory() as db:
        first = VLLMProfile(name="original-synthetic", base_url="http://10.0.0.10:8000/v1", model_name="original", status="production")
        db.add(first)
        record_synthetic_full_pass(db, first)
        db.commit()
        selected = first.id
    client.app.state.settings.agent_mode = "moduagent"
    run = direct(client, event_payload)
    newer = create_and_activate(client, "NEW_SYNTHETIC_POLICY_NOT_FOR_QUEUED_RUN")
    with client.app.state.session_factory() as db:
        db.get(VLLMProfile, selected).status = "verified"
        db.flush()
        db.add(VLLMProfile(name="new-synthetic", base_url="http://10.0.0.10:8000/v1", model_name="new", status="production"))
        db.commit()
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append((kwargs["profile"].id, kwargs["instructions"]))
        return AgentCallResult(output=fake_output(), framework_run_id="synthetic", agent_fingerprint="synthetic",
            finish_reason="completed", failure_id=None, error=None, telemetry={"framework_version": "0.6.2"})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    with client.app.state.session_factory() as db:
        row = worker.claim_next(db, "synthetic", 300)
        worker.process_moduagent(db, client.app.state.crypto, row, "", 0.10)
        assert row.status == "completed"
        assert row.prompt_policy_version_id != newer["id"]
        assert row.result_json["policy"]["verifier_confidence_threshold"] == 0.75
    assert calls and all(identifier == selected for identifier, _ in calls)
    assert all("NEW_SYNTHETIC_POLICY_NOT_FOR_QUEUED_RUN" not in instructions for _, instructions in calls)


@pytest.mark.parametrize("missing", ["name", "idempotency_key"])
def test_dataset_requires_name_and_idempotency_key_before_enqueuing(client, registered_vllm_target, missing):
    from app.models import VLLMTestRun
    from test_model_profiles import profile_payload
    login(client)
    profile = client.post("/api/v1/model-profiles", json=profile_payload()).json()
    payload = {"mode": "full", "include_dataset": True, "name": "합성 명시적 실행",
               "idempotency_key": "synthetic-required-token"}
    payload.pop(missing)
    response = client.post(f"/api/v1/model-profiles/{profile['id']}/tests", json=payload)
    assert response.status_code == 422
    assert ("test_name_required" if missing == "name" else "test_idempotency_key_required") in response.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(NamedRun)) == 0
        assert db.scalar(select(func.count()).select_from(VLLMTestRun)) == 0
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0
