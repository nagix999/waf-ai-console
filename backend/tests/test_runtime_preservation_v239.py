"""Recovery-only candidates preserve byte-pinned engines and approval provenance."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import legacy_prompts_v236, legacy_prompts_v237, legacy_prompts_v238, legacy_prompts_v239, prompts
from app.agent import input_builder_v238, input_builder_v239
from app.agent.evidence_pipeline_v238 import pipeline_artifact as archived_pipeline
from app.agent.evidence_pipeline_v239 import pipeline_artifact, pipeline_for_rules, validate_pipeline_snapshot
from app.models import ProductionPromotion
from app.services.candidate_configurations import configuration_digest
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshotError, _baseline_rules, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from app.services import evidence_relation_dispatch as old_relations
from app.services import evidence_relation_dispatch_v239 as relations
from app.services import worker_runtime_v239 as runtime


def test_all_258_archived_files_and_prompt_bytes_remain_exact():
    archived = archived_pipeline()
    assert archived["sha256"] == "848f2827e946edb03ab83e0ac0191e3d19ba26bad9cf1a789a0774ff7d9d54e8"
    assert len(archived["files"]) == 258
    current = pipeline_artifact()
    assert current["version"] == "evidence-pipeline-v19"
    assert current["input_version"] == archived["input_version"]
    assert current["source_catalog_version"] == archived["source_catalog_version"]
    assert current["relations_version"] == archived["relations_version"]
    assert all(current["files"][name] == digest for name, digest in archived["files"].items())
    assert "executor_v239.py" not in current["files"]
    assert "contracts_v239.py" not in current["files"]
    assert "source_repair_recovery_v239.py" in current["files"]
    assert hashlib.sha256(Path(legacy_prompts_v238.__file__).read_bytes()).hexdigest() == (
        "020d76442a83f4fc59c004e498e4af7bc7ff265bcf61a5aa620d4b76da52737d")
    assert pipeline_for_rules("waf-system-v2.38") == archived
    assert validate_pipeline_snapshot(current, "waf-system-v2.39") == current
    with pytest.raises(ValueError):
        validate_pipeline_snapshot(archived, "waf-system-v2.39")
    changed = deepcopy(current)
    changed["files"]["executor_v238.py"] = "0" * 64
    with pytest.raises(ValueError):
        validate_pipeline_snapshot(changed, "waf-system-v2.39")


def test_candidate_only_versions_execution_not_instructions_or_roles():
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.39"
    assert prompts.PROMPT_VERSION == "waf-judgment-v2.39"
    for name in ("FIXED_INSTRUCTIONS", "PRIMARY_ROLE", "VERIFIER_ROLE", "DEFAULT_POLICY_TEXT",
                 "BASE_INSTRUCTIONS", "PRIMARY_INSTRUCTIONS", "VERIFIER_INSTRUCTIONS"):
        assert getattr(prompts, name) == getattr(legacy_prompts_v238, name)
    assert prompts.build_role_instructions("Synthetic policy") == legacy_prompts_v238.build_role_instructions("Synthetic policy")
    assert {"waf-system-v2.36", "waf-system-v2.37", "waf-system-v2.38", "waf-system-v2.39"} <= prompts.SEMANTIC_RULES_VERSIONS


@pytest.mark.parametrize("rules", [legacy_prompts_v236, legacy_prompts_v237, legacy_prompts_v238, legacy_prompts_v239])
def test_snapshot_restores_exact_archived_text_without_ambiguity(client, rules):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        saved = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
    assert _baseline_rules(saved.metadata()) is rules
    row = SimpleNamespace(prompt_snapshot_ciphertext=crypto.encrypt_text(saved.model_dump_json()),
        prompt_version=saved.prompt_version, prompt_policy_version_id=saved.policy_version_id)
    assert load_analysis_prompt(row, crypto) == saved


@pytest.mark.parametrize("rules", [legacy_prompts_v236, legacy_prompts_v237, legacy_prompts_v238])
def test_candidate_does_not_replace_approved_production(client, rules):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        old = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
        live = live_snapshot(db, crypto, client.app.state.settings)
        saved = {**live, "prompt": {key: getattr(old, key) for key in live["prompt"]},
                 "evidence_pipeline": old.evidence_pipeline}
        approved = ProductionPromotion(kind="baseline", snapshot_json=saved,
            configuration_hash=configuration_digest(saved), actor_id="synthetic-preservation")
        db.add(approved)
        db.commit()
        assert approved_production_prompt(db, crypto) == old
        production = SimpleNamespace(analysis_purpose="production", prompt_snapshot_ciphertext=None,
            prompt_policy_version_id=None, prompt_version=None)
        assert pin_analysis_prompt(db, crypto, production) == old
        ciphertext = production.prompt_snapshot_ciphertext
        test = SimpleNamespace(analysis_purpose="test", prompt_snapshot_ciphertext=None,
            prompt_policy_version_id=None, prompt_version=None)
        assert pin_analysis_prompt(db, crypto, test).fixed_rules_version == "waf-system-v2.39"
        assert production.prompt_snapshot_ciphertext == ciphertext
        assert live_snapshot(db, crypto, client.app.state.settings) == saved
        assert approved.configuration_hash == configuration_digest(saved)


def test_input_wrapper_reuses_identical_bounded_v238_input(event_payload):
    from app.services.http_parser import parse_http_payload
    raw = event_payload["payload"]
    args = (event_payload, raw, parse_http_payload(raw), 32768, 3072)
    old = input_builder_v238.build_agent_input(*args, evidence_selection=True,
        fixed_rules_version="waf-system-v2.38")
    new = input_builder_v239.build_agent_input(*args, evidence_selection=True,
        fixed_rules_version="waf-system-v2.39")
    assert old == new
    with pytest.raises(ValueError, match="unsupported_candidate_input_contract"):
        input_builder_v239.build_agent_input(*args, evidence_selection=True,
            fixed_rules_version="waf-system-v2.38")


def test_new_read_contract_is_explicit_and_does_not_mutate_saved_policy():
    from test_mixed_evidence_relations_v238 import result, relations as saved_relations
    value, refs = result(), saved_relations()
    old_value = deepcopy(value)
    assert relations.validate_saved_relations(refs, value) == old_relations.validate_saved_relations(refs, value)
    assert value == old_value
    value["policy"]["fixed_rules_version"] = "waf-system-v2.39"
    assert relations.validate_saved_relations(refs, value) == refs
    assert value["policy"]["fixed_rules_version"] == "waf-system-v2.39"
    assert relations.empty_relations(value) == {"version": "evidence-relations-v2", "groups": []}
    value["policy"]["fixed_rules_version"] = "waf-system-v2.40"
    with pytest.raises(ValueError):
        relations.validate_saved_relations(refs, value)


def test_missing_or_future_provenance_never_dispatches_legacy(monkeypatch):
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *_a, **_k: pytest.fail("legacy fallback"))
    row = SimpleNamespace(prompt_snapshot_ciphertext=None, prompt_version="waf-judgment-v2.39/policy-1")
    with pytest.raises(PromptSnapshotError):
        runtime.process_moduagent(None, object(), row, "", .75)
    monkeypatch.setattr(runtime, "load_analysis_prompt", lambda *_a: SimpleNamespace(fixed_rules_version="waf-system-v2.40"))
    row.prompt_snapshot_ciphertext = "synthetic"
    with pytest.raises(PromptSnapshotError):
        runtime.process_moduagent(None, object(), row, "", .75)
