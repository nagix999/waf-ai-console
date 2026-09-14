"""Grounded presentation record, independent of final verdict/evidence policy.

Only the last successful, source-resolved call contributes. A role's verdict
never determines evidence direction. Raw model assessments stay in encrypted
step history; rejected citations and disconnected issues do not enter this view.
"""

VERSION = "analyst-assessment-v1"
RULES_VERSIONS = frozenset({"waf-system-v2.10", "waf-system-v2.11", "waf-system-v2.12"})


def build_analyst_assessment(primary, verifier=None):
    evidence, issues, seen = [], [], {}
    omitted_issues = 0
    for role, call in (("primary", primary), ("verifier", verifier)):
        if not call or not call.succeeded or not call.grounding_history:
            continue
        history = call.grounding_history[-1]
        assessment = history.get("assessment")
        if not assessment:
            continue
        accepted = {(item.field, item.excerpt, item.interpretation_ko) for item in call.output.evidence}
        resolved = (history.get("resolved_output") or {}).get("evidence", [])
        selections = assessment["evidence"]
        references = {}
        for source, item in zip(history.get("source_resolutions", []), resolved, strict=True):
            index = source["index"]
            key = (item["field"], item["excerpt"], item["interpretation_ko"])
            if key not in accepted:
                continue
            support = selections[index]["supports"]
            identity = (*key, support)
            origin = {"role": role, "index": index, "source_id": source["source_id"]}
            if identity not in seen:
                seen[identity] = {**item, "supports": support, "evidence_id": f"e{len(evidence) + 1}", "origins": []}
                evidence.append(seen[identity])
            entry = seen[identity]
            if origin not in entry["origins"]:
                entry["origins"].append(origin)
            references[index] = entry["evidence_id"]
        issue = assessment.get("decision_issue")
        if not issue:
            continue
        indexes = issue["evidence_indexes"]
        if any(index not in references for index in indexes) or call.telemetry.get("evidence_grounding_retry", {}).get("requires_review"):
            omitted_issues += 1
            continue
        # Decisive outputs cannot invent a missing prerequisite after the server
        # combines them. Preserve their neutral point, not a manufactured cause.
        missing = issue["missing_condition_ko"] if history["resolved_output"]["verdict"] == "inconclusive" else None
        entry = {"point_ko": issue["point_ko"],
                 "evidence_ids": list(dict.fromkeys(references[index] for index in indexes)),
                 "missing_condition_ko": missing}
        previous = next((item for item in issues if all(item[key] == value for key, value in entry.items())), None)
        if previous:
            previous["origins"].append(role)
        else:
            issues.append({**entry, "origins": [role]})
    return {"version": VERSION, "evidence": evidence, "decision_issues": issues, "omitted_issue_count": omitted_issues}
