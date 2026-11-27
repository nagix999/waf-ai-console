"""New mixed-direction policy preserves archives, sources and approvals."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent import legacy_prompts_v236, legacy_prompts_v237, legacy_prompts_v238, prompts
from app.agent.evidence_pipeline_v237 import pipeline_artifact as archived_pipeline
from app.agent.evidence_pipeline_v238 import pipeline_artifact, validate_pipeline_snapshot
from app.models import ProductionPromotion
from app.services.candidate_configurations import configuration_digest
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshotError, _baseline_rules, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from app.services import worker_runtime_v238 as runtime


def test_all_237_archived_files_and_prompt_bytes_remain_exact():
    archived = archived_pipeline()
    assert archived["sha256"] == "af1214d7c018ad2dc8e8f26bcdaee806621d42919b03c0001792baa2318d7e46"
    assert len(archived["files"]) == 237
    current = pipeline_artifact()
    assert current["version"] == "evidence-pipeline-v18"
    assert current["relations_version"] == "evidence-relations-v2"
    assert current["input_version"] == archived["input_version"]
    assert current["source_catalog_version"] == archived["source_catalog_version"]
    assert all(current["files"][name] == digest for name, digest in archived["files"].items())
    assert hashlib.sha256(Path(legacy_prompts_v236.__file__).read_bytes()).hexdigest() == (
        "b8032aa2161374a96cdc8dc91c7a537b10f3406849e20d372e9c102b742980bc")
    assert hashlib.sha256(Path(legacy_prompts_v237.__file__).read_bytes()).hexdigest() == (
        "722da6583dd54699e38e3b0a35edcb603d2037f9cd52d2f887818898c1594511")
    assert validate_pipeline_snapshot(current, "waf-system-v2.38") == current
    assert validate_pipeline_snapshot(archived, "waf-system-v2.37") == archived
    with pytest.raises(ValueError):
        validate_pipeline_snapshot(archived, "waf-system-v2.38")


def test_candidate_preserves_flat_wire_and_role_independence():
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.38"
    assert prompts.FIXED_INSTRUCTIONS == legacy_prompts_v238.FIXED_INSTRUCTIONS
    assert prompts.PRIMARY_ROLE == legacy_prompts_v236.PRIMARY_ROLE
    assert prompts.VERIFIER_ROLE == legacy_prompts_v236.VERIFIER_ROLE
    assert prompts.DEFAULT_POLICY_TEXT == legacy_prompts_v236.DEFAULT_POLICY_TEXT
    assert "related_evidence_ids" in prompts.FIXED_INSTRUCTIONS
    assert "contributes_to_evidence_ids" not in prompts.FIXED_INSTRUCTIONS
    assert "한 해석의 필수 연결을 따라가도 공격/정상 기여를 섞지 않는다" not in prompts.FIXED_INSTRUCTIONS
    assert "종합했을 때의 의미와 판단 이유를 대표 설명에 쓴다" in prompts.FIXED_INSTRUCTIONS
    assert "분류가 다른 근거의 연결이나 양쪽 정황 공존만으로" in prompts.FIXED_INSTRUCTIONS
    assert {"waf-system-v2.36", "waf-system-v2.37", "waf-system-v2.38"} <= prompts.SEMANTIC_RULES_VERSIONS


@pytest.mark.parametrize("rules", [legacy_prompts_v236, legacy_prompts_v237, legacy_prompts_v238])
def test_snapshot_restores_exact_archived_text_without_ambiguity(client, rules):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        saved = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
    assert _baseline_rules(saved.metadata()) is rules
    row = SimpleNamespace(prompt_snapshot_ciphertext=crypto.encrypt_text(saved.model_dump_json()),
        prompt_version=saved.prompt_version, prompt_policy_version_id=saved.policy_version_id)
    assert load_analysis_prompt(row, crypto) == saved


@pytest.mark.parametrize("rules", [legacy_prompts_v236, legacy_prompts_v237])
def test_new_candidate_does_not_replace_approved_production(client, rules):
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
        assert pin_analysis_prompt(db, crypto, test).fixed_rules_version == "waf-system-v2.38"
        assert production.prompt_snapshot_ciphertext == ciphertext
        assert live_snapshot(db, crypto, client.app.state.settings) == saved
        assert approved.configuration_hash == configuration_digest(saved)


def test_future_or_missing_provenance_never_dispatches_legacy(monkeypatch):
    monkeypatch.setattr(runtime.legacy, "process_moduagent", lambda *_a, **_k: pytest.fail("legacy fallback"))
    row = SimpleNamespace(prompt_snapshot_ciphertext=None, prompt_version="waf-judgment-v2.38/policy-1")
    with pytest.raises(PromptSnapshotError):
        runtime.process_moduagent(None, object(), row, "", .75)
    monkeypatch.setattr(runtime, "load_analysis_prompt", lambda *_a: SimpleNamespace(fixed_rules_version="waf-system-v2.39"))
    row.prompt_snapshot_ciphertext = "synthetic"
    with pytest.raises(PromptSnapshotError):
        runtime.process_moduagent(None, object(), row, "", .75)
