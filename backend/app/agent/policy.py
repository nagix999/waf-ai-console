from dataclasses import dataclass

from .contracts import (
    AgentVerdict,
    EvidenceItem,
    SignatureRelation,
    TuningRecommendation,
    WAFAnalysisOutput,
)


@dataclass(frozen=True)
class VerifierPolicyContext:
    waf_action: str
    parser_status: str
    input_truncated: bool
    confidence_threshold: float = 0.75


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
    result: list[EvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for item in [*primary.evidence, *(verifier.evidence if verifier else [])]:
        key = (item.field, item.excerpt)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) == 5:
            break
    return result


def _disabled_tuning(reason: str) -> TuningRecommendation:
    return TuningRecommendation(
        recommended=False,
        proposal_ko=None,
        risk_ko=reason,
        validation_ko=None,
    )


def finalize_without_verifier(primary: WAFAnalysisOutput) -> Finalization:
    return Finalization(
        output=primary,
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
                "summary_ko": "독립 검증을 완료하지 못해 최종 판정을 보류합니다.",
                "uncertainties": _dedupe_text(
                    ["독립 검증 Agent가 정상 결과를 반환하지 못했습니다.", *primary.uncertainties]
                ),
                "recommended_checks": _dedupe_text(
                    ["검증 모델 연결과 실패 ID를 확인한 뒤 다시 분석하세요.", *primary.recommended_checks]
                ),
                "tuning_recommendation": _disabled_tuning("검증 실패 상태에서는 튜닝을 제안하지 않습니다."),
                "conflicting_evidence": _dedupe_text(
                    ["독립 검증 결과 부재", *primary.conflicting_evidence]
                ),
            }
        )
        return Finalization(output, True, False, tuple(reasons), verifier_failure)

    agreement = primary.verdict == verifier.verdict
    if not agreement:
        output = primary.model_copy(
            update={
                "verdict": AgentVerdict.inconclusive,
                "confidence_score": min(primary.confidence_score, verifier.confidence_score, 0.49),
                "summary_ko": "1차 판정과 독립 검증 판정이 일치하지 않아 최종 판정을 보류합니다.",
                "evidence": _merge_evidence(primary, verifier),
                "uncertainties": _dedupe_text(
                    [
                        f"판정 불일치: primary={primary.verdict.value}, verifier={verifier.verdict.value}",
                        *primary.uncertainties,
                        *verifier.uncertainties,
                    ]
                ),
                "recommended_checks": _dedupe_text(
                    ["분석가가 원문과 양쪽 근거를 직접 확인하세요.", *primary.recommended_checks, *verifier.recommended_checks]
                ),
                "tuning_recommendation": _disabled_tuning("판정 불일치 상태에서는 튜닝을 제안하지 않습니다."),
                "conflicting_evidence": _dedupe_text(
                    ["Primary/Verifier verdict 불일치", *primary.conflicting_evidence, *verifier.conflicting_evidence]
                ),
            }
        )
        return Finalization(output, True, False, tuple(reasons))

    tuning = primary.tuning_recommendation
    if tuning.recommended and not verifier.tuning_recommendation.recommended:
        tuning = _disabled_tuning("독립 검증이 튜닝 제안에 동의하지 않았습니다.")
    output = primary.model_copy(
        update={
            "confidence_score": min(primary.confidence_score, verifier.confidence_score),
            "evidence": _merge_evidence(primary, verifier),
            "uncertainties": _dedupe_text([*primary.uncertainties, *verifier.uncertainties]),
            "recommended_checks": _dedupe_text([*primary.recommended_checks, *verifier.recommended_checks]),
            "tuning_recommendation": tuning,
            "conflicting_evidence": _dedupe_text(
                [*primary.conflicting_evidence, *verifier.conflicting_evidence]
            ),
        }
    )
    return Finalization(output, True, True, tuple(reasons))
