"""Content-addressed prompt compaction with unchanged execution provenance.

All admissions, approvals and retries use isolated synthetic fixtures. The
pre-compaction engine checksums were captured before this prompt-only change;
no model, production database or environment secret is accessed.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import func, select

from app import worker, worker_v221
from app.agent import evidence_pipeline, evidence_pipeline_v221, legacy_prompts_v220, legacy_prompts_v221, prompts
from app.models import Analysis, ProductionPromotion, TestEvaluation as EvaluationRecord, TestRun as NamedRun, VLLMProfile, utcnow
from app.services import worker_runtime
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
from test_production_promotion import BASE, qualified
from test_runtime_versions_v221 import HEAD_FILE_SHA256
from test_validation_data import login


pytestmark = pytest.mark.usefixtures("registered_vllm_target", "legacy_evidence_editor_admissions")
FROZEN_V221_SHA256 = "2b9c4ab99ddced5d3d252972792c43e5c7bc1f1b9d61747954bd0b54062a66b9"
ENGINE_SHA256 = {
    **HEAD_FILE_SHA256,
    "evidence_pipeline_v221.py": "a61fa4d3e0b26fc3c79cde73be19ada902e5db821658883ea02d2ff25267cc08",
    "input_builder_v221.py": "c5676cc415d2116f50997ebc44834cee822e8e887debaeab553fb0fa9c82deca",
    "executor_v221.py": "60d19ee56f8d1dba6abb684d083a721d90509d2a857096ce4f8c3c2635c004bb",
    "structured_json.py": "e82957d9629da96758d6d4492e5bdd04ef89a264f71c8fd58f7311493e92597e",
    "evidence_source_policy.py": "eae1f1688833ae8d77044a0dcd7ec2484795fbfef77275266de28fc740cb854f",
    "execution_policy_v221.py": "12309fbc4f52d03e9047b09ea249619cd01b2d65f22472f67b931f72098e6753",
    "../worker_v221.py": "132b2167e464f2fbd2f25fa7e0e92410ab107f972013faf54e9a3bba725019e1",
    "../services/worker_runtime.py": "6e1a3b46e59e66ab05c3829c0d464046230994844771cb45753dc7885d83d3b9",
    "../services/worker_concurrency.py": "c30451495463e1511c64202f774d637b2a8b1879a23342fb7bb33b58f20a9207",
    "../services/model_validation_worker.py": "44d2efd5f1b837a56dece0ac33e0d025a03386f9c5b53ec94eea5431c88250ef",
    "../services/provider_options.py": "13fbf2a58219a3d26d2df67d658bb6253b96173dbea6c044e1793f9a7bbe601d",
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
            configuration_hash=configuration_digest(document), actor_id="synthetic-before-prompt-compaction")
        db.add(record)
        db.commit()
        return record.id, deepcopy(document), prompt


def test_precompaction_prompt_is_byte_exact_and_content_identity_is_not_reused():
    assert hashlib.sha256(Path(legacy_prompts_v221.__file__).read_bytes()).hexdigest() == FROZEN_V221_SHA256
    assert legacy_prompts_v221.FIXED_RULES_VERSION == prompts.FIXED_RULES_VERSION == "waf-system-v2.21"
    assert legacy_prompts_v221.PROMPT_VERSION == prompts.PROMPT_VERSION == "waf-judgment-v2.21"
    assert text_hash(legacy_prompts_v221.FIXED_INSTRUCTIONS) != text_hash(prompts.FIXED_INSTRUCTIONS)
    assert len(prompts.FIXED_INSTRUCTIONS.encode("utf-8")) < len(legacy_prompts_v221.FIXED_INSTRUCTIONS.encode("utf-8"))


@pytest.mark.parametrize("filename", ENGINE_SHA256)
def test_prompt_only_compaction_preserves_each_old_and_new_execution_file(filename):
    assert set(ENGINE_SHA256) == set(evidence_pipeline._FILES) | set(evidence_pipeline_v221._FILES)
    raw = (Path(evidence_pipeline.__file__).parent / filename).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ENGINE_SHA256[filename]


def test_old_and_compact_prompts_have_distinct_text_hashes_but_identical_runtime_artifact(client):
    old, new = snapshot(client, legacy_prompts_v221), snapshot(client, prompts)
    assert old.evidence_pipeline == new.evidence_pipeline == evidence_pipeline_v221.pipeline_artifact()
    assert old.evidence_pipeline["files"] == ENGINE_SHA256
    assert old.fixed_rules_hash == text_hash(legacy_prompts_v221.FIXED_INSTRUCTIONS)
    assert new.fixed_rules_hash == text_hash(prompts.FIXED_INSTRUCTIONS)
    assert old.fixed_rules_hash != new.fixed_rules_hash and old.instructions_hash != new.instructions_hash
    changed = {name for name in type(old).model_fields if getattr(old, name) != getattr(new, name)}
    assert changed == {"fixed_rules_hash", "instructions_hash", "primary_instructions", "verifier_instructions"}
    assert _baseline_rules(old.metadata()) is legacy_prompts_v221
    assert _baseline_rules(new.metadata()) is prompts


@pytest.mark.parametrize("rules", [legacy_prompts_v220, legacy_prompts_v221, prompts],
    ids=["approved-v220", "approved-original-v221", "approved-compact-v221"])
def test_baseline_restores_exact_approved_text_instead_of_latest_same_version(client, rules):
    identifier, before, expected = seed_baseline(client, rules)
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
        assert record.snapshot_json == before and record.configuration_hash == configuration_digest(before)


@pytest.mark.parametrize("rules", [legacy_prompts_v221, prompts], ids=["original-v221", "compact-v221"])
def test_queued_reclaimed_snapshot_dispatches_exact_instructions_without_ciphertext_rewrite(
    client, event_payload, monkeypatch, rules,
):
    _identifier, _before, expected = seed_baseline(client, rules)
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert response.status_code == 202, response.text
    calls = []

    def execute(db, crypto, row, targets, threshold, *, request_check=None):
        current = load_analysis_prompt(row, crypto)
        calls.append(current)
        assert current == expected
        assert current.evidence_pipeline["files"] == ENGINE_SHA256

    def unexpected(*_args, **_kwargs):
        pytest.fail("A v2.21 prompt must never run through the frozen v2.20 worker")

    monkeypatch.setattr(worker_v221, "process_moduagent", execute)
    monkeypatch.setattr(worker, "process_moduagent", unexpected)
    with client.app.state.session_factory() as db:
        first = worker.claim_next(db, "synthetic-before-compaction", 60)
        ciphertext = first.prompt_snapshot_ciphertext
        first.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        second = worker.claim_next(db, "synthetic-after-compaction", 60)
        assert second.id == first.id and second.prompt_snapshot_ciphertext == ciphertext
        worker_runtime.process_moduagent(db, client.app.state.crypto, second, "", .75)
        assert second.prompt_snapshot_ciphertext == ciphertext
        assert calls == [expected]


def test_original_v221_retry_preserves_prompt_ciphertext_and_execution_provenance(
    client, event_payload, monkeypatch,
):
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    _identifier, _before, old = seed_baseline(client, legacy_prompts_v221)
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload)
    assert response.status_code == 202, response.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, response.json()["id"])
        execution = make_execution_snapshot(original, db.get(VLLMProfile, profile_id), old, .73, crypto=crypto)
        original.execution_snapshot_ciphertext = crypto.encrypt_text(execution.model_dump_json())
        original.status, original.error_code, original.completed_at = "failed", "synthetic-original-failure", utcnow()
        db.commit()
        before = (original.prompt_snapshot_ciphertext, original.payload_ciphertext, original.execution_snapshot_ciphertext)
    result = retry(client, response.json()["id"], key="synthetic-original-v221-compaction-retry")
    assert result.status_code == 202, result.text
    calls = []

    def execute(db, crypto, row, *_args, **_kwargs):
        calls.append(load_analysis_prompt(row, crypto))

    monkeypatch.setattr(worker_v221, "process_moduagent", execute)
    with client.app.state.session_factory() as db:
        original = db.get(Analysis, response.json()["id"])
        child = db.get(Analysis, result.json()["analysis_id"])
        assert original.status == "failed" and original.error_code == "synthetic-original-failure"
        assert (original.prompt_snapshot_ciphertext, original.payload_ciphertext,
            original.execution_snapshot_ciphertext) == before
        assert (child.prompt_snapshot_ciphertext, child.payload_ciphertext) == before[:2]
        assert load_analysis_prompt(child, crypto) == old
        assert load_execution_snapshot(child, crypto) == execution
        worker_runtime.process_moduagent(db, crypto, child, "", .73)
        assert calls == [old] and child.prompt_snapshot_ciphertext == before[0]


@pytest.mark.parametrize("damage", ["unknown_hash", "unknown_version", "wrong_version_with_known_hash"])
def test_baseline_resolution_refuses_unknown_or_version_mismatched_hashes(client, damage):
    expected = snapshot(client, legacy_prompts_v221).metadata()
    if damage == "unknown_hash":
        expected["fixed_rules_hash"] = "0" * 64
    elif damage == "unknown_version":
        expected["fixed_rules_version"] = "waf-system-v999"
    else:
        expected["fixed_rules_version"] = "waf-system-v2.20"
    with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
        _baseline_rules(expected)


def test_same_module_alias_is_not_a_second_baseline_candidate(client, monkeypatch):
    expected = snapshot(client, legacy_prompts_v221).metadata()
    monkeypatch.setattr("app.agent.prompts", legacy_prompts_v221)
    assert _baseline_rules(expected) is legacy_prompts_v221


def test_distinct_matching_modules_are_ambiguous_not_arbitrarily_selected(client, monkeypatch):
    expected = snapshot(client, legacy_prompts_v221).metadata()
    duplicate = ModuleType("synthetic_duplicate_original_v221")
    duplicate.FIXED_RULES_VERSION = legacy_prompts_v221.FIXED_RULES_VERSION
    duplicate.FIXED_INSTRUCTIONS = legacy_prompts_v221.FIXED_INSTRUCTIONS
    monkeypatch.setattr("app.agent.legacy_prompts_v220", duplicate)
    with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
        _baseline_rules(expected)


@pytest.mark.parametrize("field,value", [
    ("fixed_rules_hash", "0" * 64),
    ("instructions_hash", "1" * 64),
    ("prompt_version", "waf-judgment-v2.21/policy-999"),
    ("reserved_tokens", 99999),
])
def test_rehashing_modified_approval_cannot_transfer_approval_to_a_different_prompt(client, field, value):
    identifier, before, expected = seed_baseline(client, legacy_prompts_v221)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        queued = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, queued) == expected
        ciphertext = queued.prompt_snapshot_ciphertext
        record = db.get(ProductionPromotion, identifier)
        changed = deepcopy(record.snapshot_json)
        changed["prompt"][field] = value
        record.snapshot_json, record.configuration_hash = changed, configuration_digest(changed)
        db.commit()
        with pytest.raises(PromptSnapshotError, match="^prompt_snapshot_invalid$"):
            approved_production_prompt(db, crypto)
        assert load_analysis_prompt(queued, crypto) == expected
        assert queued.prompt_snapshot_ciphertext == ciphertext
        assert record.snapshot_json == changed and before["prompt"][field] != value


def test_old_official_evaluation_is_not_automatically_reused_for_compacted_candidate(
    client, event_payload, candidate, monkeypatch,
):
    with monkeypatch.context() as scope:
        scope.setattr("app.agent.prompts", legacy_prompts_v221)
        original = qualified(client, event_payload, candidate)
    original_configuration = original["configuration_snapshot"]
    assert original_configuration["prompt"]["fixed_rules_hash"] == text_hash(legacy_prompts_v221.FIXED_INSTRUCTIONS)
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert response.status_code == 202, response.text
    compact = response.json()
    assert compact["configuration_hash"] != original["configuration_hash"]
    assert compact["configuration_snapshot"]["evidence_pipeline"] == original_configuration["evidence_pipeline"]
    assert compact["configuration_snapshot"]["prompt"]["fixed_rules_hash"] == text_hash(prompts.FIXED_INSTRUCTIONS)
    review = client.get(f"{BASE}/preflight/{original['id']}")
    assert review.status_code == 200, review.text
    checks = {check["code"]: check["passed"] for check in review.json()["checks"]}
    assert checks["candidate_snapshot_valid"] and not checks["tested_instructions_still_current"]
    baseline = client.get(BASE).json()
    rejected = client.post(BASE + "/promote", json={"candidate_test_run_id": original["id"],
        "expected_production_configuration_hash": baseline["configuration_hash"], "acknowledge_schema_change": True})
    assert rejected.status_code == 409 and rejected.json()["detail"] == "candidate_not_eligible_for_promotion"
    with client.app.state.session_factory() as db:
        old_run, new_run = db.get(NamedRun, original["id"]), db.get(NamedRun, compact["id"])
        assert verify_configuration(old_run, client.app.state.crypto) == original_configuration
        assert old_run.configuration_hash == original["configuration_hash"]
        assert new_run.configuration_hash == compact["configuration_hash"]
        assert db.scalar(select(func.count()).select_from(EvaluationRecord).where(
            EvaluationRecord.configuration_hash == original["configuration_hash"])) == 1
        assert db.scalar(select(func.count()).select_from(EvaluationRecord).where(
            EvaluationRecord.configuration_hash == compact["configuration_hash"])) == 0
        assert db.scalar(select(func.count()).select_from(ProductionPromotion).where(
            ProductionPromotion.kind == "promotion")) == 0


def test_already_promoted_original_v221_keeps_approved_text_after_compaction(
    client, event_payload, candidate, monkeypatch,
):
    with monkeypatch.context() as scope:
        scope.setattr("app.agent.prompts", legacy_prompts_v221)
        original = qualified(client, event_payload, candidate)
        baseline = client.get(BASE).json()
        response = client.post(BASE + "/promote", json={"candidate_test_run_id": original["id"],
            "expected_production_configuration_hash": baseline["configuration_hash"], "acknowledge_schema_change": True})
        assert response.status_code == 200, response.text
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        run = db.get(NamedRun, original["id"])
        ciphertext = run.prompt_snapshot_ciphertext
        expected = load_analysis_prompt(run, crypto)
        record = db.get(ProductionPromotion, response.json()["id"])
        before = deepcopy(record.snapshot_json), record.configuration_hash
        assert approved_production_prompt(db, crypto) == expected
        assert expected.fixed_rules_hash == text_hash(legacy_prompts_v221.FIXED_INSTRUCTIONS)
        assert expected.fixed_rules_hash != text_hash(prompts.FIXED_INSTRUCTIONS)
        current = live_snapshot(db, crypto, client.app.state.settings)
        assert current == original["configuration_snapshot"]
        assert run.prompt_snapshot_ciphertext == ciphertext
        assert (record.snapshot_json, record.configuration_hash) == before
    created = client.post("/api/v1/analyses?wait_seconds=0", json={**event_payload,
        "event_id": "synthetic-after-compaction-still-approved-original", "vendor_score": 2})
    assert created.status_code == 202, created.text
    with client.app.state.session_factory() as db:
        pinned = load_analysis_prompt(db.get(Analysis, created.json()["id"]), crypto)
        assert pinned == expected
