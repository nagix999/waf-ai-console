"""Narrow normal-verdict guard; does not judge attack semantics or rewrite roles."""
from .contracts import AgentVerdict, AnalystCheck, ThreatSeverity, TuningRecommendation
from ..services.request_integrity import INTEGRITY_VERSION, NORMAL_GUARD_CODES
from ..services.decision_explanation import input_limit_explanation


def affected_normal_findings(output, assessment, raw, parsed):
    if output.verdict != AgentVerdict.false_positive or not assessment:
        return []
    findings = []
    for finding in assessment["issues"]:
        if finding["code"] not in NORMAL_GUARD_CODES:
            continue
        affected = False
        for item in output.evidence:
            field = item.field.strip().removeprefix("event.")
            if field in {"payload", "raw_payload"}:
                ranges = [(0, len(raw))]
            elif field.startswith("payload."):
                key = field[8:]
                if key.startswith("headers."):
                    key = key.lower()
                ranges = parsed["raw_source_spans"].get(key, [])
            else:
                continue  # Event/extension evidence is not an HTTP region.
            for start, end in ranges:
                for low, high in finding["affected_spans"]:
                    # A citation crossing a boundary also intersects it. No
                    # inference from arbitrary interpretation text/field names.
                    begin = max(start, low - len(item.excerpt) + 1)
                    finish = min(end, high + len(item.excerpt) - 1)
                    position = raw.find(item.excerpt, begin, finish)
                    if position >= 0 and position < high and position + len(item.excerpt) > low:
                        affected = True
        if affected:
            findings.append(finding)
    return findings


def apply_integrity_guard(output, assessment, raw, parsed):
    """Preserve grounded evidence and individual model outputs for audit.

    JSON duplicates/extra bytes alone never trigger this guard. An independent
    attack finding is never downgraded just because another region is missing.
    Location overlap is a necessary conservative check, not semantic proof.
    """
    affected = affected_normal_findings(output, assessment, raw, parsed)
    metadata = {"version": assessment["version"],
                "issue_codes": list(dict.fromkeys(item["code"] for item in assessment["issues"])),
                "limitation_codes": list(assessment["limitations"]),
                "omitted_issues": assessment["omitted_issues"],
                "downgraded_to_inconclusive": bool(affected),
                "affected_issue_codes": list(dict.fromkeys(item["code"] for item in affected))}
    if not affected:
        return output, metadata
    groups = []
    for item in affected:
        group = "json" if item["code"] == "json_container_unclosed" else item["scope"]
        if group not in groups:
            groups.append(group)
    check_text = {
        "body": ("수집 설정과 같은 요청의 수집 기록", "본문이 전부 수집됐는지, 길이·전송 방식 정보와 수집된 본문이 일치하는지 확인하세요.",
                 "현재 수집본의 누락 가능성이 있는 구간을 정상 판정의 근거로 사용했습니다. 빠진 내용을 확인해야 해당 구간을 정상으로 볼 수 있습니다."),
        "framing": ("WAF 수집 설정과 프록시의 요청 처리 규격", "중복된 길이 정보와 전송 방식이 수집 과정에서 합쳐졌는지, 실제 요청 경계를 어떻게 처리하는지 확인하세요.",
                    "서로 맞지 않는 경계 정보가 정상 판정의 근거 구간에 있습니다. 수집 형태와 실제 처리 기준을 구분해야 합니다."),
        "json": ("같은 요청의 수집 기록과 애플리케이션 입력 규격", "JSON 본문의 닫는 구문이 수집 중 빠졌는지, 실제로 닫히지 않은 입력이 전송됐는지 확인하세요.",
                 "끝나지 않은 JSON 구조를 정상 본문으로 해석한 근거가 있습니다. 누락된 내용이나 실제 입력 형식을 확인해야 합니다."),
    }
    checks = [AnalystCheck(source_ko=check_text[group][0], check_ko=check_text[group][1], why_ko=check_text[group][2])
              for group in groups]
    for check in output.analyst_checks:
        if check not in checks and len(checks) < 5:
            checks.append(check)
    recommended = list(dict.fromkeys([check.check_ko for check in checks] + output.recommended_checks))[:10]
    revised = output.model_copy(update={
        "verdict": AgentVerdict.inconclusive,
        "confidence_score": min(output.confidence_score, .49),
        "summary_ko": "정상 판정의 근거로 사용한 구간에 본문 누락 가능성이나 구조·경계 문제가 있어 판정을 보류합니다. 수집된 내용과 실제 요청 형식을 확인해야 합니다.",
        "threat_analysis": output.threat_analysis.model_copy(update={"severity": ThreatSeverity.UNKNOWN}),
        "analyst_checks": checks, "recommended_checks": recommended,
        "tuning_recommendation": TuningRecommendation(recommended=False),
    })
    if assessment["version"] == INTEGRITY_VERSION:
        revised = revised.model_copy(update={"summary_ko": input_limit_explanation(metadata["affected_issue_codes"])["reason_ko"]})
    return revised, metadata
