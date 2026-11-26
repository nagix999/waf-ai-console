"""Candidate admission preserves archived bytes and Production approval gates."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import worker_v236
from app.agent import evidence_pipeline_v235 as previous_pipeline
from app.agent import evidence_pipeline_v236 as pipeline
from app.agent import execution_policy_v235, execution_policy_v236
from app.agent import evidence_source_policy_v235, evidence_source_policy_v236
from app.agent import legacy_prompts_v235, legacy_prompts_v236 as prompts
from app.agent.policy import VerifierPolicyContext
from app.models import Analysis, ProductionPromotion
from app.services import worker_runtime_v236 as runtime
from app.services.candidate_configurations import configuration_digest
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshotError, _baseline_rules, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from test_execution_policy_v221 import output

pytestmark = pytest.mark.usefixtures("legacy_v236_admissions")


FROZEN_V235 = "065b5951d7b9c6f543f76e55015339d0bbb6cf27c122a856a9ac91d166a8c395"
FROZEN_PROMPTS_V235 = "3aa4390c6d7f3c602c924103d89419c6ea0afa69c886ee62ce6116ca6828eb73"


def test_all_206_archived_execution_files_are_byte_for_byte_preserved():
    previous = previous_pipeline.pipeline_artifact()
    current = pipeline.pipeline_artifact()
    assert previous["sha256"] == FROZEN_V235 and len(previous["files"]) == 206
    assert hashlib.sha256(Path(legacy_prompts_v235.__file__).read_bytes()).hexdigest() == FROZEN_PROMPTS_V235
    assert current["version"] == "evidence-pipeline-v16"
    assert all(current["files"][name] == digest for name, digest in previous["files"].items())
    assert current["input_version"] == "agent-input-v4"
    for key in ("source_catalog_version", "relations_version"):
        assert current[key] == previous[key]
    for name in ("conflict_repair_v1.py", "capture_separator_v1.py", "executor_v236.py",
                 "input_builder_v236.py", "../worker_v236.py", "../worker_entrypoint_v236.py",
                 "../services/worker_runtime_v236.py", "../services/evidence_display_worker_v236.py"):
        assert name in current["files"]
    root = Path(pipeline.__file__).parent
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
               for name, digest in current["files"].items())
    assert "contracts_v236.py" not in current["files"]
    assert pipeline.validate_pipeline_snapshot(previous, "waf-system-v2.35") == previous
    assert pipeline.validate_pipeline_snapshot(current, pipeline.RULES_VERSION) == current
    for value in (previous, {**current, "version": "evidence-pipeline-v15"}):
        with pytest.raises(ValueError, match="evidence_pipeline_snapshot_invalid"):
            pipeline.validate_pipeline_snapshot(value, pipeline.RULES_VERSION)
    tampered = deepcopy(current)
    tampered["files"]["conflict_repair_v1.py"] = "0" * 64
    with pytest.raises(ValueError, match="evidence_pipeline_snapshot_invalid"):
        pipeline.validate_pipeline_snapshot(tampered, pipeline.RULES_VERSION)


@pytest.mark.parametrize("rules", [legacy_prompts_v235, prompts])
def test_snapshots_restore_exact_instructions_and_dispatch_original_engine(client, monkeypatch, rules):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        snapshot = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
    row = SimpleNamespace(prompt_snapshot_ciphertext=crypto.encrypt_text(snapshot.model_dump_json()),
        prompt_version=snapshot.prompt_version, prompt_policy_version_id=snapshot.policy_version_id)
    assert load_analysis_prompt(row, crypto) == snapshot
    assert _baseline_rules(snapshot.metadata()) is rules
    calls = []
    monkeypatch.setattr(worker_v236, "process_moduagent", lambda *a, **k: calls.append("v236"))
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *a, **k: calls.append("v235"))
    runtime.process_moduagent(None, crypto, row, "", .75)
    assert calls == (["v236"] if rules is prompts else ["v235"])


@pytest.mark.parametrize("action", ["D", "A"])
@pytest.mark.parametrize("omitted", [False, True])
def test_verifier_and_final_policy_remain_unchanged(action, omitted):
    primary = output()
    old = VerifierPolicyContext(waf_action=action, parser_status="partial", confidence_threshold=.75,
        input_truncated=omitted, fixed_rules_version="waf-system-v2.35")
    new = VerifierPolicyContext(waf_action=action, parser_status="partial", confidence_threshold=.75,
        input_truncated=omitted, fixed_rules_version="waf-system-v2.36")
    assert execution_policy_v236.verifier_reasons(primary, new) == execution_policy_v235.verifier_reasons(primary, old)
    assert execution_policy_v236.integrity_version_for_rules(new.fixed_rules_version) == (
        execution_policy_v235.integrity_version_for_rules(old.fixed_rules_version))
    assert execution_policy_v236.verifier_policy_metadata(new.fixed_rules_version) == (
        execution_policy_v235.verifier_policy_metadata(old.fixed_rules_version))
    for name in ("affected_normal_findings", "apply_integrity_guard", "apply_final_followup_policy"):
        assert getattr(execution_policy_v236, name) is getattr(execution_policy_v235, name)


def test_source_roles_and_signature_context_restriction_remain_unchanged():
    catalog = {"items": [{"source_id": "c1", "field": "signature", "excerpt": "Synthetic detector"},
                         {"source_id": "c2", "field": "payload", "excerpt": "synthetic request"}]}
    assert evidence_source_policy_v236.catalog_for_source_policy(catalog, fixed_rules_version=pipeline.RULES_VERSION) == (
        evidence_source_policy_v235.catalog_for_source_policy(catalog, fixed_rules_version="waf-system-v2.35"))
    assert evidence_source_policy_v236.is_reference_source_field is evidence_source_policy_v235.is_reference_source_field


def test_v235_approved_production_remains_exact_and_new_candidate_is_not_applied(client):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        archived = _build_snapshot(get_active_policy(db, crypto), crypto, rules=legacy_prompts_v235)
        live = live_snapshot(db, crypto, client.app.state.settings)
        saved = {**live, "prompt": {name: getattr(archived, name) for name in live["prompt"]},
                 "evidence_pipeline": archived.evidence_pipeline}
        record = ProductionPromotion(kind="baseline", snapshot_json=saved,
            configuration_hash=configuration_digest(saved), actor_id="synthetic-before-v236")
        db.add(record)
        db.commit()
        assert _baseline_rules(archived.metadata()) is legacy_prompts_v235
        assert approved_production_prompt(db, crypto) == archived
        row = Analysis(analysis_purpose="production")
        assert pin_analysis_prompt(db, crypto, row) == archived
        ciphertext = row.prompt_snapshot_ciphertext
        candidate = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert candidate.fixed_rules_version == pipeline.RULES_VERSION
        assert candidate.evidence_pipeline["version"] == "evidence-pipeline-v16"
        assert candidate.instructions_hash != archived.instructions_hash
        assert live_snapshot(db, crypto, client.app.state.settings) == saved
        assert row.prompt_snapshot_ciphertext == ciphertext
        assert record.configuration_hash == configuration_digest(saved)


@pytest.mark.parametrize("version", ["v2.34", "v2.35", "v2.36", "v2.37"])
def test_new_execution_without_pinned_provenance_never_falls_back(monkeypatch, version):
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *a, **k: pytest.fail("legacy fallback"))
    row = SimpleNamespace(prompt_snapshot_ciphertext=None, prompt_version=f"waf-judgment-{version}/policy-1")
    with pytest.raises(PromptSnapshotError):
        runtime.process_moduagent(None, object(), row, "", .7)


def test_unknown_future_snapshot_is_fail_closed(monkeypatch):
    monkeypatch.setattr(runtime, "load_analysis_prompt", lambda *a: SimpleNamespace(fixed_rules_version="waf-system-v2.37"))
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *a, **k: pytest.fail("legacy fallback"))
    row = SimpleNamespace(prompt_snapshot_ciphertext="synthetic", prompt_version=None)
    with pytest.raises(PromptSnapshotError):
        runtime.process_moduagent(None, object(), row, "", .7)
