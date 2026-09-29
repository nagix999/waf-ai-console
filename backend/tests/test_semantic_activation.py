"""New candidate rules require official promotion; no live service or models."""
import copy

import pytest
from app.agent import legacy_prompts_v212, legacy_prompts_v213
from app.agent import result_editor
from app.models import Analysis, ProductionPromotion, TestRun as AnalysisTestRun
from app.services.candidate_configurations import configuration_digest
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot, load_analysis_prompt, pin_analysis_prompt, PromptSnapshotError
from app.services.evidence_editor import capture_editor_profile
from test_candidate_configurations import candidate
from test_production_promotion import qualified, BASE

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def legacy_baseline(client, rules=legacy_prompts_v212):
    with client.app.state.session_factory() as db:
        crypto = client.app.state.crypto
        snapshot = live_snapshot(db, crypto, client.app.state.settings)
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
        snapshot["prompt"] = {key: getattr(prompt, key) for key in snapshot["prompt"]}
        db.add(ProductionPromotion(kind="baseline", snapshot_json=snapshot,
            configuration_hash=configuration_digest(snapshot), actor_id="synthetic-legacy"))
        db.commit()
        return copy.deepcopy(snapshot)


@pytest.mark.parametrize("rules", [legacy_prompts_v212, legacy_prompts_v213])
def test_legacy_production_stays_pinned_until_official_candidate_is_promoted(client, event_payload, candidate, rules):
    before = legacy_baseline(client, rules)
    current = client.get(BASE).json()
    assert current["snapshot"] == before
    old = client.post("/api/v1/analyses", json=event_payload).json()
    assert old["prompt_version"].startswith(rules.PROMPT_VERSION + "/")
    run = qualified(client, event_payload, candidate)
    assert run["configuration_snapshot"]["prompt"]["fixed_rules_version"] == "waf-system-v2.14"
    assert client.get(BASE).json()["snapshot"] == before
    review = client.get(f"{BASE}/preflight/{run['id']}").json()
    assert review["eligible"], review["checks"]
    response = client.post(f"{BASE}/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": current["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 200, response.text
    new = client.post("/api/v1/analyses", json={**event_payload, "event_id": "after-promotion", "vendor_score": 2}).json()
    assert new["prompt_version"].startswith("waf-judgment-v2.14/")
    with client.app.state.session_factory() as db:
        old_snapshot = load_analysis_prompt(db.get(Analysis, old["id"]), client.app.state.crypto)
        new_snapshot = load_analysis_prompt(db.get(Analysis, new["id"]), client.app.state.crypto)
        tested = load_analysis_prompt(db.get(AnalysisTestRun, run["id"]), client.app.state.crypto)
        assert old_snapshot.fixed_rules_version == rules.FIXED_RULES_VERSION
        assert new_snapshot.instructions_hash == tested.instructions_hash
        assert new_snapshot.primary_instructions == tested.primary_instructions
        assert new_snapshot.verifier_instructions == tested.verifier_instructions


def test_corrupt_approval_never_falls_back_to_latest_rules(client, candidate):
    legacy_baseline(client)
    with client.app.state.session_factory() as db:
        record = db.query(ProductionPromotion).one()
        record.configuration_hash = "0" * 64
        db.commit()
        with pytest.raises(PromptSnapshotError):
            pin_analysis_prompt(db, client.app.state.crypto, Analysis(analysis_purpose="production"))


def test_production_editor_preserves_v2_instructions_while_candidate_uses_v3(client, candidate):
    from app.models import VLLMProfile
    snapshot = legacy_baseline(client)
    with client.app.state.session_factory() as db:
        from app.agent.evidence_editor import instructions_hash
        profile = db.get(VLLMProfile, candidate["evidence_editor_profile_id"])
        snapshot["evidence_editor"] = {"enabled": True, "profile_id": profile.id,
            "profile_fingerprint": capture_editor_profile(profile).profile_fingerprint,
            "version": result_editor.LEGACY_VERSION, "instructions_hash": instructions_hash(result_editor.LEGACY_INSTRUCTIONS)}
        row = db.query(ProductionPromotion).one()
        row.snapshot_json = snapshot
        row.configuration_hash = configuration_digest(snapshot)
        db.commit()
        pinned = capture_editor_profile(profile, db=db)
        assert pinned.version == "result-editor-v2"
        assert pinned.instructions == result_editor.LEGACY_INSTRUCTIONS
        assert capture_editor_profile(profile).version == "result-editor-v3"
