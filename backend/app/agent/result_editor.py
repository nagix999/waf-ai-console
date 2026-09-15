"""Versioned extension of the optional editor; originals are never rewritten."""
from pydantic import BaseModel, ConfigDict, Field

from .contracts import AnalystCheck
from .evidence_editor import EvidenceGroup, INSTRUCTIONS as EVIDENCE_INSTRUCTIONS, _key, validate_groups

VERSION = "result-editor-v2"
CHECK_VERSION = "follow-up-editor-v1"
CHECK_FIELDS = ("source_ko", "check_ko", "why_ko")
INSTRUCTIONS = EVIDENCE_INSTRUCTIONS + """
추가 확인사항 checks도 정리한다. check_allowed_groups 안에서 같은 자료·대상·처리 경로·확인 조건을 점검하는 동일 작업만 묶는다.
자료가 같다는 이유만으로 묶지 않는다. 요청 수신 여부와 실제 실행 여부, 공격 판단 조건과 피해 확인, 서로 다른 기능·필드·설정·시간 범위는 별도 작업이다.
표현이나 확인 이유만 다르고 실제 점검 작업·판단 조건이 같은 경우 가장 구체적인 기존 항목의 번호를 대표로 선택한다.
모든 check_id를 정확히 한 번씩 check_groups의 member_ids에 포함한다. 대표 번호는 구성원이어야 한다.
확인 항목·설명·자료·조건을 새로 작성하거나 바꾸지 않는다. 의미가 같은지 확실하지 않으면 각각 남긴다.
근거는 groups, 확인사항은 check_groups로 분리하고 서로 섞지 않는다. 입력 목록이 비어 있으면 해당 그룹 목록도 빈 배열이다."""


class ResultEditorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    groups: list[EvidenceGroup] = Field(max_length=10)
    check_groups: list[EvidenceGroup] = Field(max_length=5)


def check_items(checks):
    if not isinstance(checks, list) or len(checks) > 5:
        raise ValueError("editor_checks_invalid")
    items = []
    for index, check in enumerate(checks):
        AnalystCheck.model_validate(check)
        items.append({"check_id": f"c{index + 1}", **{key: check[key] for key in CHECK_FIELDS}})
    return items


def check_buckets(items):
    buckets = {}
    for item in items:
        # Do not guess source aliases or normalize field/path values. The LLM
        # still must distinguish different tasks within an identical source.
        buckets.setdefault(item["source_ko"], []).append(item["check_id"])
    return list(buckets.values())


def editor_input(assessment, checks):
    evidence = assessment.get("evidence", []) if isinstance(assessment, dict) and assessment.get("version") == "analyst-assessment-v1" else []
    if not isinstance(evidence, list) or len(evidence) > 10:
        raise ValueError("editor_evidence_count_invalid")
    buckets = {}
    for item in evidence:
        buckets.setdefault(_key(item), []).append(item["evidence_id"])
    items = check_items(checks)
    allowed = list(buckets.values())
    check_allowed = check_buckets(items)
    if not any(len(group) > 1 for group in [*allowed, *check_allowed]):
        return None
    return {
        "evidence": [{key: item[key] for key in ("evidence_id", "field", "excerpt", "supports", "interpretation_ko")} for item in evidence],
        "allowed_groups": allowed, "checks": items, "check_allowed_groups": check_allowed,
    }


def validate_check_groups(checks, groups):
    items = check_items(checks)
    parsed = ResultEditorOutput.model_validate({"groups": [], "check_groups": groups}).check_groups
    indexed = {item["check_id"]: item for item in items}
    seen = set()
    for group in parsed:
        ids = group.member_ids
        if (len(ids) != len(set(ids)) or seen.intersection(ids) or not set(ids) <= indexed.keys()
                or group.representative_id not in ids):
            raise ValueError("editor_invalid_check_ids")
        if len({indexed[identifier]["source_ko"] for identifier in ids}) != 1:
            raise ValueError("editor_check_source_mismatch")
        seen.update(ids)
    if seen != indexed.keys():
        raise ValueError("editor_incomplete_check_coverage")
    order = {item["check_id"]: index for index, item in enumerate(items)}
    return sorted([group.model_dump() for group in parsed], key=lambda group: min(order[key] for key in group["member_ids"]))


def validate_result(assessment, checks, output):
    parsed = ResultEditorOutput.model_validate(output)
    evidence = assessment.get("evidence", []) if isinstance(assessment, dict) and assessment.get("version") == "analyst-assessment-v1" else []
    if evidence:
        evidence_groups = validate_groups(assessment, {"groups": [group.model_dump() for group in parsed.groups]})
    elif parsed.groups:
        raise ValueError("editor_unexpected_evidence_groups")
    else:
        evidence_groups = []
    check_groups = validate_check_groups(checks, [group.model_dump() for group in parsed.check_groups])
    return evidence_groups, check_groups


def present_checks(checks, presentation):
    """Apply only a complete mapping bound to the unchanged original list."""
    if not isinstance(presentation, dict) or presentation.get("version") != CHECK_VERSION or presentation.get("status") != "completed":
        return checks
    try:
        items = check_items(checks)
        if presentation.get("items") != items:
            return checks
        groups = validate_check_groups(checks, presentation.get("groups"))
        indexed = {item["check_id"]: item for item in items}
        return [{key: indexed[group["representative_id"]][key] for key in CHECK_FIELDS} for group in groups]
    except (ValueError, TypeError, KeyError):
        return checks
