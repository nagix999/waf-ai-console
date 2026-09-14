"""Read-only follow-up from linked saved evidence, never a new verdict.

No intermediate role outputs, raw payload, natural-language diagnosis or
external lookups. Kept in parity with frontend/src/holdReview.js.
"""
from .analyst_presentation import assessment_view


def _location(field):
    field = field.removeprefix("event.")
    if field in {"payload", "raw_payload"}:
        return "HTTP 원문"
    if field.startswith("extra_fields."):
        return "추가 입력값"
    for key, label in (("body", "요청 본문"), ("query", "요청 파라미터"), ("headers", "HTTP 헤더"),
                       ("uri", "요청 주소"), ("request_target", "요청 주소"), ("path", "요청 경로")):
        if field == "payload." + key or field.startswith("payload." + key + "."):
            return label
    return None


def hold_review(detail, decision):
    result = detail.get("result") if isinstance(detail.get("result"), dict) else {}
    if (not decision or detail.get("status") != "completed" or result.get("verdict") != "inconclusive"
            or decision["code"] not in {"assessment_pending", "model_abstained", "reason_unrecorded", "input_limited"}):
        return {"issues": [], "checks": []}
    view = assessment_view(result)
    if view is None:
        return {"issues": [], "checks": []}
    issues = list(view["issues"])
    # Do not invent a missing condition. When the final policy abstained but
    # neither role supplied a point, anchor a manual review to saved sources.
    if not issues and decision["code"] == "assessment_pending":
        groups = {}
        for item in view["evidence"]:
            location = _location(item["field"])
            if location:
                groups.setdefault(location, []).append(item["number"])
        issues = [{"point_ko": f"{location}에 있는 값을 공격 구문으로 볼지 정상 데이터로 볼지 검토해야 합니다.",
                   "evidence_numbers": numbers, "missing_condition_ko": None}
                  for location, numbers in list(groups.items())[:3]]
    checks, seen = [], set()
    if decision["code"] != "input_limited":
        for issue in issues:
            condition = issue["missing_condition_ko"]
            if not condition or condition in seen:
                continue
            seen.add(condition)
            numbers = " · ".join(map(str, issue["evidence_numbers"]))
            checks.append({"source_ko": "해당 기능의 담당자 또는 입력·처리 규격",
                           "check_ko": condition,
                           "why_ko": f"관련 근거 {numbers}의 해석을 구분하기 위해 확인할 조건입니다."})
            if len(checks) == 3:
                break
        if not checks and issues:
            numbers = " · ".join(map(str, dict.fromkeys(n for issue in issues for n in issue["evidence_numbers"])))
            checks.append({"source_ko": "저장된 HTTP 원문과 판정 근거",
                           "check_ko": f"근거 {numbers}의 발췌와 판단 이유를 대조해 공격·정상 해석 중 어느 쪽이 요청 문맥에 맞는지 검토하세요.",
                           "why_ko": "추가 자료가 부족하다고 확인된 것은 아닙니다. 먼저 기록된 근거의 해석을 검토해야 합니다."})
    return {"issues": issues, "checks": checks}
