"""Prompt-only semantics revision with unchanged execution and approvals.

All rows, references and profiles are isolated synthetic fixtures. Qualification
explicitly disables the optional Editor, so the real official-evaluation gate
can be exercised without model calls or substituting its display-job checks.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import func, select

from app import worker, worker_v222
from app.agent import (
    evidence_pipeline_v222, legacy_prompts_v220, legacy_prompts_v221_reasoning,
    legacy_prompts_v222, prompts,
)
from app.models import Analysis, ProductionPromotion, TestEvaluation as EvaluationRecord, TestRun as NamedRun, VLLMProfile, utcnow
from app.services import prompt_snapshots, worker_runtime_v222
from app.services.analysis_retries import load_execution_snapshot, make_execution_snapshot
from app.services.candidate_configurations import configuration_digest, verify_configuration
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshotError, _baseline_rules, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from test_analysis_retries_keys import profile_fixture, retry
from test_candidate_configurations import candidate, direct
from test_compact_prompt_preservation import ENGINE_SHA256 as HISTORICAL_ENGINE_SHA256
from test_production_promotion import BASE, qualified
from test_validation_data import login


pytestmark = pytest.mark.usefixtures("registered_vllm_target")
FROZEN_V222_SHA256 = "a9f0aa75f947cc9f6c779a60400e6eb7beb8686d62b9770746d79dd77b7ebd84"
ENGINE_SHA256 = {
    **HISTORICAL_ENGINE_SHA256,
    "evidence_pipeline_v222.py": "3c7effaa948139dc277143babd08d183ff58919007a71393fbd21db21d8ebd33",
    "input_builder_v222.py": "16027567b0bd06dd661dc5db42155424f2f66ff153da31bbcf49bb035b20ce7f",
    "executor_v222.py": "fb7435d96da2366cef0e64370f652a0b9d33e0f354adf30d3c8b76e7cf519e6c",
    "relation_recovery.py": "12994d6704f84ff86dd8035db4c007acfdeacc98f29597be11693b321255059c",
    "execution_policy_v222.py": "2a2c3caafae1812eea273ec844dfb94946e7515cfa993768c509b6c5990f8936",
    "../worker_v222.py": "bafc7d00bbdb75a5438b1856eefbfdec5f1e62fa3267de9f80cc57f3c8af1618",
    "../services/worker_runtime_v222.py": "3bd72aa1eda7cf8036d28b241764de334dee3334e1be2c7401b9465923bbbc7b",
    "../services/worker_concurrency_v222.py": "cf15b5cd69ad29db2e51da7a3076df49e4b3790a0cce6d69441357eca4445f7c",
    "../services/model_validation_worker_v222.py": "a0cb148a8e130f58bf9b23c9a2ad451c33efc1ba52ac1f2d2a2eda9b2e21bf0b",
    "../worker_entrypoint_v222.py": "4dd18a68c2d64fc2c4b6116eb6e6cfbbee43b91614cd5a092196090133b40fed",
}


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def snapshot(client, rules):
    with client.app.state.session_factory() as db:
        return _build_snapshot(get_active_policy(db, client.app.state.crypto), client.app.state.crypto, rules=rules)


def seed_baseline(client, rules):
    login(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        current = live_snapshot(db, crypto, client.app.state.settings)
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
        document = {**current, "schema_version": 3,
            "prompt": {key: getattr(prompt, key) for key in current["prompt"]},
            "evidence_pipeline": prompt.evidence_pipeline}
        record = ProductionPromotion(kind="baseline", snapshot_json=document,
            configuration_hash=configuration_digest(document), actor_id="synthetic-before-evidence-semantics")
        db.add(record)
        db.commit()
        return record.id, deepcopy(document), prompt


@contextmanager
def archived_admissions(monkeypatch):
    """Admit saved text without hiding the current module from hash resolution."""
    build = prompt_snapshots._build_snapshot
    def archived(version, crypto, *, origin="enqueue", rules=None):
        return build(version, crypto, origin=origin, rules=rules or legacy_prompts_v222)
    with monkeypatch.context() as scope:
        scope.setattr(prompt_snapshots, "_build_snapshot", archived)
        yield


def without_editor(candidate):
    return {**candidate, "evidence_editor_enabled": False, "evidence_editor_profile_id": None}


def test_original_v222_module_is_byte_exact_and_new_text_has_distinct_identity():
    assert hashlib.sha256(Path(legacy_prompts_v222.__file__).read_bytes()).hexdigest() == FROZEN_V222_SHA256
    assert legacy_prompts_v222.FIXED_RULES_VERSION == prompts.FIXED_RULES_VERSION == "waf-system-v2.22"
    assert legacy_prompts_v222.PROMPT_VERSION == prompts.PROMPT_VERSION == "waf-judgment-v2.22"
    assert text_hash(legacy_prompts_v222.FIXED_INSTRUCTIONS) != text_hash(prompts.FIXED_INSTRUCTIONS)
    assert legacy_prompts_v222.V3_RULES_VERSIONS == prompts.V3_RULES_VERSIONS
    assert legacy_prompts_v222.SEMANTIC_RULES_VERSIONS == prompts.SEMANTIC_RULES_VERSIONS
    assert legacy_prompts_v222.PRIMARY_ROLE == prompts.PRIMARY_ROLE
    assert legacy_prompts_v222.VERIFIER_ROLE == prompts.VERIFIER_ROLE


@pytest.mark.parametrize("filename,expected", ENGINE_SHA256.items())
def test_prompt_only_change_preserves_all_forty_runtime_files_byte_exact(filename, expected):
    assert len(ENGINE_SHA256) == 40
    assert set(ENGINE_SHA256) == set(evidence_pipeline_v222.pipeline_artifact()["files"])
    assert hashlib.sha256((Path(evidence_pipeline_v222.__file__).parent / filename).read_bytes()).hexdigest() == expected


def test_old_and_new_v222_share_execution_but_not_instruction_identity(client):
    old, new = snapshot(client, legacy_prompts_v222), snapshot(client, prompts)
    assert old.evidence_pipeline == new.evidence_pipeline == evidence_pipeline_v222.pipeline_artifact()
    assert old.evidence_pipeline["files"] == ENGINE_SHA256
    assert old.fixed_rules_hash == text_hash(legacy_prompts_v222.FIXED_INSTRUCTIONS)
    assert new.fixed_rules_hash == text_hash(prompts.FIXED_INSTRUCTIONS)
    assert old.fixed_rules_hash != new.fixed_rules_hash and old.instructions_hash != new.instructions_hash
    changed = {name for name in type(old).model_fields if getattr(old, name) != getattr(new, name)}
    assert changed == {"fixed_rules_hash", "instructions_hash", "primary_instructions", "verifier_instructions"}
    assert _baseline_rules(old.metadata()) is legacy_prompts_v222
    assert _baseline_rules(new.metadata()) is prompts


@pytest.mark.parametrize("rules", [legacy_prompts_v220, legacy_prompts_v221_reasoning, legacy_prompts_v222],
    ids=["approved-v220", "approved-v221", "approved-original-v222"])
def test_baseline_restores_exact_approved_text_and_does_not_rewrite_existing_records(client, rules):
    identifier, before, expected = seed_baseline(client, rules)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        assert approved_production_prompt(db, crypto) == expected
        queued = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, queued) == expected
        cipher = queued.prompt_snapshot_ciphertext
        assert load_analysis_prompt(queued, crypto) == expected
        assert live_snapshot(db, crypto, client.app.state.settings) == before
        assert queued.prompt_snapshot_ciphertext == cipher
        record = db.get(ProductionPromotion, identifier)
        assert record.snapshot_json == before and record.configuration_hash == configuration_digest(before)


def test_queued_reclaimed_v222_keeps_original_ciphertext_and_exact_instructions(client, event_payload, monkeypatch):
    _identifier, _before, expected = seed_baseline(client, legacy_prompts_v222)
    created = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert created.status_code == 202, created.text
    calls = []
    def execute(db, crypto, row, _targets, _threshold, *, request_check=None):
        current = load_analysis_prompt(row, crypto)
        assert current == expected
        assert current.evidence_pipeline["files"] == ENGINE_SHA256
        calls.append(current)
    monkeypatch.setattr(worker_v222, "process_moduagent", execute)
    with client.app.state.session_factory() as db:
        first = worker.claim_next(db, "synthetic-before-semantics", 60)
        cipher = first.prompt_snapshot_ciphertext
        first.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        reclaimed = worker.claim_next(db, "synthetic-after-semantics", 60)
        assert reclaimed.id == first.id and reclaimed.prompt_snapshot_ciphertext == cipher
        worker_runtime_v222.process_moduagent(db, client.app.state.crypto, reclaimed, "", .75)
        assert calls == [expected] and reclaimed.prompt_snapshot_ciphertext == cipher


def test_original_v222_retry_preserves_input_prompt_and_execution_snapshots(client, event_payload, monkeypatch):
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    _identifier, _before, old = seed_baseline(client, legacy_prompts_v222)
    created = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert created.status_code == 202, created.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, created.json()["id"])
        execution = make_execution_snapshot(original, db.get(VLLMProfile, profile_id), old, .73, crypto=crypto)
        original.execution_snapshot_ciphertext = crypto.encrypt_text(execution.model_dump_json())
        original.status, original.error_code, original.completed_at = "failed", "synthetic-original-failure", utcnow()
        db.commit()
        before = (original.prompt_snapshot_ciphertext, original.payload_ciphertext, original.execution_snapshot_ciphertext)
    response = retry(client, created.json()["id"], key="synthetic-original-v222-semantics-retry")
    assert response.status_code == 202, response.text
    calls = []
    monkeypatch.setattr(worker_v222, "process_moduagent", lambda db, key, row, *_a, **_k: calls.append(load_analysis_prompt(row, key)))
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, created.json()["id"])
        child = db.get(Analysis, response.json()["analysis_id"])
        assert original.status == "failed" and original.error_code == "synthetic-original-failure"
        assert (original.prompt_snapshot_ciphertext, original.payload_ciphertext, original.execution_snapshot_ciphertext) == before
        assert (child.prompt_snapshot_ciphertext, child.payload_ciphertext) == before[:2]
        assert load_analysis_prompt(child, crypto) == old and load_execution_snapshot(child, crypto) == execution
        worker_runtime_v222.process_moduagent(db, crypto, child, "", .73)
        assert calls == [old] and child.prompt_snapshot_ciphertext == before[0]


@pytest.mark.parametrize("damage", ["unknown_hash", "unknown_version", "wrong_version"])
def test_unknown_or_mismatched_baseline_identity_is_rejected(client, damage):
    expected = snapshot(client, legacy_prompts_v222).metadata()
    if damage == "unknown_hash":
        expected["fixed_rules_hash"] = "0" * 64
    elif damage == "unknown_version":
        expected["fixed_rules_version"] = "waf-system-v999"
    else:
        expected["fixed_rules_version"] = "waf-system-v2.20"
    with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
        _baseline_rules(expected)


def test_duplicate_module_alias_is_not_ambiguous_but_distinct_matching_modules_are(client, monkeypatch):
    expected = snapshot(client, legacy_prompts_v222).metadata()
    with monkeypatch.context() as scope:
        scope.setattr("app.agent.prompts", legacy_prompts_v222)
        assert _baseline_rules(expected) is legacy_prompts_v222
    duplicate = ModuleType("synthetic_duplicate_original_v222")
    duplicate.FIXED_RULES_VERSION, duplicate.FIXED_INSTRUCTIONS = legacy_prompts_v222.FIXED_RULES_VERSION, legacy_prompts_v222.FIXED_INSTRUCTIONS
    monkeypatch.setattr("app.agent.legacy_prompts_v220", duplicate)
    with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
        _baseline_rules(expected)


def test_rehashing_only_the_new_fixed_text_cannot_transfer_a_previous_baseline_approval(client):
    identifier, _before, expected = seed_baseline(client, legacy_prompts_v222)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        queued = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, queued) == expected
        cipher = queued.prompt_snapshot_ciphertext
        record = db.get(ProductionPromotion, identifier)
        document = deepcopy(record.snapshot_json)
        document["prompt"]["fixed_rules_hash"] = text_hash(prompts.FIXED_INSTRUCTIONS)
        record.snapshot_json, record.configuration_hash = document, configuration_digest(document)
        db.commit()
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            approved_production_prompt(db, crypto)
        assert load_analysis_prompt(queued, crypto) == expected and queued.prompt_snapshot_ciphertext == cipher


def test_old_official_evaluation_remains_frozen_and_does_not_authorize_the_new_text(client, event_payload, candidate, monkeypatch):
    candidate = without_editor(candidate)
    with archived_admissions(monkeypatch):
        original = qualified(client, event_payload, candidate)
    original_configuration = original["configuration_snapshot"]
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        old_run = db.get(NamedRun, original["id"])
        record = db.scalar(select(EvaluationRecord).where(EvaluationRecord.test_run_id == old_run.id))
        assert record is not None
        before_evaluation = deepcopy(record.summary_json), record.configuration_hash
        old_prompt_cipher = old_run.prompt_snapshot_ciphertext
        assert old_run.display_editor_snapshot_ciphertext is None
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert response.status_code == 202, response.text
    current = response.json()
    assert current["configuration_hash"] != original["configuration_hash"]
    assert current["configuration_snapshot"]["evidence_pipeline"] == original_configuration["evidence_pipeline"]
    assert current["configuration_snapshot"]["prompt"]["fixed_rules_hash"] == text_hash(prompts.FIXED_INSTRUCTIONS)
    review = client.get(f"{BASE}/preflight/{original['id']}")
    assert review.status_code == 200, review.text
    checks = {item["code"]: item["passed"] for item in review.json()["checks"]}
    assert checks["candidate_snapshot_valid"] and not checks["tested_instructions_still_current"]
    baseline = client.get(BASE).json()
    rejected = client.post(BASE + "/promote", json={"candidate_test_run_id": original["id"],
        "expected_production_configuration_hash": baseline["configuration_hash"], "acknowledge_schema_change": True})
    assert rejected.status_code == 409 and rejected.json()["detail"] == "candidate_not_eligible_for_promotion"
    with client.app.state.session_factory() as db:
        old_run, new_run = db.get(NamedRun, original["id"]), db.get(NamedRun, current["id"])
        assert verify_configuration(old_run, crypto) == original_configuration
        assert old_run.prompt_snapshot_ciphertext == old_prompt_cipher
        record = db.scalar(select(EvaluationRecord).where(EvaluationRecord.test_run_id == old_run.id))
        assert (record.summary_json, record.configuration_hash) == before_evaluation
        assert db.scalar(select(func.count()).select_from(EvaluationRecord).where(EvaluationRecord.test_run_id == new_run.id)) == 0
        assert db.scalar(select(func.count()).select_from(ProductionPromotion).where(ProductionPromotion.kind == "promotion")) == 0


def test_already_promoted_v222_keeps_approved_text_after_candidate_guidance_changes(client, event_payload, candidate, monkeypatch):
    candidate = without_editor(candidate)
    with archived_admissions(monkeypatch):
        original = qualified(client, event_payload, candidate)
        baseline = client.get(BASE).json()
        response = client.post(BASE + "/promote", json={"candidate_test_run_id": original["id"],
            "expected_production_configuration_hash": baseline["configuration_hash"], "acknowledge_schema_change": True})
        assert response.status_code == 200, response.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        run = db.get(NamedRun, original["id"])
        expected, cipher = load_analysis_prompt(run, crypto), run.prompt_snapshot_ciphertext
        record = db.get(ProductionPromotion, response.json()["id"])
        before = deepcopy(record.snapshot_json), record.configuration_hash
        assert approved_production_prompt(db, crypto) == expected
        assert expected.fixed_rules_hash == text_hash(legacy_prompts_v222.FIXED_INSTRUCTIONS)
        assert expected.fixed_rules_hash != text_hash(prompts.FIXED_INSTRUCTIONS)
        assert live_snapshot(db, crypto, client.app.state.settings) == original["configuration_snapshot"]
        assert run.prompt_snapshot_ciphertext == cipher and (record.snapshot_json, record.configuration_hash) == before
    created = client.post("/api/v1/analyses?wait_seconds=0", json={**event_payload,
        "event_id": "synthetic-after-semantics-still-approved-original", "vendor_score": 2})
    assert created.status_code == 202, created.text
    with client.app.state.session_factory() as db:
        assert load_analysis_prompt(db.get(Analysis, created.json()["id"]), crypto) == expected
