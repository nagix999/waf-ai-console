"""Read-only display of saved causes. No inference, role output reads or writes.

Kept in parity with frontend/src/decisionExplanation.js by shared test cases.
Parser/input signals alone never explain a final abstention.
"""

GENERIC_HOLD_SUMMARY = "현재 분석에서는 정탐·오탐 판정을 보류했습니다."
GENERIC_CHECK = {
    "source_ko": "대상 애플리케이션의 요청 처리 규격 또는 담당자",
    "check_ko": "탐지된 입력이 해당 기능에서 허용되는 업무 데이터인지, 저장·출력·명령 실행 중 어떤 처리 경로로 사용되는지 확인하세요.",
    "why_ko": "허용된 업무 값으로만 취급되는지, 보안 경계를 우회하는 구문으로 해석될 수 있는지 구분하는 데 도움이 됩니다. 공격 시도 여부와 실제 성공 여부는 따로 판단하세요.",
}
GENERIC_TUNING_RISK = "추가 확인 전에는 WAF 설정 변경을 제안하지 않습니다."
PROVISIONAL_ANALYSIS_NOTICE = "아래는 자동 분석 중 작성된 해석입니다. 공격·정상으로 단정하는 문장이 있어도 확정된 결론으로 사용하지 마세요."
EXPLANATIONS = {
    "execution_incomplete": ("자동 분석 미완료", "자동 분석의 필수 절차를 완료하지 못해 판정을 확정하지 않았습니다.", "서비스 담당자가 실행 오류를 확인해야 합니다. 분석 미완료를 이유로 업무 자료를 추가로 요구하지 않습니다."),
    "evidence_unverified": ("근거 확인 실패", "판정에 사용한 인용 근거의 원문 대조를 통과하지 못해 판정을 확정하지 않았습니다.", "분석가는 저장된 원문을 직접 검토하고, 서비스 담당자는 근거 대조 오류를 확인해야 합니다. 이 오류만으로 요청 정보가 부족하거나 공격이 없다고 볼 수 없습니다."),
    "evidence_review": ("근거 재검토 필요", "인용 근거를 교정했지만 기존 판단을 뒷받침하는지 다시 검토해야 하므로 판정을 확정하지 않았습니다.", "남은 원문 근거와 분석 내용의 연결을 다시 검토하세요. 인용 위치가 맞는다는 사실만으로 해석까지 정확하다고 볼 수는 없습니다."),
    "input_limited": ("요청 구조 확인 필요", "정상 판단에 사용한 구간에서 본문·길이·경계 문제가 확인되어 판정을 확정하지 않았습니다. 수집 중 누락인지 실제 요청 형식의 문제인지는 별도 확인이 필요합니다.", "같은 요청의 수집 기록과 실제 요청 형식을 대조하세요. 구체적인 확인 항목이 있으면 아래에 표시합니다."),
    "assessment_pending": ("판단 미확정", "자동 분석에서 공격·정상 여부를 확정할 수 있는 결론을 얻지 못했습니다.", "원문과 근거 해석을 직접 검토해야 합니다. 추가 자료가 반드시 필요한지는 현재 기록만으로 알 수 없습니다."),
    "model_abstained": ("보류 이유", "자동 분석이 공격·정상 여부를 판단하지 못했습니다. 구체적인 구분 조건은 기록된 설명과 확인 항목을 검토해 주세요.", "원문과 기록된 확인 항목을 검토하세요. 추가 자료는 판정을 가르는 경우에만 확인하면 됩니다."),
    "reason_unrecorded": ("보류 이유", "보류 판정은 저장되어 있지만 구체적인 사유를 확인할 수 없습니다.", "원문과 기록된 확인 항목을 검토하세요. 추가 자료는 판정을 가르는 경우에만 확인하면 됩니다."),
}

INPUT_LIMIT_DETAILS = {
    "declared_body_not_captured": (
        "요청 본문 확인 필요",
        "본문 길이는 선언되어 있지만 수집된 로그에는 본문이 없습니다. 본문 내용을 확인할 수 없어 정상 판정을 보류했습니다.",
        "수집 설정과 같은 요청의 원본 기록을 대조해 본문이 수집 중 빠진 것인지, 실제로 비어 있던 것인지 확인하세요."),
    "body_shorter_than_content_length": (
        "본문 길이 확인 필요",
        "수집된 본문이 선언된 길이보다 짧아 해당 구간을 정상으로 확정하지 못했습니다. 수집 과정의 생략인지 실제 입력의 길이 문제인지 확인해야 합니다.",
        "같은 요청의 수집 기록에서 길이 선언과 실제 본문을 대조하고, 수집 시 본문 길이 제한이 적용됐는지 확인하세요."),
    "json_container_unclosed": (
        "JSON 본문 확인 필요",
        "JSON 본문이 닫히지 않아 해당 구간을 정상 판정의 근거로 사용할 수 없습니다. 수집 누락과 잘못된 입력을 구분해야 합니다.",
        "같은 요청의 원본 기록에서 닫는 구문이 빠졌는지 확인하고, 애플리케이션의 JSON 입력 규격과 대조하세요."),
}


def input_limit_explanation(codes):
    explanation = _make("input_limited")
    codes = codes if isinstance(codes, list) else []
    for code, (title, reason, action) in INPUT_LIMIT_DETAILS.items():
        if code in codes:
            explanation.update(title_ko=title, reason_ko=reason, action_ko=action)
            break
    return explanation


def _record(value):
    return value if isinstance(value, dict) else {}


def is_generic_check(value):
    return isinstance(value, dict) and all(value.get(key) == text for key, text in GENERIC_CHECK.items())


def _make(code):
    title, reason, action = EXPLANATIONS[code]
    return {"code": code, "title_ko": title, "reason_ko": reason, "action_ko": action,
            "use_recorded_summary": code in {"model_abstained", "reason_unrecorded"}}


def decision_explanation(detail):
    result = _record(detail.get("result"))
    if detail.get("status") == "failed":
        return _make("execution_incomplete")
    if detail.get("status") != "completed" or result.get("verdict") != "inconclusive":
        return None
    diagnostics = _record(result.get("diagnostics"))
    values = diagnostics.get("inconclusive_reasons")
    reasons = {value for value in values if isinstance(value, str)} if isinstance(values, list) else set()
    verifier = _record(result.get("verifier"))
    if "verifier_failed" in reasons or (verifier.get("executed") is True and any(
            isinstance(value, str) and value.strip() for value in (verifier.get("failure_id"), verifier.get("error")))):
        return _make("execution_incomplete")
    if reasons & {"primary_evidence_rejected", "verifier_evidence_rejected"} or result.get("summary_ko") == "LLM이 제시한 판정 근거를 지정된 입력 필드의 원문에서 확인할 수 없어 최종 판정을 보류합니다.":
        roles = _record(diagnostics.get("roles"))
        review = any(f"{role}_evidence_rejected" in reasons and _record(roles.get(role)).get("correction_requires_review") is True for role in ("primary", "verifier"))
        return _make("evidence_review" if review else "evidence_unverified")
    if "input_integrity_limited" in reasons and _record(diagnostics.get("request_integrity")).get("downgraded_to_inconclusive") is True:
        return input_limit_explanation(diagnostics["request_integrity"].get("affected_issue_codes"))
    if "verdict_disagreement" in reasons:
        return _make("assessment_pending")
    if reasons & {"primary_model_inconclusive", "verifier_model_inconclusive"}:
        return _make("model_abstained")
    return _make("reason_unrecorded")


def threat_category_label(verdict):
    return "공격 유형" if verdict == "true_positive" else "탐지 유형" if verdict == "false_positive" else "검토한 유형"
