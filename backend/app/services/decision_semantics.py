"""Presentation only, mirrored by frontend/src/decisionSemantics.js.

Never reclassifies a verdict, changes saved output or interprets free text.
"""
SEVERITY_MEANING = "잠재 영향 기준 · 실제 공격 성공 여부와 별도"
DISAGREEMENT_NOTICE = "자동 분석 해석이 서로 달랐습니다."
LEGACY_SIGNATURE_NOTICE = "관계 설명은 기록된 관계값을 기준으로 표시합니다. 기존 분석 설명은 검증된 결론이 아닙니다."
SIGNATURE_SUMMARIES = {
    "exact": "탐지 설명과 요청의 핵심 동작이 일치합니다.",
    "partial": "일부는 일치하지만 중요한 차이가 있습니다.",
    "mismatch": "탐지 설명과 요청의 핵심 동작이 다릅니다.",
    "unknown": "현재 입력만으로 관계를 확인하기 어렵습니다.",
}
WAF_OBSERVATIONS = {
    "D": "이 요청은 WAF에서 차단으로 기록됐습니다. 우회·반복 시도나 실제 영향은 별도로 확인합니다.",
    "A": "이 요청은 WAF에서 허용으로 기록됐습니다. 애플리케이션 처리나 공격 성공을 뜻하지는 않습니다.",
}


def verifier_disagreed(detail):
    result = detail.get("result") or {}
    diagnostics = result.get("diagnostics") or {}
    return (detail.get("status") == "completed" and result.get("verdict") == "inconclusive"
            and isinstance(diagnostics, dict)
            and isinstance(diagnostics.get("inconclusive_reasons"), list)
            and "verdict_disagreement" in diagnostics["inconclusive_reasons"])


def evidence_sections(verdict):
    if verdict == "false_positive":
        sides = [("false_positive", "오탐 근거"), ("true_positive", "반대 근거")]
    elif verdict == "true_positive":
        sides = [("true_positive", "정탐 근거"), ("false_positive", "반대 근거")]
    elif verdict == "inconclusive":
        sides = [("true_positive", "공격 해석"), ("false_positive", "정상 해석")]
    else:
        sides = [("true_positive", "정탐 근거"), ("false_positive", "오탐 근거")]
    return [*sides, ("context", "참고 내용"), ("unclassified", "구분 미기록")]
