"""Synthetic version isolation and approval continuity, without model calls.

HEAD_FILE_SHA256 was extracted from each Git HEAD blob at implementation start
(eb3c026efedb378333f7222340793dcb938a7fa7). The assertions also run in the offline
test image without requiring a Git executable or access to a mutable branch.
File drift is simulated in memory and never writes application source files.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from threading import Barrier, Lock

import pytest
from pydantic import ValidationError

from app import worker, worker_v221
from app.agent import evidence_pipeline, evidence_pipeline_v221, legacy_prompts_v220, prompts
from app.models import Analysis, ProductionPromotion, TestRun as NamedRun, VLLMProfile, utcnow
from app.services import worker_runtime
from app.services.analysis_retries import (
    load_execution_snapshot, make_execution_snapshot,
)
from app.services.candidate_configurations import configuration_digest, verify_configuration
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshot, PromptSnapshotError, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from test_analysis_retries_keys import profile_fixture, retry
from test_candidate_configurations import candidate, direct
from test_production_promotion import BASE, qualified
from test_validation_data import login


OLD = "waf-system-v2.20"
NEW = "waf-system-v2.21"
V220_PROMPT_SHA256 = "145e9e1c56ee0967689cc33bdd9acbaf718013bf304675e2aae8f7e3862940b6"
HEAD_FILE_SHA256 = {
    "evidence_pipeline.py": "59e3b801d28401ade166f734d69835adfd070e40a2c3dd7740a82e7141b7edc8",
    "source_blocks.py": "c8f1c294054fa659e58dac3e6064fa45fc25b7be4933ca994ceac46a695b572c",
    "input_builder.py": "fa53a21030d90d36ae8cd74e0ed79de4bd4b420d8a7d700fd61572dd42f5a67a",
    "evidence_candidates.py": "ea7245548527a2bf442fd69736470030a06cd8b7cb4d33b0fddb21048b515cb4",
    "evidence_relations.py": "cd48554322c19eb217a153c87371855f641eea2656a50c580aec4d707a4c7843",
    "contracts.py": "fcbe17100223ea338a10f1d511628c96bda881a11357b2402136641ffcf7bfaf",
    "evidence.py": "1a3a627b40e7315e1c7f1d487fc09fd2fa1a81a3334a1b84940da8561f3f6249",
    "grounding_repair.py": "5b7dbf6e2f83b9dd420490c2a93de76776fa17d555f6e553c9c69824faa602e3",
    "analyst_assessment.py": "150dd54453af83052f8a40f66e1444c3676fdb26c304161616e6a22ba7ea36a5",
    "security_analysis.py": "005aa4129890863514073267713e4a991403c79e73073e3f28c2a2f82a7f80b6",
    "executor.py": "b8a361f914c42f0dfd8b0f5fbb2d0cc28b65696637ba8d5f34a5565f0d8927d5",
    "policy.py": "12f0678b0ab90432d73e5f7a098116cc978e1f7c853ada0ea4840e41d4a1c6ff",
    "input_integrity.py": "33ff1104e448751002f9b4d7d64c97eba1cf5cbe39c26ceec74b87d2572810b9",
    "followup_policy.py": "44acb72e7efdff554cc24fb1d12d27c572d6b27e619ec095a07d5be07ee5e2a5",
    "../worker.py": "cc9120fb304163b72bde62cbe27db69d29ec2b687a745d90b27d10f02bdd1cf8",
    "../services/payload_decoding.py": "4f3b59c4ec58db41649662efd9e3417cebf7b326a60ae9fe777abd1c4d786511",
    "../services/request_integrity.py": "f871c85aa267e4d575a8810f167639d6df0d8c28ea8678705bafd4fd72870f4e",
    "../services/json_duplicate_values.py": "cd0eb740c9490cbedfabd3e915e28ffa430d04aa89a411be0766b56b2b18220f",
    "../services/log4j_normalization.py": "42733ebc4958c37e6114023034ae0573effac5a9d67c49dba9f76462be05c3bf",
}


def build_prompt(client, *, legacy=False):
    with client.app.state.session_factory() as db:
        return _build_snapshot(get_active_policy(db, client.app.state.crypto), client.app.state.crypto,
            **({"rules": legacy_prompts_v220} if legacy else {}))


def pinned_row(client, prompt):
    return Analysis(analysis_purpose="production", prompt_version=prompt.prompt_version,
        prompt_policy_version_id=prompt.policy_version_id,
        prompt_snapshot_ciphertext=client.app.state.crypto.encrypt_text(prompt.model_dump_json()))


def simulate_drift(monkeypatch, name="executor_v221.py"):
    target = (Path(evidence_pipeline_v221.__file__).parent / name).resolve()
    original = Path.read_bytes

    def read_bytes(path):
        value = original(path)
        return value + b"\n# synthetic in-memory version drift\n" if path.resolve() == target else value

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def seed_v220_baseline(client):
    login(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        current = live_snapshot(db, crypto, client.app.state.settings)
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto, rules=legacy_prompts_v220)
        document = {**current, "schema_version": 3,
            "prompt": {name: getattr(prompt, name) for name in current["prompt"]},
            "evidence_pipeline": prompt.evidence_pipeline}
        row = ProductionPromotion(kind="baseline", snapshot_json=document,
            configuration_hash=configuration_digest(document), actor_id="synthetic-v220-existing-baseline")
        db.add(row)
        db.commit()
        return row.id, deepcopy(document), prompt


def install_handlers(monkeypatch, *, barrier=None):
    calls, lock = [], Lock()

    def handler(version):
        def process(db, crypto, row, targets, threshold, *, request_check=None):
            prompt = load_analysis_prompt(row, crypto) if row.prompt_snapshot_ciphertext else None
            actual = prompt.fixed_rules_version if prompt else None
            assert actual == version or (version == OLD and actual is None)
            if barrier is not None:
                barrier.wait(timeout=10)
            with lock:
                calls.append((version, row.prompt_version, targets, threshold, request_check))
            return version
        return process

    monkeypatch.setattr(worker, "process_moduagent", handler(OLD))
    monkeypatch.setattr(worker_v221, "process_moduagent", handler(NEW))
    return calls


def dispatch(client, row, *, request_check=None):
    return worker_runtime.process_moduagent(None, client.app.state.crypto, row,
        "synthetic-targets", .73, request_check=request_check)


@pytest.mark.parametrize("filename", HEAD_FILE_SHA256)
def test_each_pinned_v220_runtime_file_still_matches_original_git_head_bytes(filename):
    assert set(evidence_pipeline._FILES) == set(HEAD_FILE_SHA256)
    raw = (Path(evidence_pipeline.__file__).parent / filename).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == HEAD_FILE_SHA256[filename]


def test_frozen_v220_prompt_and_manifest_keep_previous_content_identity():
    assert hashlib.sha256(Path(legacy_prompts_v220.__file__).read_bytes()).hexdigest() == V220_PROMPT_SHA256
    original = evidence_pipeline.pipeline_artifact()
    assert original["version"] == "evidence-pipeline-v1"
    assert original["files"] == HEAD_FILE_SHA256
    assert evidence_pipeline_v221.pipeline_for_rules(OLD) == original
    assert evidence_pipeline_v221.validate_pipeline_snapshot(original, OLD) == original


def test_v221_manifest_is_additive_and_does_not_rehash_existing_runtime_versions():
    old, new = evidence_pipeline.pipeline_artifact(), evidence_pipeline_v221.pipeline_artifact()
    assert new == evidence_pipeline_v221.pipeline_artifact()
    assert new["version"] == "evidence-pipeline-v2"
    assert new["source_catalog_version"] == "evidence-selection-v4"
    assert {key: new["files"][key] for key in old["files"]} == old["files"]
    assert {"input_builder_v221.py", "executor_v221.py", "structured_json.py", "evidence_source_policy.py",
        "execution_policy_v221.py", "../worker_v221.py", "../services/worker_runtime.py"} <= new["files"].keys()
    assert set(new["files"]) == set(old["files"]) | set(evidence_pipeline_v221._FILES)
    identity = {key: value for key, value in new.items() if key != "sha256"}
    assert new["sha256"] == hashlib.sha256(json.dumps(identity, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    assert evidence_pipeline_v221.validate_pipeline_snapshot(new, NEW) == new


@pytest.mark.parametrize("alter", [
    lambda value: None,
    lambda value: {**value, "sha256": "f" * 64},
    lambda value: {**value, "files": {}},
    lambda value: {**value, "version": "evidence-pipeline-v1"},
    lambda value: {**value, "source_catalog_version": "evidence-selection-v3"},
    lambda value: {**value, "untrusted_extension": "SYNTHETIC_SECRET"},
])
def test_v221_missing_tampered_or_downcast_artifact_is_rejected(alter):
    value = alter(evidence_pipeline_v221.pipeline_artifact())
    with pytest.raises(ValueError, match="^evidence_pipeline_snapshot_invalid$") as error:
        evidence_pipeline_v221.validate_pipeline_snapshot(value, NEW)
    assert "SYNTHETIC_SECRET" not in str(error.value)


@pytest.mark.parametrize("filename", ["executor_v221.py", "evidence_source_policy.py", "../worker_v221.py",
                                     "../services/worker_runtime.py"])
def test_new_implementation_drift_invalidates_v221_but_not_saved_v220_manifest(monkeypatch, filename):
    old, new = evidence_pipeline.pipeline_artifact(), evidence_pipeline_v221.pipeline_artifact()
    simulate_drift(monkeypatch, filename)
    changed = evidence_pipeline_v221.pipeline_artifact()
    assert changed["sha256"] != new["sha256"]
    assert [key for key in changed["files"] if changed["files"][key] != new["files"][key]] == [filename]
    with pytest.raises(ValueError, match="^evidence_pipeline_snapshot_invalid$"):
        evidence_pipeline_v221.validate_pipeline_snapshot(new, NEW)
    assert evidence_pipeline_v221.validate_pipeline_snapshot(old, OLD) == old


def test_default_prompt_is_v221_and_explicit_historical_prompt_stays_v220(client):
    old, new = build_prompt(client, legacy=True), build_prompt(client)
    assert (old.fixed_rules_version, new.fixed_rules_version) == (OLD, NEW)
    assert old.schema_version == new.schema_version == 2
    assert old.policy_hash == new.policy_hash and old.policy_version_id == new.policy_version_id
    assert old.evidence_pipeline == evidence_pipeline.pipeline_artifact()
    assert new.evidence_pipeline == evidence_pipeline_v221.pipeline_artifact()
    assert old.fixed_rules_hash != new.fixed_rules_hash and old.instructions_hash != new.instructions_hash
    assert "evidence_pipeline" not in new.metadata()
    assert PromptSnapshot.model_validate_json(old.model_dump_json()) == old
    assert PromptSnapshot.model_validate_json(new.model_dump_json()) == new


@pytest.mark.parametrize("alter", [
    lambda document: document.pop("evidence_pipeline"),
    lambda document: document.update(evidence_pipeline=None),
    lambda document: document.update(schema_version=1),
    lambda document: document.update(evidence_pipeline=evidence_pipeline.pipeline_artifact()),
    lambda document: document["evidence_pipeline"].update(sha256="0" * 64),
])
def test_v221_snapshot_cannot_lose_downgrade_or_replace_its_pipeline(client, alter):
    document = build_prompt(client).model_dump(mode="json")
    alter(document)
    with pytest.raises(ValidationError):
        PromptSnapshot.model_validate(document)


@pytest.mark.parametrize("legacy", [True, False])
def test_dispatch_selects_only_validated_encrypted_revision_and_preserves_arguments(client, monkeypatch, legacy):
    prompt = build_prompt(client, legacy=legacy)
    row = pinned_row(client, prompt)
    before = row.prompt_snapshot_ciphertext
    calls = install_handlers(monkeypatch)
    check = lambda: None
    assert dispatch(client, row, request_check=check) == prompt.fixed_rules_version
    assert calls == [(prompt.fixed_rules_version, prompt.prompt_version, "synthetic-targets", .73, check)]
    assert row.prompt_snapshot_ciphertext == before


@pytest.mark.parametrize("damage", ["old_as_new", "new_as_old", "corrupt_ciphertext", "missing_new_snapshot"])
def test_dispatch_rejects_plaintext_version_spoof_or_missing_new_provenance_before_handlers(client, monkeypatch, damage):
    old, new = build_prompt(client, legacy=True), build_prompt(client)
    row = pinned_row(client, old if damage == "old_as_new" else new)
    if damage == "old_as_new":
        row.prompt_version = new.prompt_version
    elif damage == "new_as_old":
        row.prompt_version = old.prompt_version
    elif damage == "corrupt_ciphertext":
        row.prompt_snapshot_ciphertext = "SYNTHETIC_INVALID_CIPHERTEXT"
    else:
        row.prompt_snapshot_ciphertext = None
    calls = install_handlers(monkeypatch)
    with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
        dispatch(client, row)
    assert calls == []


def test_unversioned_historical_row_dispatches_only_to_legacy_builder(client, monkeypatch):
    calls = install_handlers(monkeypatch)
    row = Analysis(prompt_version=None, prompt_snapshot_ciphertext=None)
    assert dispatch(client, row) == OLD
    assert calls[0][0] == OLD and row.prompt_snapshot_ciphertext is None


def test_mixed_v220_and_v221_dispatch_is_thread_isolated_without_worker_global_swaps(client, monkeypatch):
    old, new = build_prompt(client, legacy=True), build_prompt(client)
    rows = [pinned_row(client, old if index % 2 == 0 else new) for index in range(20)]
    names = ("execute_structured_agent", "build_agent_input", "_execute_grounded_agent", "_organize_evidence")
    old_globals = {name: getattr(worker, name) for name in names}
    new_globals = {name: getattr(worker_v221, name) for name in names}
    ciphertexts = [row.prompt_snapshot_ciphertext for row in rows]
    calls = install_handlers(monkeypatch, barrier=Barrier(2))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda row: dispatch(client, row), rows))
    assert results == [OLD if index % 2 == 0 else NEW for index in range(20)]
    assert len(calls) == 20 and sum(call[0] == OLD for call in calls) == 10
    assert all(getattr(worker, name) is value for name, value in old_globals.items())
    assert all(getattr(worker_v221, name) is value for name, value in new_globals.items())
    assert [row.prompt_snapshot_ciphertext for row in rows] == ciphertexts


def test_v220_baseline_and_already_queued_prompt_remain_valid_under_new_runtime(client, monkeypatch):
    identifier, before, prompt = seed_v220_baseline(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        assert approved_production_prompt(db, crypto) == prompt
        queued = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, queued) == prompt
        ciphertext = queued.prompt_snapshot_ciphertext
        assert load_analysis_prompt(queued, crypto) == prompt
        assert live_snapshot(db, crypto, client.app.state.settings) == before
        record = db.get(ProductionPromotion, identifier)
        assert record.snapshot_json == before and record.configuration_hash == configuration_digest(before)
    simulate_drift(monkeypatch)
    with client.app.state.session_factory() as db:
        assert approved_production_prompt(db, crypto) == prompt
        assert pin_analysis_prompt(db, crypto, Analysis(analysis_purpose="production")) == prompt
        assert load_analysis_prompt(queued, crypto) == prompt
        assert queued.prompt_snapshot_ciphertext == ciphertext


def test_legacy_v220_queue_reclaim_preserves_ciphertext_and_dispatches_original_runtime(
    client, event_payload, monkeypatch,
):
    _identifier, _baseline, prompt = seed_v220_baseline(client)
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        first = worker.claim_next(db, "synthetic-owner-one", 60)
        ciphertext = first.prompt_snapshot_ciphertext
        assert load_analysis_prompt(first, client.app.state.crypto) == prompt
        first.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        second = worker.claim_next(db, "synthetic-owner-two", 60)
        assert second.id == first.id and second.prompt_snapshot_ciphertext == ciphertext
        calls = install_handlers(monkeypatch)
        assert dispatch(client, second) == OLD
        assert calls[0][0] == OLD and second.prompt_snapshot_ciphertext == ciphertext


def test_failed_v220_retry_copies_frozen_pipeline_and_survives_new_runtime_drift(
    client, event_payload, registered_vllm_target, monkeypatch,
):
    _baseline_id, _baseline, old = seed_v220_baseline(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert response.status_code == 202, response.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, response.json()["id"])
        execution = make_execution_snapshot(original, db.get(VLLMProfile, profile_id), old, .73, crypto=crypto)
        original.execution_snapshot_ciphertext = crypto.encrypt_text(execution.model_dump_json())
        original.status, original.error_code, original.completed_at = "failed", "synthetic-original-failure", utcnow()
        db.commit()
        before = original.prompt_snapshot_ciphertext, original.payload_ciphertext, original.execution_snapshot_ciphertext
    simulate_drift(monkeypatch)
    result = retry(client, response.json()["id"], key="synthetic-v220-runtime-retry")
    assert result.status_code == 202, result.text
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, response.json()["id"])
        child = db.get(Analysis, result.json()["analysis_id"])
        assert original.status == "failed" and original.error_code == "synthetic-original-failure"
        assert (original.prompt_snapshot_ciphertext, original.payload_ciphertext,
                original.execution_snapshot_ciphertext) == before
        assert child.prompt_snapshot_ciphertext == before[0] and child.payload_ciphertext == before[1]
        assert load_analysis_prompt(child, crypto) == old
        assert load_execution_snapshot(child, crypto) == execution
        calls = install_handlers(monkeypatch)
        assert dispatch(client, child) == OLD and calls[0][0] == OLD


@pytest.mark.usefixtures("registered_vllm_target")
def test_new_candidate_pins_v221_without_replacing_approved_v220_production(
    client, event_payload, candidate,
):
    # Candidate setup is local metadata only; no analysis worker is run.
    _identifier, baseline, old = seed_v220_baseline(client)
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert response.status_code == 202, response.text
    run = response.json()
    with client.app.state.session_factory() as db:
        saved = db.get(NamedRun, run["id"])
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        assert load_analysis_prompt(saved, client.app.state.crypto).fixed_rules_version == NEW
        assert verify_configuration(saved, client.app.state.crypto, row) == run["configuration_snapshot"]
        assert run["configuration_snapshot"]["evidence_pipeline"] == evidence_pipeline_v221.pipeline_artifact()
        assert approved_production_prompt(db, client.app.state.crypto) == old
        assert live_snapshot(db, client.app.state.crypto, client.app.state.settings) == baseline


@pytest.mark.parametrize("damage", ["missing", "tampered", "file_drift"])
@pytest.mark.usefixtures("registered_vllm_target")
def test_v221_official_promotion_keeps_manifest_and_refuses_altered_provenance(
    client, event_payload, candidate, monkeypatch, damage,
):
    run = qualified(client, event_payload, candidate)
    baseline = client.get(BASE).json()
    response = client.post(BASE + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": baseline["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 200, response.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        approved = approved_production_prompt(db, crypto)
        assert approved.fixed_rules_version == NEW
        assert approved.evidence_pipeline == run["configuration_snapshot"]["evidence_pipeline"]
        record = db.get(ProductionPromotion, response.json()["id"])
        changed = deepcopy(record.snapshot_json)
        if damage == "missing":
            changed.pop("evidence_pipeline")
        elif damage == "tampered":
            changed["evidence_pipeline"]["sha256"] = "b" * 64
        if damage != "file_drift":
            record.snapshot_json, record.configuration_hash = changed, configuration_digest(changed)
            db.commit()
    if damage == "file_drift":
        simulate_drift(monkeypatch)
    with client.app.state.session_factory() as db:
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            approved_production_prompt(db, crypto)
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            pin_analysis_prompt(db, crypto, Analysis(analysis_purpose="production"))
