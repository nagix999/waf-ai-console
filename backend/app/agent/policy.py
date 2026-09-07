from dataclasses import dataclass

from .analyst_guidance import FOLLOW_UP_SUMMARY, merge_analyst_checks
from .contracts import (
    AgentVerdict,
    EvidenceItem,
    SignatureRelation,
    ThreatAnalysis,
    ThreatSeverity,
    TuningRecommendation,
    WAFAnalysisOutput,
)
from .evidence_deduplication import deduplicate_evidence


@dataclass(frozen=True)
class VerifierPolicyContext:
    waf_action: str
    parser_status: str
    input_truncated: bool
    confidence_threshold: float = 0.75
    evidence_grounding_failed: bool = False


@dataclass(frozen=True)
class Finalization:
    output: WAFAnalysisOutput
    verifier_executed: bool
    agreement: bool | None
    verifier_reasons: tuple[str, ...]
    verifier_failure: str | None = None


def verifier_reasons(primary: WAFAnalysisOutput, context: VerifierPolicyContext) -> list[str]:
    reasons: list[str] = []
    if primary.verdict == AgentVerdict.inconclusive:
        reasons.append("primary_inconclusive")
    if primary.confidence_score < context.confidence_threshold:
        reasons.append("low_confidence")
    if primary.signature_assessment.relation in {SignatureRelation.partial, SignatureRelation.mismatch}:
        reasons.append("signature_not_exact")
    if primary.verdict == AgentVerdict.true_positive and context.waf_action == "A":
        reasons.append("true_positive_but_allowed")
    if primary.verdict == AgentVerdict.false_positive and context.waf_action == "D":
        reasons.append("false_positive_but_denied")
    if context.parser_status in {"partial", "failed"}:
        reasons.append("parser_incomplete")
    if context.input_truncated or primary.input_truncated:
        reasons.append("input_truncated")
    if primary.tuning_recommendation.recommended:
        reasons.append("tuning_recommended")
    if primary.conflicting_evidence:
        reasons.append("conflicting_evidence")
    if context.evidence_grounding_failed:
        reasons.append("evidence_grounding_failed")
    return reasons


def _dedupe_text(values: list[str], limit: int = 10) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
        if len(result) == limit:
            break
    return result


def _merge_evidence(primary: WAFAnalysisOutput, verifier: WAFAnalysisOutput | None) -> list[EvidenceItem]:
    return deduplicate_evidence([*primary.evidence, *(verifier.evidence if verifier else [])])


def _disabled_tuning(reason: str) -> TuningRecommendation:
    return TuningRecommendation(
        recommended=False,
        proposal_ko=None,
        risk_ko=reason,
        validation_ko=None,
    )


def _severity_for_inconclusive(primary: WAFAnalysisOutput) -> ThreatAnalysis:
    return primary.threat_analysis.model_copy(update={"severity": ThreatSeverity.UNKNOWN})


def _conservative_agreed_severity(
    primary: WAFAnalysisOutput,
    verifier: WAFAnalysisOutput,
) -> ThreatAnalysis:
    if primary.verdict == AgentVerdict.false_positive:
        severity = ThreatSeverity.NONE
    elif primary.verdict == AgentVerdict.inconclusive:
        severity = ThreatSeverity.UNKNOWN
    else:
        threat_rank = {
            ThreatSeverity.LOW: 0,
            ThreatSeverity.MEDIUM: 1,
            ThreatSeverity.HIGH: 2,
            ThreatSeverity.CRITICAL: 3,
        }
        severity = min(
            primary.threat_analysis.severity,
            verifier.threat_analysis.severity,
            key=threat_rank.__getitem__,
        )
    return primary.threat_analysis.model_copy(update={"severity": severity})


def finalize_without_verifier(primary: WAFAnalysisOutput) -> Finalization:
    return Finalization(
        output=primary.model_copy(update={"evidence": _merge_evidence(primary, None)}),
        verifier_executed=False,
        agreement=None,
        verifier_reasons=(),
    )


def finalize_with_verifier(
    primary: WAFAnalysisOutput,
    verifier: WAFAnalysisOutput | None,
    reasons: list[str],
    verifier_failure: str | None = None,
) -> Finalization:
    if verifier is None:
        output = primary.model_copy(
            update={
                "verdict": AgentVerdict.inconclusive,
                "confidence_score": min(primary.confidence_score, 0.49),
                "summary_ko": FOLLOW_UP_SUMMARY,
                "threat_analysis": _severity_for_inconclusive(primary),
                "evidence": _merge_evidence(primary, None),
                "recommended_checks": _dedupe_text(
                    primary.recommended_checks
                ),
                "analyst_checks": merge_analyst_checks(primary),
                "tuning_recommendation": _disabled_tuning("추가 확인 전에는 WAF 설정 변경을 제안하지 않습니다."),
            }
        )
        return Finalization(output, True, False, tuple(reasons), verifier_failure)

    agreement = primary.verdict == verifier.verdict
    if not agreement:
        output = primary.model_copy(
            update={
                "verdict": AgentVerdict.inconclusive,
                "confidence_score": min(primary.confidence_score, verifier.confidence_score, 0.49),
                "summary_ko": FOLLOW_UP_SUMMARY,
                "threat_analysis": _severity_for_inconclusive(primary),
                "evidence": _merge_evidence(primary, verifier),
                "recommended_checks": _dedupe_text(
                    [*primary.recommended_checks, *verifier.recommended_checks]
                ),
                "analyst_checks": merge_analyst_checks(primary, verifier),
                "tuning_recommendation": _disabled_tuning("추가 확인 전에는 WAF 설정 변경을 제안하지 않습니다."),
                "conflicting_evidence": _dedupe_text(
                    [*primary.conflicting_evidence, *verifier.conflicting_evidence]
                ),
            }
        )
        return Finalization(output, True, False, tuple(reasons))

    tuning = primary.tuning_recommendation
    if tuning.recommended and not verifier.tuning_recommendation.recommended:
        tuning = _disabled_tuning("현재 자료만으로 안전한 변경 범위를 확정하지 않아 튜닝을 제안하지 않습니다.")
    output = primary.model_copy(
        update={
            "confidence_score": min(primary.confidence_score, verifier.confidence_score),
            "threat_analysis": _conservative_agreed_severity(primary, verifier),
            "evidence": _merge_evidence(primary, verifier),
            "recommended_checks": _dedupe_text([*primary.recommended_checks, *verifier.recommended_checks]),
            "analyst_checks": merge_analyst_checks(primary, verifier),
            "tuning_recommendation": tuning,
            "conflicting_evidence": _dedupe_text(
                [*primary.conflicting_evidence, *verifier.conflicting_evidence]
            ),
        }
    )
    return Finalization(output, True, True, tuple(reasons))
