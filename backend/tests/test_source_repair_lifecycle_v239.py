"""Persisted recovery keeps public v3, failure retries and independent policy.

All events and provider completions are synthetic; no model is contacted.
"""
from copy import deepcopy
import json

import httpx
import pytest

from app.models import AgentRun, Analysis, VLLMProfile, VLLMTestRun
from app.result_contracts import validate_new_result
from app.services.analysis_retries import eligibility
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from app.services.worker_runtime_v239 import process_moduagent
from app.services.vllm_profiles import profile_fingerprint
from app.worker import WorkerExecutionError, mark_failed
from test_agent_openai import completion, install_http
from test_flat_evidence_v228 import wire
from test_source_repair_length_recovery_v239 import review, stage, user_document


@pytest.mark.parametrize("path", ["valid_first_output", "completed_source_correction", "completed_correction_failed_review"])
def test_existing_success_paths_keep_their_previous_semantics(monkeypatch, path):
    from test_source_repair_length_recovery_v239 import case, install, run
    data = case()
    correction = {"corrections": [{"index": 1, "source_id": data["actual"]["source_id"]}],
                  "requires_reanalysis": False}
    if path == "valid_first_output":
        data["value"]["evidence"] = data["value"]["evidence"][:1]
    elif path == "completed_correction_failed_review":
        correction = {"corrections": [], "requires_reanalysis": True}
    requests, counts = install(monkeypatch, data, correction_content=json.dumps(correction),
        correction_finish="stop", review_defect="malformed")
    call = run(data)
    assert call.succeeded
    assert call.output.verdict.value == ("inconclusive" if path == "completed_correction_failed_review" else "true_positive")
    assert not call.telemetry["evidence_source_recovery"]["eligible"]
    assert not call.telemetry["evidence_source_recovery"]["attempted"]
    assert not call.telemetry["evidence_source_recovery"]["recovered"]
    assert len(requests) == {"valid_first_output": 1, "completed_source_correction": 2,
                             "completed_correction_failed_review": 3}[path]
    assert counts["decision_review"] == (1 if path == "completed_correction_failed_review" else 0)


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("mode", ["recover_both", "primary_review_failed", "verifier_review_failed", "verifier_disagrees"])
def test_persisted_recovery_preserves_api_and_role_failure_semantics(
        client, event_payload, service_headers, monkeypatch, caplog, mode):
    raw = ("POST /synthetic/catalog HTTP/1.1\r\nHost: example.invalid\r\n"
           "Content-Type: application/x-www-form-urlencoded\r\n\r\ncommand=synthetic_execute")
    event = {**event_payload, "payload": raw, "signature": "Synthetic detection", "waf_action": "A"}
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(name="Synthetic239", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="production")
        db.add(profile)
        db.flush()
        db.add(VLLMTestRun(profile_id=profile.id, mode="full", status="passed",
            profile_fingerprint=profile_fingerprint(profile)))
        row = db.get(Analysis, identifier)
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.39"
        row.prompt_version, row.prompt_policy_version_id = prompt.prompt_version, prompt.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(prompt.model_dump_json())
        db.commit()

    role = 0
    original_inputs = []

    def model(request, _count):
        nonlocal role
        current = stage(request)
        if current == "analysis":
            role += 1
            document = user_document(request)
            original_inputs.append(document)
            catalog = document["evidence_candidates"]["items"]
            actual = next(item for item in catalog if item["field"] == "payload" and "command=" in item["excerpt"])
            reference = next(item for item in catalog if item["field"] == "signature")
            value = wire()
            good = deepcopy(value["evidence"][0])
            good.update(evidence_id="e1", source_id=actual["source_id"], related_evidence_ids=[])
            if role == 2 and mode == "verifier_disagrees":
                good.update(supports="normal_request", interpretation_ko="합성 입력의 값은 허용된 기능을 지정하는 업무 입력으로 해석됩니다.")
                value.update(verdict="false_positive")
                value["threat_analysis"]["severity"] = "NONE"
            invalid = deepcopy(good)
            invalid.update(evidence_id="e2", source_id=reference["source_id"],
                interpretation_ko="SYNTHETIC_INVALID_SIGNATURE_CLAIM")
            value.update(evidence=[good, invalid], summary_ko="SYNTHETIC_PRIOR_SUMMARY")
            return httpx.Response(200, json=completion(json.dumps(value)))
        if current == "source_repair":
            return httpx.Response(200, json=completion('{"corrections":[', finish_reason="length"))
        fail_review = ((role == 1 and mode == "primary_review_failed")
                       or (role == 2 and mode == "verifier_review_failed"))
        reviewed = review(verdict="false_positive" if role == 2 and mode == "verifier_disagrees" else "true_positive")
        return httpx.Response(200, json=completion("{" if fail_review else json.dumps(reviewed)))

    requests, _ = install_http(monkeypatch, model)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        if mode == "primary_review_failed":
            with pytest.raises(WorkerExecutionError) as failure:
                process_moduagent(db, crypto, row, "", .75)
            assert failure.value.code == "primary_agent_failed"
            mark_failed(db, row, failure.value)
            assert row.status == "failed" and row.result_json is None
            assert eligibility(db, crypto, identifier, "moduagent").allowed
            assert len(requests) == 3 and role == 1
        else:
            process_moduagent(db, crypto, row, "", .75)
            assert row.status == "completed"
            value = validate_new_result(row.result_json)
            assert value["schema_version"] == "waf-analysis-v3"
            assert value["verifier"]["executed"]
            assert value["verdict"] == ("true_positive" if mode == "recover_both" else "inconclusive")
            assert value["threat_analysis"]["severity"] == ("MEDIUM" if mode == "recover_both" else "UNKNOWN")
            assert "SYNTHETIC_INVALID_SIGNATURE_CLAIM" not in json.dumps(value)
            assert "SYNTHETIC_PRIOR_SUMMARY" not in json.dumps(value)
            assert len(requests) == 6 and role == 2
            assert original_inputs[0] == original_inputs[1]
            assert eligibility(db, crypto, identifier, "moduagent").blocked_reason == "retry_requires_failed_analysis"
        assert crypto.decrypt_text(row.payload_ciphertext) == raw
        run = db.query(AgentRun).filter_by(analysis_id=identifier).one()
        for step in run.steps:
            if step.step_type not in {"llm_primary", "llm_verifier"}:
                continue
            recovered = not (mode == "primary_review_failed" or
                (mode == "verifier_review_failed" and step.step_type == "llm_verifier"))
            assert step.status == ("completed" if recovered else "failed")
            metadata = step.metadata_json["evidence_source_recovery"]
            assert metadata["attempted"] and metadata["recovered"] is recovered
            saved = json.loads(crypto.decrypt_text(step.output_ciphertext))
            assert saved["grounding_history"][1]["output"] is None
            assert saved["grounding_history"][-1]["decision_review_validated"] is recovered
            assert raw not in json.dumps(step.metadata_json)
        db.commit()
    public = client.get(f"/api/v1/analyses/{identifier}?result_version=v3", headers=service_headers)
    assert public.status_code == 200, public.text
    assert public.json()["status"] == ("failed" if mode == "primary_review_failed" else "completed")
    assert "SYNTHETIC_INVALID_SIGNATURE_CLAIM" not in public.text
    assert "evidence_source_recovery" not in public.text
    assert raw not in caplog.text
