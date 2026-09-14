"""Allowlisted, read-only analyst display; parity tested with the web view."""

EVIDENCE_LABELS = {"true_positive": "정탐 근거", "false_positive": "오탐 근거", "context": "참고 내용", "unclassified": "구분 미기록"}
EVIDENCE_NOTICE = "로그에서 확인한 발췌와 그에 대한 해석입니다. 근거 개수로 판정을 결정하지 않습니다."
LEGACY_EVIDENCE_NOTICE = "기존 분석에는 정탐·오탐 근거 구분이 기록되지 않았습니다."
INPUT_LIMITATION = "분석 입력의 일부가 생략되었습니다. 전체 원문을 함께 확인해 주세요."
OLD_INPUT_LIMITATION = "원문 전체가 분석 입력에 포함되지 않았습니다. 생략된 구간을 원문에서 확인하세요."
# Exact, content-free legacy diagnostics only. Do not search prose for words
# such as verifier: code_verifier and primary_email are legitimate log fields.
LEGACY_DIAGNOSTICS = frozenset({
    "Primary와 Verifier의 분석 결과가 서로 다릅니다.",
    "Primary와 Verifier 판정이 서로 다릅니다.",
    "Primary/Verifier 판정 불일치로 최종 판정을 보류합니다.",
    "독립 검증 실패", "output_validation_failed", "framework_run_id",
    "Primary와 Verifier 판정이 다릅니다.", "독립 검증 실패로 보류합니다.",
    "분석 결과가 서로 다릅니다.", "판정 불일치 발생", "판정이 일치하지 않아 보류",
    "1차 판정의 신뢰도가 낮습니다", "실패 ID 확인", "failure_id",
    "Primary 오류", "Primary/Verifier 오류", "Primary / Verifier 결과를 확인하세요.",
    "Verifier 결과", "독립 검증 결과 확인", "Primary와 Verifier의 판정 불일치",
    "판정 불일치 상태", "Primary/Verifier verdict 불일치",
})


def analyst_text(value, fallback="미기록"):
    if not isinstance(value, str) or not value.strip() or value.strip() in LEGACY_DIAGNOSTICS:
        return fallback
    return INPUT_LIMITATION if value == OLD_INPUT_LIMITATION else value


def assessment_view(result):
    """No raw input reads, role-result reads, inference or legacy rewrites."""
    assessment = result.get("analyst_assessment")
    if not isinstance(assessment, dict) or assessment.get("version") != "analyst-assessment-v1":
        return None
    evidence, seen, references = [], {}, {}
    for item in assessment.get("evidence", []) if isinstance(assessment.get("evidence"), list) else []:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("field", "excerpt", "interpretation_ko")):
            continue
        support = item.get("supports")
        support = support if isinstance(support, str) and support in EVIDENCE_LABELS else "unclassified"
        key = (item["field"], item["excerpt"], item["interpretation_ko"], support)
        if key not in seen:
            seen[key] = {"number": len(evidence) + 1, "field": item["field"], "excerpt": item["excerpt"],
                         "interpretation_ko": analyst_text(item["interpretation_ko"]), "supports": support}
            evidence.append(seen[key])
        reference = item.get("evidence_id")
        if isinstance(reference, str) and reference:
            # Ambiguous IDs cannot anchor a decision issue.
            references[reference] = seen[key]["number"] if reference not in references else None
    for item in evidence:
        item["related_numbers"] = [other["number"] for other in evidence
            if other["supports"] != item["supports"] and (other["field"], other["excerpt"]) == (item["field"], item["excerpt"])]
    issues = []
    for item in assessment.get("decision_issues", []) if isinstance(assessment.get("decision_issues"), list) else []:
        if not isinstance(item, dict) or not analyst_text(item.get("point_ko"), ""):
            continue
        ids = item.get("evidence_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) or not references.get(key) for key in ids):
            continue
        issue = {"point_ko": item["point_ko"], "evidence_numbers": list(dict.fromkeys(references[key] for key in ids)),
                 "missing_condition_ko": analyst_text(item.get("missing_condition_ko"), "") or None}
        if issue not in issues:
            issues.append(issue)
    return {"evidence": evidence, "issues": issues}
