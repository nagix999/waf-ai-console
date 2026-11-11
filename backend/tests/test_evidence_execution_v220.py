"""Synthetic-only v2.20 worker execution; every model boundary is mocked."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest
from sqlalchemy import select

from app import worker
from app.agent.contracts import EvidenceSelectionCorrectionOutput, SecurityAnalysisOutputV220
from app.agent.executor import AgentCallResult
from app.agent.input_builder import build_agent_input
from app.models import AgentRun, AgentStep, Analysis, VLLMProfile
from app.result_contracts import validate_new_result
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from test_evidence_relations import wire

pytestmark = pytest.mark.usefixtures("registered_vllm_target")

# Archived v2.20 worker replay; current runtime is covered by test_worker_v221.py.
pytestmark = [*(pytestmark if isinstance(pytestmark, list) else [pytestmark]),
              pytest.mark.usefixtures("legacy_v220_admissions")]

JS = "<img src=x onerror=alert(1)>"


def synthetic_event(event_payload):
    body = json.dumps({"before": "a" * 480, "content": JS, "after": "z" * 950}, separators=(",", ":"))
    raw = ("POST /api/content HTTP/1.1\r\nHost: synthetic.invalid\r\nContent-Type: application/json\r\n"
           f"Content-Length: {len(body.encode())}\r\n\r\n{body}")
    return {**event_payload, "payload": raw}


def enqueue(client, event_payload, service_headers):
    event = synthetic_event(event_payload)
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-v220-execution", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", status="production"))
        row = db.get(Analysis, response.json()["id"])
        prompt = _build_snapshot(get_active_policy(db, crypto), crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.20"
        row.prompt_version, row.prompt_policy_version_id = prompt.prompt_version, prompt.policy_version_id
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(prompt.model_dump_json())
        db.commit()
    return response.json()["id"], event


def sources(document):
    assert document["evidence_candidates"]["version"] == "evidence-selection-v3"
    assert "payload" not in document["event"]
    assert "raw_payload" not in document
    raw = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
    assert len(raw) >= 5 and all(len(item["excerpt"]) <= 300 for item in raw)
    script = next(item for item in raw if JS in item["excerpt"])
    assert script is not raw[0] and script is not raw[-1]
    return [script, raw[0], *[item for item in raw if item not in (script, raw[0])]]


def output(document, *, role="primary", verdict="true_positive", invalid_related=False, count=3):
    selected = sources(document)
    value = wire()
    value["verdict"] = verdict
    value["confidence_score"] = .65 if role == "primary" else .93
    value["threat_analysis"]["severity"] = "NONE" if verdict == "false_positive" else "HIGH"
    direction = "normal_request" if verdict == "false_positive" else "attack_request"
    value["evidence"] = [{"source_id": item["source_id"], "supports": direction if index == 0 else "context",
        "interpretation_ko": f"{role} integrated explanation" if index == 0 else f"{role} independent observation {index}"}
        for index, item in enumerate(selected[:count])]
    value["evidence_relations"] = [{"representative_index": 0, "related_indexes": [1]}]
    if invalid_related:
        value["evidence"][1]["source_id"] = "c999"
    return SecurityAnalysisOutputV220.model_validate(value)


def install_model(monkeypatch, *, disagreement=False, repair=None, count=3, verifier_failed=False,
                  shadow_representative=False):
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        document = json.loads(kwargs["user_input"])
        selected = sources(document)
        name = kwargs["agent_name"]
        if name.endswith("-evidence-repair"):
            assert kwargs["output_model"] is EvidenceSelectionCorrectionOutput
            assert kwargs["output_validation_max_attempts"] == 1
            assert {item["index"] for item in document["evidence_correction"]["items"]} == {1}
            value = EvidenceSelectionCorrectionOutput(
                corrections=[{"index": 1, "source_id": selected[1]["source_id"]}] if repair in {"success", "reanalysis"} else [],
                requires_reanalysis=repair == "reanalysis")
        else:
            assert kwargs["output_model"] is SecurityAnalysisOutputV220
            assert not {"primary", "primary_output", "evidence_correction"}.intersection(document)
            role = "verifier" if name == "waf-verifier" else "primary"
            if role == "verifier" and verifier_failed:
                return AgentCallResult(None, "synthetic-v220-failed", None, "error", "synthetic-failure",
                    "synthetic invocation failed", {"error_summary": {"code": "output_validation_failed"}})
            value = output(document, role=role,
                verdict="false_positive" if disagreement and role == "verifier" else "true_positive",
                invalid_related=repair is not None and role == "primary", count=count)
            if shadow_representative and role == "primary":
                document = value.model_dump(mode="json")
                # Keep an independently accepted index with the representative's
                # exact source and text. Exclusion must bind to the role/index,
                # not infer acceptance from a surviving identical text tuple.
                document["evidence"][2] = {**document["evidence"][0], "supports": "context"}
                value = SecurityAnalysisOutputV220.model_validate(document)
        return AgentCallResult(value, "synthetic-v220", None, "completed", None, None,
            {"framework_version": "0.6.2"})

    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    return calls


def process(client, identifier):
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        before_prompt, before_payload = row.prompt_snapshot_ciphertext, row.payload_ciphertext
        worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        assert row.status == "completed"
        assert row.prompt_snapshot_ciphertext == before_prompt and row.payload_ciphertext == before_payload
        public = deepcopy(row.result_json)
        assert public["schema_version"] == "waf-analysis-v3" and validate_new_result(public) == public
        assert "evidence_relations" not in public and "evidence_pipeline" not in public["policy"]
        latest = db.scalar(select(AgentRun).where(AgentRun.analysis_id == identifier))
        assert latest.status == "completed"
        final = db.scalar(select(AgentStep).where(AgentStep.run_id == latest.id, AgentStep.step_type == "finalize"))
        encrypted = json.loads(client.app.state.crypto.decrypt_text(final.output_ciphertext))
        assert encrypted["evidence"] == public["evidence"]
        assert encrypted["evidence_relations"]["version"] == "evidence-relations-v1"
        assert "evidence_relations" not in final.output_ciphertext
        steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id == latest.id)))
        histories = {step.step_type: json.loads(client.app.state.crypto.decrypt_text(step.output_ciphertext))
            for step in steps if step.step_type in {"llm_primary", "llm_verifier"}}
        return public, encrypted["evidence_relations"], histories


def assert_independent_inputs(calls, raw):
    initial = [call for call in calls if not call["agent_name"].endswith("-evidence-repair")]
    assert [call["agent_name"] for call in initial] == ["waf-primary", "waf-verifier"]
    assert initial[0]["user_input"] == initial[1]["user_input"]
    assert initial[0]["instructions"] != initial[1]["instructions"]
    document = json.loads(initial[0]["user_input"])
    blocks = [item for item in document["evidence_candidates"]["items"] if item["field"] == "payload"]
    assert "".join(item["excerpt"] for item in blocks) == raw
    assert all(item["excerpt"] == raw[item["start"]:item["end"]] for item in blocks)
    assert sum(len(item["excerpt"]) for item in blocks) == len(raw)


def test_worker_links_multi_block_evidence_without_extending_public_result(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch)
    result, relations, _ = process(client, identifier)
    assert result["verdict"] == "true_positive"
    assert_independent_inputs(calls, event["payload"])
    assert len(calls) == 2 and len(relations["groups"]) == 2
    indexed = {item["evidence_id"]: item for item in result["evidence"]}
    for group in relations["groups"]:
        representative = indexed[group["representative_evidence_id"]]
        related = indexed[group["related_evidence_ids"][0]]
        assert JS in representative["excerpt"] and "POST /api/content" in related["excerpt"]
        assert representative["supports"] == "attack_request" and related["supports"] == "context"
        assert representative["interpretation_ko"] == f"{group['role']} integrated explanation"
    assert client.get(f"/api/v1/analyses/{identifier}/evidence-relations", headers=service_headers).status_code == 403
    public = client.get(f"/api/v1/analyses/{identifier}", headers=service_headers)
    assert public.status_code == 200 and "evidence_relations" not in public.json()["result"]
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200
    response = client.get(f"/api/v1/analyses/{identifier}/evidence-relations")
    assert response.status_code == 200 and response.json() == relations
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("repair", ["success", "failure", "reanalysis"])
def test_related_citation_repair_preserves_claim_only_when_every_required_source_survives(
    client, event_payload, service_headers, monkeypatch, repair,
):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch, repair=repair)
    result, relations, histories = process(client, identifier)
    assert_independent_inputs(calls, event["payload"])
    assert len(calls) == 3
    primary = result["diagnostics"]["roles"]["primary"]
    assert primary["repair_attempted"] is True
    original = histories["llm_primary"]["grounding_history"][0]["output"]
    assert original["evidence"][1]["source_id"] == "c999"
    assert original["evidence"][0]["interpretation_ko"] == "primary integrated explanation"
    explanations = {item["interpretation_ko"] for item in result["evidence"]}
    assert "primary independent observation 2" in explanations
    if repair == "success":
        assert result["verdict"] == "true_positive" and primary["repair_recovered"] is True
        assert "primary integrated explanation" in explanations
        assert {item["role"] for item in relations["groups"]} == {"primary", "verifier"}
    else:
        assert result["verdict"] == "inconclusive" and primary["correction_requires_review"] is True
        assert "primary integrated explanation" not in explanations
        assert "verifier integrated explanation" in explanations
        assert all(group["role"] != "primary" for group in relations["groups"])
        if repair == "reanalysis":
            # Exact source IDs alone cannot authenticate an explanation when
            # the correction model explicitly asks to reconsider its meaning.
            final = histories["llm_primary"]["grounding_history"][-1]
            assert final["rejected_evidence"] == []
            assert final["output"]["requires_reanalysis"] is True


def test_disagreement_keeps_role_specific_interpretations_of_same_sources_and_holds(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch, disagreement=True)
    result, relations, _ = process(client, identifier)
    assert_independent_inputs(calls, event["payload"])
    assert result["verdict"] == "inconclusive" and result["verifier"]["agreement"] is False
    assert len(relations["groups"]) == 2
    groups = {group["role"]: group for group in relations["groups"]}
    indexed = {item["evidence_id"]: item for item in result["evidence"]}
    primary = indexed[groups["primary"]["representative_evidence_id"]]
    verifier = indexed[groups["verifier"]["representative_evidence_id"]]
    assert primary["excerpt"] == verifier["excerpt"] and primary["source_spans"] == verifier["source_spans"]
    assert primary["interpretation_ko"] != verifier["interpretation_ko"]
    assert primary["supports"] == "attack_request" and verifier["supports"] == "normal_request"


def test_five_evidence_per_role_keeps_all_ten_without_expanding_policy_subset(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch, count=5)
    result, relations, _ = process(client, identifier)
    assert_independent_inputs(calls, event["payload"])
    assert len(result["evidence"]) == 10
    assert len(result["primary"]["evidence_ids"]) == len(result["verifier"]["output"]["evidence_ids"]) == 5
    assert len(result["policy"]["decision_evidence_ids"]) <= 5 and len(relations["groups"]) == 2


def test_required_verifier_failure_holds_without_inventing_verifier_relations(
    client, event_payload, service_headers, monkeypatch,
):
    identifier, event = enqueue(client, event_payload, service_headers)
    calls = install_model(monkeypatch, verifier_failed=True)
    result, relations, _ = process(client, identifier)
    assert_independent_inputs(calls, event["payload"])
    assert result["verdict"] == "inconclusive" and result["verifier"]["executed"] is True
    assert result["verifier"]["output"] is None
    assert [group["role"] for group in relations["groups"]] == ["primary"]


@pytest.mark.parametrize("catalog", [None, "evidence-selection-v1", "evidence-selection-v2"])
def test_v220_missing_or_historical_catalog_fails_before_any_model_call(
    client, event_payload, service_headers, monkeypatch, catalog,
):
    identifier, _ = enqueue(client, event_payload, service_headers)
    calls = []

    async def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("A mismatched source catalog must fail before invoking a model")

    def invalid_catalog(*args, **kwargs):
        built = build_agent_input(*args, **kwargs)
        document = json.loads(built.text)
        if catalog is None:
            document.pop("evidence_candidates")
        else:
            document["evidence_candidates"]["version"] = catalog
        return replace(built, text=json.dumps(document))

    monkeypatch.setattr(worker, "execute_structured_agent", forbidden)
    monkeypatch.setattr(worker, "build_agent_input", invalid_catalog)
    with client.app.state.session_factory() as db:
        with pytest.raises(worker.WorkerExecutionError, match="^evidence_selection_catalog_invalid$"):
            worker.process_moduagent(db, client.app.state.crypto, db.get(Analysis, identifier), "", .75)
    assert calls == []


@pytest.mark.parametrize("repair", ["failure", "reanalysis"])
def test_rejected_representative_origin_cannot_return_through_identical_surviving_evidence(
    client, event_payload, service_headers, monkeypatch, repair,
):
    identifier, _ = enqueue(client, event_payload, service_headers)
    install_model(monkeypatch, repair=repair, shadow_representative=True)
    result, relations, histories = process(client, identifier)
    assert result["verdict"] == "inconclusive"
    initial = histories["llm_primary"]["grounding_history"][0]["output"]
    assert initial["evidence"][0]["source_id"] == initial["evidence"][2]["source_id"]
    assert initial["evidence"][0]["interpretation_ko"] == initial["evidence"][2]["interpretation_ko"]
    assert all(group["role"] != "primary" for group in relations["groups"])
    indexes = {origin["index"] for item in result["evidence"] for origin in item["origins"]
               if origin["role"] == "primary"}
    assert 2 in indexes
    assert 0 not in indexes
