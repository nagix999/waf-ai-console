"""Replay the archived v236 rollback without changing its historical behavior."""
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agent import legacy_prompts_v236, legacy_prompts_v237, prompts as current_prompts
from app.agent import legacy_prompts_v236 as prompts
from app.agent.evidence_pipeline_v236 import pipeline_artifact as previous_pipeline
from app.agent.evidence_pipeline_v237 import pipeline_artifact as archived_pipeline
from app.models import Analysis, ProductionPromotion, TestRun as NamedRun, VLLMProfile
from app.result_contracts import validate_new_result
from app.services.candidate_configurations import configuration_digest
from app.services.production_configurations import live_snapshot
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import (
    PromptSnapshotError, _baseline_rules, _build_snapshot, approved_production_prompt,
    load_analysis_prompt, pin_analysis_prompt,
)
from app.services.worker_runtime_v237 import process_moduagent
from test_agent_openai import completion, install_http
from test_flat_evidence_v228 import wire


pytestmark = pytest.mark.usefixtures("legacy_v236_admissions")


def test_archived_rollback_instructions_are_exact_v236_not_a_relabeled_v237():
    for name in ("PROMPT_VERSION", "FIXED_RULES_VERSION", "FIXED_INSTRUCTIONS", "PRIMARY_ROLE",
                 "VERIFIER_ROLE", "DEFAULT_POLICY_NAME", "DEFAULT_POLICY_TEXT",
                 "BASE_INSTRUCTIONS", "PRIMARY_INSTRUCTIONS", "VERIFIER_INSTRUCTIONS"):
        assert getattr(prompts, name) == getattr(legacy_prompts_v236, name)
    assert prompts.build_role_instructions is legacy_prompts_v236.build_role_instructions
    assert prompts.build_role_instructions("Synthetic policy") == legacy_prompts_v236.build_role_instructions("Synthetic policy")
    assert "contributes_to_evidence_ids" not in prompts.FIXED_INSTRUCTIONS
    assert "related_evidence_ids" in prompts.FIXED_INSTRUCTIONS
    assert {"waf-system-v2.36", "waf-system-v2.37"} <= current_prompts.SEMANTIC_RULES_VERSIONS
    assert {"waf-system-v2.36", "waf-system-v2.37"} <= current_prompts.V3_RULES_VERSIONS


def test_frozen_pipelines_and_the_withdrawn_prompt_remain_exact():
    assert previous_pipeline()["sha256"] == "f96ea22d6a273e24e7a1394e5c29f1019dbcf09b7575092c881577515249ee67"
    assert archived_pipeline()["sha256"] == "af1214d7c018ad2dc8e8f26bcdaee806621d42919b03c0001792baa2318d7e46"
    from pathlib import Path
    assert hashlib.sha256(Path(legacy_prompts_v237.__file__).read_bytes()).hexdigest() == (
        "722da6583dd54699e38e3b0a35edcb603d2037f9cd52d2f887818898c1594511")


def test_new_snapshot_selects_original_v236_pipeline_and_archive_without_ambiguity(client):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        policy = get_active_policy(db, crypto)
        current = _build_snapshot(policy, crypto)
        expected = _build_snapshot(policy, crypto, rules=legacy_prompts_v236)
    assert current == expected
    assert current.fixed_rules_version == "waf-system-v2.36"
    assert current.evidence_pipeline == previous_pipeline()
    assert _baseline_rules(current.metadata()) is legacy_prompts_v236
    with pytest.raises(PromptSnapshotError):
        _baseline_rules({**current.metadata(), "fixed_rules_hash": "0" * 64})


@pytest.mark.parametrize("rules", [legacy_prompts_v236, legacy_prompts_v237])
def test_existing_approved_configuration_and_queued_execution_are_not_silently_rewritten(client, rules):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        policy = get_active_policy(db, crypto)
        saved_prompt = _build_snapshot(policy, crypto, rules=rules)
        current = live_snapshot(db, crypto, client.app.state.settings)
        saved = {**current, "prompt": {key: getattr(saved_prompt, key) for key in current["prompt"]},
                 "evidence_pipeline": saved_prompt.evidence_pipeline}
        record = ProductionPromotion(kind="baseline", snapshot_json=saved,
            configuration_hash=configuration_digest(saved), actor_id="synthetic-rollback")
        db.add(record)
        db.commit()
        assert _baseline_rules(saved_prompt.metadata()) is rules
        assert approved_production_prompt(db, crypto) == saved_prompt
        production = SimpleNamespace(analysis_purpose="production", prompt_snapshot_ciphertext=None,
                                     prompt_policy_version_id=None, prompt_version=None)
        assert pin_analysis_prompt(db, crypto, production) == saved_prompt
        ciphertext = production.prompt_snapshot_ciphertext
        assert pin_analysis_prompt(db, crypto, production) == saved_prompt
        assert production.prompt_snapshot_ciphertext == ciphertext
        assert load_analysis_prompt(production, crypto) == saved_prompt
        fresh_test = SimpleNamespace(analysis_purpose="test", prompt_snapshot_ciphertext=None,
                                     prompt_policy_version_id=None, prompt_version=None)
        assert pin_analysis_prompt(db, crypto, fresh_test).fixed_rules_version == "waf-system-v2.36"
        assert record.configuration_hash == configuration_digest(saved)


def test_new_named_test_pins_v236_through_the_actual_test_api(client, event_payload):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200
    response = client.post("/api/v1/test-runs", json={"name": "Synthetic rollback test",
        "idempotency_key": "synthetic-rollback-v236", "event": event_payload})
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        row = db.get(NamedRun, response.json()["id"])
        prompt = load_analysis_prompt(row, client.app.state.crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.36"
        assert prompt.evidence_pipeline == previous_pipeline()


@pytest.mark.usefixtures("registered_vllm_target")
def test_new_snapshot_runs_the_old_schema_through_the_versioned_dispatcher(
        client, event_payload, service_headers, monkeypatch):
    event = {**event_payload, "waf_action": "A",
             "payload": "POST /synthetic HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\nsynthetic body"}
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="Synthetic rollback", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="production"))
        row = db.get(Analysis, identifier)
        assert load_analysis_prompt(row, crypto).fixed_rules_version == "waf-system-v2.36"
        db.commit()

    def model(request, _count):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        assert "related_evidence_ids" in json.dumps(schema)
        assert "contributes_to_evidence_ids" not in json.dumps(schema)
        document = json.loads(next(item["content"] for item in body["messages"] if item["role"] == "user"))
        sources = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
        value = wire()
        value["evidence"][0]["source_id"] = sources[-1]["source_id"]
        value["evidence"][1]["source_id"] = sources[0]["source_id"]
        value["confidence_score"] = .99
        value["signature_assessment"].update(relation="exact", matched_points=["합성 요청 문맥을 비교했습니다."],
            mismatched_points=[], uncertainty_ko=None)
        return httpx.Response(200, json=completion(json.dumps(value)))

    requests, _ = install_http(monkeypatch, model)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        process_moduagent(db, crypto, row, "", .75)
        assert row.status == "completed"
        assert load_analysis_prompt(row, crypto).fixed_rules_version == "waf-system-v2.36"
        assert validate_new_result(row.result_json)["schema_version"] == "waf-analysis-v3"
    assert len(requests) == 2
    first, second = [next(item["content"] for item in json.loads(request.content)["messages"]
        if item["role"] == "user") for request in requests]
    assert first == second
