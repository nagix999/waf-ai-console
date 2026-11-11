"""Allowlisted, read-only analyst display; parity tested with the web view."""

EVIDENCE_LABELS = {"attack_request": "공격 요청 근거", "normal_request": "정상 요청 근거", "context": "참고 내용", "unclassified": "구분 미기록"}
EVIDENCE_NOTICE = "로그에서 확인한 발췌와 그에 대한 해석입니다. 근거 개수로 판정을 결정하지 않습니다."
LEGACY_EVIDENCE_NOTICE = "기존 분석에는 공격·정상 요청 근거 구분이 기록되지 않았습니다."
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


def clean_display_view(result, display_view):
    """Allowlist an administrator sidecar before exports or renderer IPC."""
    if not isinstance(display_view, dict) or result.get("schema_version") != "waf-analysis-v3":
        return None
    from ..agent.evidence_relations import validate_evidence_relations
    from .evidence_display_plan import build_display_plan, validate_display_plan
    try:
        relations = validate_evidence_relations(display_view.get("relations") or {
            "version": "evidence-relations-v1", "groups": []}, result["evidence"])
        plan = validate_display_plan(result, relations, display_view["plan"])
        status = display_view.get("status")
        if status not in {"disabled", "pending", "running", "completed", "skipped", "fallback"}:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        # Invalid presentation metadata must not conceal a single source.
        relations = {"version": "evidence-relations-v1", "groups": []}
        try:
            plan = build_display_plan(result, relations, status="fallback", reason="display_record_invalid")
        except (ValueError, TypeError, KeyError):
            return None
        status = "fallback"
    return {"version": "evidence-display-view-v1", "status": status,
            "relations": relations, "plan": plan, "metadata": {}}


def _planned_assessment(result, display_view):
    clean = clean_display_view(result, display_view)
    if clean is None:
        return None
    plan = clean["plan"]
    indexed = {item["evidence_id"]: item for item in result["evidence"]}
    references = {identifier: group["number"] for group in plan["groups"] for identifier in group["member_ids"]}
    evidence = []
    for group in plan["groups"]:
        original = indexed[group["representative_id"]]
        additional, seen = [], set()
        def source_key(item):
            return item["field"], item["excerpt"], tuple((span["start"], span["end"]) for span in item.get("source_spans", []))
        seen.add(source_key(original))
        for identifier in [*group["member_ids"], *group["related_ids"]]:
            item = indexed[identifier]
            key = source_key(item)
            if key in seen:
                continue
            seen.add(key)
            additional.append({"evidence_id": identifier, "field": item["field"], "excerpt": item["excerpt"],
                               "source_spans": item.get("source_spans", [])})
        evidence.append({"number": group["number"], "field": original["field"], "excerpt": original["excerpt"],
            "interpretation_ko": analyst_text(original["interpretation_ko"]), "supports": group["supports"],
            "evidence_id": original["evidence_id"], "group_id": group["group_id"], "member_ids": group["member_ids"],
            "related_sources": additional, "default_visible": group["default_visible"], "related_numbers": []})
    for item in evidence:
        item["related_numbers"] = [other["number"] for other in evidence if other["supports"] != item["supports"]
            and (other["field"], other["excerpt"]) == (item["field"], item["excerpt"])]
    issues = []
    for issue in result.get("decision_issues", []):
        ids = issue["evidence_ids"]
        if not ids or any(identifier not in references for identifier in ids):
            continue
        row = {"point_ko": issue["point_ko"],
               "evidence_numbers": list(dict.fromkeys(references[identifier] for identifier in ids)),
               "missing_condition_ko": analyst_text(issue.get("missing_condition_ko"), "") or None}
        if row not in issues:
            issues.append(row)
    return {"evidence": evidence, "issues": issues, "display_plan": plan}


def _core_assessment(result):
    """Format the already-projected API list without a second selection pass.

    Stable evidence IDs, not the position in the shortened response, identify
    references. Two different original objects must not be silently merged by
    a report formatter, even if their excerpts happen to be identical.
    """
    evidence = [{"number": int(item["evidence_id"][1:]),
                 "evidence_id": item["evidence_id"], "field": item["field"],
                 "excerpt": item["excerpt"], "interpretation_ko": item["interpretation_ko"],
                 "supports": item["supports"], "related_numbers": []}
                for item in result["evidence"]]
    numbers = {item["evidence_id"]: item["number"] for item in evidence}
    issues = [{"point_ko": issue["point_ko"],
               "evidence_numbers": [numbers[identifier] for identifier in issue["evidence_ids"]],
               "missing_condition_ko": issue.get("missing_condition_ko")}
              for issue in result.get("decision_issues", [])]
    return {"evidence": evidence, "issues": issues}


def assessment_view(result, *, display_view=None, evidence_view=None):
    """No raw input reads, role-result reads, inference or legacy rewrites."""
    if evidence_view == "core-v1" and result.get("schema_version") == "waf-analysis-v3":
        return _core_assessment(result)
    if display_view is not None and result.get("schema_version") == "waf-analysis-v3":
        planned = _planned_assessment(result, display_view)
        if planned is not None:
            return planned
    assessment = ({"version": "analyst-assessment-v1", "evidence": result.get("evidence"),
                   "decision_issues": result.get("decision_issues")}
                  if result.get("schema_version") == "waf-analysis-v3" else result.get("analyst_assessment"))
    if not isinstance(assessment, dict) or assessment.get("version") != "analyst-assessment-v1":
        return None
    items = assessment.get("evidence", []) if isinstance(assessment.get("evidence"), list) else []
    presentation = result.get("evidence_presentation")
    if isinstance(presentation, dict) and presentation.get("version") == "evidence-editor-v1" and presentation.get("status") == "completed":
        from ..agent.evidence_editor import validate_groups
        try:
            groups = validate_groups(assessment, {"groups": presentation.get("groups")})
            indexed = {item["evidence_id"]: item for item in items}
            items = [{**indexed[group["representative_id"]], "_evidence_ids": group["member_ids"]} for group in groups]
        except (ValueError, TypeError, KeyError):
            pass  # Corrupt/legacy grouping must not hide original evidence.
    evidence, seen, references = [], {}, {}
    for item in items:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("field", "excerpt", "interpretation_ko")):
            continue
        support = item.get("supports")
        support = {"true_positive": "attack_request", "false_positive": "normal_request"}.get(support, support) if isinstance(support, str) else None
        support = support if isinstance(support, str) and support in EVIDENCE_LABELS else "unclassified"
        key = (item["field"], item["excerpt"], item["interpretation_ko"], support)
        if key not in seen:
            seen[key] = {"number": len(evidence) + 1, "field": item["field"], "excerpt": item["excerpt"],
                         "interpretation_ko": analyst_text(item["interpretation_ko"]), "supports": support}
            evidence.append(seen[key])
        for reference in item.get("_evidence_ids", [item.get("evidence_id")]):
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
