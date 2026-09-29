"""PR A: synthetic records and offline workers; never call a live provider."""
import json

import pytest
from sqlalchemy import select

from app import worker
from app.agent import legacy_prompts_v213, prompts, result_editor
from app.agent.contracts import AnalystCheck, AgentVerdict, ThreatSeverity, TuningRecommendation, WAFAnalysisOutput
from app.agent.executor import AgentCallResult
from app.agent.followup_policy import apply_final_followup_policy
from app.models import AgentStep, Analysis, VLLMProfile, utcnow
from app.services.analysis_retries import make_execution_snapshot
from app.services.evidence_editor import capture_editor
from app.services.prompt_policies import get_active_policy
from app.services.prompt_snapshots import _build_snapshot
from test_semantic_contract import check, resolved
from test_moduagent_worker import primary_output
from test_model_profiles import login_admin
from test_model_test_role import create_verified
from test_evidence_editor import configure
from agent_selection_helpers import model_output


def tuning(recommended):
    return TuningRecommendation(recommended=recommended, scope="uri",
        proposal_ko="예외 범위를 검토합니다." if recommended else None,
        risk_ko="공격 누락 가능성", validation_ko="공격·정상 회귀 사례로 검증합니다.")


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
@pytest.mark.parametrize("action", ["D", "A"])
@pytest.mark.parametrize("recommended", [False, True])
def test_final_policy_matrix_preserves_every_field_except_checks(verdict, action, recommended):
    purposes = (["decision_condition"] if verdict == "inconclusive" else []) + ["impact_followup", "tuning_validation"]
    original = WAFAnalysisOutput.model_validate({**resolved(verdict),
        "analyst_checks": [check(p) for p in purposes], "tuning_recommendation": tuning(recommended)})
    before = original.model_dump(mode="json")
    final, diagnostics = apply_final_followup_policy(original, action, prompts.FIXED_RULES_VERSION)
    expected = [p for p in purposes if not (p == "impact_followup" and verdict == "true_positive" and action == "D")
        and not (p == "tuning_validation" and not recommended)]
    assert [row.purpose.value for row in final.analyst_checks] == expected
    assert diagnostics["suppressed_count"] == len(purposes) - len(expected)
    assert final.model_dump(exclude={"analyst_checks"}) == original.model_dump(exclude={"analyst_checks"})
    assert original.model_dump(mode="json") == before
    assert set(diagnostics) == {"version", "suppressed_count", "reason_codes"}
    assert check("impact_followup")["check_ko"] not in json.dumps(diagnostics, ensure_ascii=False)
    assert WAFAnalysisOutput.model_validate(final.model_dump()).analyst_checks == final.analyst_checks


@pytest.mark.parametrize("text", ["응답 코드 HTTP 200 response status origin", "후속 피해 확인", "unrelated neutral text"])
def test_policy_never_uses_language_or_keywords(text):
    data = resolved("true_positive")
    data["analyst_checks"] = [{**check("impact_followup"), "check_ko": text}]
    original = WAFAnalysisOutput.model_validate(data)
    assert apply_final_followup_policy(original, "D", prompts.FIXED_RULES_VERSION)[0].analyst_checks == []
    assert apply_final_followup_policy(original, "A", prompts.FIXED_RULES_VERSION)[0] is original


@pytest.mark.parametrize("version", ["waf-system-v2.12", "waf-system-v2.13", "waf-system-v2.15", "unknown"])
def test_only_explicitly_supported_revision_changes_output(version):
    original = WAFAnalysisOutput.model_validate({**resolved("true_positive"), "analyst_checks": [check("impact_followup"), check("tuning_validation")]})
    final, diagnostics = apply_final_followup_policy(original, "D", version)
    assert final is original and diagnostics is None


def test_all_semantic_features_remain_enabled_and_v213_is_frozen():
    from app.agent.analyst_assessment import RULES_VERSIONS
    from app.agent.evidence_candidates import SELECTION_RULES_VERSIONS
    from app.services.request_integrity import INTEGRITY_V2_RULES_VERSIONS
    for version in ["waf-system-v2.13", "waf-system-v2.14"]:
        assert all(version in versions for versions in [RULES_VERSIONS, SELECTION_RULES_VERSIONS,
            INTEGRITY_V2_RULES_VERSIONS, prompts.SEMANTIC_RULES_VERSIONS])
    assert "우회·반복·영향 확인은 선택사항이다." in legacy_prompts_v213.FIXED_INSTRUCTIONS
    assert "우회·반복·영향 확인은 선택사항이다." not in prompts.FIXED_INSTRUCTIONS


@pytest.mark.usefixtures("registered_vllm_target")
@pytest.mark.parametrize("mode", ["no_tuning", "tuning", "guard_hold", "pinned_v213", "retry_v213"])
def test_worker_orders_policy_after_guard_before_editor_and_preserves_role_history(client, event_payload, monkeypatch, mode):
    login_admin(client)
    client.app.state.settings.agent_mode = "moduagent"
    primary, editor = create_verified(client, "policy-primary"), create_verified(client, "policy-editor")
    assert configure(client, primary, editor).status_code == 200
    created = client.post("/api/v1/analyses", json=event_payload).json()
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        if kwargs["output_model"] is result_editor.ResultEditorOutput:
            document = json.loads(kwargs["user_input"])
            value = result_editor.ResultEditorOutput(groups=[{"member_ids": [row["evidence_id"]], "representative_id": row["evidence_id"]} for row in document["evidence"]],
                check_groups=[{"member_ids": [row["check_id"]], "representative_id": row["check_id"]} for row in document["checks"]])
        else:
            value = primary_output("q=test")
            value.confidence_score = .6  # Exercise both independent roles.
            value.analyst_checks = [AnalystCheck(**{**check("impact_followup"), "check_ko": "SUPPRESSED_SYNTHETIC_CANARY"}),
                AnalystCheck(**{**check("tuning_validation"), "check_ko": "회귀 검증 " + kwargs["agent_name"]})]
            value.tuning_recommendation = tuning(mode == "tuning")
            value = model_output(value, kwargs)
        return AgentCallResult(value, "synthetic-run", None, "completed", None, None, {})
    monkeypatch.setattr(worker, "execute_structured_agent", execute)
    if mode == "guard_hold":
        original_guard = worker.apply_integrity_guard
        def guard(output, *args):
            output, metadata = original_guard(output, *args)
            # The present guard only downgrades FP. Simulate a later guard that
            # downgrades TP to verify ordering, without broadening real policy.
            metadata["downgraded_to_inconclusive"] = True
            return output.model_copy(update={"verdict": AgentVerdict.inconclusive,
                "threat_analysis": output.threat_analysis.model_copy(update={"severity": ThreatSeverity.UNKNOWN}),
                "analyst_checks": [AnalystCheck(**check("decision_condition")), *output.analyst_checks]}), metadata
        monkeypatch.setattr(worker, "apply_integrity_guard", guard)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["id"])
        if mode in {"pinned_v213", "retry_v213"}:
            snapshot = _build_snapshot(get_active_policy(db, client.app.state.crypto), client.app.state.crypto, rules=legacy_prompts_v213)
            analysis.prompt_version = snapshot.prompt_version
            analysis.prompt_snapshot_ciphertext = client.app.state.crypto.encrypt_text(snapshot.model_dump_json())
            db.commit()
            if mode == "retry_v213":
                profile = db.get(VLLMProfile, primary["id"])
                execution = make_execution_snapshot(analysis, profile, snapshot, .75, profile,
                    capture_editor(db, "production", profile))
                analysis.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(execution.model_dump_json())
                analysis.status, analysis.error_code, analysis.completed_at = "failed", "synthetic_failure", utcnow()
                db.commit()
                retry = client.post(f"/api/v1/analyses/{analysis.id}/retry", json={"idempotency_key": "v213-policy-retry", "cost_acknowledged": True})
                assert retry.status_code == 202, retry.text
                analysis = db.get(Analysis, retry.json()["analysis_id"])
        worker.process_moduagent(db, client.app.state.crypto, analysis, "", .75)
        result = analysis.result_json
        assert analysis.status == "completed"
        assert calls[0]["user_input"] == calls[1]["user_input"]
        assert "request_integrity" in calls[0]["user_input"] and "evidence_candidates" in calls[0]["user_input"]
        assert result["analyst_assessment"]["evidence"]
        assert result["diagnostics"]["request_integrity"]["version"] == "request-integrity-v2"
        for original in [result["primary"], result["verifier"]["output"]]:
            assert original["analyst_checks"][0]["check_ko"] == "SUPPRESSED_SYNTHETIC_CANARY"
        steps = db.scalars(select(AgentStep).where(AgentStep.step_type.in_(["llm_primary", "llm_verifier"]))).all()
        assert len(steps) == 2
        assert all("SUPPRESSED_SYNTHETIC_CANARY" in client.app.state.crypto.decrypt_text(step.output_ciphertext) for step in steps)
        checks = result["analyst_guidance"]["checks"]
        assert checks == result["analyst_checks"]
        if mode in {"no_tuning", "tuning"}:
            assert "SUPPRESSED_SYNTHETIC_CANARY" not in str(checks)
            assert [row["purpose"] for row in checks] == (["tuning_validation"] * 2 if mode == "tuning" else [])
            assert result["diagnostics"]["followup_policy"]["suppressed_count"] == (1 if mode == "tuning" else 3)
        elif mode == "guard_hold":
            assert result["verdict"] == "inconclusive"
            assert [row["purpose"] for row in checks] == ["decision_condition", "impact_followup"]
        else:
            assert "followup_policy" not in result["diagnostics"]
            assert len(checks) == 3
        if mode in {"no_tuning", "guard_hold"}:
            assert len(calls) == 2
        else:
            assert len(calls) == 3
            assert json.loads(calls[-1]["user_input"])["checks"] == result_editor.check_items(checks)
        assert "SUPPRESSED_SYNTHETIC_CANARY" not in str(result["diagnostics"])
