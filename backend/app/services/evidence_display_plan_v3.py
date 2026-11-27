"""Same-excerpt display planning with cross-role ID equivalence, never text edits.

The source hash uses UTF-8 JSON with recursively sorted keys, unescaped Unicode,
compact separators and no NaN. The hashed document is explicitly constructed
in source_document(), shared with the frontend parity fixture. Group ordering
may rank existing IDs; source numbers always refer to canonical array indexes.
"""
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import hmac
import json

from pydantic import ValidationError

from ..agent.evidence_display import DisplayEditorOutput
from ..agent.evidence_editor import validate_groups as validate_legacy_groups
from ..agent.evidence_relations_v238 import validate_evidence_relations
from ..result_contracts import ResultEvidence, ResultIssue


VERSION = "evidence-display-plan-v3"
STATUSES = frozenset({"pending", "skipped", "completed", "fallback"})
SUPPORTS = frozenset({"attack_request", "normal_request", "context"})
EMPTY_RELATIONS = {"version": "evidence-relations-v2", "groups": []}
MAX_CORE_GROUPS = 3
REASONS = frozenset({
    "no_duplicate_candidates", "no_reduction", "queued", "processing", "pending",
    "disabled", "display_disabled", "not_configured", "not_completed", "not_applicable",
    "input_budget_exceeded", "editor_call_failed", "model_call_failed", "call_failed",
    "output_validation_failed", "editor_unavailable_or_invalid", "invalid_grouping",
    "invalid_group_output", "invalid_plan", "source_mismatch", "source_changed",
    "execution_failed", "interrupted", "snapshot_invalid", "profile_unavailable",
    "job_failed", "job_pending", "job_running", "legacy_mapping_incompatible",
    "display_plan_invalid", "display_groups_invalid", "display_source_invalid",
    "display_support_mismatch", "display_condition_mismatch", "display_dependency_mismatch",
    "display_profile_unavailable", "display_snapshot_invalid", "display_output_invalid",
    "display_call_failed", "display_input_budget_exceeded", "display_source_changed",
    "editor_disabled", "display_record_invalid", "display_lease_expired", "analysis_not_completed",
    "display_editor_unavailable", "display_editor_call_failed", "display_lease_lost",
    "display_source_unavailable", "display_input_already_dispatched", "display_editor_snapshot_invalid",
    "display_editor_approval_invalid", "display_editor_snapshot_conflict", "display_editor_implementation_unavailable",
    "display_timeout", "display_egress_blocked", "display_output_incomplete",
    "display_test_stopped", "display_worker_stopping",
    "display_source_mismatch",
})


class DisplayPlanError(ValueError):
    def __init__(self, code="display_plan_invalid"):
        self.code = code
        super().__init__(code)


def _canonical(result, relations):
    try:
        if not isinstance(result, Mapping) or result.get("schema_version") != "waf-analysis-v3":
            raise ValueError()
        evidence = result.get("evidence")
        if not isinstance(evidence, list) or len(evidence) > 10:
            raise ValueError()
        for item in evidence:
            ResultEvidence.model_validate(item)
        identifiers = [item["evidence_id"] for item in evidence]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError()
        issues = result.get("decision_issues", [])
        if not isinstance(issues, list) or len(issues) > 10:
            raise ValueError()
        for issue in issues:
            ResultIssue.model_validate(issue)
            if not set(issue["evidence_ids"]) <= set(identifiers):
                raise ValueError()
        policy = result.get("policy", {})
        decision_ids = policy.get("decision_evidence_ids", []) if isinstance(policy, Mapping) else None
        if (not isinstance(decision_ids, list) or len(decision_ids) > 5
                or any(not isinstance(value, str) or value not in identifiers for value in decision_ids)):
            raise ValueError()
        if result.get("verdict") not in {"true_positive", "false_positive", "inconclusive"}:
            raise ValueError()
        validated = validate_evidence_relations(EMPTY_RELATIONS if relations is None else relations, evidence)
        return evidence, issues, decision_ids, validated
    except (ValueError, TypeError, KeyError, ValidationError):
        raise DisplayPlanError("display_source_invalid") from None


def source_document(result, relations=None):
    evidence, issues, decision_ids, validated = _canonical(result, relations)
    return deepcopy({"evidence": evidence, "decision_issues": issues,
                     "role_decision_issues": _role_issues(result, evidence),
                     "policy": {"decision_evidence_ids": decision_ids},
                     "verdict": result["verdict"], "relations": validated})


def _role_issues(result, evidence):
    """Bind role-local issue text too; different issue numbers are not semantics."""
    identifiers = {item["evidence_id"] for item in evidence}
    roles = {"primary": result.get("primary"),
             "verifier": (result.get("verifier") or {}).get("output")
             if isinstance(result.get("verifier"), Mapping) else None}
    value = {}
    try:
        for role, output in roles.items():
            if output is not None and not isinstance(output, Mapping):
                raise ValueError()
            issues = output.get("decision_issues", []) if output is not None else []
            if not isinstance(issues, list) or len(issues) > 2:
                raise ValueError()
            for issue in issues:
                parsed = ResultIssue.model_validate(issue)
                if not set(parsed.evidence_ids) <= identifiers:
                    raise ValueError()
            value[role] = deepcopy(issues)
        return value
    except (ValueError, TypeError, KeyError, ValidationError):
        raise DisplayPlanError("display_source_invalid") from None


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise DisplayPlanError("display_source_invalid") from None


def source_hash(result, relations=None):
    return hashlib.sha256(_encoded(source_document(result, relations))).hexdigest()


def _physical_source(item):
    field = item["field"].strip().removeprefix("event.")
    if field == "raw_payload":
        field = "payload"
    spans = tuple(sorted({(span["start"], span["end"]) for span in item.get("source_spans", [])}))
    return field, item["excerpt"], spans


def _located_source(item):
    """Empty legacy positions cannot certify a same-occurrence replacement."""
    spans = item.get("source_spans")
    if not isinstance(spans, list) or not spans:
        return None
    if any(not isinstance(span, Mapping) or type(span.get("start")) is not int
           or type(span.get("end")) is not int or span["start"] < 0
           or span["end"] <= span["start"] for span in spans):
        return None
    return _physical_source(item)


def _roles(item):
    return frozenset(origin["role"] for origin in item["origins"])


def _constraints(evidence, issues, relations):
    indexed = {item["evidence_id"]: item for item in evidence}
    dependencies = {identifier: set() for identifier in indexed}
    for relation in relations["groups"]:
        dependencies[relation["representative_evidence_id"]].update(relation["related_evidence_ids"])
    # A linked representative can itself depend on other cited material. Keep
    # the full bounded closure; cycles cannot make traversal unbounded.
    for identifier in indexed:
        pending, reached = list(dependencies[identifier]), set()
        while pending:
            other = pending.pop()
            if other == identifier or other in reached:
                continue
            reached.add(other)
            pending.extend(dependencies[other] - reached)
        dependencies[identifier] = reached
    issue_memberships = {
        identifier: frozenset(index for index, issue in enumerate(issues) if identifier in issue["evidence_ids"])
        for identifier in indexed
    }
    dependency_sources = {
        identifier: frozenset(((_located_source(indexed[other]) or ("unlocated", other)),
                               indexed[other]["interpretation_ko"], indexed[other]["supports"])
                              for other in dependencies[identifier])
        for identifier in indexed
    }
    return indexed, dependencies, issue_memberships, dependency_sources


def _merge_key(item, memberships, dependencies):
    identifier = item["evidence_id"]
    located = _located_source(item)
    return (located if located is not None else ("unlocated", identifier),
            item["supports"], dependencies[identifier])


def _allowed_groups(evidence, memberships, dependencies):
    buckets = {}
    indexed = {item["evidence_id"]: item for item in evidence}
    for item in evidence:
        buckets.setdefault(_merge_key(item, memberships, dependencies), []).append(item["evidence_id"])
    groups = []
    for ids in buckets.values():
        roles = set().union(*(_roles(indexed[identifier]) for identifier in ids))
        if len(roles) > 1:
            # Source, contribution and support are already exact. Only the LLM
            # may decide whether the two independent interpretations are equal.
            groups.append(ids)
        else:
            by_membership = {}
            for identifier in ids:
                by_membership.setdefault(memberships[identifier], []).append(identifier)
            groups.extend(by_membership.values())
    return groups


def _check_groups(evidence, groups, memberships, dependencies):
    if not evidence and groups == []:
        return []
    try:
        parsed = DisplayEditorOutput.model_validate({"groups": groups})
    except (ValueError, TypeError, ValidationError):
        raise DisplayPlanError("display_groups_invalid") from None
    indexed = {item["evidence_id"]: item for item in evidence}
    seen, result = set(), []
    for group in parsed.groups:
        ids = group.member_ids
        if (len(set(ids)) != len(ids) or seen.intersection(ids)
                or not set(ids) <= indexed.keys() or group.representative_id not in ids):
            raise DisplayPlanError("display_groups_invalid")
        if len({indexed[identifier]["supports"] for identifier in ids}) != 1:
            raise DisplayPlanError("display_support_mismatch")
        if len(ids) > 1:
            sources = [_located_source(indexed[identifier]) for identifier in ids]
            if any(source is None for source in sources) or len(set(sources)) != 1:
                raise DisplayPlanError("display_source_mismatch")
            roles = set().union(*(_roles(indexed[identifier]) for identifier in ids))
            if len(roles) == 1 and len({memberships[identifier] for identifier in ids}) != 1:
                raise DisplayPlanError("display_condition_mismatch")
        if len({dependencies[identifier] for identifier in ids}) != 1:
            raise DisplayPlanError("display_dependency_mismatch")
        seen.update(ids)
        result.append({"representative_id": group.representative_id,
                       "member_ids": [item["evidence_id"] for item in evidence if item["evidence_id"] in ids]})
    if seen != indexed.keys():
        raise DisplayPlanError("display_groups_invalid")
    return result


def _exact_groups(evidence, memberships, dependencies):
    groups, indexed = [], {item["evidence_id"]: item for item in evidence}
    for eligible in _allowed_groups(evidence, memberships, dependencies):
        by_text = {}
        for identifier in eligible:
            item = indexed[identifier]
            group = by_text.get(item["interpretation_ko"])
            if group is None:
                group = {"representative_id": identifier, "member_ids": []}
                by_text[item["interpretation_ko"]] = group
                groups.append(group)
            group["member_ids"].append(identifier)
    verified = []
    for group in groups:
        roles = set().union(*(_roles(indexed[identifier]) for identifier in group["member_ids"]))
        if len(roles) > 1:
            verified.append(group)
            continue
        # A mixed-role candidate bucket can contain an exact-text subgroup
        # contributed by only one role. Keep its original issue-membership
        # boundary even when the other role has a different interpretation.
        by_membership = {}
        for identifier in group["member_ids"]:
            target = by_membership.setdefault(memberships[identifier],
                {"representative_id": identifier, "member_ids": []})
            target["member_ids"].append(identifier)
        verified.extend(by_membership.values())
    order = {item["evidence_id"]: index for index, item in enumerate(evidence)}
    return sorted(verified, key=lambda group: order[group["representative_id"]])


def _legacy_groups(result, evidence, memberships, dependencies):
    presentation = result.get("evidence_presentation")
    if (not isinstance(presentation, Mapping) or presentation.get("version") != "evidence-editor-v1"
            or presentation.get("status") != "completed"):
        return None
    try:
        groups = validate_legacy_groups({"version": "analyst-assessment-v1", "evidence": evidence},
                                        {"groups": presentation.get("groups")})
        return _check_groups(evidence, groups, memberships, dependencies)
    except (ValueError, KeyError, TypeError):
        return None  # Invalid old metadata may never conceal canonical sources.


def build_editor_input(result, relations=None):
    evidence, issues, _, validated = _canonical(result, relations)
    _, dependencies, memberships, dependency_sources = _constraints(evidence, issues, validated)
    buckets = _allowed_groups(evidence, memberships, dependency_sources)
    indexed = {item["evidence_id"]: item for item in evidence}
    if not any(len({(_physical_source(indexed[identifier]), indexed[identifier]["interpretation_ko"])
                    for identifier in ids}) > 1 for ids in buckets):
        return None
    return {"evidence": deepcopy(evidence), "allowed_groups": buckets,
            "relations": validated, "decision_issues": deepcopy(issues), "verdict": result["verdict"],
            "role_decision_issues": _role_issues(result, evidence),
            "required_source_ids": {key: [item["evidence_id"] for item in evidence
                                          if item["evidence_id"] in value]
                                    for key, value in dependencies.items() if value}}


def build_display_plan(result, relations=None, groups=None, status="skipped", reason=None, *, group_output=None):
    evidence, issues, _, validated = _canonical(result, relations)
    if (not isinstance(status, str) or status not in STATUSES
            or (reason is not None and (not isinstance(reason, str) or reason not in REASONS))):
        raise DisplayPlanError()
    indexed, dependencies, memberships, dependency_sources = _constraints(evidence, issues, validated)
    if groups is not None and group_output is not None:
        raise DisplayPlanError("display_groups_invalid")
    if group_output is not None:
        try:
            parsed = DisplayEditorOutput.model_validate(group_output)
            groups = parsed.model_dump(mode="json")["groups"]
        except (ValueError, TypeError, ValidationError):
            raise DisplayPlanError("display_groups_invalid") from None
    if status == "fallback":
        chosen = [{"representative_id": item["evidence_id"], "member_ids": [item["evidence_id"]]} for item in evidence]
    elif groups is not None:
        chosen = _check_groups(evidence, groups, memberships, dependency_sources)
    else:
        chosen = (_legacy_groups(result, evidence, memberships, dependency_sources)
                  or _exact_groups(evidence, memberships, dependency_sources))
    if status == "completed" and len(chosen) == len(evidence) and reason is None:
        reason = "no_reduction"
    if status == "skipped" and reason is None:
        reason = "no_duplicate_candidates"
    numbers = {item["evidence_id"]: index + 1 for index, item in enumerate(evidence)}
    directional_rank = 0
    display = []
    for group in chosen:
        representative, members = group["representative_id"], group["member_ids"]
        support = indexed[representative]["supports"]
        required = set().union(*(dependencies[identifier] for identifier in members)) - set(members)
        related = [item["evidence_id"] for item in evidence if item["evidence_id"] in required]
        reasons = []
        if status == "fallback":
            reasons.append("fallback_original")
        else:
            if support != "context":
                directional_rank += 1
                if directional_rank <= MAX_CORE_GROUPS:
                    reasons.append("rank_top")
            if any(memberships[identifier] for identifier in members):
                reasons.append("decision_issue")
            if ((result["verdict"] == "true_positive" and support == "normal_request")
                    or (result["verdict"] == "false_positive" and support == "attack_request")):
                reasons.append("counter_evidence")
            if result["verdict"] == "inconclusive" and support != "context":
                reasons.append("inconclusive_directional")
        visible = bool(reasons)
        if not reasons:
            reasons.append("context_auxiliary" if support == "context" else "remaining_evidence")
        number = numbers[representative]
        display.append({"group_id": f"g{number}", "representative_id": representative,
                        "member_ids": members, "related_ids": related, "supports": support,
                        "number": number, "default_visible": visible, "visibility_reasons": reasons})
    return {"version": VERSION, "source_hash": source_hash(result, validated), "status": status, "reason": reason,
            "groups": display, "metrics": {"original_evidence_count": len(evidence), "group_count": len(display),
                "core_group_count": sum(group["default_visible"] for group in display),
                "merged_count": len(evidence) - len(display)}}


def validate_display_plan(result, relations, plan):
    """Recompute the entire map; a matching hash alone is not authorization."""
    try:
        if not isinstance(plan, Mapping) or set(plan) != {"version", "source_hash", "status", "reason", "groups", "metrics"}:
            raise ValueError()
        if plan.get("version") != VERSION or not isinstance(plan.get("source_hash"), str):
            raise ValueError()
        if not hmac.compare_digest(plan["source_hash"], source_hash(result, relations)):
            raise ValueError()
        groups = [{"member_ids": group["member_ids"], "representative_id": group["representative_id"]}
                  for group in plan["groups"]]
        expected = build_display_plan(result, relations, groups=groups,
                                      status=plan["status"], reason=plan["reason"])
        if _encoded(dict(plan)) != _encoded(expected):
            raise ValueError()
        return expected
    except (ValueError, TypeError, KeyError, ValidationError):
        raise DisplayPlanError() from None

