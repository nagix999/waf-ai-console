"""Synthetic-only regression of version-pinned parser diagnostic routing."""
import importlib
import json
from dataclasses import replace

import pytest
from agent_selection_helpers import model_output
from sqlalchemy import select

from app import worker
from app.agent.executor import AgentCallResult
from app.agent.policy import VerifierPolicyContext, verifier_reasons
from app.models import AgentStep, Analysis, VLLMProfile
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from test_agent_policy import output
from test_moduagent_worker import primary_output


NEW_RULES = "waf-system-v2.17"


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_new_rules_keep_parser_incompleteness_diagnostic_only(status):
    context = VerifierPolicyContext("D", status, False, fixed_rules_version=NEW_RULES)
    assert verifier_reasons(output(), context) == []


@pytest.mark.parametrize("status", ["partial", "failed"])
@pytest.mark.parametrize("version", [None, "waf-system-v2.14", "waf-system-v2.15",
                                    "waf-system-v2.16", "waf-system-v2.18", "unknown"])
def test_missing_legacy_and_unknown_versions_retain_original_parser_trigger(status, version):
    context = VerifierPolicyContext("D", status, False, fixed_rules_version=version)
    assert verifier_reasons(output(), context) == ["parser_incomplete"]


@pytest.mark.parametrize("status", ["partial", "failed"])
@pytest.mark.parametrize("case,expected", [
    ("hold", "primary_inconclusive"),
    ("confidence", "low_confidence"),
    ("signature_partial", "signature_not_exact"),
    ("signature_mismatch", "signature_not_exact"),
    ("allowed_attack", "true_positive_but_allowed"),
    ("denied_normal", "false_positive_but_denied"),
    ("context_truncation", "input_truncated"),
    ("output_truncation", "input_truncated"),
    ("conflict", "interpretation_conflict"),
    ("legacy_conflict", "conflicting_evidence"),
    ("grounding", "evidence_grounding_failed"),
    ("legacy_tuning", "tuning_recommended"),
])
def test_only_parser_trigger_is_removed_and_other_reasons_are_unchanged(status, case, expected):
    primary = output(
        verdict="inconclusive" if case == "hold" else "false_positive" if case == "denied_normal" else "true_positive",
        confidence=.4 if case == "confidence" else .93,
        relation="partial" if case == "signature_partial" else "mismatch" if case == "signature_mismatch" else "exact",
        conflicts=["서로 다른 요청 해석을 확인했습니다."] if case == "legacy_conflict" else [],
        tuning=case == "legacy_tuning",
    )
    if case == "output_truncation":
        primary = primary.model_copy(update={"input_truncated": True})
    context = VerifierPolicyContext(
        "A" if case == "allowed_attack" else "D", status, case == "context_truncation",
        evidence_grounding_failed=case == "grounding",
        interpretation_conflict=True if case == "conflict" else None,
        fixed_rules_version=NEW_RULES,
    )
    assert verifier_reasons(primary, context) == [expected]
    legacy = verifier_reasons(primary, replace(context, fixed_rules_version="waf-system-v2.16"))
    assert "parser_incomplete" in legacy
    assert [reason for reason in legacy if reason != "parser_incomplete"] == [expected]


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("status,raw", [
    ("partial", "GET /synthetic\r\nHost: synthetic.test\r\n\r\n"),
    ("failed", "synthetic non-HTTP payload"),
])
@pytest.mark.parametrize("rules_module,expected_calls,policy_version,retired", [
    ("prompts", 1, "security-analysis-policy-v2", ["tuning_recommended", "parser_incomplete"]),
    ("legacy_prompts_v216", 2, "security-analysis-policy-v1", ["tuning_recommended"]),
])
def test_pinned_worker_keeps_raw_parser_history_but_changes_only_new_verifier_routing(
    client, event_payload, service_headers, monkeypatch, status, raw,
    rules_module, expected_calls, policy_version, retired,
):
    event_payload["payload"] = raw
    response = client.post("/api/v1/analyses", headers=service_headers, json=event_payload)
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        return AgentCallResult(
            output=model_output(primary_output(excerpt="synthetic", field="payload"), kwargs),
            framework_run_id=f"synthetic-parser-{len(calls)}",
            agent_fingerprint="synthetic-parser-fingerprint", finish_reason="completed",
            failure_id=None, error=None, telemetry={"framework_version": "0.6.2"},
        )

    monkeypatch.setattr(worker, "execute_structured_agent", fake_execute)
    crypto = client.app.state.crypto
    rules = importlib.import_module(f"app.agent.{rules_module}")
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-parser-version", base_url="http://10.0.0.10:8000/v1",
                           model_name="synthetic-model", status="production"))
        row = db.get(Analysis, identifier)
        pinned = _build_snapshot(get_active_policy(db, crypto), crypto, rules=rules)
        row.prompt_policy_version_id = pinned.policy_version_id
        row.prompt_version = pinned.prompt_version
        row.prompt_snapshot_ciphertext = crypto.encrypt_text(pinned.model_dump_json())
        db.commit()
        worker.process_moduagent(db, crypto, row, "", .75)

        assert row.status == "completed" and row.verdict == "true_positive"
        assert row.result_json["policy"]["fixed_rules_version"] == rules.FIXED_RULES_VERSION
        assert row.result_json["policy"]["verifier_policy_version"] == policy_version
        assert row.result_json["policy"]["retired_verifier_triggers"] == retired
        assert row.result_json["diagnostics"]["input_signals"] == ["parser_incomplete"]
        assert row.result_json["verifier"]["executed"] is (expected_calls == 2)
        reasons = ["parser_incomplete"] if expected_calls == 2 else []
        assert row.result_json["verifier"]["reasons"] == reasons
        parser = db.scalar(select(AgentStep).where(AgentStep.step_type == "parser"))
        parser_output = json.loads(crypto.decrypt_text(parser.output_ciphertext))
        assert parser.metadata_json["parse_status"] == parser_output["parse_status"] == status
        assert parser_output["warnings"]
        assert parser.metadata_json["parser_warning_codes"] == parser_output["warnings"]
        assert json.loads(crypto.decrypt_text(parser.input_ciphertext)) == {"payload": raw}
        assert crypto.decrypt_text(row.payload_ciphertext) == raw
        policy = db.scalar(select(AgentStep).where(AgentStep.step_type == "policy"))
        assert policy.metadata_json["execute_verifier"] is (expected_calls == 2)
        assert policy.metadata_json["verifier_reason_codes"] == reasons

    assert [call["agent_name"] for call in calls] == ["waf-primary"] + (["waf-verifier"] if expected_calls == 2 else [])
    for call in calls:
        document = json.loads(call["user_input"])
        assert document["event"]["payload"] == raw
        assert document["generic_http_parser_hints"]["parse_status"] == status
        assert "primary" not in document
    if expected_calls == 2:
        assert calls[0]["user_input"] == calls[1]["user_input"]
