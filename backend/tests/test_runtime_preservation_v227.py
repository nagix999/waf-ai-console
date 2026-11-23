"""Synthetic release gates and immutable mixed-version execution."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import worker_v226, worker_v227
from app.agent import evidence_pipeline_v226, evidence_pipeline_v227
from app.agent import input_builder_v226, input_builder_v227, execution_policy_v226, execution_policy_v227
from app.agent import legacy_prompts_v226, legacy_prompts_v227 as prompts
from app.agent.policy import VerifierPolicyContext
from app.models import Analysis, ProductionPromotion, TestRun as NamedRun, VLLMProfile
from app.services import worker_runtime_v227, prompt_snapshots
from app.services.analysis_retries import make_execution_snapshot, load_execution_snapshot
from app.services.candidate_configurations import configuration_digest, verify_configuration
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    _baseline_rules, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt, PromptSnapshotError,
)
from app.services.http_parser import parse_http_payload
from test_candidate_configurations import candidate
from test_signature_context_lifecycle import direct, qualified
from test_execution_policy_v221 import output

pytestmark = pytest.mark.usefixtures("registered_vllm_target", "legacy_v227_admissions")
PRODUCTION = "/api/v1/admin/production-configurations"
FROZEN_V226 = "108bf447e4ac0eb34baa78fa8e4477f42dbf95627b12abbef6b8a0269b4bd4cf"


def without_editor(configuration):
    return {**configuration, "evidence_editor_enabled": False, "evidence_editor_profile_id": None}


def test_new_pipeline_preserves_all_90_historical_files_and_rejects_tampered_binding():
    old = evidence_pipeline_v226.pipeline_artifact()
    assert len(old["files"]) == 90 and old["sha256"] == FROZEN_V226
    current = evidence_pipeline_v227.pipeline_artifact()
    assert current["version"] == "evidence-pipeline-v8"
    assert len(current["files"]) == 104
    assert "contracts_v227.py" in current["files"]
    assert current["input_version"] == old["input_version"] == "agent-input-v3"
    assert current["source_catalog_version"] == old["source_catalog_version"]
    assert current["relations_version"] == old["relations_version"]
    assert all(current["files"][name] == digest for name, digest in old["files"].items())
    root = Path(evidence_pipeline_v227.__file__).parent
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
               for name, digest in current["files"].items())
    assert evidence_pipeline_v227.validate_pipeline_snapshot(old, "waf-system-v2.26") == old
    with pytest.raises(ValueError, match="evidence_pipeline_snapshot_invalid"):
        evidence_pipeline_v227.validate_pipeline_snapshot(old, "waf-system-v2.27")
    broken = deepcopy(current)
    broken["files"]["executor_v227.py"] = "0" * 64
    with pytest.raises(ValueError, match="evidence_pipeline_snapshot_invalid"):
        evidence_pipeline_v227.validate_pipeline_snapshot(broken, "waf-system-v2.27")


def test_display_editor_implementation_remains_byte_preserved():
    from app.services.evidence_display_jobs import implementation_artifact
    assert implementation_artifact()["files"] == {
        "../agent/evidence_display.py": "b7111efd4af18c313053c3e8642213416db28624bc7393f6bb1101f9685256e6",
        "evidence_display_plan.py": "707bb2db2bb365afeb83d46db5dfd19f0f244dd5c2c99bd21c80e8cf6bb275f4",
        "evidence_display_jobs.py": "f16bbeb1bad17e3b285723fb8e2da90a21415b8935d6e440d041cff0eaa960d4",
        "../worker_entrypoint.py": "bc03c1f881305f4d6762a8afb75899ed6ad0a4c92074f4463a719d7414f3ef08",
    }


@pytest.mark.parametrize("large", [False, True])
def test_input_adapter_keeps_exact_historical_sources_and_capture_facts(large):
    raw = "POST /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n" + "body " * (15000 if large else 1)
    event = {"payload": raw, "signature": "synthetic", "event_id": "synthetic-id"}
    parsed = parse_http_payload(raw)
    old = input_builder_v226.build_agent_input(event, raw, parsed, 32768, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.26")
    current = input_builder_v227.build_agent_input(event, raw, parsed, 32768, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.27")
    assert old == current
    with pytest.raises(ValueError, match="unsupported_candidate_input_contract"):
        input_builder_v227.build_agent_input(event, raw, parsed, 32768, 3072,
            evidence_selection=True, fixed_rules_version="waf-system-v2.26")


@pytest.mark.parametrize("parser_status", ["success", "partial", "failed"])
@pytest.mark.parametrize("omitted", [False, True])
@pytest.mark.parametrize("action", ["A", "D"])
def test_verifier_conditions_and_integrity_policy_are_identical(parser_status, omitted, action):
    primary = output()
    current = VerifierPolicyContext(waf_action=action, parser_status=parser_status,
        input_truncated=omitted, fixed_rules_version="waf-system-v2.27")
    old = replace(current, fixed_rules_version="waf-system-v2.26")
    assert execution_policy_v227.verifier_reasons(primary, current) == execution_policy_v226.verifier_reasons(primary, old)
    assert execution_policy_v227.integrity_version_for_rules(current.fixed_rules_version) == execution_policy_v226.integrity_version_for_rules(old.fixed_rules_version)


@pytest.mark.parametrize("rules", [legacy_prompts_v226, prompts])
def test_dispatch_uses_encrypted_snapshot_not_the_latest_version(client, monkeypatch, rules):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        snapshot = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
    row = SimpleNamespace(prompt_snapshot_ciphertext=crypto.encrypt_text(snapshot.model_dump_json()),
        prompt_version=snapshot.prompt_version, prompt_policy_version_id=snapshot.policy_version_id)
    calls = []
    monkeypatch.setattr(worker_v226, "process_moduagent", lambda *args, **kwargs: calls.append("v226"))
    monkeypatch.setattr(worker_v227, "process_moduagent", lambda *args, **kwargs: calls.append("v227"))
    worker_runtime_v227.process_moduagent(None, crypto, row, "", .75)
    assert calls == (["v227"] if rules is prompts else ["v226"])


def test_new_version_without_snapshot_never_falls_back_to_legacy(client):
    row = SimpleNamespace(prompt_snapshot_ciphertext=None, prompt_version="waf-judgment-v2.27")
    with pytest.raises(PromptSnapshotError):
        worker_runtime_v227.execution_rules(row, client.app.state.crypto)


def test_approved_v226_baseline_keeps_exact_code_and_instructions_after_deployment(client):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        previous = _build_snapshot(get_active_policy(db, crypto), crypto, rules=legacy_prompts_v226)
        current = live_snapshot(db, crypto, client.app.state.settings)
        saved = {**current,
            "prompt": {name: getattr(previous, name) for name in current["prompt"]},
            "evidence_pipeline": previous.evidence_pipeline}
        record = ProductionPromotion(kind="baseline", snapshot_json=saved,
            configuration_hash=configuration_digest(saved), actor_id="synthetic-before-v227")
        db.add(record)
        db.commit()
        assert _baseline_rules(previous.metadata()) is legacy_prompts_v226
        assert approved_production_prompt(db, crypto) == previous
        production = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, production) == previous
        encrypted = production.prompt_snapshot_ciphertext
        assert load_analysis_prompt(production, crypto) == previous
        candidate = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert candidate.fixed_rules_version == "waf-system-v2.27"
        assert candidate.evidence_pipeline["version"] == "evidence-pipeline-v8"
        assert candidate.instructions_hash != previous.instructions_hash
        assert live_snapshot(db, crypto, client.app.state.settings) == saved
        assert production.prompt_snapshot_ciphertext == encrypted
        assert record.snapshot_json == saved
        assert record.configuration_hash == configuration_digest(saved)


@pytest.mark.parametrize("change", ["version", "rules_hash"])
def test_previous_baseline_does_not_accept_new_version_or_unknown_rules_hash(client, change):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        previous = _build_snapshot(get_active_policy(db, crypto), crypto, rules=legacy_prompts_v226)
    metadata = previous.metadata()
    if change == "version":
        metadata["fixed_rules_version"] = "waf-system-v2.27"
    else:
        metadata["fixed_rules_hash"] = "0" * 64
    with pytest.raises(PromptSnapshotError):
        _baseline_rules(metadata)


def test_same_verified_profile_is_allowed_for_independent_roles_and_retry_keeps_version(client, event_payload, candidate):
    configuration = {**without_editor(candidate), "verifier_profile_id": candidate["primary_profile_id"]}
    response = direct(client, event_payload, configuration)
    assert response.status_code == 202, response.text
    run_doc = response.json()
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        run = db.get(NamedRun, run_doc["id"])
        row = db.get(Analysis, run_doc["items"][0]["analysis_id"])
        snapshot = verify_configuration(run, crypto, row)
        assert snapshot["primary"] == snapshot["verifier"]
        prompt = load_analysis_prompt(row, crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.27"
        assert prompt.primary_instructions != prompt.verifier_instructions
        assert prompt.evidence_pipeline["version"] == "evidence-pipeline-v8"
        profile = db.get(VLLMProfile, candidate["primary_profile_id"])
        execution = make_execution_snapshot(row, profile, prompt, .75, profile, crypto=crypto)
        row.execution_snapshot_ciphertext = crypto.encrypt_text(execution.model_dump_json())
        row.status, row.error_code = "failed", "synthetic-failure"
        db.commit()
        original = row.prompt_snapshot_ciphertext
    retried = client.post(f"/api/v1/analyses/{row.id}/retry", json={
        "idempotency_key": "synthetic-v227-preserved-retry", "cost_acknowledged": True})
    assert retried.status_code == 202, retried.text
    with client.app.state.session_factory() as db:
        child = db.get(Analysis, retried.json()["analysis_id"])
        assert child.prompt_snapshot_ciphertext == original
        assert load_execution_snapshot(child, crypto) == execution


def test_informal_candidate_cannot_apply_to_production_but_official_evaluation_can(client, event_payload, candidate):
    configuration = without_editor(candidate)
    response = direct(client, event_payload, configuration)
    assert response.status_code == 202, response.text
    informal = response.json()
    before = client.get(PRODUCTION).json()
    rejected = client.post(PRODUCTION + "/promote", json={"candidate_test_run_id": informal["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert rejected.status_code == 409
    assert client.get(PRODUCTION).json()["configuration_hash"] == before["configuration_hash"]
    # Synthetic completion exercises the official evaluation and admin gate;
    # it does not claim measured model quality.
    official = qualified(client, event_payload, configuration)
    response = client.post(PRODUCTION + "/promote", json={"candidate_test_run_id": official["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 200, response.text
    active = client.get(PRODUCTION).json()
    assert active["snapshot"]["prompt"]["fixed_rules_version"] == "waf-system-v2.27"
