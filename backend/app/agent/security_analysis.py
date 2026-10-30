"""Canonical security analysis assembled only from source-validated role calls."""
from .analyst_assessment import build_analyst_assessment
from .analyst_guidance import EVIDENCE_SOURCE_NOTICE
from ..services.analyst_presentation import INPUT_LIMITATION

VERIFIER_POLICY_VERSION = "security-analysis-policy-v1"
RETIRED_VERIFIER_TRIGGERS = ["tuning_recommended"]
SUPPORTS = {"true_positive": "attack_request", "false_positive": "normal_request", "context": "context"}


def verifier_policy_metadata(fixed_rules_version):
    from .policy import parser_status_triggers_verifier

    diagnostic_only = not parser_status_triggers_verifier(fixed_rules_version)
    return {
        "verifier_policy_version": "security-analysis-policy-v2" if diagnostic_only else VERIFIER_POLICY_VERSION,
        "retired_verifier_triggers": [*RETIRED_VERIFIER_TRIGGERS, *(["parser_incomplete"] if diagnostic_only else [])],
    }


def canonical_evidence(primary, verifier):
    assessment = build_analyst_assessment(primary, verifier)
    evidence = [{**item, "supports": SUPPORTS[item["supports"]],
                 "source_spans": item.get("source_spans", [])} for item in assessment["evidence"]]
    return evidence, assessment["decision_issues"], assessment["omitted_issue_count"]


def conflict_observed(call):
    if not call or not call.succeeded:
        return False
    return any(record.get("assessment", {}).get("interpretation_conflict") is True for record in call.grounding_history)


def role_result(call, role, evidence, issues):
    if not call or not call.succeeded:
        return None
    values = call.output.model_dump(mode="json", include={
        "verdict", "confidence_score", "summary_ko", "threat_analysis", "signature_assessment", "input_truncated"})
    values["evidence_ids"] = [item["evidence_id"] for item in evidence
                              if any(origin["role"] == role for origin in item["origins"])]
    values["decision_issues"] = [issue for issue in issues if role in issue["origins"]]
    values["interpretation_conflict"] = conflict_observed(call)
    return values


def decision_evidence_ids(output, evidence):
    # Preserve the established final-policy max-five subset independently from
    # the complete, direction-preserving list contributed by both roles.
    result = []
    for item in output.evidence:
        identifier = next((value["evidence_id"] for value in evidence if all(
            value[key] == getattr(item, key) for key in ("field", "excerpt", "interpretation_ko"))), None)
        if identifier is not None and identifier not in result:
            result.append(identifier)
    return result


def observational_summary(primary, verifier, finalization, integrity_metadata=None):
    grounding = primary.telemetry.get("evidence_grounding", {})
    if grounding.get("downgraded_to_inconclusive") or grounding.get("correction_requires_review"):
        return "수집된 요청과 인용 자료만으로는 요청의 동작을 충분히 확인하기 어렵습니다."
    if integrity_metadata and integrity_metadata.get("downgraded_to_inconclusive"):
        return "수집된 요청의 일부 구간에 누락 가능성이나 구조·경계 문제가 있습니다."
    if finalization.verifier_executed and (verifier is None or not verifier.succeeded):
        return "요청의 원문 근거는 기록되었으나 보안 분석을 모두 완료하지 못했습니다."
    if finalization.agreement is False:
        return "요청 내용에 공격 시도와 정상 데이터로 다르게 해석될 수 있는 부분이 있습니다."
    return primary.output.summary_ko


def guidance(summary, output, primary, verifier, finalization):
    limitations = []
    if any(call and call.telemetry.get("evidence_grounding", {}).get("rejected_count")
           for call in (primary, verifier)):
        limitations.append(EVIDENCE_SOURCE_NOTICE)
    if output.input_truncated:
        limitations.append(INPUT_LIMITATION)
    if finalization.verifier_executed and (not verifier or not verifier.succeeded):
        limitations.append("필요한 분석 절차를 완료하지 못했습니다. 실행 정보에서 실패 원인을 확인할 수 있습니다.")
    return {"summary_ko": summary, "limitations": limitations}
