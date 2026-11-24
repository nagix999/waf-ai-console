"""Synthetic V1 preservation and V2 editor jobs; provider HTTP is offline."""
from copy import deepcopy
import hashlib
import json

import httpx
import pytest
from sqlalchemy import select

from app.models import AgentConfiguration, Analysis, EvidenceDisplayJob, InternalEgressTarget, ProductionPromotion
from app.models import TestRun as NamedRun, VLLMProfile, utcnow
from app.services import evidence_display_jobs as legacy
from app.services import evidence_display_jobs_v2 as jobs
from app.services.candidate_configurations import configuration_digest
from app.services.core_evidence_v3 import build_core_result
from app.services.crypto import CryptoService
from app.services.evidence_editor import capture_editor_profile
from app.services.production_configurations import live_snapshot
from app.services.prompt_snapshots import approved_production_record
from test_agent_openai import completion, install_http, openai_profile
from test_candidate_configurations import candidate, direct
from test_core_evidence_meaning_groups import two_role_same_excerpt
from test_evidence_display_jobs import enqueue_job, install_model


pytestmark = pytest.mark.usefixtures("registered_vllm_target")

FROZEN_V1_FILES = {
    "../agent/evidence_display.py": "b7111efd4af18c313053c3e8642213416db28624bc7393f6bb1101f9685256e6",
    "evidence_display_plan.py": "707bb2db2bb365afeb83d46db5dfd19f0f244dd5c2c99bd21c80e8cf6bb275f4",
    "evidence_display_jobs.py": "f16bbeb1bad17e3b285723fb8e2da90a21415b8935d6e440d041cff0eaa960d4",
    "../worker_entrypoint.py": "bc03c1f881305f4d6762a8afb75899ed6ad0a4c92074f4463a719d7414f3ef08",
}


def synthetic_profile():
    return openai_profile(id="synthetic-profile", provider="vllm", base_url="http://10.0.0.10:8000/v1")


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
    owner = "synthetic-v2-editor-owner"
    with client.app.state.session_factory() as db:
        assert jobs.claim_next(db, owner) == identifier
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, identifier, owner)


def user_document(request):
    body = json.loads(request.content)
    return json.loads(next(message["content"] for message in body["messages"] if message["role"] == "user"))


def test_old_approved_implementation_files_are_byte_identical_and_contained_in_v2_artifact():
    artifact = legacy.implementation_artifact()
    assert artifact["files"] == FROZEN_V1_FILES
    for name, digest in FROZEN_V1_FILES.items():
        assert jobs.implementation_artifact()["files"][name] == digest
    encoded = json.dumps({"version": legacy.VERSION, "files": FROZEN_V1_FILES},
                         sort_keys=True, separators=(",", ":"))
    assert artifact["sha256"] == hashlib.sha256(encoded.encode()).hexdigest()
    assert jobs.implementation_artifact()["sha256"] != artifact["sha256"]


@pytest.mark.parametrize("version", [legacy.VERSION, jobs.VERSION])
def test_loading_and_retry_copy_preserve_original_ciphertext_and_version(client, event_payload, version):
    analysis_id, identifier, profile_id, ciphertext = queued(client, event_payload, version=version)
    crypto = client.app.state.crypto
    created = client.post("/api/v1/analyses?wait_seconds=0", json={**event_payload,
        "event_id": "synthetic-display-snapshot-child"})
    assert created.status_code == 202, created.text
    with client.app.state.session_factory() as db:
        original, child = db.get(Analysis, analysis_id), db.get(Analysis, created.json()["id"])
        copied = jobs.copy_job(db, crypto, original, child)
        db.commit()
        assert copied.snapshot_ciphertext == ciphertext
        assert copied.status == "pending" and copied.input_ciphertext is None and copied.output_ciphertext is None
        selected = jobs.load_snapshot(crypto, ciphertext)
        assert selected.version == version
        if version == legacy.VERSION:
            assert selected.model_dump() == legacy.capture_display_profile(db.get(VLLMProfile, profile_id)).model_dump()
            assert selected.implementation == legacy.implementation_artifact()
        assert db.get(EvidenceDisplayJob, identifier).snapshot_ciphertext == ciphertext


@pytest.mark.parametrize("version", [legacy.VERSION, jobs.VERSION])
@pytest.mark.parametrize("damage", ["version", "instructions", "instructions_hash", "implementation", "implementation_hash"])
def test_version_instructions_and_artifact_tampering_is_rejected(settings, version, damage):
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    selected = jobs.capture_display_profile(synthetic_profile(), version=version).model_dump()
    if damage == "version":
        selected["version"] = jobs.VERSION if version == legacy.VERSION else legacy.VERSION
    elif damage == "instructions":
        selected["instructions"] += "\nSYNTHETIC_UNAPPROVED_RULE"
        selected["instructions_hash"] = hashlib.sha256(selected["instructions"].encode()).hexdigest()
    elif damage == "implementation":
        selected["implementation"]["files"]["evidence_display_jobs.py"] = "0" * 64
        selected["implementation"]["sha256"] = "1" * 64
        selected["implementation_hash"] = "1" * 64
    else:
        selected[damage] = "0" * 64
    with pytest.raises(ValueError, match="display_editor_snapshot_invalid"):
        jobs.load_snapshot(crypto, crypto.encrypt_text(json.dumps(selected)))


def test_existing_v1_job_dispatches_to_exact_legacy_worker_and_not_new_input_builder(
        client, event_payload, monkeypatch):
    analysis_id, identifier, _profile, ciphertext = queued(client, event_payload, version=legacy.VERSION)
    original_process, routed = legacy.process_job, []

    def old_worker(*args, **kwargs):
        routed.append((args, kwargs))
        return original_process(*args, **kwargs)

    monkeypatch.setattr(legacy, "process_job", old_worker)
    monkeypatch.setattr("app.agent.evidence_display_v2.build_editor_input", lambda *_args, **_kwargs:
        pytest.fail("A V1 approval must never use the V2 grouping input"))
    calls = install_model(monkeypatch)
    execute_job(client, identifier)
    assert len(routed) == 1 and len(calls) == 1
    assert "same_source_groups" not in json.loads(calls[0]["user_input"])
    with client.app.state.session_factory() as db:
        row = db.get(EvidenceDisplayJob, identifier)
        assert row.status == "completed" and row.snapshot_ciphertext == ciphertext
        assert jobs.get_display_view(db, client.app.state.crypto, db.get(Analysis, analysis_id))["status"] == "completed"


@pytest.mark.parametrize("source_run", [False, True])
def test_existing_v1_production_approval_is_not_recaptured_as_v2(client, event_payload, source_run):
    _analysis, _job, profile_id, _ciphertext = queued(client, event_payload, version=legacy.VERSION)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, profile_id)
        selected = jobs.capture_display_profile(profile, version=legacy.VERSION)
        approved = live_snapshot(db, crypto, client.app.state.settings)
        approved["evidence_editor"] = jobs.editor_metadata(selected)
        previous_run = None
        if source_run:
            original = db.get(Analysis, _analysis)
            previous_run = NamedRun(name="synthetic-v1-approved-run", kind="direct",
                idempotency_key="synthetic-v1-approved-run", request_hash="0" * 64,
                source_system="synthetic-v1-approved-run", execution_mode="moduagent",
                encryption_key_version=crypto.key_version, created_by="synthetic-editor-approval",
                prompt_snapshot_ciphertext=original.prompt_snapshot_ciphertext,
                prompt_policy_version_id=original.prompt_policy_version_id, prompt_version=original.prompt_version,
                display_editor_snapshot_ciphertext=crypto.encrypt_text(selected.model_dump_json()))
            db.add(previous_run)
            db.flush()
        db.add(ProductionPromotion(kind="promotion", snapshot_json=approved,
            configuration_hash=configuration_digest(approved), actor_id="synthetic-editor-approval",
            source_test_run_id=previous_run.id if previous_run else None))
        config = db.get(AgentConfiguration, 1)
        if config is None:
            config = AgentConfiguration(id=1, revision=0)
            db.add(config)
        config.production_evidence_editor_enabled = True
        config.production_evidence_editor_profile_id = profile_id
        db.flush()
        before = deepcopy(approved)
        captured = jobs.capture_for_purpose(db, crypto, "production")
        legacy_bridge = capture_editor_profile(profile, db=db)
        assert captured.version == legacy.VERSION and legacy_bridge.version == legacy.VERSION
        assert captured.model_dump() == selected.model_dump()
        assert jobs.editor_metadata(captured) == approved["evidence_editor"]
        assert approved_production_record(db).snapshot_json == before
        assert jobs.capture_display_profile(profile).version == jobs.VERSION


@pytest.mark.usefixtures("legacy_v231_admissions")
def test_new_old231_candidate_pins_v2_display_separately_and_never_runs_inline_editor(
        client, event_payload, candidate, monkeypatch):
    from app import worker_v231 as worker
    from app.agent.contracts_v231 import SecurityAnalysisOutputV231
    from app.agent.executor import AgentCallResult
    from app.services.candidate_configurations import verify_configuration
    from app.services.evidence_editor import capture_editor
    from app.services.prompt_snapshots import load_analysis_prompt
    from app.services.worker_runtime_v231 import process_moduagent
    from test_flat_evidence_v228 import wire

    accepted = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert accepted.status_code == 202, accepted.text
    run, calls = accepted.json(), []

    async def security_call(**kwargs):
        calls.append(kwargs["agent_name"])
        kwargs["egress_check"]()
        catalog = json.loads(kwargs["user_input"])["evidence_candidates"]
        source = next(item for item in catalog["items"] if item["field"] == "payload")
        value = wire()
        value["signature_assessment"].update(relation="exact", uncertainty_ko=None,
            matched_points=["합성 탐지와 입력 문맥을 비교했습니다."])
        value["evidence"] = [{"evidence_id": "e1", "source_id": source["source_id"],
            "supports": "attack_request", "related_evidence_ids": [],
            "interpretation_ko": "합성 입력의 동작과 사용 위치가 결합되어 처리 변경을 시도합니다."}]
        value["confidence_score"] = .9
        return AgentCallResult(SecurityAnalysisOutputV231.model_validate(value),
            "synthetic-v231", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", security_call)
    monkeypatch.setattr(worker, "_organize_evidence", lambda *_args, **_kwargs:
        pytest.fail("Independently pinned V2 display must never run in the V231 inline editor"))
    with client.app.state.session_factory() as db:
        saved = db.get(NamedRun, run["id"])
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        assert saved.evidence_editor_snapshot_ciphertext is None
        assert jobs.load_snapshot(client.app.state.crypto, saved.display_editor_snapshot_ciphertext).version == jobs.VERSION
        assert load_analysis_prompt(analysis, client.app.state.crypto).fixed_rules_version == "waf-system-v2.31"
        assert verify_configuration(saved, client.app.state.crypto, analysis) == run["configuration_snapshot"]
        display = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == analysis.id))
        pinned = display.snapshot_ciphertext
        process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        assert analysis.status == "completed" and analysis.result_json["schema_version"] == "waf-analysis-v3"
        assert display.status == "pending" and display.snapshot_ciphertext == pinned and display.attempt_count == 0
        assert capture_editor(db, "test", db.get(VLLMProfile, candidate["primary_profile_id"])) is None
    assert calls and set(calls).issubset({"waf-primary", "waf-verifier"})


def test_v2_same_point_grouping_calls_model_once_with_same_source_hints_and_exact_egress(
        client, event_payload, monkeypatch):
    original = two_role_same_excerpt()
    analysis_id, identifier, _profile, ciphertext = queued(client, event_payload, result=original)
    output = {"groups": [{"member_ids": ["e1", "e2", "e3", "e4"], "representative_id": "e3"}]}
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(output))))
    execute_job(client, identifier)
    assert len(requests) == 1
    assert str(requests[0].url) == "http://10.0.0.10:8000/v1/chat/completions"
    document = user_document(requests[0])
    assert document["same_source_groups"] == [["e1", "e2", "e3", "e4"]]
    assert document["evidence"] == original["evidence"]
    body = json.loads(requests[0].content)
    assert body["max_tokens"] == 1024
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    with client.app.state.session_factory() as db:
        analysis, row = db.get(Analysis, analysis_id), db.get(EvidenceDisplayJob, identifier)
        assert analysis.result_json == original and row.status == "completed" and row.attempt_count == 1
        assert row.snapshot_ciphertext == ciphertext
        assert "q=sample" not in row.input_ciphertext and "q=sample" not in row.output_ciphertext
        assert "q=sample" not in json.dumps(row.metadata_json)
        view = jobs.get_display_view(db, client.app.state.crypto, analysis)
        assert len(view["plan"]["groups"]) == 1
        projected = build_core_result(original, display_view=view)["result"]
        assert projected["evidence"] == [original["evidence"][2]]
        assert projected["primary"]["evidence_ids"] == []
        assert projected["verifier"]["output"]["evidence_ids"] == ["e3"]
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, identifier,
                     "synthetic-v2-editor-owner")
    assert len(requests) == 1  # A completed optional job is never redispatched.


@pytest.mark.parametrize("failure", ["incomplete", "missing_members"])
def test_v2_editor_failure_keeps_analysis_and_code_fallback_without_resending(
        client, event_payload, monkeypatch, failure):
    original = two_role_same_excerpt()
    analysis_id, identifier, _profile, _ciphertext = queued(client, event_payload, result=original)
    output = {"groups": [{"member_ids": ["e1"], "representative_id": "e1"}]}
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200, json=completion(json.dumps(output),
        finish_reason="length" if failure == "incomplete" else "stop")))
    execute_job(client, identifier)
    assert len(requests) == 1
    with client.app.state.session_factory() as db:
        analysis, row = db.get(Analysis, analysis_id), db.get(EvidenceDisplayJob, identifier)
        assert row.status == "failed"
        assert row.error_code == ("display_output_incomplete" if failure == "incomplete" else "display_groups_invalid")
        assert analysis.status == "completed" and analysis.result_json == original
        view = jobs.get_display_view(db, client.app.state.crypto, analysis)
        assert view["status"] == "fallback"
        projected = build_core_result(original, display_view=view)["result"]
        assert len(projected["evidence"]) == 3  # No semantic guesses and no reset to all four.
    jobs.process_job(client.app.state.session_factory, client.app.state.crypto, identifier,
                     "synthetic-v2-editor-owner")
    assert len(requests) == 1


def test_revoked_internal_target_prevents_v2_provider_dispatch(client, event_payload, monkeypatch):
    analysis_id, identifier, _profile, _ciphertext = queued(client, event_payload)
    with client.app.state.session_factory() as db:
        for target in db.scalars(select(InternalEgressTarget)):
            db.delete(target)
        db.commit()
    requests, _ = install_http(monkeypatch, lambda *_: pytest.fail("Revoked egress must not send provider HTTP"))
    execute_job(client, identifier)
    assert requests == []
    with client.app.state.session_factory() as db:
        assert db.get(EvidenceDisplayJob, identifier).status == "failed"
        assert db.get(Analysis, analysis_id).status == "completed"


def test_stopped_test_never_dispatches_v2_editor(client, event_payload, candidate, monkeypatch):
    accepted = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        analysis.status, analysis.result_json = "completed", two_role_same_excerpt()
        db.get(NamedRun, run["id"]).stopped_at = utcnow()
        row = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == analysis.id))
        assert jobs.load_snapshot(client.app.state.crypto, row.snapshot_ciphertext).version == jobs.VERSION
        identifier = row.id
        db.commit()
    requests, _ = install_http(monkeypatch, lambda *_: pytest.fail("Stopped tests must not send provider HTTP"))
    execute_job(client, identifier)
    assert requests == []
    with client.app.state.session_factory() as db:
        assert db.get(EvidenceDisplayJob, identifier).error_code == "display_test_stopped"
