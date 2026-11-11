"""Synthetic, offline pinning of candidate input/evidence artifacts.

Artifacts are measured during each test, not asserted against a moving source
tree's hard-coded hash. File drift is simulated without writing source files.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agent import evidence_pipeline, legacy_prompts_v219, legacy_prompts_v220
from app.models import Analysis, ProductionPromotion, TestRun as NamedRun, VLLMProfile
from app.services.analysis import AnalysisIngestError
from app.services.analysis_retries import (
    ExecutionSnapshot, RetryError, load_execution_snapshot, make_execution_snapshot,
)
from app.services.candidate_configurations import configuration_digest, verify_configuration
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshot, PromptSnapshotError, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from test_candidate_configurations import candidate, direct
from test_production_promotion import BASE as PRODUCTION_BASE, qualified
from test_validation_data import login

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


@pytest.fixture(autouse=True)
def frozen_v220_candidate_rules(monkeypatch):
    # These tests recreate the v2.20 release's actual candidate admission and
    # promotion. Do not turn a v2.21 snapshot into v2.20 by rewriting hashes or
    # encrypted provenance; select the frozen rules before admission instead.
    monkeypatch.setattr("app.agent.prompts", legacy_prompts_v220)


def build_prompt(client, *, legacy=False):
    with client.app.state.session_factory() as db:
        return _build_snapshot(get_active_policy(db, client.app.state.crypto), client.app.state.crypto,
            **({"rules": legacy_prompts_v219} if legacy else {}))


def candidate_run(client, event_payload, candidate):
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert response.status_code == 202, response.text
    return response.json()


def install_execution_snapshot(client, run):
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, run["items"][0]["analysis_id"])
        profile = db.get(VLLMProfile, run["configuration_snapshot"]["primary"]["profile_id"])
        verifier = db.get(VLLMProfile, run["configuration_snapshot"]["verifier"]["profile_id"])
        snapshot = make_execution_snapshot(row, profile, load_analysis_prompt(row, client.app.state.crypto), .75,
            verifier_profile=verifier, crypto=client.app.state.crypto)
        row.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(snapshot.model_dump_json())
        db.commit()
        return row.id, snapshot


def simulate_file_drift(monkeypatch, filename="source_blocks.py"):
    original = Path.read_bytes
    path = (Path(evidence_pipeline.__file__).parent / filename).resolve()

    def read_bytes(current):
        content = original(current)
        return content + b"\n# synthetic artifact drift\n" if current.resolve() == path else content

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def test_manifest_identity_is_complete_deterministic_and_contains_no_content():
    manifest = evidence_pipeline.pipeline_artifact()
    assert manifest == evidence_pipeline.pipeline_artifact()
    assert manifest["version"] == "evidence-pipeline-v1"
    assert manifest["input_version"] == "agent-input-v2"
    assert manifest["source_catalog_version"] == "evidence-selection-v3"
    assert manifest["relations_version"] == "evidence-relations-v1"
    assert {"source_blocks.py", "contracts.py", "evidence_relations.py", "../worker.py",
            "executor.py", "policy.py", "input_integrity.py", "followup_policy.py",
            "../services/payload_decoding.py", "../services/request_integrity.py",
            "../services/json_duplicate_values.py", "../services/log4j_normalization.py"} <= manifest["files"].keys()
    assert all(len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
               for digest in manifest["files"].values())
    document = {key: value for key, value in manifest.items() if key != "sha256"}
    expected = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert manifest["sha256"] == expected
    assert evidence_pipeline.validate_pipeline_snapshot(manifest, "waf-system-v2.20") == manifest


@pytest.mark.parametrize("rules", [None, "waf-system-v2.16", "waf-system-v2.19", "unknown"])
def test_historical_and_unknown_rules_never_inherit_latest_artifacts(rules):
    assert evidence_pipeline.pipeline_for_rules(rules) is None
    assert evidence_pipeline.validate_pipeline_snapshot(None, rules) is None
    with pytest.raises(ValueError, match="^evidence_pipeline_snapshot_invalid$"):
        evidence_pipeline.validate_pipeline_snapshot(evidence_pipeline.pipeline_artifact(), rules)


@pytest.mark.parametrize("alter", [
    lambda manifest: None,
    lambda manifest: {**manifest, "sha256": "0" * 64},
    lambda manifest: {**manifest, "version": "evidence-pipeline-v2"},
    lambda manifest: {**manifest, "files": {}},
    lambda manifest: {**manifest, "extra": "SYNTHETIC_SECRET"},
])
def test_v220_rejects_missing_modified_or_extended_manifest(alter):
    with pytest.raises(ValueError, match="^evidence_pipeline_snapshot_invalid$"):
        evidence_pipeline.validate_pipeline_snapshot(alter(evidence_pipeline.pipeline_artifact()), "waf-system-v2.20")


def test_manifest_file_read_failure_has_only_fixed_diagnostic(monkeypatch):
    def unavailable(current):
        raise OSError("SYNTHETIC_SECRET_MUST_NOT_LEAK")
    monkeypatch.setattr(Path, "read_bytes", unavailable)
    with pytest.raises(ValueError, match="^evidence_pipeline_unavailable$") as failure:
        evidence_pipeline.pipeline_artifact()
    assert "SYNTHETIC_SECRET" not in str(failure.value)


@pytest.mark.parametrize("filename", ["source_blocks.py", "evidence_relations.py", "contracts.py", "../worker.py"])
def test_each_runtime_artifact_change_invalidates_previously_frozen_identity(monkeypatch, filename):
    before = evidence_pipeline.pipeline_artifact()
    simulate_file_drift(monkeypatch, filename)
    after = evidence_pipeline.pipeline_artifact()
    assert before["sha256"] != after["sha256"]
    assert [key for key in before["files"] if before["files"][key] != after["files"][key]] == [filename]
    with pytest.raises(ValueError, match="^evidence_pipeline_snapshot_invalid$"):
        evidence_pipeline.validate_pipeline_snapshot(before, "waf-system-v2.20")
    assert evidence_pipeline.validate_pipeline_snapshot(after, "waf-system-v2.20") == after


def test_prompt_schema_two_freezes_pipeline_but_does_not_publish_manifest_metadata(client):
    prompt = build_prompt(client)
    assert prompt.schema_version == 2 and prompt.fixed_rules_version == "waf-system-v2.20"
    assert prompt.evidence_pipeline == evidence_pipeline.pipeline_artifact()
    assert PromptSnapshot.model_validate_json(prompt.model_dump_json()) == prompt
    metadata = prompt.metadata()
    assert not {"evidence_pipeline", "schema_version", "primary_instructions", "verifier_instructions"}.intersection(metadata)
    assert all(name not in json.dumps(metadata) for name in prompt.evidence_pipeline["files"])


@pytest.mark.parametrize("alter", [
    lambda document: document.pop("evidence_pipeline"),
    lambda document: document.update(evidence_pipeline=None),
    lambda document: document.update(schema_version=1),
    lambda document: document["evidence_pipeline"].update(sha256="f" * 64),
])
def test_v220_prompt_cannot_drop_manifest_or_downgrade_snapshot_version(client, alter):
    document = build_prompt(client).model_dump(mode="json")
    alter(document)
    with pytest.raises(ValidationError):
        PromptSnapshot.model_validate(document)


def test_legacy_prompt_remains_schema_one_without_pipeline_and_pins_exact_saved_instructions(client):
    prompt = build_prompt(client, legacy=True)
    assert prompt.schema_version == 1 and prompt.fixed_rules_version == "waf-system-v2.19"
    assert prompt.evidence_pipeline is None
    document = prompt.model_dump(mode="json")
    document.pop("evidence_pipeline")
    assert PromptSnapshot.model_validate(document) == prompt
    row = Analysis(analysis_purpose="production", prompt_version=prompt.prompt_version,
        prompt_policy_version_id=prompt.policy_version_id,
        prompt_snapshot_ciphertext=client.app.state.crypto.encrypt_text(json.dumps(document)))
    before = row.prompt_snapshot_ciphertext
    with client.app.state.session_factory() as db:
        assert pin_analysis_prompt(db, client.app.state.crypto, row) == prompt
    assert row.prompt_snapshot_ciphertext == before
    assert load_analysis_prompt(row, client.app.state.crypto).primary_instructions == prompt.primary_instructions
    document["evidence_pipeline"] = evidence_pipeline.pipeline_artifact()
    with pytest.raises(ValidationError):
        PromptSnapshot.model_validate(document)


def test_existing_v219_baseline_restores_v219_not_new_candidate_rules(client):
    login(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        current = live_snapshot(db, crypto, client.app.state.settings)
        legacy = _build_snapshot(get_active_policy(db, crypto), crypto, rules=legacy_prompts_v219)
        before = {**current, "schema_version": 2,
            "prompt": {key: getattr(legacy, key) for key in current["prompt"]}}
        before.pop("evidence_pipeline", None)
        db.add(ProductionPromotion(kind="baseline", snapshot_json=before,
            configuration_hash=configuration_digest(before), actor_id="synthetic-v219-baseline"))
        db.commit()
        restored = approved_production_prompt(db, crypto)
        assert restored == legacy and restored.evidence_pipeline is None
        row = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, row) == legacy
        assert live_snapshot(db, crypto, client.app.state.settings) == before
        assert db.scalar(select(ProductionPromotion)).configuration_hash == configuration_digest(before)


def test_candidate_schema_three_pins_same_manifest_for_run_and_item_without_public_analysis_leak(
    client, event_payload, candidate,
):
    response = candidate_run(client, event_payload, candidate)
    configuration = response["configuration_snapshot"]
    assert configuration["schema_version"] == 3
    assert configuration["evidence_pipeline"] == evidence_pipeline.pipeline_artifact()
    assert response["configuration_hash"] == configuration_digest(configuration)
    with client.app.state.session_factory() as db:
        saved = db.get(NamedRun, response["id"])
        row = db.get(Analysis, response["items"][0]["analysis_id"])
        assert verify_configuration(saved, client.app.state.crypto, row) == configuration
        assert load_analysis_prompt(saved, client.app.state.crypto).evidence_pipeline == configuration["evidence_pipeline"]
        assert load_analysis_prompt(row, client.app.state.crypto).evidence_pipeline == configuration["evidence_pipeline"]
    public = client.get(f"/api/v1/analyses/{response['items'][0]['analysis_id']}")
    assert public.status_code == 200, public.text
    assert "evidence_pipeline" not in public.text and "evidence_relations.py" not in public.text


@pytest.mark.parametrize("alter", [
    lambda value: value.pop("evidence_pipeline"),
    lambda value: value.update(schema_version=2),
    lambda value: value["evidence_pipeline"].update(sha256="a" * 64),
])
def test_rehashing_modified_candidate_does_not_override_queued_pipeline(client, event_payload, candidate, alter):
    response = candidate_run(client, event_payload, candidate)
    with client.app.state.session_factory() as db:
        saved = db.get(NamedRun, response["id"])
        changed = deepcopy(saved.configuration_snapshot_json)
        alter(changed)
        saved.configuration_snapshot_json, saved.configuration_hash = changed, configuration_digest(changed)
        with pytest.raises(AnalysisIngestError, match="^candidate_configuration_invalid$"):
            verify_configuration(saved, client.app.state.crypto)


def test_candidate_refuses_individually_valid_historical_item_prompt(client, event_payload, candidate):
    response = candidate_run(client, event_payload, candidate)
    with client.app.state.session_factory() as db:
        saved = db.get(NamedRun, response["id"])
        row = db.get(Analysis, response["items"][0]["analysis_id"])
        legacy = _build_snapshot(get_active_policy(db, client.app.state.crypto), client.app.state.crypto, rules=legacy_prompts_v219)
        row.prompt_version, row.prompt_policy_version_id = legacy.prompt_version, legacy.policy_version_id
        row.prompt_snapshot_ciphertext = client.app.state.crypto.encrypt_text(legacy.model_dump_json())
        assert load_analysis_prompt(row, client.app.state.crypto) == legacy
        with pytest.raises(AnalysisIngestError, match="^candidate_configuration_invalid$"):
            verify_configuration(saved, client.app.state.crypto, row)


def test_execution_schema_five_binds_pipeline_and_old_v219_execution_stays_four(client, event_payload, candidate):
    response = candidate_run(client, event_payload, candidate)
    identifier, execution = install_execution_snapshot(client, response)
    assert execution.schema_version == 5
    assert execution.evidence_pipeline == response["configuration_snapshot"]["evidence_pipeline"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        assert load_execution_snapshot(row, client.app.state.crypto) == execution
        profile = db.get(VLLMProfile, execution.profile_id)
        legacy = _build_snapshot(get_active_policy(db, client.app.state.crypto), client.app.state.crypto, rules=legacy_prompts_v219)
        historical = make_execution_snapshot(row, profile, legacy, .75, crypto=client.app.state.crypto)
        assert historical.schema_version == 4 and historical.evidence_pipeline is None
        old_document = historical.model_dump(mode="json")
        old_document.pop("evidence_pipeline")
        assert ExecutionSnapshot.model_validate(old_document) == historical


@pytest.mark.parametrize("alter", [
    lambda document: document.pop("evidence_pipeline"),
    lambda document: document.update(evidence_pipeline=None),
    lambda document: document.update(schema_version=4),
])
def test_execution_schema_five_cannot_drop_pipeline_or_claim_old_version(client, event_payload, candidate, alter):
    response = candidate_run(client, event_payload, candidate)
    _, execution = install_execution_snapshot(client, response)
    document = execution.model_dump(mode="json")
    alter(document)
    with pytest.raises(ValidationError):
        ExecutionSnapshot.model_validate(document)


def test_retry_binding_rejects_pipeline_different_from_encrypted_prompt(client, event_payload, candidate):
    response = candidate_run(client, event_payload, candidate)
    identifier, execution = install_execution_snapshot(client, response)
    document = execution.model_dump(mode="json")
    document["evidence_pipeline"]["sha256"] = "b" * 64
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(json.dumps(document))
        with pytest.raises(RetryError, match="^retry_execution_snapshot_invalid$"):
            load_execution_snapshot(row, client.app.state.crypto)


def test_file_drift_blocks_candidate_prompt_and_retry_loading_before_model_calls(
    client, event_payload, candidate, monkeypatch,
):
    response = candidate_run(client, event_payload, candidate)
    identifier, _ = install_execution_snapshot(client, response)
    simulate_file_drift(monkeypatch)
    with client.app.state.session_factory() as db:
        run = db.get(NamedRun, response["id"])
        row = db.get(Analysis, identifier)
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            load_analysis_prompt(row, client.app.state.crypto)
        with pytest.raises(AnalysisIngestError, match="^candidate_configuration_invalid$"):
            verify_configuration(run, client.app.state.crypto, row)
        with pytest.raises(RetryError, match="^retry_input_or_prompt_snapshot_unavailable$"):
            load_execution_snapshot(row, client.app.state.crypto)


def test_v219_retry_without_manifest_survives_unrelated_new_pipeline_artifact_drift(
    client, event_payload, candidate, monkeypatch,
):
    response = candidate_run(client, event_payload, candidate)
    identifier, _ = install_execution_snapshot(client, response)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        legacy = _build_snapshot(get_active_policy(db, crypto), crypto, rules=legacy_prompts_v219)
        row.prompt_version, row.prompt_policy_version_id = legacy.prompt_version, legacy.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(legacy.model_dump_json())
        profile = db.get(VLLMProfile, response["configuration_snapshot"]["primary"]["profile_id"])
        execution = make_execution_snapshot(row, profile, legacy, .75, crypto=crypto)
        row.execution_snapshot_ciphertext = crypto.encrypt_text(execution.model_dump_json())
        before = row.prompt_snapshot_ciphertext
        simulate_file_drift(monkeypatch)
        assert load_analysis_prompt(row, crypto) == legacy
        assert load_execution_snapshot(row, crypto) == execution
        assert row.prompt_snapshot_ciphertext == before


def seed_v220_baseline(client):
    login(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        current = live_snapshot(db, crypto, client.app.state.settings)
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        document = {**current, "schema_version": 3,
            "prompt": {key: getattr(prompt, key) for key in current["prompt"]},
            "evidence_pipeline": prompt.evidence_pipeline}
        record = ProductionPromotion(kind="baseline", snapshot_json=document,
            configuration_hash=configuration_digest(document), actor_id="synthetic-v220-baseline")
        db.add(record)
        db.commit()
        assert approved_production_prompt(db, crypto) == prompt
        queued = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, queued) == prompt
        return record.id, queued, prompt


@pytest.mark.parametrize("filename", ["source_blocks.py", "../worker.py"])
def test_v220_baseline_artifact_drift_blocks_approval_saved_prompt_and_new_production_admission(
    client, event_payload, monkeypatch, filename,
):
    identifier, queued, _ = seed_v220_baseline(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        record = db.get(ProductionPromotion, identifier)
        before_document, before_hash = deepcopy(record.snapshot_json), record.configuration_hash
    simulate_file_drift(monkeypatch, filename)
    with client.app.state.session_factory() as db:
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            approved_production_prompt(db, crypto)
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            load_analysis_prompt(queued, crypto)
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            pin_analysis_prompt(db, crypto, Analysis(analysis_purpose="production"))
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert response.status_code == 503 and response.json()["detail"] == "prompt_snapshot_invalid"
    with client.app.state.session_factory() as db:
        record = db.get(ProductionPromotion, identifier)
        assert record.snapshot_json == before_document and record.configuration_hash == before_hash
        assert db.scalar(select(Analysis.id)) is None


@pytest.mark.parametrize("alter", [
    lambda value: value.pop("evidence_pipeline"),
    lambda value: value.update(evidence_pipeline=None),
    lambda value: value["evidence_pipeline"].update(sha256="d" * 64),
    lambda value: value["evidence_pipeline"]["files"].update({"source_blocks.py": "e" * 64}),
])
def test_rehashed_v220_baseline_cannot_remove_or_replace_approved_pipeline(client, event_payload, alter):
    identifier, queued, prompt = seed_v220_baseline(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        record = db.get(ProductionPromotion, identifier)
        document = deepcopy(record.snapshot_json)
        alter(document)
        record.snapshot_json, record.configuration_hash = document, configuration_digest(document)
        db.commit()
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            approved_production_prompt(db, crypto)
        # A previously valid encrypted job does not acquire a changed approval.
        assert load_analysis_prompt(queued, crypto) == prompt
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert response.status_code == 503 and response.json()["detail"] == "prompt_snapshot_invalid"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(Analysis.id)) is None


@pytest.mark.parametrize("damage", ["source_blocks.py", "../worker.py", "missing", "modified"])
def test_v220_promotion_preserves_pipeline_and_refuses_drift_or_rehashed_missing_manifest(
    client, event_payload, candidate, monkeypatch, damage,
):
    run = qualified(client, event_payload, candidate)
    baseline = client.get(PRODUCTION_BASE).json()
    response = client.post(f"{PRODUCTION_BASE}/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": baseline["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 200
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        approved = approved_production_prompt(db, crypto)
        assert approved.evidence_pipeline == run["configuration_snapshot"]["evidence_pipeline"]
        if damage in {"missing", "modified"}:
            record = db.get(ProductionPromotion, response.json()["id"])
            document = deepcopy(record.snapshot_json)
            if damage == "missing":
                document.pop("evidence_pipeline")
            else:
                document["evidence_pipeline"]["sha256"] = "f" * 64
            record.snapshot_json, record.configuration_hash = document, configuration_digest(document)
            db.commit()
    if damage not in {"missing", "modified"}:
        simulate_file_drift(monkeypatch, damage)
    with client.app.state.session_factory() as db:
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            approved_production_prompt(db, crypto)
    response = client.post("/api/v1/analyses?wait_seconds=0", json={**event_payload,
        "event_id": "after-invalid-promotion", "vendor_score": 2})
    assert response.status_code == 503 and response.json()["detail"] == "prompt_snapshot_invalid"
