"""Offline preservation and release gates for the v2.24 text-only revision.

All records use isolated synthetic fixtures. Official evaluation completions
are explicit mocks with the Editor disabled, not measurements of model quality.
The real publication, qualification and promotion paths remain in use.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import func, select

from app import worker, worker_v224
from app.agent import evidence_pipeline_v224, legacy_prompts_v224, prompts
from app.models import (
    Analysis, ProductionPromotion, TestEvaluation as EvaluationRecord,
    TestRun as NamedRun, VLLMProfile, utcnow,
)
from app.services import prompt_snapshots, worker_runtime_v224
from app.services.analysis_retries import load_execution_snapshot, make_execution_snapshot
from app.services.candidate_configurations import configuration_digest, verify_configuration
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshotError, _baseline_rules, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from test_analysis_retries_keys import profile_fixture, retry
from test_candidate_configurations import candidate
from test_signature_context_lifecycle import direct, qualified
from test_validation_data import forbid_model_calls, login


pytestmark = pytest.mark.usefixtures("registered_vllm_target")
FROZEN_PROMPT_SHA256 = "94f166e554a46a63e3e5a8d0f7b7b361a226d97edee06e2017591e937f2dca49"
FROZEN_PIPELINE_SHA256 = "7b7ad16e74c7b0784ad28d161f32443db4a1e304d1c03b01eccf06f6fe126830"
BASE = "/api/v1/admin/production-configurations"


@pytest.fixture(autouse=True)
def forbid_current_model_calls(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("Prompt preservation tests must never invoke a model")

    monkeypatch.setattr(worker_v224, "execute_structured_agent", forbidden)


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def snapshot(client, rules):
    with client.app.state.session_factory() as db:
        return _build_snapshot(get_active_policy(db, client.app.state.crypto),
                               client.app.state.crypto, rules=rules)


def seed_baseline(client):
    login(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        current = live_snapshot(db, crypto, client.app.state.settings)
        old = _build_snapshot(get_active_policy(db, crypto), crypto, rules=legacy_prompts_v224)
        document = {**current,
                    "prompt": {key: getattr(old, key) for key in current["prompt"]},
                    "evidence_pipeline": old.evidence_pipeline}
        record = ProductionPromotion(kind="baseline", snapshot_json=document,
            configuration_hash=configuration_digest(document), actor_id="synthetic-before-capture-semantics")
        db.add(record)
        db.commit()
        return record.id, deepcopy(document), old


@contextmanager
def archived_admissions(monkeypatch):
    """Replay old admissions without hiding either module from hash resolution."""
    build = prompt_snapshots._build_snapshot

    def archived(version, crypto, *, origin="enqueue", rules=None):
        return build(version, crypto, origin=origin, rules=rules or legacy_prompts_v224)

    with monkeypatch.context() as scope:
        scope.setattr(prompt_snapshots, "_build_snapshot", archived)
        yield


def without_editor(configuration):
    return {**configuration, "evidence_editor_enabled": False, "evidence_editor_profile_id": None}


def test_original_v224_source_and_execution_artifact_are_byte_preserved():
    assert hashlib.sha256(Path(legacy_prompts_v224.__file__).read_bytes()).hexdigest() == FROZEN_PROMPT_SHA256
    pipeline = evidence_pipeline_v224.pipeline_artifact()
    assert pipeline["sha256"] == FROZEN_PIPELINE_SHA256
    assert len(pipeline["files"]) == 66
    for filename, expected in pipeline["files"].items():
        assert hashlib.sha256((Path(evidence_pipeline_v224.__file__).parent / filename).read_bytes()).hexdigest() == expected


def test_same_version_text_revision_has_distinct_identity_but_unchanged_roles_and_defaults():
    for name in (
        "FIXED_RULES_VERSION", "PROMPT_VERSION", "V3_RULES_VERSIONS", "SEMANTIC_RULES_VERSIONS",
        "PRIMARY_ROLE", "VERIFIER_ROLE", "DEFAULT_POLICY_NAME", "DEFAULT_POLICY_TEXT",
    ):
        assert getattr(prompts, name) == getattr(legacy_prompts_v224, name)
    assert prompts.policy_reserved_tokens is legacy_prompts_v224.policy_reserved_tokens
    assert text_hash(prompts.FIXED_INSTRUCTIONS) != text_hash(legacy_prompts_v224.FIXED_INSTRUCTIONS)


def test_snapshots_change_only_instruction_text_and_its_identity(client):
    old, current = snapshot(client, legacy_prompts_v224), snapshot(client, prompts)
    assert old.evidence_pipeline == current.evidence_pipeline == evidence_pipeline_v224.pipeline_artifact()
    assert old.fixed_rules_hash == text_hash(legacy_prompts_v224.FIXED_INSTRUCTIONS)
    assert current.fixed_rules_hash == text_hash(prompts.FIXED_INSTRUCTIONS)
    changed = {name for name in type(old).model_fields if getattr(old, name) != getattr(current, name)}
    assert changed == {"fixed_rules_hash", "instructions_hash", "primary_instructions", "verifier_instructions"}
    assert _baseline_rules(old.metadata()) is legacy_prompts_v224
    assert _baseline_rules(current.metadata()) is prompts


def test_existing_baseline_restores_exact_original_v224_without_rewriting_records(client):
    identifier, before, expected = seed_baseline(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        assert approved_production_prompt(db, crypto) == expected
        queued = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, queued) == expected
        ciphertext = queued.prompt_snapshot_ciphertext
        assert load_analysis_prompt(queued, crypto) == expected
        assert live_snapshot(db, crypto, client.app.state.settings) == before
        assert queued.prompt_snapshot_ciphertext == ciphertext
        record = db.get(ProductionPromotion, identifier)
        assert record.snapshot_json == before
        assert record.configuration_hash == configuration_digest(before)


def test_pending_reclaimed_work_keeps_original_ciphertext_and_dispatches_same_engine(client, event_payload, monkeypatch):
    _identifier, _before, expected = seed_baseline(client)
    created = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert created.status_code == 202, created.text
    calls = []

    def execute(db, crypto, row, _targets, _threshold, *, request_check=None):
        current = load_analysis_prompt(row, crypto)
        assert current == expected
        assert current.evidence_pipeline["sha256"] == FROZEN_PIPELINE_SHA256
        calls.append(current)

    monkeypatch.setattr(worker_v224, "process_moduagent", execute)
    with client.app.state.session_factory() as db:
        first = worker.claim_next(db, "synthetic-before-capture-revision", 60)
        ciphertext = first.prompt_snapshot_ciphertext
        first.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        reclaimed = worker.claim_next(db, "synthetic-after-capture-revision", 60)
        assert reclaimed.id == first.id
        assert reclaimed.prompt_snapshot_ciphertext == ciphertext
        worker_runtime_v224.process_moduagent(db, client.app.state.crypto, reclaimed, "", .75)
        assert calls == [expected]
        assert reclaimed.prompt_snapshot_ciphertext == ciphertext


def test_failed_retry_preserves_original_input_prompt_and_execution_binding(client, event_payload, monkeypatch):
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    _identifier, _before, old = seed_baseline(client)
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
    response = retry(client, created.json()["id"], key="synthetic-original-v224-capture-retry")
    assert response.status_code == 202, response.text
    calls = []
    monkeypatch.setattr(worker_v224, "process_moduagent",
        lambda db, key, row, *_args, **_kwargs: calls.append(load_analysis_prompt(row, key)))
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, created.json()["id"])
        child = db.get(Analysis, response.json()["analysis_id"])
        assert original.status == "failed" and original.error_code == "synthetic-original-failure"
        assert (original.prompt_snapshot_ciphertext, original.payload_ciphertext, original.execution_snapshot_ciphertext) == before
        assert (child.prompt_snapshot_ciphertext, child.payload_ciphertext) == before[:2]
        assert load_analysis_prompt(child, crypto) == old
        assert load_execution_snapshot(child, crypto) == execution
        worker_runtime_v224.process_moduagent(db, crypto, child, "", .73)
        assert calls == [old] and child.prompt_snapshot_ciphertext == before[0]


@pytest.mark.parametrize("damage", ["unknown_hash", "unknown_version", "wrong_version"])
def test_unknown_or_mismatched_baseline_never_falls_back_to_latest_text(client, damage):
    expected = snapshot(client, legacy_prompts_v224).metadata()
    if damage == "unknown_hash":
        expected["fixed_rules_hash"] = "0" * 64
    elif damage == "unknown_version":
        expected["fixed_rules_version"] = "waf-system-v999"
    else:
        expected["fixed_rules_version"] = "waf-system-v2.23"
    with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
        _baseline_rules(expected)


def test_module_alias_is_deduplicated_but_distinct_matching_modules_are_rejected(client, monkeypatch):
    expected = snapshot(client, legacy_prompts_v224).metadata()
    with monkeypatch.context() as scope:
        scope.setattr("app.agent.prompts", legacy_prompts_v224)
        assert _baseline_rules(expected) is legacy_prompts_v224
    duplicate = ModuleType("synthetic_duplicate_original_v224")
    duplicate.FIXED_RULES_VERSION = legacy_prompts_v224.FIXED_RULES_VERSION
    duplicate.FIXED_INSTRUCTIONS = legacy_prompts_v224.FIXED_INSTRUCTIONS
    monkeypatch.setattr("app.agent.legacy_prompts_v223", duplicate)
    with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
        _baseline_rules(expected)


def test_changing_only_fixed_text_hash_does_not_transfer_baseline_approval(client):
    identifier, _before, expected = seed_baseline(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        queued = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, queued) == expected
        ciphertext = queued.prompt_snapshot_ciphertext
        record = db.get(ProductionPromotion, identifier)
        changed = deepcopy(record.snapshot_json)
        changed["prompt"]["fixed_rules_hash"] = text_hash(prompts.FIXED_INSTRUCTIONS)
        record.snapshot_json, record.configuration_hash = changed, configuration_digest(changed)
        db.commit()
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            approved_production_prompt(db, crypto)
        assert load_analysis_prompt(queued, crypto) == expected
        assert queued.prompt_snapshot_ciphertext == ciphertext


def test_old_official_evaluation_does_not_authorize_revised_text(client, event_payload, candidate, monkeypatch):
    configuration = without_editor(candidate)
    with archived_admissions(monkeypatch):
        original = qualified(client, event_payload, configuration)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        old_run = db.get(NamedRun, original["id"])
        evaluation = db.scalar(select(EvaluationRecord).where(EvaluationRecord.test_run_id == old_run.id))
        assert evaluation is not None
        before_evaluation = deepcopy(evaluation.summary_json), evaluation.configuration_hash
        old_ciphertext = old_run.prompt_snapshot_ciphertext
    response = direct(client, event_payload, configuration)
    assert response.status_code == 202, response.text
    revised = response.json()
    assert revised["configuration_hash"] != original["configuration_hash"]
    assert revised["configuration_snapshot"]["evidence_pipeline"] == original["configuration_snapshot"]["evidence_pipeline"]
    assert revised["configuration_snapshot"]["prompt"]["fixed_rules_hash"] == text_hash(prompts.FIXED_INSTRUCTIONS)
    review = client.get(f"{BASE}/preflight/{original['id']}")
    assert review.status_code == 200, review.text
    checks = {check["code"]: check["passed"] for check in review.json()["checks"]}
    assert checks["candidate_snapshot_valid"] and not checks["tested_instructions_still_current"]
    before = client.get(BASE).json()
    rejected = client.post(BASE + "/promote", json={"candidate_test_run_id": original["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "candidate_not_eligible_for_promotion"
    assert client.get(BASE).json()["configuration_hash"] == before["configuration_hash"]
    with client.app.state.session_factory() as db:
        old_run = db.get(NamedRun, original["id"])
        assert verify_configuration(old_run, crypto) == original["configuration_snapshot"]
        assert old_run.prompt_snapshot_ciphertext == old_ciphertext
        evaluation = db.scalar(select(EvaluationRecord).where(EvaluationRecord.test_run_id == old_run.id))
        assert (evaluation.summary_json, evaluation.configuration_hash) == before_evaluation
        assert db.scalar(select(func.count()).select_from(EvaluationRecord).where(
            EvaluationRecord.test_run_id == revised["id"])) == 0
        assert db.scalar(select(func.count()).select_from(ProductionPromotion).where(
            ProductionPromotion.kind == "promotion")) == 0


def test_already_promoted_original_v224_remains_active_after_text_revision(client, event_payload, candidate, monkeypatch):
    configuration = without_editor(candidate)
    with archived_admissions(monkeypatch):
        original = qualified(client, event_payload, configuration)
        baseline = client.get(BASE).json()
        response = client.post(BASE + "/promote", json={"candidate_test_run_id": original["id"],
            "expected_production_configuration_hash": baseline["configuration_hash"], "acknowledge_schema_change": True})
        assert response.status_code == 200, response.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        run = db.get(NamedRun, original["id"])
        expected, ciphertext = load_analysis_prompt(run, crypto), run.prompt_snapshot_ciphertext
        record = db.get(ProductionPromotion, response.json()["id"])
        before = deepcopy(record.snapshot_json), record.configuration_hash
        assert approved_production_prompt(db, crypto) == expected
        assert expected.fixed_rules_hash == text_hash(legacy_prompts_v224.FIXED_INSTRUCTIONS)
        assert expected.fixed_rules_hash != text_hash(prompts.FIXED_INSTRUCTIONS)
        assert live_snapshot(db, crypto, client.app.state.settings) == original["configuration_snapshot"]
        assert run.prompt_snapshot_ciphertext == ciphertext
        assert (record.snapshot_json, record.configuration_hash) == before
    created = client.post("/api/v1/analyses?wait_seconds=0", json={**event_payload,
        "event_id": "synthetic-after-capture-revision-still-approved-original", "vendor_score": 2})
    assert created.status_code == 202, created.text
    with client.app.state.session_factory() as db:
        assert load_analysis_prompt(db.get(Analysis, created.json()["id"]), crypto) == expected
