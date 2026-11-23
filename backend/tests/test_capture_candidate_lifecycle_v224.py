"""Synthetic-only provenance, admission and release gates for the v2.24 candidate."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import evidence_pipeline_v223, evidence_pipeline_v224, legacy_prompts_v223, legacy_prompts_v224_semantics as prompts
from app.agent import execution_policy_v223, execution_policy_v224
from app.agent.evidence_source_policy_v224 import catalog_for_source_policy, signature_support_issues
from app.agent.policy import VerifierPolicyContext
from app.models import Analysis, ProductionPromotion, TestRun as NamedRun, VLLMProfile
from app.services.analysis_retries import make_execution_snapshot, load_execution_snapshot, RetryError
from app.services.candidate_configurations import configuration_digest, verify_configuration
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    _baseline_rules, _build_snapshot, approved_production_prompt, load_analysis_prompt, pin_analysis_prompt,
)
from app.services.signature_explanations import read_snapshot, metadata
from test_candidate_configurations import candidate
from test_evidence_relations import wire
from app.agent.contracts import SecurityAnalysisOutputV220
from test_signature_context_lifecycle import direct, promote, qualified, save_catalog, selected


pytestmark = pytest.mark.usefixtures("registered_vllm_target", "legacy_v224_admissions")
RULES = "waf-system-v2.24"


def test_complete_v223_prompt_is_byte_preserved_and_baseline_resolves_both_versions():
    assert hashlib.sha256(Path(legacy_prompts_v223.__file__).read_bytes()).hexdigest() == (
        "37144a5f9651b149a49f05f1a3c3aa31c6283df55139469cfd47b6d1959f112a")
    assert legacy_prompts_v223.FIXED_RULES_VERSION == "waf-system-v2.23"
    assert prompts.FIXED_RULES_VERSION == RULES
    for module in (legacy_prompts_v223, prompts):
        expected = {"fixed_rules_version": module.FIXED_RULES_VERSION,
                    "fixed_rules_hash": hashlib.sha256(module.FIXED_INSTRUCTIONS.encode()).hexdigest()}
        assert _baseline_rules(expected) is module


def test_new_manifest_is_additive_and_cross_version_substitution_is_rejected():
    before, after = evidence_pipeline_v223.pipeline_artifact(), evidence_pipeline_v224.pipeline_artifact()
    assert {key: after["files"][key] for key in before["files"]} == before["files"]
    assert after["version"] == "evidence-pipeline-v5"
    assert after["input_version"] == "agent-input-v3"
    assert after["source_catalog_version"] == before["source_catalog_version"] == "evidence-selection-v5"
    assert {"contracts_v224.py", "executor_v224.py", "relation_recovery_v224.py",
            "../worker_v224.py", "../services/worker_runtime_v224.py",
            "../services/evidence_display_worker_v224.py"} <= after["files"].keys()
    assert evidence_pipeline_v224.validate_pipeline_snapshot(before, "waf-system-v2.23") == before
    assert evidence_pipeline_v224.validate_pipeline_snapshot(after, RULES) == after
    for value in (before, {**after, "input_version": "agent-input-v2"}):
        with pytest.raises(ValueError, match="evidence_pipeline_snapshot_invalid"):
            evidence_pipeline_v224.validate_pipeline_snapshot(value, RULES)


def test_capture_guidance_is_shared_and_old_administrator_summary_order_is_overridden():
    primary, verifier = prompts.build_role_instructions("요청을 먼저 요약한다. 합성 관리자 지침.")
    for value in (primary, verifier):
        for term in ("capture_context", "received_payload_bytes", "received_tail_submitted", "payload_omitted",
                     "capture_assessment", "possible", "no_clear_signs", "unknown",
                     "완전성 보증", "240자", "보류 조건", "표시 순서일 뿐"):
            assert term in value
        assert "길이나 한도 근처라는 사실만으로 절단을 확정하지 않는다" in value
        assert "뒤쪽 공격 구문이 안 보인다는 말만으로 보류하지 않는다" in value
        assert "시그니처와 signature_explanation" in value
    assert primary != verifier
    assert prompts.PRIMARY_ROLE == legacy_prompts_v223.PRIMARY_ROLE
    assert prompts.VERIFIER_ROLE == legacy_prompts_v223.VERIFIER_ROLE


@pytest.mark.parametrize("field", ["signature", "signature_explanation", "extra_fields.signature_explanation"])
def test_reference_only_source_restriction_is_unchanged(field):
    original = {"version": "synthetic", "items": [{"source_id": "c1", "field": field,
        "excerpt": "Synthetic reference", "start": None, "end": None}]}
    original_copy = deepcopy(original)
    catalog = catalog_for_source_policy(original, fixed_rules_version=RULES)
    assert catalog["version"] == "evidence-selection-v5"
    assert catalog["items"][0]["allowed_supports"] == ["context"]
    assert original == original_copy
    for supports in ("attack_request", "normal_request", "context"):
        selection = SimpleNamespace(evidence=[SimpleNamespace(source_id="c1", supports=supports)])
        issues = signature_support_issues(selection, catalog, fixed_rules_version=RULES)
        assert bool(issues) is (supports != "context")


@pytest.mark.parametrize("waf_action,omitted", [("A", False), ("D", False), ("A", True), ("D", True)])
def test_capture_candidate_keeps_real_omission_and_waf_policy(waf_action, omitted):
    data = wire()
    data["confidence_score"] = .95
    primary = SecurityAnalysisOutputV220.model_validate(data).legacy_bridge()
    context = VerifierPolicyContext(waf_action, "partial", omitted, fixed_rules_version=RULES)
    assert execution_policy_v224.verifier_reasons(primary, context) == execution_policy_v223.verifier_reasons(
        primary, replace(context, fixed_rules_version="waf-system-v2.23"))
    assert "parser_incomplete" not in execution_policy_v224.verifier_reasons(primary, context)
    assert ("input_truncated" in execution_policy_v224.verifier_reasons(primary, context)) is omitted


def test_approved_v223_baseline_stays_v223_after_candidate_code_deployment(client):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        current = live_snapshot(db, crypto, client.app.state.settings)
        old = _build_snapshot(get_active_policy(db, crypto), crypto, rules=legacy_prompts_v223)
        document = {**current, "prompt": {key: getattr(old, key) for key in current["prompt"]},
                    "evidence_pipeline": old.evidence_pipeline}
        baseline = ProductionPromotion(kind="baseline", snapshot_json=document,
            configuration_hash=configuration_digest(document), actor_id="synthetic-v223-baseline")
        db.add(baseline)
        db.commit()
        assert approved_production_prompt(db, crypto) == old
        row = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, row) == old
        assert load_analysis_prompt(row, crypto).fixed_rules_version == "waf-system-v2.23"
        assert live_snapshot(db, crypto, client.app.state.settings) == document
        assert db.get(ProductionPromotion, baseline.id).snapshot_json == document


def test_v224_candidate_pins_reference_and_retry_requires_same_schema6_binding(client, candidate, event_payload):
    catalog = save_catalog(client, event_payload)
    configuration = selected(candidate, catalog)
    response = direct(client, event_payload, configuration)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["configuration_snapshot"]["prompt"]["fixed_rules_version"] == RULES
    assert body["configuration_snapshot"]["evidence_pipeline"]["version"] == "evidence-pipeline-v5"
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        run = db.get(NamedRun, body["id"])
        row = db.get(Analysis, body["items"][0]["analysis_id"])
        assert verify_configuration(run, crypto, row) == body["configuration_snapshot"]
        pinned = read_snapshot(crypto, row)
        snapshot = make_execution_snapshot(row, db.get(VLLMProfile, configuration["primary_profile_id"]),
            load_analysis_prompt(row, crypto), .75,
            verifier_profile=db.get(VLLMProfile, configuration["verifier_profile_id"]), crypto=crypto)
        assert snapshot.schema_version == 6
        assert snapshot.signature_explanations == metadata(pinned)
        row.execution_snapshot_ciphertext = crypto.encrypt_text(snapshot.model_dump_json())
        assert load_execution_snapshot(row, crypto) == snapshot
        corrupted = snapshot.model_copy(update={"signature_explanations": {**snapshot.signature_explanations,
            "content_hash": "0" * 64}})
        row.execution_snapshot_ciphertext = crypto.encrypt_text(corrupted.model_dump_json())
        with pytest.raises(RetryError, match="retry_execution_snapshot_invalid"):
            load_execution_snapshot(row, crypto)
    assert client.get("/api/v1/admin/signature-explanations").json()["active_version_id"] is None


def test_v224_requires_approved_ground_truth_evaluation_then_explicit_promotion(client, candidate, event_payload):
    catalog = save_catalog(client, event_payload)
    configuration = selected(candidate, catalog)
    trial = direct(client, event_payload, configuration).json()
    base = "/api/v1/admin/production-configurations"
    before = client.get(base).json()
    assert not client.get(f"{base}/preflight/{trial['id']}").json()["eligible"]
    assert client.post(base + "/promote", json={"candidate_test_run_id": trial["id"],
        "expected_production_configuration_hash": before["configuration_hash"],
        "acknowledge_schema_change": True}).status_code == 409
    assert client.get(base).json()["configuration_hash"] == before["configuration_hash"]
    official = qualified(client, event_payload, configuration)
    applied = promote(client, official)
    assert applied["configuration_hash"] == official["configuration_hash"]
    assert client.get(base).json()["snapshot"]["prompt"]["fixed_rules_version"] == RULES
