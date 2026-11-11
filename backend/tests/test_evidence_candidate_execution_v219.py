"""Offline worker integration for pinned source eligibility and corrections."""
import json

import pytest

from app import worker
from app.agent import legacy_prompts_v218, legacy_prompts_v219 as prompts
from app.agent.contracts import EvidenceSelectionCorrectionOutput, SecurityAnalysisOutputV216
from app.agent.executor import AgentCallResult
from app.agent.input_builder import build_agent_input
from app.models import Analysis, VLLMProfile
from app.services.http_parser import parse_http_payload
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from test_security_analysis_v3 import output


IDENTIFIERS = {"source_system", "event_id", "company_name", "waf_vendor"}


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("rules,catalog_version", [
    (legacy_prompts_v218, "evidence-selection-v1"),
    (prompts, "evidence-selection-v2"),
])
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("disagreement", [False, True])
def test_worker_keeps_source_policy_and_direction_through_both_independent_roles(
    client, event_payload, service_headers, monkeypatch, rules, catalog_version, repair, disagreement,
):
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event_payload)
    assert response.status_code == 202
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        document = json.loads(kwargs["user_input"])
        catalog = document["evidence_candidates"]
        assert catalog["version"] == catalog_version
        fields = {item["field"] for item in catalog["items"]}
        if rules is prompts:
            assert not IDENTIFIERS & fields
        else:
            assert IDENTIFIERS <= fields
        assert IDENTIFIERS <= document["event"].keys()
        assert document["event"]["payload"] == event_payload["payload"]
        source = next(item for item in catalog["items"] if item["field"] == "payload")
        if kwargs["agent_name"].endswith("-evidence-repair"):
            assert kwargs["output_model"] is EvidenceSelectionCorrectionOutput
            assert kwargs["output_validation_max_attempts"] == 1
            value = EvidenceSelectionCorrectionOutput(
                corrections=[{"index": 0, "source_id": source["source_id"]}], requires_reanalysis=False,
            )
        else:
            assert kwargs["output_model"] is SecurityAnalysisOutputV216
            is_verifier = kwargs["agent_name"] == "waf-verifier"
            verdict = "false_positive" if is_verifier and disagreement else "true_positive"
            direction = "normal_request" if verdict == "false_positive" else "attack_request"
            value = output(direction, "context", verdict=verdict, source=source["source_id"]).model_dump(mode="json")
            value["confidence_score"] = .65 if not is_verifier else .93
            if repair:
                value["evidence"][0]["source_id"] = "c999"
            value = SecurityAnalysisOutputV216.model_validate(value)
        return AgentCallResult(value, "synthetic-source-policy", None, "completed", None, None, {})

    monkeypatch.setattr(worker, "execute_structured_agent", fake_execute)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-source-policy", base_url="http://10.0.0.10:8000/v1",
                           model_name="synthetic-model", status="production"))
        row = db.get(Analysis, response.json()["id"])
        pinned = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
        row.prompt_policy_version_id = pinned.policy_version_id
        row.prompt_version = pinned.prompt_version
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(pinned.model_dump_json())
        db.commit()
        original_prompt = row.prompt_snapshot_ciphertext
        original_payload = row.payload_ciphertext
        worker.process_moduagent(db, crypto, row, "", .75)

        assert row.status == "completed"
        assert row.verdict == ("inconclusive" if disagreement else "true_positive")
        assert row.prompt_snapshot_ciphertext == original_prompt
        assert row.payload_ciphertext == original_payload
        assert row.result_json["policy"]["fixed_rules_version"] == rules.FIXED_RULES_VERSION
        assert row.result_json["diagnostics"]["version"] == catalog_version
        directions = {item["supports"] for item in row.result_json["evidence"]}
        assert directions == ({"attack_request", "normal_request", "context"} if disagreement
                              else {"attack_request", "context"})
        roles = row.result_json["diagnostics"]["roles"]
        assert roles["primary"]["repair_attempted"] is repair
        assert roles["verifier"]["repair_attempted"] is repair
        assert row.result_json["verifier"]["executed"] is True

    initial = [call for call in calls if not call["agent_name"].endswith("-evidence-repair")]
    assert len(calls) == (4 if repair else 2)
    assert [call["agent_name"] for call in initial] == ["waf-primary", "waf-verifier"]
    assert initial[0]["user_input"] == initial[1]["user_input"]
    document = json.loads(initial[1]["user_input"])
    assert not {"primary", "primary_output", "evidence_correction"} & document.keys()


@pytest.mark.parametrize("catalog", [None, {}, {"version": "evidence-selection-v1", "items": []},
                                     {"version": "unknown", "items": []}])
def test_v219_catalog_mismatch_cannot_fall_back_to_identifier_citations(monkeypatch, catalog):
    raw = "GET /synthetic HTTP/1.1\r\n\r\n"
    parsed = parse_http_payload(raw)
    built = build_agent_input({}, raw, parsed, 32768, 3072,
                              evidence_selection=True, fixed_rules_version=prompts.FIXED_RULES_VERSION)
    document = json.loads(built.text)
    if catalog is None:
        document.pop("evidence_candidates")
    else:
        document["evidence_candidates"] = catalog

    async def forbidden(**kwargs):
        pytest.fail("A mismatched v2.19 source catalog must fail before any model call")

    monkeypatch.setattr(worker, "execute_structured_agent", forbidden)
    with pytest.raises(worker.WorkerExecutionError, match="evidence_selection_catalog_invalid"):
        worker._execute_grounded_agent(analysis=Analysis(extra_fields={}), raw_payload=raw, parsed=parsed,
            input_truncated=False, security_analysis_enabled=True, fixed_rules_version=prompts.FIXED_RULES_VERSION,
            user_input=json.dumps(document))
