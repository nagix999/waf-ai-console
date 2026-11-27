"""Offline V3 display jobs and exact V1/V2 approval/retry preservation."""
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from app.models import AgentConfiguration, Analysis, EvidenceDisplayJob, ProductionPromotion, VLLMProfile, utcnow
from app.models import TestRun as NamedRun, TestRunItem as NamedItem
from app.services import evidence_display_jobs as v1
from app.services import evidence_display_jobs_v2 as v2
from app.services import evidence_display_jobs_v3 as jobs
from app.services.crypto import CryptoService
from app.services.candidate_configurations import configuration_digest
from app.services.evidence_editor import capture_editor_profile
from app.services.production_configurations import live_snapshot
from test_agent_openai import completion, install_http, openai_profile
from test_core_evidence_meaning_groups import two_role_same_excerpt
from test_core_evidence_v3 import issue
from test_evidence_display_jobs import enqueue_job, install_model
from test_candidate_configurations import candidate


pytestmark = pytest.mark.usefixtures("registered_vllm_target")


@pytest.fixture
def archived_v3_admission(monkeypatch):
    """Keep the original V3 qualification test on an explicit V3 snapshot."""
    from app.services import evidence_display_jobs_v4 as admission
    monkeypatch.setattr(admission, "capture_display_profile", lambda profile, *, version=jobs.VERSION:
        admission.DisplayEditorSnapshot.model_validate(jobs.capture_display_profile(profile, version=version).model_dump()))


def synthetic_profile():
    return openai_profile(id="synthetic-profile", provider="vllm", base_url="http://10.0.0.10:8000/v1")


def cross_role_result():
    value = two_role_same_excerpt()
    first, second = issue("e1"), issue("e3", origins=["verifier"])
    value["decision_issues"] = [first, second]
    value["primary"]["decision_issues"] = [deepcopy(first)]
    value["verifier"]["output"]["decision_issues"] = [deepcopy(second)]
    return value


def queued(client, event_payload, *, version=jobs.VERSION, result=None):
    analysis_id, identifier, profile_id = enqueue_job(client, event_payload)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        selected = jobs.capture_display_profile(db.get(VLLMProfile, profile_id), version=version)
        row = db.get(EvidenceDisplayJob, identifier)
        row.snapshot_ciphertext = crypto.encrypt_text(selected.model_dump_json())
        if result is not None:
            db.get(Analysis, analysis_id).result_json = deepcopy(result)
        db.commit()
        return analysis_id, identifier, profile_id, row.snapshot_ciphertext


def execute_job(client, identifier):
    owner = "synthetic-v3-display-owner"
    with client.app.state.session_factory() as db:
        assert jobs.claim_next(db, owner) == identifier
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, identifier, owner)


def named_qualification_run(db, crypto, analysis_id, job_id):
    analysis, job = db.get(Analysis, analysis_id), db.get(EvidenceDisplayJob, job_id)
    selected = jobs.load_snapshot(crypto, job.snapshot_ciphertext)
    run = NamedRun(name="synthetic-display-qualification", kind="direct",
        idempotency_key="synthetic-display-qualification", request_hash="0" * 64,
        source_system="synthetic-display-qualification", execution_mode="moduagent",
        encryption_key_version=crypto.key_version, created_by="synthetic-display-qualification",
        prompt_snapshot_ciphertext=analysis.prompt_snapshot_ciphertext,
        prompt_policy_version_id=analysis.prompt_policy_version_id, prompt_version=analysis.prompt_version,
        display_editor_snapshot_ciphertext=job.snapshot_ciphertext,
        configuration_snapshot_json={"evidence_editor": jobs.editor_metadata(selected)})
    db.add(run)
    db.flush()
    db.add(NamedItem(test_run_id=run.id, row_number=1, analysis_id=analysis.id,
        event_id="synthetic-display-qualification", ingest_status="accepted"))
    db.flush()
    return run


def test_frozen_editor_artifacts_are_contained_byte_for_byte_in_new_version():
    artifact = jobs.implementation_artifact()
    assert artifact["version"] == jobs.VERSION == "evidence-display-editor-v3"
    assert set(v2.implementation_artifact()["files"]) < set(artifact["files"])
    for module in (v1, v2):
        for name, digest in module.implementation_artifact()["files"].items():
            assert artifact["files"][name] == digest
    assert {"evidence_display_plan_v2.py", "core_evidence_v2.py", "../core_result_contracts.py",
            "../worker_entrypoint_v233.py", "worker_coordinator_v233.py",
            "evidence_display_worker_v233.py"} <= set(artifact["files"])


@pytest.mark.parametrize("version", [v1.VERSION, v2.VERSION, jobs.VERSION])
def test_load_and_retry_copy_keep_original_snapshot_bytes(client, event_payload, version):
    analysis_id, _identifier, _profile_id, ciphertext = queued(client, event_payload, version=version)
    created = client.post("/api/v1/analyses?wait_seconds=0", json={**event_payload,
        "event_id": "synthetic-v3-retry-snapshot"})
    assert created.status_code == 202, created.text
    with client.app.state.session_factory() as db:
        old, child = db.get(Analysis, analysis_id), db.get(Analysis, created.json()["id"])
        copied = jobs.copy_job(db, client.app.state.crypto, old, child)
        assert copied.snapshot_ciphertext == ciphertext
        assert copied.status == "pending" and copied.output_ciphertext is None
        assert jobs.load_snapshot(client.app.state.crypto, ciphertext).version == version


@pytest.mark.parametrize("version", [v1.VERSION, v2.VERSION, jobs.VERSION])
@pytest.mark.parametrize("damage", ["version", "instructions", "instructions_hash", "implementation_hash"])
def test_new_snapshot_loader_does_not_reinterpret_tampered_old_or_new_approval(settings, version, damage):
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    selected = jobs.capture_display_profile(synthetic_profile(), version=version).model_dump()
    if damage == "version":
        selected["version"] = v2.VERSION if version == jobs.VERSION else jobs.VERSION
    elif damage == "instructions":
        selected["instructions"] += "\nSYNTHETIC_UNAPPROVED"
        selected["instructions_hash"] = hashlib.sha256(selected["instructions"].encode()).hexdigest()
    else:
        selected[damage] = "0" * 64
    with pytest.raises(ValueError, match="display_editor_snapshot_invalid"):
        jobs.load_snapshot(crypto, crypto.encrypt_text(json.dumps(selected)))


@pytest.mark.parametrize("version,module", [(v1.VERSION, v1), (v2.VERSION, v2)])
def test_old_jobs_process_and_read_through_frozen_implementations(
        client, event_payload, monkeypatch, version, module):
    analysis_id, identifier, _profile, ciphertext = queued(client, event_payload, version=version)
    old_worker, old_getter, processed, read = module.process_job, v2.get_display_view, [], []

    def frozen_worker(*args, **kwargs):
        processed.append(True)
        return old_worker(*args, **kwargs)

    def frozen_getter(*args, **kwargs):
        read.append(True)
        return old_getter(*args, **kwargs)

    monkeypatch.setattr(module, "process_job", frozen_worker)
    monkeypatch.setattr(v2, "get_display_view", frozen_getter)
    monkeypatch.setattr("app.agent.evidence_display_v3.build_editor_input",
                        lambda *_a, **_k: pytest.fail("Old job must not use V3 input"))
    calls = install_model(monkeypatch)
    execute_job(client, identifier)
    assert processed == [True] and len(calls) == 1
    with client.app.state.session_factory() as db:
        row = db.get(EvidenceDisplayJob, identifier)
        assert row.snapshot_ciphertext == ciphertext and row.status == "completed"
        view = jobs.get_display_view(db, client.app.state.crypto, db.get(Analysis, analysis_id))
        assert view["version"] == "evidence-display-view-v1" and read == [True]


@pytest.mark.parametrize("version", [v1.VERSION, v2.VERSION])
@pytest.mark.parametrize("source_run", [False, True])
def test_v3_loader_keeps_existing_production_approval_on_its_original_editor_version(
        client, event_payload, version, source_run):
    analysis_id, _job, profile_id, _ciphertext = queued(client, event_payload, version=version)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, profile_id)
        selected = jobs.capture_display_profile(profile, version=version)
        approved = live_snapshot(db, crypto, client.app.state.settings)
        approved["evidence_editor"] = jobs.editor_metadata(selected)
        previous = None
        if source_run:
            original = db.get(Analysis, analysis_id)
            previous = NamedRun(name="synthetic-approved-editor-run", kind="direct",
                idempotency_key="synthetic-approved-editor-run", request_hash="0" * 64,
                source_system="synthetic-approved-editor-run", execution_mode="moduagent",
                encryption_key_version=crypto.key_version, created_by="synthetic-display-approval",
                prompt_snapshot_ciphertext=original.prompt_snapshot_ciphertext,
                prompt_policy_version_id=original.prompt_policy_version_id, prompt_version=original.prompt_version,
                display_editor_snapshot_ciphertext=crypto.encrypt_text(selected.model_dump_json()))
            db.add(previous)
            db.flush()
        db.add(ProductionPromotion(kind="promotion", snapshot_json=approved,
            configuration_hash=configuration_digest(approved), actor_id="synthetic-display-approval",
            source_test_run_id=previous.id if previous else None))
        config = db.get(AgentConfiguration, 1)
        if config is None:
            config = AgentConfiguration(id=1, revision=0)
            db.add(config)
        config.production_evidence_editor_enabled = True
        config.production_evidence_editor_profile_id = profile_id
        db.flush()
        captured = jobs.capture_for_purpose(db, crypto, "production")
        assert captured.model_dump() == selected.model_dump()
        assert capture_editor_profile(profile, db=db).version == version
        assert jobs.capture_display_profile(profile).version == jobs.VERSION


def test_v3_actual_offline_executor_preserves_strict_wire_egress_and_call_budget(
        client, event_payload, monkeypatch):
    original = cross_role_result()
    analysis_id, identifier, _profile, _snapshot = queued(client, event_payload, result=original)
    output = {"groups": [{"member_ids": ["e1", "e2", "e3", "e4"], "representative_id": "e3"}]}
    requests, _ = install_http(monkeypatch, lambda *_:
        httpx.Response(200, json=completion(json.dumps(output))))
    execute_job(client, identifier)
    assert len(requests) == 1 and str(requests[0].url) == "http://10.0.0.10:8000/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert body["max_tokens"] == 1024 and body["chat_template_kwargs"] == {"enable_thinking": False}
    document = json.loads(next(message["content"] for message in body["messages"] if message["role"] == "user"))
    assert document["role_decision_issues"]["primary"] == original["primary"]["decision_issues"]
    with client.app.state.session_factory() as db:
        analysis, row = db.get(Analysis, analysis_id), db.get(EvidenceDisplayJob, identifier)
        assert row.status == "completed" and row.attempt_count == 1 and analysis.result_json == original
        assert jobs.get_display_view(db, client.app.state.crypto, analysis)["plan"]["metrics"]["group_count"] == 1


def test_v3_cross_role_same_point_uses_one_existing_call_and_bound_new_plan(client, event_payload, monkeypatch):
    original = cross_role_result()
    before = deepcopy(original)
    analysis_id, identifier, _profile, ciphertext = queued(client, event_payload, result=original)
    calls = install_model(monkeypatch)
    execute_job(client, identifier)
    assert len(calls) == 1
    document = json.loads(calls[0]["user_input"])
    assert document["same_source_groups"] == [["e1", "e2", "e3", "e4"]]
    assert document["decision_issues"] == original["decision_issues"]
    assert document["role_decision_issues"]["verifier"] == original["verifier"]["output"]["decision_issues"]
    with client.app.state.session_factory() as db:
        analysis, row = db.get(Analysis, analysis_id), db.get(EvidenceDisplayJob, identifier)
        assert analysis.result_json == before and analysis.status == "completed"
        assert row.status == "completed" and row.attempt_count == 1 and row.snapshot_ciphertext == ciphertext
        assert "q=sample" not in row.input_ciphertext and "q=sample" not in row.output_ciphertext
        assert "q=sample" not in json.dumps(row.metadata_json)
        view = jobs.get_display_view(db, client.app.state.crypto, analysis)
        assert view["version"] == "evidence-display-view-v2"
        assert view["plan"]["version"] == "evidence-display-plan-v2"
        assert view["plan"]["groups"][0]["member_ids"] == ["e1", "e2", "e3", "e4"]
        assert view["metadata"]["model_called"] is True
        assert view["metadata"]["source_count"] == 4 and view["metadata"]["group_count"] == 1
        assert view["metadata"]["returned_count"] == 1 and view["metadata"]["aliased_count"] == 3
        assert view["metadata"]["issue_reference_reduction"] == 1
        assert view["metadata"]["required_dependency_count"] == 0
        assert view["metadata"]["projection_fallback"] is False
        assert row.metadata_json["returned_count"] == view["metadata"]["returned_count"]
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, identifier,
                     "synthetic-v3-display-owner")
    assert len(calls) == 1


@pytest.mark.parametrize("mode,reason", [("invalid", "display_groups_invalid"), ("timeout", "display_timeout")])
def test_v3_editor_failure_keeps_analysis_and_does_not_retry_model(client, event_payload, monkeypatch, mode, reason):
    original = cross_role_result()
    analysis_id, identifier, _profile, _snapshot = queued(client, event_payload, result=original)
    calls = install_model(monkeypatch, mode=mode)
    execute_job(client, identifier)
    with client.app.state.session_factory() as db:
        row, analysis = db.get(EvidenceDisplayJob, identifier), db.get(Analysis, analysis_id)
        assert row.status == "failed" and row.error_code == reason
        assert analysis.status == "completed" and analysis.result_json == original
        view = jobs.get_display_view(db, client.app.state.crypto, analysis)
        assert view["status"] == "fallback" and len(view["plan"]["groups"]) == 4
    assert len(calls) == 1


def test_v3_no_job_uses_old_view_without_authorizing_cross_role_response_mapping(client, event_payload):
    analysis_id, identifier, _profile, _snapshot = queued(client, event_payload)
    with client.app.state.session_factory() as db:
        db.delete(db.get(EvidenceDisplayJob, identifier))
        db.flush()
        view = jobs.get_display_view(db, client.app.state.crypto, db.get(Analysis, analysis_id))
        assert view["version"] == "evidence-display-view-v1" and view["status"] == "disabled"


def test_optional_projection_counts_never_fail_analysis_or_display_job(client, event_payload, monkeypatch):
    original = cross_role_result()
    analysis_id, identifier, _profile, _snapshot = queued(client, event_payload, result=original)
    calls = install_model(monkeypatch)

    def diagnostics_unavailable(*_args, **_kwargs):
        raise ValueError("SYNTHETIC_PRIVATE_SOURCE_MUST_NOT_APPEAR")

    monkeypatch.setattr("app.services.core_evidence_v2.build_core_result", diagnostics_unavailable)
    execute_job(client, identifier)
    with client.app.state.session_factory() as db:
        row, analysis = db.get(EvidenceDisplayJob, identifier), db.get(Analysis, analysis_id)
        assert row.status == "completed" and analysis.status == "completed" and analysis.result_json == original
        assert row.metadata_json["projection_fallback"] is True
        assert "returned_count" not in row.metadata_json
        assert "SYNTHETIC_PRIVATE_SOURCE" not in json.dumps(row.metadata_json)
        view = jobs.get_display_view(db, client.app.state.crypto, analysis)
        assert view["status"] == "completed" and view["metadata"]["projection_fallback"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("with_crypto", [False, True])
@pytest.mark.parametrize("flag", [None, False, True])
def test_v3_qualification_requires_successful_projection_without_failing_completed_analysis(
        client, event_payload, monkeypatch, with_crypto, flag):
    original = cross_role_result()
    analysis_id, identifier, _profile, _snapshot = queued(client, event_payload, result=original)
    calls = install_model(monkeypatch)
    execute_job(client, identifier)
    with client.app.state.session_factory() as db:
        row, analysis = db.get(EvidenceDisplayJob, identifier), db.get(Analysis, analysis_id)
        metadata = {key: value for key, value in row.metadata_json.items() if key != "projection_fallback"}
        if flag is not None:
            metadata["projection_fallback"] = flag
        row.metadata_json = metadata
        run = named_qualification_run(db, client.app.state.crypto, analysis_id, identifier)
        summary = jobs.qualification(db, run, client.app.state.crypto if with_crypto else None)
        assert summary == {"required": True, "ready": flag is False, "pending": False,
                           "failed": int(flag is not False)}
        assert row.status == "completed" and analysis.status == "completed" and analysis.result_json == original
    assert len(calls) == 1


@pytest.mark.parametrize("version", [v1.VERSION, v2.VERSION])
@pytest.mark.parametrize("with_crypto", [False, True])
def test_legacy_qualification_keeps_missing_projection_marker_contract(
        client, event_payload, monkeypatch, version, with_crypto):
    analysis_id, identifier, _profile, _snapshot = queued(client, event_payload, version=version)
    install_model(monkeypatch)
    execute_job(client, identifier)
    with client.app.state.session_factory() as db:
        row = db.get(EvidenceDisplayJob, identifier)
        assert "projection_fallback" not in row.metadata_json
        run = named_qualification_run(db, client.app.state.crypto, analysis_id, identifier)
        assert jobs.qualification(db, run, client.app.state.crypto if with_crypto else None) == {
            "required": True, "ready": True, "pending": False, "failed": 0}


def test_v3_promotion_gate_rechecks_projection_even_when_saved_marker_says_success(
        client, event_payload, monkeypatch):
    analysis_id, identifier, _profile, _snapshot = queued(client, event_payload, result=cross_role_result())
    install_model(monkeypatch)
    execute_job(client, identifier)
    with client.app.state.session_factory() as db:
        row = db.get(EvidenceDisplayJob, identifier)
        assert row.metadata_json["projection_fallback"] is False
        run = named_qualification_run(db, client.app.state.crypto, analysis_id, identifier)
        assert jobs.qualification(db, run, client.app.state.crypto)["ready"]
        monkeypatch.setattr("app.services.core_evidence_v2.build_core_result", lambda *_a, **_k:
            {"metadata": {"status": "fallback"}})
        summary = jobs.qualification(db, run, client.app.state.crypto)
        assert not summary["ready"] and summary["failed"] == 1
        assert row.status == "completed" and db.get(Analysis, analysis_id).status == "completed"


def test_completed_projection_fallback_is_not_an_official_success_and_cannot_promote(
        client, event_payload, archived_v3_admission, candidate, monkeypatch):
    from sqlalchemy import select
    from app.models import TestEvaluation as Evaluation
    from test_official_evaluations import approved_item, execute
    from test_validation_data import create_dataset

    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2}, "false_positive")
    run = execute(client, dataset, candidate)
    analysis_id = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, analysis_id)
        analysis.status, analysis.result_json, analysis.verdict = "completed", cross_role_result(), "false_positive"
        analysis.completed_at = utcnow()
        row = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == analysis_id))
        identifier = row.id
        db.commit()
    calls = install_model(monkeypatch)
    monkeypatch.setattr(jobs, "_projection_metrics", lambda *_a, **_k: {"projection_fallback": True})
    execute_job(client, identifier)
    with client.app.state.session_factory() as db:
        analysis, row = db.get(Analysis, analysis_id), db.get(EvidenceDisplayJob, identifier)
        assert analysis.status == "completed" and row.status == "completed"
        assert row.metadata_json["projection_fallback"] is True
        evaluation = db.scalar(select(Evaluation).where(Evaluation.test_run_id == run["id"]))
        assert evaluation.summary_json["display_editor"] == {
            "required": True, "ready": False, "pending": False, "failed": 1}
    base = "/api/v1/admin/production-configurations"
    preflight = client.get(f"{base}/preflight/{run['id']}")
    assert preflight.status_code == 200, preflight.text
    checks = {item["code"]: item["passed"] for item in preflight.json()["checks"]}
    assert checks["display_editor_completed"] is False and preflight.json()["eligible"] is False
    current = client.get(base).json()
    response = client.post(base + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": current["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 409, response.text
    assert len(calls) == 1
