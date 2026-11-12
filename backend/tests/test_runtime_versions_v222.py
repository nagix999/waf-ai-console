"""Additive recovery revision: immutable historical execution and approval.

Only synthetic fixtures are used. No model or production setting is accessed.
The archived engine hashes come from the pre-change historical test contract.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent import (
    evidence_pipeline_v221 as old_pipeline, evidence_pipeline_v222 as new_pipeline,
    execution_policy_v221 as old_policy, execution_policy_v222 as new_policy,
    input_builder_v221 as old_input, input_builder_v222 as new_input,
    legacy_prompts_v221_reasoning as old_rules, legacy_prompts_v222 as prompts,
)
from app.agent.contracts import SecurityAnalysisOutputV220
from app.agent.policy import VerifierPolicyContext
from app.models import Analysis, ProductionPromotion
from app.services.candidate_configurations import configuration_digest
from app.services.prompt_snapshots import (
    PromptSnapshot, _baseline_rules, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from app.services.prompt_policies import get_active_policy
from app.services.production_configurations import live_snapshot
from app.services.http_parser import parse_http_payload
from test_candidate_configurations import candidate, direct
from test_compact_prompt_preservation import ENGINE_SHA256
from test_evidence_relations import wire
from test_production_promotion import BASE, qualified
from test_validation_data import login


OLD, NEW = old_rules.FIXED_RULES_VERSION, prompts.FIXED_RULES_VERSION
pytestmark = pytest.mark.usefixtures("registered_vllm_target", "legacy_v222_admissions")
DISPLAY_SHA256 = {
    "agent/evidence_display.py": "b7111efd4af18c313053c3e8642213416db28624bc7393f6bb1101f9685256e6",
    "services/evidence_display_plan.py": "707bb2db2bb365afeb83d46db5dfd19f0f244dd5c2c99bd21c80e8cf6bb275f4",
    "services/evidence_display_jobs.py": "f16bbeb1bad17e3b285723fb8e2da90a21415b8935d6e440d041cff0eaa960d4",
    "worker_entrypoint.py": "bc03c1f881305f4d6762a8afb75899ed6ad0a4c92074f4463a719d7414f3ef08",
}


def snapshot(client, rules=None):
    with client.app.state.session_factory() as db:
        return _build_snapshot(get_active_policy(db, client.app.state.crypto), client.app.state.crypto, rules=rules)


@pytest.mark.parametrize("filename", ENGINE_SHA256)
def test_all_thirty_pinned_old_runtime_files_keep_their_exact_bytes(filename):
    raw = (Path(old_pipeline.__file__).parent / filename).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ENGINE_SHA256[filename]


@pytest.mark.parametrize("filename,expected", DISPLAY_SHA256.items())
def test_display_runtime_is_not_changed_or_silently_readmitted(filename, expected):
    raw = (Path(old_pipeline.__file__).parent.parent / filename).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected


def test_original_reasoning_instructions_are_byte_preserved_and_content_is_not_changed():
    assert hashlib.sha256(Path(old_rules.__file__).read_bytes()).hexdigest() == "57cc3438f3f073e9c5785931c9bfed9cc7e1bc403333bcee0506999495353c59"
    assert OLD == "waf-system-v2.21" and NEW == "waf-system-v2.22"
    assert old_rules.FIXED_INSTRUCTIONS == prompts.FIXED_INSTRUCTIONS
    assert old_rules.build_role_instructions("synthetic policy") == prompts.build_role_instructions("synthetic policy")
    assert old_rules.policy_reserved_tokens("synthetic policy") == prompts.policy_reserved_tokens("synthetic policy")
    assert old_rules.V3_RULES_VERSIONS | {NEW} == prompts.V3_RULES_VERSIONS
    assert old_rules.SEMANTIC_RULES_VERSIONS | {NEW} == prompts.SEMANTIC_RULES_VERSIONS


def test_new_manifest_is_additive_and_pins_the_actual_entrypoint_and_recovery_paths():
    old, new = old_pipeline.pipeline_artifact(), new_pipeline.pipeline_artifact()
    assert old["files"] == ENGINE_SHA256
    assert new["version"] == "evidence-pipeline-v3"
    assert {name: new["files"][name] for name in old["files"]} == old["files"]
    assert set(new["files"]) == set(old["files"]) | set(new_pipeline._FILES)
    assert len(new["files"]) == 40
    assert {"relation_recovery.py", "../worker_entrypoint_v222.py",
            "../services/model_validation_worker_v222.py"} <= new["files"].keys()
    identity = {key: value for key, value in new.items() if key != "sha256"}
    assert new["sha256"] == hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert new_pipeline.validate_pipeline_snapshot(new, NEW) == new
    assert new_pipeline.validate_pipeline_snapshot(old, OLD) == old


@pytest.mark.parametrize("filename", new_pipeline._FILES)
def test_new_runtime_drift_invalidates_only_new_revision(monkeypatch, filename):
    old, new = old_pipeline.pipeline_artifact(), new_pipeline.pipeline_artifact()
    target = (Path(new_pipeline.__file__).parent / filename).resolve()
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: read_bytes(path) + (b"\n# synthetic drift" if path.resolve() == target else b""))
    with pytest.raises(ValueError, match="^evidence_pipeline_snapshot_invalid$"):
        new_pipeline.validate_pipeline_snapshot(new, NEW)
    assert new_pipeline.validate_pipeline_snapshot(old, OLD) == old


@pytest.mark.parametrize("change", [
    lambda value: value.pop("evidence_pipeline"),
    lambda value: value.update(evidence_pipeline=None),
    lambda value: value.update(evidence_pipeline=old_pipeline.pipeline_artifact()),
    lambda value: value.update(schema_version=1),
    lambda value: value["evidence_pipeline"].update(sha256="0" * 64),
])
def test_new_snapshot_cannot_downgrade_or_lose_execution_provenance(client, change):
    value = snapshot(client).model_dump(mode="json")
    change(value)
    with pytest.raises(ValidationError):
        PromptSnapshot.model_validate(value)


def test_latest_candidate_has_new_identity_but_exact_same_instructions_and_wire(client):
    old, new = snapshot(client, old_rules), snapshot(client)
    assert new.fixed_rules_version == NEW and old.fixed_rules_version == OLD
    assert new.fixed_rules_hash == old.fixed_rules_hash
    assert new.instructions_hash == old.instructions_hash
    assert new.evidence_pipeline != old.evidence_pipeline
    assert new.primary_instructions == old.primary_instructions
    assert new.verifier_instructions == old.verifier_instructions
    assert _baseline_rules(old.metadata()) is old_rules
    assert _baseline_rules(new.metadata()) is prompts
    assert SecurityAnalysisOutputV220.model_validate(wire()).model_dump(mode="json") == wire()


def test_existing_production_baseline_and_queued_work_remain_on_exact_v221(client):
    login(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        current = live_snapshot(db, crypto, client.app.state.settings)
        old = _build_snapshot(get_active_policy(db, crypto), crypto, rules=old_rules)
        document = {**current, "schema_version": 3,
            "prompt": {name: getattr(old, name) for name in current["prompt"]},
            "evidence_pipeline": old.evidence_pipeline}
        baseline = ProductionPromotion(kind="baseline", snapshot_json=document,
            configuration_hash=configuration_digest(document), actor_id="synthetic-v221-before-recovery")
        db.add(baseline)
        db.commit()
        queued = Analysis(analysis_purpose="production")
        assert approved_production_prompt(db, crypto) == old
        assert pin_analysis_prompt(db, crypto, queued) == old
        before = queued.prompt_snapshot_ciphertext
        assert load_analysis_prompt(queued, crypto) == old
        assert live_snapshot(db, crypto, client.app.state.settings) == document
        assert queued.prompt_snapshot_ciphertext == before
        assert baseline.configuration_hash == configuration_digest(document)


@pytest.mark.parametrize("status", ["success", "partial", "failed"])
def test_input_catalog_and_verifier_safety_policy_are_unchanged(event_payload, status):
    raw = "POST /test HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\nvalue=test"
    parsed = parse_http_payload(raw)
    arguments = (event_payload, raw, parsed, 32000, 3072)
    old = old_input.build_agent_input(*arguments, evidence_selection=True, fixed_rules_version=OLD)
    new = new_input.build_agent_input(*arguments, evidence_selection=True, fixed_rules_version=NEW)
    assert new == old
    primary = SecurityAnalysisOutputV220.model_validate(wire()).legacy_bridge()
    for action in ("A", "D"):
        for truncated in (True, False):
            for conflict in (True, False):
                context = VerifierPolicyContext(action, status, truncated, interpretation_conflict=conflict, fixed_rules_version=OLD)
                assert new_policy.verifier_reasons(primary, replace(context, fixed_rules_version=NEW)) == old_policy.verifier_reasons(primary, context)
    assert new_policy.integrity_version_for_rules(NEW) == old_policy.integrity_version_for_rules(OLD)
    assert new_policy.verifier_policy_metadata(NEW) == old_policy.verifier_policy_metadata(OLD)


def test_old_official_evaluation_cannot_authorize_the_new_runtime(client, event_payload, candidate, monkeypatch):
    with monkeypatch.context() as scope:
        scope.setattr("app.agent.prompts", old_rules)
        original = qualified(client, event_payload, candidate)
    response = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert response.status_code == 202, response.text
    current = response.json()
    assert current["configuration_snapshot"]["prompt"]["fixed_rules_version"] == NEW
    assert original["configuration_snapshot"]["prompt"]["fixed_rules_version"] == OLD
    assert current["configuration_hash"] != original["configuration_hash"]
    old_hash = original["configuration_snapshot"]["evidence_pipeline"]["sha256"]
    assert current["configuration_snapshot"]["evidence_pipeline"]["sha256"] != old_hash
    preflight = client.get(f"{BASE}/preflight/{original['id']}")
    assert preflight.status_code == 200, preflight.text
    checks = {item["code"]: item["passed"] for item in preflight.json()["checks"]}
    assert checks["candidate_snapshot_valid"] and not checks["tested_instructions_still_current"]
    before = client.get(BASE).json()
    rejected = client.post(BASE + "/promote", json={"candidate_test_run_id": original["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "candidate_not_eligible_for_promotion"
