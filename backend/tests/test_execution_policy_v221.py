"""Synthetic policy regressions; no model calls or production configuration."""
from copy import deepcopy
from dataclasses import replace

import pytest

from app.agent import execution_policy_v221 as candidate
from app.agent import policy as frozen_policy
from app.agent import input_integrity as frozen_guard
from app.agent.contracts import AgentVerdict, SignatureRelation, WAFAnalysisOutput
from app.agent.security_analysis import guidance, verifier_policy_metadata
from app.services import request_integrity as frozen_integrity
from app.services.http_parser import parse_http_payload


def output(verdict="true_positive", *, truncated=False, excerpt="value", field="payload.body"):
    return WAFAnalysisOutput.model_validate({
        "verdict": verdict,
        "confidence_score": 0.94,
        "summary_ko": "제공된 요청의 값과 사용 문맥을 비교한 분석 결과입니다.",
        "threat_analysis": {
            "severity": {"true_positive": "HIGH", "false_positive": "NONE", "inconclusive": "UNKNOWN"}[verdict],
            "category": "synthetic",
            "target": "body",
            "technique_ko": "제공된 값의 의미와 사용 위치를 확인했습니다.",
            "obfuscations": [],
            "potential_impact_ko": "영향은 대상의 실제 처리 방식에 따라 달라집니다.",
        },
        "signature_assessment": {
            "version": "signature-assessment-v2", "relation": "exact", "explanation_ko": None,
            "matched_points": ["입력 구문과 탐지 유형의 관계를 비교했습니다."],
            "mismatched_points": [], "uncertainty_ko": None,
        },
        "evidence": [{"field": field, "excerpt": excerpt,
            "interpretation_ko": "제공된 원문과 주변 문맥에서 확인한 구체적인 의미입니다."}],
        "recommended_checks": [], "analyst_checks": [], "conflicting_evidence": [],
        "tuning_recommendation": {"recommended": False},
        "input_truncated": truncated,
    })


def context(verdict="true_positive", *, truncated=False, status="success", revision=candidate.RULES_VERSION):
    return candidate.VerifierPolicyContext(
        waf_action="D" if verdict != "false_positive" else "A",
        parser_status=status, input_truncated=truncated,
        interpretation_conflict=False, fixed_rules_version=revision,
    )


def inspect(raw):
    parsed = parse_http_payload(raw)
    full = candidate.assess_request_integrity(raw, parsed,
        version=candidate.integrity_version_for_rules(candidate.RULES_VERSION))
    return parsed, frozen_integrity.submitted_integrity(full, [(0, len(raw))], 8192)


@pytest.mark.parametrize("status", ["success", "partial", "failed"])
def test_candidate_keeps_parser_diagnostic_only_without_mutating_frozen_module(status):
    before = frozen_policy.PARSER_DIAGNOSTICS_ONLY_RULES_VERSIONS
    assert candidate.verifier_reasons(output(), context(status=status)) == []
    assert not candidate.parser_status_triggers_verifier(candidate.RULES_VERSION)
    assert candidate.RULES_VERSION not in before
    assert frozen_policy.PARSER_DIAGNOSTICS_ONLY_RULES_VERSIONS is before
    assert frozen_policy.parser_status_triggers_verifier(candidate.RULES_VERSION)
    assert frozen_policy.verifier_reasons(output(), context(status=status)) == (
        [] if status == "success" else ["parser_incomplete"])


@pytest.mark.parametrize("revision", [None, "unknown", "waf-system-v2.16", "waf-system-v2.17", "waf-system-v2.20"])
@pytest.mark.parametrize("status", ["partial", "failed"])
def test_historical_and_unknown_policy_routing_is_unchanged(revision, status):
    value, settings = output(), context(status=status, revision=revision)
    assert candidate.verifier_reasons(value, settings) == frozen_policy.verifier_reasons(value, settings)
    assert candidate.parser_status_triggers_verifier(revision) == frozen_policy.parser_status_triggers_verifier(revision)
    assert candidate.verifier_policy_metadata(revision) == verifier_policy_metadata(revision)


@pytest.mark.parametrize("reason", [
    "primary_inconclusive", "low_confidence", "signature_not_exact", "true_positive_but_allowed",
    "false_positive_but_denied", "input_truncated", "interpretation_conflict", "evidence_grounding_failed",
])
def test_every_nonparser_verifier_condition_is_preserved(reason):
    value, settings = output(), context(status="partial")
    if reason == "primary_inconclusive":
        value = output("inconclusive")
    elif reason == "low_confidence":
        value = value.model_copy(update={"confidence_score": 0.5})
    elif reason == "signature_not_exact":
        value = value.model_copy(update={"signature_assessment": value.signature_assessment.model_copy(
            update={"relation": SignatureRelation.partial})})
    elif reason == "true_positive_but_allowed":
        settings = replace(settings, waf_action="A")
    elif reason == "false_positive_but_denied":
        value = output("false_positive")
    elif reason == "input_truncated":
        settings = replace(settings, input_truncated=True)
    elif reason == "interpretation_conflict":
        settings = replace(settings, interpretation_conflict=True)
    elif reason == "evidence_grounding_failed":
        settings = replace(settings, evidence_grounding_failed=True)
    before_output, before_context = value.model_dump(mode="json"), deepcopy(settings)
    assert reason in candidate.verifier_reasons(value, settings)
    assert candidate.verifier_reasons(value, settings) == frozen_policy.verifier_reasons(
        value, replace(settings, fixed_rules_version="waf-system-v2.20"))
    assert value.model_dump(mode="json") == before_output and settings == before_context


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
@pytest.mark.parametrize("context_truncated,output_truncated", [(True, False), (False, True), (True, True)])
def test_truncation_is_preserved_signal_not_an_automatic_verdict(verdict, context_truncated, output_truncated):
    primary = output(verdict, truncated=output_truncated)
    settings = context(verdict, truncated=context_truncated, status="partial")
    reasons = candidate.verifier_reasons(primary, settings)
    assert "input_truncated" in reasons
    assert "parser_incomplete" not in reasons
    verifier = output(verdict, truncated=output_truncated)
    final = candidate.finalize_with_verifier(primary, verifier, reasons)
    assert final.agreement and final.output.verdict.value == verdict
    assert final.output.input_truncated == output_truncated
    assert settings.input_truncated == context_truncated


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
def test_truncated_independent_decisive_roles_can_agree_without_an_integrity_defect(verdict):
    raw = "POST /submit HTTP/1.1\r\nHost: example.invalid\r\nContent-Length: 5\r\n\r\nvalue"
    parsed, assessment = inspect(raw)
    primary = output(verdict, truncated=True)
    final = candidate.finalize_with_verifier(primary, output(verdict, truncated=True), ["input_truncated"])
    guarded, metadata = candidate.apply_integrity_guard(final.output, assessment, raw, parsed)
    assert guarded.verdict.value == verdict and guarded.input_truncated
    assert not metadata["downgraded_to_inconclusive"]
    assert primary.input_truncated


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
@pytest.mark.parametrize("verifier", [None, "inconclusive"])
def test_truncation_does_not_disable_required_verifier_failure_or_disagreement(verdict, verifier):
    primary = output(verdict, truncated=True)
    checked = output(verifier, truncated=True) if verifier else None
    final = candidate.finalize_with_verifier(primary, checked, ["input_truncated"],
        verifier_failure="synthetic_failure" if verifier is None else None)
    assert final.output.verdict == AgentVerdict.inconclusive
    assert final.output.input_truncated
    assert final.verifier_executed and final.agreement is False


@pytest.mark.parametrize("body,headers,code,excerpt", [
    ("value", "Content-Length: 50\r\n", "body_shorter_than_content_length", "value"),
    ("", "Content-Length: 50\r\n", "declared_body_not_captured", "Content-Length: 50"),
    ("value", "Content-Length: 5\r\nContent-Length: 50\r\n", "content_length_conflict", "value"),
    ("value", "Content-Length: invalid\r\n", "content_length_invalid", "Content-Length: invalid"),
    ("5\r\nvalue\r\n", "Transfer-Encoding: chunked\r\n", "chunked_body_incomplete", "value"),
    ("5\r\nvalue\r\n0\r\n\r\n", "Transfer-Encoding: chunked\r\nContent-Length: 5\r\n",
     "transfer_encoding_content_length", "value"),
    ('{"value":1', "Content-Type: application/json\r\n", "json_container_unclosed", '"value":1'),
])
def test_actual_affected_capture_defects_still_limit_normal_findings(body, headers, code, excerpt):
    raw = "POST /submit HTTP/1.1\r\nHost: example.invalid\r\n" + headers + "\r\n" + body
    parsed, assessment = inspect(raw)
    value = output("false_positive", truncated=True, excerpt=excerpt, field="payload")
    before = value.model_dump(mode="json")
    assert code in {finding["code"] for finding in candidate.affected_normal_findings(value, assessment, raw, parsed)}
    final, metadata = candidate.apply_integrity_guard(value, assessment, raw, parsed)
    assert final.verdict == AgentVerdict.inconclusive and final.input_truncated
    assert metadata["downgraded_to_inconclusive"] and code in metadata["affected_issue_codes"]
    assert final.evidence == value.evidence
    assert value.model_dump(mode="json") == before
    WAFAnalysisOutput.model_validate(final.model_dump(mode="json"))


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive"])
def test_body_defect_does_not_blanket_rewrite_independent_request_line_evidence(verdict):
    raw = "POST /submit?name=hello HTTP/1.1\r\nHost: example.invalid\r\nContent-Length: 50\r\n\r\nvalue"
    parsed, assessment = inspect(raw)
    value = output(verdict, truncated=True, excerpt="name=hello", field="payload")
    final, metadata = candidate.apply_integrity_guard(value, assessment, raw, parsed)
    assert final.verdict.value == verdict and final.input_truncated
    assert metadata["issue_codes"] == ["body_shorter_than_content_length"]
    assert not metadata["downgraded_to_inconclusive"]


def test_untrusted_body_does_not_erase_a_grounded_attack_finding():
    raw = "POST /submit HTTP/1.1\r\nContent-Length: 50\r\n\r\nvalue"
    parsed, assessment = inspect(raw)
    value = output("true_positive", truncated=True)
    final, metadata = candidate.apply_integrity_guard(value, assessment, raw, parsed)
    assert final.verdict == AgentVerdict.true_positive and final.input_truncated
    assert not metadata["downgraded_to_inconclusive"]


def test_explicitly_missing_decisive_content_stays_model_inconclusive():
    primary = output("inconclusive", truncated=True)
    final = candidate.finalize_with_verifier(primary, output("inconclusive", truncated=True),
        candidate.verifier_reasons(primary, context("inconclusive", truncated=True)))
    assert final.output.verdict == AgentVerdict.inconclusive
    assert final.output.summary_ko == primary.summary_ko and final.output.input_truncated


def test_truncation_stays_visible_in_guidance_not_a_new_generated_judgment():
    value = output("false_positive", truncated=True)
    final = candidate.finalize_without_verifier(value)
    rendered = guidance(value.summary_ko, final.output, None, None, final)
    assert rendered["limitations"]
    assert rendered["summary_ko"] == value.summary_ko
    assert final.output.verdict == AgentVerdict.false_positive and final.output.input_truncated


@pytest.mark.parametrize("revision", ["waf-system-v2.9", "waf-system-v2.12", "waf-system-v2.16", "waf-system-v2.20"])
def test_integrity_routing_preserves_every_earlier_revision(revision):
    assert candidate.integrity_version_for_rules(revision) == frozen_integrity.integrity_version_for_rules(revision)


def test_v221_extends_integrity_v3_without_changing_any_frozen_sets():
    assert candidate.integrity_version_for_rules(candidate.RULES_VERSION) == frozen_integrity.INTEGRITY_V3_VERSION
    assert candidate.RULES_VERSION in candidate.INTEGRITY_RULES_VERSIONS
    assert candidate.RULES_VERSION in candidate.INTEGRITY_V3_RULES_VERSIONS
    assert candidate.RULES_VERSION not in candidate.INTEGRITY_V2_RULES_VERSIONS
    assert candidate.RULES_VERSION not in frozen_integrity.INTEGRITY_RULES_VERSIONS
    with pytest.raises(ValueError, match="request_integrity_rules_unsupported"):
        frozen_integrity.integrity_version_for_rules(candidate.RULES_VERSION)
    with pytest.raises(ValueError, match="request_integrity_rules_unsupported"):
        candidate.integrity_version_for_rules("unknown")


def test_candidate_reuses_pure_guards_without_altering_original_bindings():
    assert candidate.affected_normal_findings is frozen_guard.affected_normal_findings
    assert candidate.apply_integrity_guard is frozen_guard.apply_integrity_guard
    assert candidate.finalize_with_verifier is frozen_policy.finalize_with_verifier
    assert candidate.finalize_without_verifier is frozen_policy.finalize_without_verifier
    assert candidate.VerifierPolicyContext is frozen_policy.VerifierPolicyContext
    assert candidate.assess_request_integrity is frozen_integrity.assess_request_integrity
    value = output("false_positive", truncated=True)
    preserved, metadata = candidate.apply_final_followup_policy(value, "D", candidate.RULES_VERSION)
    assert preserved is value and metadata is None
    assert candidate.verifier_policy_metadata(candidate.RULES_VERSION) == {
        "verifier_policy_version": "security-analysis-policy-v2",
        "retired_verifier_triggers": ["tuning_recommended", "parser_incomplete"],
    }
