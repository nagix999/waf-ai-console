"""Synthetic-only end-to-end v2.21 processing; no network or model calls."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from app import worker_v221
from app.agent.contracts import EvidenceSelectionCorrectionOutput, SecurityAnalysisOutputV220
from app.agent.evidence_editor import EvidenceEditorOutput
from app.agent.executor import AgentCallResult
from app.agent.input_builder_v221 import build_agent_input
from app.models import AgentConfiguration, AgentRun, AgentStep, Analysis, VLLMProfile, VLLMTestRun, utcnow
from app.result_contracts import validate_new_result
from app.services.http_parser import parse_http_payload
from app.services.analysis_retries import load_execution_snapshot
from app.services.analyst_presentation import assessment_view
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot, load_analysis_prompt
from app.services.worker_runtime import process_moduagent
from app.services.vllm_profiles import profile_fingerprint
from test_evidence_relations import wire


pytestmark = pytest.mark.usefixtures("registered_vllm_target", "legacy_v221_admissions")
SIGNATURE = "Synthetic Signature"
OBSERVATION = "요청에 실제로 들어 있는 입력값과 위치를 확인했습니다."
INTEGRATED = "요청 위치와 관련 자료를 연결한 통합 설명입니다."


def raw_request(body=None, *, declared_length=None):
    body = body if body is not None else json.dumps({
        "value": "<img src=x onerror=alert(1)>", "signature": SIGNATURE,
        "separator_sample": "/@/", "message": "synthetic-only",
    }, ensure_ascii=False, separators=(",", ":"))
    length = len(body.encode()) if declared_length is None else declared_length
    return ("POST /api/content HTTP/1.1\r\nHost: synthetic.invalid\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {length}\r\n\r\n{body}")


def enqueue(client, event_payload, service_headers, *, raw=None, action="D"):
    event = {**event_payload, "payload": raw if raw is not None else raw_request(),
             "signature": SIGNATURE, "waf_action": action}
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-v221-worker", base_url="http://10.0.0.10:8000/v1",
                          model_name="synthetic-only-model", status="production"))
        row = db.get(Analysis, response.json()["id"])
        # A synthetic candidate is explicit: no real Production promotion or
        # Test-default/profile setting is modified by this fixture.
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.21"
        assert prompt.evidence_pipeline["source_catalog_version"] == "evidence-selection-v4"
        # The archived marker must pin the API admission itself, not merely
        # replace a v2.22 snapshot just before invoking the frozen worker.
        queued = load_analysis_prompt(row, crypto)
        assert queued.fixed_rules_version == prompt.fixed_rules_version
        assert queued.evidence_pipeline == prompt.evidence_pipeline
        row.prompt_version, row.prompt_policy_version_id = prompt.prompt_version, prompt.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(prompt.model_dump_json())
        db.commit()
    return response.json()["id"], event


def submitted_sources(document):
    catalog = document["evidence_candidates"]
    assert catalog["version"] == "evidence-selection-v4"
    assert "payload" not in document["event"]
    items = catalog["items"]
    signature = next(item for item in items if item["field"] == "signature")
    assert signature["allowed_supports"] == ["context"]
    assert signature["excerpt"] == SIGNATURE
    payload = [item for item in items if item["field"] == "payload"]
    first = next(item for item in payload if item["excerpt"].startswith("POST /api/content"))
    body = next(item for item in payload if '"value"' in item["excerpt"] or item["excerpt"].startswith("prefix="))
    return signature, body, first


def install_model(monkeypatch, *, mode="context", verdict="true_positive", correction="signature",
                  requires_reanalysis=False, editor_mode=None):
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        assert kwargs["structured_output_recovery"] is True
        document = json.loads(kwargs["user_input"])
        if kwargs["agent_name"] == "waf-evidence-editor":
            assert kwargs["output_model"] is EvidenceEditorOutput
            assert kwargs["output_validation_max_attempts"] == 1
            assert "concurrency_engine" in kwargs
            kwargs["egress_check"]()
            if editor_mode == "raise":
                raise TimeoutError("synthetic-private-provider-output")
            if editor_mode == "lost":
                raise worker_v221.WorkerExecutionError("analysis_lease_lost")
            if editor_mode == "failed":
                return AgentCallResult(None, "synthetic-v221-editor-failed", None, "error", None,
                                       "output_validation_failed", {"error_summary": {"code": "output_validation_failed"}})
            assert len(document["evidence"]) == 2
            assert len(document["allowed_groups"]) == 1
            ids = document["allowed_groups"][0]
            assert len(ids) == 2
            if editor_mode == "invalid":
                ids = ids[:1]
            value = EvidenceEditorOutput(groups=[{"member_ids": ids, "representative_id": ids[-1]}])
            return AgentCallResult(value, "synthetic-v221-editor", None, "completed", None, None,
                                   {"framework_version": "0.6.2"})
        signature, body, first = submitted_sources(document)
        if kwargs["agent_name"].endswith("-evidence-repair"):
            assert kwargs["output_model"] is EvidenceSelectionCorrectionOutput
            assert kwargs["output_validation_max_attempts"] == 1
            assert {item["index"] for item in document["evidence_correction"]["items"]} == {0}
            source = signature if correction == "signature" else body
            value = EvidenceSelectionCorrectionOutput(corrections=[{
                "index": 0, "source_id": source["source_id"],
            }], requires_reanalysis=requires_reanalysis)
        else:
            assert kwargs["output_model"] is SecurityAnalysisOutputV220
            assert not {"primary", "primary_output", "evidence_correction"}.intersection(document)
            data = wire()
            data["verdict"] = verdict
            data["confidence_score"] = .65  # Explicitly exercises both independent roles.
            data["threat_analysis"]["severity"] = "NONE" if verdict == "false_positive" else "HIGH"
            support = "normal_request" if verdict == "false_positive" else "attack_request"
            if mode == "context":
                data["evidence"] = [
                    {"source_id": body["source_id"], "supports": support, "interpretation_ko": OBSERVATION},
                    {"source_id": signature["source_id"], "supports": "context", "interpretation_ko": "장비에 기록된 탐지 정보입니다."},
                ]
                data["evidence_relations"] = [{"representative_index": 0, "related_indexes": [1]}]
            elif mode == "payload":
                interpretation = OBSERVATION
                if editor_mode:
                    interpretation += ("첫 번째 설명입니다." if kwargs["agent_name"] == "waf-primary"
                                       else "두 번째 설명입니다.")
                data["evidence"] = [{"source_id": body["source_id"], "supports": support,
                                     "interpretation_ko": interpretation}]
                data["evidence_relations"] = []
            else:
                data["evidence"] = [
                    {"source_id": signature["source_id"], "supports": support, "interpretation_ko": OBSERVATION},
                    {"source_id": body["source_id"], "supports": "context", "interpretation_ko": INTEGRATED},
                    {"source_id": first["source_id"], "supports": "context", "interpretation_ko": "독립적으로 확인할 수 있는 요청 위치입니다."},
                ]
                data["evidence_relations"] = [{"representative_index": 1, "related_indexes": [0]}]
            value = SecurityAnalysisOutputV220.model_validate(data)
        return AgentCallResult(value, "synthetic-v221-run", None, "completed", None, None,
                               {"framework_version": "0.6.2"})

    monkeypatch.setattr(worker_v221, "execute_structured_agent", execute)
    return calls


def process(client, identifier):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        prompt_before, raw_before = row.prompt_snapshot_ciphertext, row.payload_ciphertext
        process_moduagent(db, crypto, row, "", .75)
        assert row.status == "completed"
        assert row.prompt_snapshot_ciphertext == prompt_before and row.payload_ciphertext == raw_before
        public = deepcopy(row.result_json)
        assert public["schema_version"] == "waf-analysis-v3" and validate_new_result(public) == public
        assert "evidence_relations" not in public and "analyst_assessment" not in public
        assert "evidence_pipeline" not in public["policy"]
        latest = db.scalar(select(AgentRun).where(AgentRun.analysis_id == identifier))
        steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id == latest.id)))
        by_type = {step.step_type: json.loads(crypto.decrypt_text(step.output_ciphertext))
                   for step in steps if step.output_ciphertext}
        finalized = by_type["finalize"]
        assert finalized["evidence"] == public["evidence"]
        assert finalized["evidence_relations"]["version"] == "evidence-relations-v1"
        assert by_type["agent_input"]["evidence_pipeline"]["version"] == "evidence-pipeline-v2"
        return public, by_type


def assert_independent(calls, raw, *, truncated=False):
    initial = [call for call in calls if call["agent_name"] in {"waf-primary", "waf-verifier"}]
    assert [call["agent_name"] for call in initial] == ["waf-primary", "waf-verifier"]
    assert initial[0]["user_input"] == initial[1]["user_input"]
    assert initial[0]["instructions"] != initial[1]["instructions"]
    document = json.loads(initial[0]["user_input"])
    assert document["input_truncated"] is truncated
    blocks = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
    assert all(item["excerpt"] == raw[item["start"]:item["end"]] for item in blocks)
    if not truncated:
        assert "".join(item["excerpt"] for item in blocks) == raw
    else:
        assert sum(len(item["excerpt"]) for item in blocks) < len(raw)


def test_signature_context_allowed_and_payload_unmodified(client, event_payload, service_headers, monkeypatch):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch)
    public, steps = process(client, identifier)
    assert_independent(calls, event["payload"])
    assert len(calls) == 2 and public["verdict"] == "true_positive"
    assert all(item["supports"] == "context" for item in public["evidence"] if item["field"] == "signature")
    assert any(item["field"] == "signature" for item in public["evidence"])
    assert len(steps["finalize"]["evidence_relations"]["groups"]) == 2
    assert "/@/" in event["payload"] and any("/@/" in item["excerpt"] for item in public["evidence"])
    response = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers)
    assert response.status_code == 200 and "evidence_relations" not in response.json()["result"]


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
def test_signature_direction_rejected_again_after_citation_correction(
    client, event_payload, service_headers, monkeypatch, verdict,
):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch, mode="misuse", verdict=verdict)
    public, steps = process(client, identifier)
    assert_independent(calls, event["payload"])
    assert len(calls) == 4 and public["verdict"] == "inconclusive"
    assert public["threat_analysis"]["severity"] == "UNKNOWN"
    assert not any(item["field"] == "signature" for item in public["evidence"])
    assert INTEGRATED not in {item["interpretation_ko"] for item in public["evidence"]}
    assert steps["finalize"]["evidence_relations"]["groups"] == []
    for role in ("llm_primary", "llm_verifier"):
        history = steps[role]["grounding_history"]
        assert len(history) == 2
        assert all({"index": 0, "reason": "signature_support_not_allowed"} in record["rejected_evidence"]
                   for record in history)
        assert history[-1]["excluded_relation_indexes"] == [1]
        assert history[0]["output"]["evidence"][0]["supports"] == (
            "attack_request" if verdict == "true_positive" else "normal_request")
        assert history[0]["output"]["evidence"][1]["interpretation_ko"] == INTEGRATED


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
def test_same_text_inside_payload_remains_eligible_without_signature_provenance(
    client, event_payload, service_headers, monkeypatch, verdict,
):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch, mode="payload", verdict=verdict)
    public, _ = process(client, identifier)
    assert_independent(calls, event["payload"])
    assert len(calls) == 2 and public["verdict"] == verdict
    assert any(SIGNATURE in item["excerpt"] and item["field"] == "payload" for item in public["evidence"])
    assert all(item["supports"] == ("normal_request" if verdict == "false_positive" else "attack_request")
               for item in public["evidence"])


def test_signature_citation_can_only_recover_by_actual_request_source(client, event_payload, service_headers, monkeypatch):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch, mode="misuse", correction="payload")
    public, steps = process(client, identifier)
    assert_independent(calls, event["payload"])
    assert len(calls) == 4 and public["verdict"] == "true_positive"
    assert any(item["interpretation_ko"] == INTEGRATED for item in public["evidence"])
    assert all(item["field"] != "signature" for item in public["evidence"])
    for role in ("llm_primary", "llm_verifier"):
        history = steps[role]["grounding_history"]
        assert history[0]["rejected_evidence"] == [{"index": 0, "reason": "signature_support_not_allowed"}]
        assert history[-1]["rejected_evidence"] == []


def test_reanalysis_request_does_not_restore_integrated_claim(client, event_payload, service_headers, monkeypatch):
    identifier, _ = enqueue(client, event_payload, service_headers)
    install_model(monkeypatch, mode="misuse", correction="payload", requires_reanalysis=True)
    public, steps = process(client, identifier)
    assert public["verdict"] == "inconclusive"
    assert INTEGRATED not in {item["interpretation_ko"] for item in public["evidence"]}
    assert steps["finalize"]["evidence_relations"]["groups"] == []


@pytest.mark.parametrize("version", [None, "evidence-selection-v3", "evidence-selection-v1", "unknown"])
def test_v221_wrong_source_catalog_never_falls_back(event_payload, monkeypatch, version):
    raw = raw_request()
    built = build_agent_input(event_payload, raw, parse_http_payload(raw), 32768, 3072,
        evidence_selection=True, fixed_rules_version="waf-system-v2.21")
    document = json.loads(built.text)
    document["evidence_candidates"]["version"] = version

    async def forbidden(**kwargs):
        pytest.fail("Wrong catalog must fail before any model boundary")

    monkeypatch.setattr(worker_v221, "execute_structured_agent", forbidden)
    with pytest.raises(worker_v221.WorkerExecutionError, match="evidence_selection_catalog_invalid"):
        worker_v221._execute_grounded_agent(analysis=Analysis(extra_fields={}), raw_payload=raw,
            parsed=parse_http_payload(raw), input_truncated=False, assessment_enabled=True,
            semantic_enabled=True, security_analysis_enabled=True,
            fixed_rules_version="waf-system-v2.21", user_input=json.dumps(document))


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
def test_real_submitted_input_truncation_does_not_force_agreeing_models_to_hold(
    client, event_payload, service_headers, monkeypatch, verdict,
):
    raw = raw_request("prefix=" + "a" * 150000)
    identifier, event = enqueue(client, event_payload, service_headers, raw=raw)
    calls = install_model(monkeypatch, mode="payload", verdict=verdict)
    public, steps = process(client, identifier)
    assert_independent(calls, event["payload"], truncated=True)
    assert public["input_truncated"] is True and public["verdict"] == verdict
    assert "input_truncated" in public["verifier"]["reasons"]
    assert steps["agent_input"]["retained_payload_spans"]
    assert public["diagnostics"]["request_integrity"]["downgraded_to_inconclusive"] is False


def test_observed_missing_body_still_restricts_agreeing_normal_verdict(client, event_payload, service_headers, monkeypatch):
    raw = raw_request(declared_length=9999)
    identifier, _ = enqueue(client, event_payload, service_headers, raw=raw)
    install_model(monkeypatch, mode="payload", verdict="false_positive")
    public, _ = process(client, identifier)
    assert public["verdict"] == "inconclusive"
    integrity = public["diagnostics"]["request_integrity"]
    assert integrity["downgraded_to_inconclusive"] is True
    assert "body_shorter_than_content_length" in integrity["affected_issue_codes"]


def test_new_public_result_stays_closed(client, event_payload, service_headers, monkeypatch):
    identifier, _ = enqueue(client, event_payload, service_headers)
    install_model(monkeypatch)
    public, _ = process(client, identifier)
    changed = {**public, "structured_output": {"invented": True}}
    with pytest.raises(ValueError):
        validate_new_result(changed)
    assert all("allowed_supports" not in item for item in public["evidence"])


def enable_editor(client):
    """Seed a synthetic preapproved role, without API calls or live validation."""
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(name="synthetic-v221-optional-editor", base_url="http://10.0.0.10:8000/v1",
                              model_name="synthetic-only-editor", status="verified", last_verified_at=utcnow())
        db.add(profile)
        db.flush()
        db.add(VLLMTestRun(profile_id=profile.id, mode="full", status="passed",
            profile_fingerprint=profile_fingerprint(profile), completed_at=profile.last_verified_at))
        config = db.get(AgentConfiguration, 1)
        if config is None:
            config = AgentConfiguration(id=1, revision=0)
            db.add(config)
        config.production_evidence_editor_enabled = True
        config.production_evidence_editor_profile_id = profile.id
        config.revision += 1
        db.commit()
        return profile.id


@pytest.mark.parametrize("editor_mode", ["valid", "invalid", "raise", "failed"])
def test_optional_editor_pinned_bounded_and_failure_never_changes_analysis(
    client, event_payload, service_headers, monkeypatch, editor_mode,
):
    identifier, event = enqueue(client, event_payload, service_headers)
    editor_id = enable_editor(client)
    calls = install_model(monkeypatch, mode="payload", editor_mode=editor_mode)
    public, steps = process(client, identifier)
    assert_independent(calls, event["payload"])
    assert len(calls) == 3 and calls[-1]["profile"].id == editor_id
    assert public["verdict"] == public["primary"]["verdict"] == "true_positive"
    assert len(public["evidence"]) == 2  # Canonical sources/explanations stay intact.
    expected = "completed" if editor_mode == "valid" else "fallback"
    assert public["evidence_presentation"]["status"] == expected
    assert len(assessment_view(public)["evidence"]) == (1 if editor_mode == "valid" else 2)
    assert public["evidence"] == steps["finalize"]["evidence"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        snapshot = load_execution_snapshot(row, client.app.state.crypto)
        assert snapshot.evidence_editor.profile_id == editor_id
        assert snapshot.evidence_editor.version == "evidence-editor-v2"
        step = db.scalar(select(AgentStep).join(AgentRun).where(AgentRun.analysis_id == identifier,
                                                              AgentStep.step_type == "llm_evidence_editor"))
        assert step.status == ("completed" if editor_mode == "valid" else "failed")
        assert step.metadata_json["duration_ms"] >= 0
        metadata = json.dumps(step.metadata_json)
        assert "synthetic-private-provider-output" not in metadata
        assert SIGNATURE not in metadata and "<img" not in metadata
        assert "<img" not in step.input_ciphertext
        assert "<img" in client.app.state.crypto.decrypt_text(step.input_ciphertext)


def test_optional_editor_lost_lease_propagates_without_completed_result(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, _ = enqueue(client, event_payload, service_headers)
    enable_editor(client)
    calls = install_model(monkeypatch, mode="payload", editor_mode="lost")
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        with pytest.raises(worker_v221.WorkerExecutionError, match="analysis_lease_lost"):
            process_moduagent(db, client.app.state.crypto, row, "", .75)
        assert row.status != "completed"
        assert "evidence_presentation" not in row.result_json
    assert len(calls) == 3 and calls[-1]["agent_name"] == "waf-evidence-editor"
