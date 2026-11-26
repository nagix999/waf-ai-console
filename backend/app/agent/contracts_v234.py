"""Flat, bounded model evidence with server-resolved interpretation links.

Related references may form cycles; a bounded visited-set traversal preserves
all original edges while checking every reachable support classification.
Local evidence IDs identify interpretations, not source excerpts. The same
excerpt may support distinct interpretations without losing either one. Model
IDs are removed by the existing internal bridge before public v3 adaptation.
Source-support violations remain strict unless the grounded caller explicitly
routes them to the existing source-only correction and safe-hold path.
"""
from copy import deepcopy
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, PrivateAttr, ValidationInfo, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from .contracts import AgentVerdict, RequestEvidenceSupport, StrictContract, ThreatAnalysis
from .contracts_v224 import CaptureAssessment, SecurityAnalysisOutputV224
from .contracts_v227 import (
    CONTRACT_ERRORS as V227_CONTRACT_ERRORS, SignatureComparison, SourceId,
    has_directional_content, is_request_content,
)
from .evidence_source_policy_v234 import source_support_issues

VERSION = "evidence-interpretation-v6"
CONTRACT_ERRORS = {
    **V227_CONTRACT_ERRORS,
    "decision_review_context_invalid": ("$", "서버가 고정한 검토 기준과 원문 후보를 확인할 수 없습니다."),
    "review_evidence_repeated": ("retained_evidence_ids", "검증된 근거 번호를 중복하지 마세요."),
    "review_evidence_not_eligible": ("retained_evidence_ids", "서버가 검토 대상으로 고정한 근거 번호만 사용하세요."),
    "review_evidence_omitted": ("retained_evidence_ids", "검증된 근거를 하나도 빼지 말고 유지하세요. 반대 방향 근거도 보존해야 합니다."),
    "review_evidence_dependency_missing": ("retained_evidence_ids", "해석에 필요한 관련 근거를 모두 유지하세요."),
    "evidence_text_required": ("evidence", "보안상 의미와 관련 원문의 기여를 공백 없이 작성하세요."),
    "evidence_id_repeated": ("evidence", "근거마다 서로 다른 evidence_id를 사용하세요."),
    "evidence_link_repeated": ("evidence.related_evidence_ids", "같은 관련 근거 번호를 중복하지 마세요."),
    "evidence_link_unknown": ("evidence.related_evidence_ids", "이번 evidence 목록에 있는 근거 번호만 연결하세요."),
    "evidence_link_self": ("evidence.related_evidence_ids", "근거를 자기 자신에게 연결하지 마세요."),
    "evidence_support_conflict": ("evidence", "관련 번호는 해당 해석의 성립에 필요한 근거만 연결하세요. 같은 주제나 반대 의견이라는 이유로 연결하지 마세요. 독립적인 공격/정상 근거와 공통 참고 내용은 유지하세요."),
    "evidence_source_unavailable": ("evidence", "실제 제공된 원문 후보의 source_id만 선택하세요."),
    "evidence_source_support_forbidden": ("evidence", "원문 후보의 allowed_supports를 지키세요. 시그니처와 등록 설명은 context 전용이며 해당 설명만으로 공격/정상 근거를 만들지 마세요."),
    "decisive_verdict_requires_directional_content": ("evidence", "확정 판정과 같은 방향의 요청 내용 해석이 필요합니다. 원문으로 해석과 supports 및 판정을 함께 재검토하세요. 분류만 바꾸거나 근거를 만들어 채우지 마세요."),
    "decision_evidence_unselected": ("decision_issue.evidence_ids", "확인 조건에는 이번 evidence 목록에 있는 근거 번호만 연결하세요."),
    "decision_evidence_repeated": ("decision_issue.evidence_ids", "확인 조건의 근거 번호를 중복하지 마세요."),
}


def error(code, context=None):
    return PydanticCustomError(code, CONTRACT_ERRORS[code][1], context)


def nonblank(value):
    if not value.strip():
        raise error("evidence_text_required")
    return value


EvidenceId = Literal["e1", "e2", "e3", "e4", "e5"]
Interpretation = Annotated[str, Field(min_length=1, max_length=2000, strict=True), AfterValidator(nonblank)]


REFERENCE_NORMALIZATION_VERSION = "evidence-reference-normalization-v1"
RELATED_REFERENCE_DUPLICATES = "evidence_link_duplicates_removed"
DECISION_REFERENCE_DUPLICATES = "decision_evidence_duplicates_removed"
NORMALIZATION_CODES = frozenset({RELATED_REFERENCE_DUPLICATES, DECISION_REFERENCE_DUPLICATES})
_EVIDENCE_IDS = frozenset({"e1", "e2", "e3", "e4", "e5"})
_SUPPORT_VALUES = frozenset({"attack_request", "normal_request", "context"})
_SOURCE_ID = re.compile(r"c[1-9][0-9]{0,2}\Z")


def safe_conflict_paths(value, *, selected_source_ids=None):
    """Accept only bounded, typed identities; never model or exception text.

    Diagnostics describe one rejected complete response, not instructions to
    edit IDs in the next regenerated response. A caller with a trusted source
    catalog must additionally restrict every source ID to that catalog.
    """
    if type(value) is not list or not 1 <= len(value) <= 2:
        return []
    result = []
    for path in value:
        if type(path) is not list or not 1 <= len(path) <= 5:
            return []
        nodes, seen = [], set()
        for node in path:
            if type(node) is not dict or set(node) != {"evidence_id", "source_id", "supports"}:
                return []
            identifier, source, support = node["evidence_id"], node["source_id"], node["supports"]
            if (type(identifier) is not str or identifier not in _EVIDENCE_IDS or identifier in seen
                    or type(source) is not str or _SOURCE_ID.fullmatch(source) is None
                    or type(support) is not str or support not in _SUPPORT_VALUES
                    or selected_source_ids is not None and source not in selected_source_ids):
                return []
            seen.add(identifier)
            nodes.append({"evidence_id": identifier, "source_id": source, "supports": support})
        result.append(nodes)
    return result


def verified_conflict_paths(value, evidence, *, selected_source_ids=None):
    """Require the diagnostic to prove actual edges in this one response."""
    paths = safe_conflict_paths(value, selected_source_ids=selected_source_ids)
    if len(paths) != 2 or paths[0][0] != paths[1][0]:
        return []
    if {path[-1]["supports"] for path in paths} != {"attack_request", "normal_request"}:
        return []
    if type(evidence) is not list or not 1 <= len(evidence) <= 5:
        return []
    by_id = {}
    for item in evidence:
        if type(item) is not dict:
            return []
        identifier = item.get("evidence_id")
        if type(identifier) is not str or identifier not in _EVIDENCE_IDS or identifier in by_id:
            return []
        by_id[identifier] = item
    for path in paths:
        for index, node in enumerate(path):
            item = by_id.get(node["evidence_id"])
            if item is None or any(type(item.get(key)) is not str or item[key] != value
                                   for key, value in node.items()):
                return []
            if index + 1 < len(path):
                refs = item.get("related_evidence_ids")
                if (type(refs) is not list or len(refs) > 4
                        or any(type(key) is not str or key not in by_id for key in refs)
                        or path[index + 1]["evidence_id"] not in refs):
                    return []
    return paths


def _conflict_diagnostic(by_id, paths, catalog):
    """Create diagnostics from already field-validated evidence identities."""
    selected_sources = None
    if catalog is not None:
        if type(catalog) is not dict or type(catalog.get("items")) is not list:
            return {}
        selected_sources = {item.get("source_id") for item in catalog["items"]
                            if type(item) is dict and type(item.get("source_id")) is str}
    nodes = [[{"evidence_id": key, "source_id": by_id[key].source_id,
               "supports": by_id[key].supports.value} for key in path] for path in paths]
    safe = safe_conflict_paths(nodes, selected_source_ids=selected_sources)
    return {"conflict_paths": safe} if safe else {}


def normalize_duplicate_references(value: Any) -> tuple[Any, tuple[str, ...]]:
    """Deduplicate known references only, retaining every selected evidence.

    The raw size and field-type limits are checked before any normalization.
    Unknown, self or ambiguous declaration references are never inferred or
    removed. A changed document is a deep copy; unchanged input keeps identity.
    Full domain and source validation must still run on the returned document.
    """
    if type(value) is not dict:
        return value, ()
    evidence = value.get("evidence")
    if type(evidence) is not list or len(evidence) > 5:
        return value, ()
    selected = set()
    for item in evidence:
        if type(item) is not dict:
            return value, ()
        identifier = item.get("evidence_id")
        if type(identifier) is not str or identifier not in _EVIDENCE_IDS or identifier in selected:
            return value, ()
        selected.add(identifier)
    related_updates = {}
    for index, item in enumerate(evidence):
        refs = item.get("related_evidence_ids")
        if (type(refs) is not list or len(refs) > 4
                or any(type(key) is not str or key not in selected
                       or key == item["evidence_id"] for key in refs)):
            return value, ()
        unique = list(dict.fromkeys(refs))
        if unique != refs:
            related_updates[index] = unique
    decision_update = None
    issue = value.get("decision_issue")
    if issue is not None:
        if type(issue) is not dict:
            return value, ()
        refs = issue.get("evidence_ids")
        if (type(refs) is not list or not 1 <= len(refs) <= 5
                or any(type(key) is not str or key not in selected for key in refs)):
            return value, ()
        unique = list(dict.fromkeys(refs))
        if unique != refs:
            decision_update = unique
    if not related_updates and decision_update is None:
        return value, ()
    candidate = deepcopy(value)
    for index, refs in related_updates.items():
        candidate["evidence"][index]["related_evidence_ids"] = refs
    if decision_update is not None:
        candidate["decision_issue"]["evidence_ids"] = decision_update
    codes = ((RELATED_REFERENCE_DUPLICATES,) if related_updates else ())
    if decision_update is not None:
        codes += (DECISION_REFERENCE_DUPLICATES,)
    return candidate, codes


class FlatEvidenceInterpretation(StrictContract):
    evidence_id: EvidenceId
    source_id: SourceId
    interpretation_ko: Interpretation
    supports: RequestEvidenceSupport
    related_evidence_ids: list[EvidenceId] = Field(max_length=4)

    @model_validator(mode="after")
    def distinct_links(self):
        if len(self.related_evidence_ids) != len(set(self.related_evidence_ids)):
            raise error("evidence_link_repeated")
        if self.evidence_id in self.related_evidence_ids:
            raise error("evidence_link_self")
        return self


class DecisionCondition(StrictContract):
    point_ko: Annotated[str, Field(min_length=1, max_length=400), AfterValidator(nonblank)]
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=5)
    missing_condition_ko: Annotated[str, Field(max_length=500), AfterValidator(nonblank)] | None

    @model_validator(mode="after")
    def unique_interpretations(self):
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise error("decision_evidence_repeated")
        return self


class SecurityAnalysisOutputV234(StrictContract):
    evidence: list[FlatEvidenceInterpretation] = Field(max_length=5)
    signature_assessment: SignatureComparison
    capture_assessment: CaptureAssessment
    interpretation_conflict: bool = Field(strict=True)
    summary_ko: Annotated[str, Field(min_length=1, max_length=3000), AfterValidator(nonblank)]
    verdict: AgentVerdict
    confidence_score: float = Field(ge=0, le=1)
    threat_analysis: ThreatAnalysis
    decision_issue: DecisionCondition | None

    @model_validator(mode="before")
    @classmethod
    def normalize_references(cls, value):
        return normalize_duplicate_references(value)[0]

    def expanded_evidence(self):
        """Assign positional indexes without changing text, order or direction."""
        positions = {item.evidence_id: index for index, item in enumerate(self.evidence)}
        evidence = [{"source_id": item.source_id, "interpretation_ko": item.interpretation_ko,
                     "supports": item.supports.value} for item in self.evidence]
        relations = [{"representative_index": index,
                      "related_indexes": [positions[key] for key in item.related_evidence_ids]}
                     for index, item in enumerate(self.evidence) if item.related_evidence_ids]
        return evidence, relations

    def to_selection(self, *, input_truncated):
        evidence, relations = self.expanded_evidence()
        issue = None
        if self.decision_issue is not None:
            positions = {item.evidence_id: index for index, item in enumerate(self.evidence)}
            issue = {"point_ko": self.decision_issue.point_ko,
                     "missing_condition_ko": self.decision_issue.missing_condition_ko,
                     "evidence_indexes": [positions[key] for key in self.decision_issue.evidence_ids]}
        return SecurityAnalysisOutputV224.model_validate({
            **self.model_dump(mode="json", exclude={"evidence", "signature_assessment", "decision_issue"}),
            "evidence": evidence, "evidence_relations": relations, "decision_issue": issue,
            "signature_assessment": self.signature_assessment.expanded().model_dump(mode="json"),
            "input_truncated": input_truncated,
        })

    @model_validator(mode="after")
    def consistent_output(self, info: ValidationInfo):
        by_id = {item.evidence_id: item for item in self.evidence}
        if len(by_id) != len(self.evidence):
            raise error("evidence_id_repeated")
        for item in self.evidence:
            if not set(item.related_evidence_ids) <= by_id.keys():
                raise error("evidence_link_unknown")
        if self.decision_issue and not set(self.decision_issue.evidence_ids) <= by_id.keys():
            raise error("decision_evidence_unselected")

        # Direction checks include transitive dependencies. Shared context is
        # allowed between independent opposite interpretations; edges never
        # imply that two such interpretations are one merged claim.
        def visit(key):
            # At most five selected nodes, irrespective of how many cycles or
            # shared dependencies appear. Traversal does not rewrite any edge.
            seen, pending = {}, [(key, [key])]
            while pending:
                current, path = pending.pop(0)
                if current in seen:
                    continue
                seen[current] = path
                pending.extend((related, [*path, related])
                               for related in by_id[current].related_evidence_ids if related not in seen)
            return seen

        for key in by_id:
            paths = visit(key)
            directions = {by_id[related].supports for related in paths}
            if {RequestEvidenceSupport.attack_request, RequestEvidenceSupport.normal_request} <= directions:
                conflicting = [next(path for related, path in paths.items() if by_id[related].supports == direction)
                               for direction in (RequestEvidenceSupport.attack_request,
                                                 RequestEvidenceSupport.normal_request)]
                raise error("evidence_support_conflict", _conflict_diagnostic(
                    by_id, conflicting, (info.context or {}).get("source_catalog")))

        direction = {AgentVerdict.true_positive: "attack_request", AgentVerdict.false_positive: "normal_request"}.get(self.verdict)
        if direction and not any(item.supports == direction for item in self.evidence):
            raise error("decisive_verdict_requires_directional_content")
        catalog = (info.context or {}).get("source_catalog")
        if catalog is not None:
            by_source = {item["source_id"]: item for item in catalog["items"]}
            # Check every source before classifying any support restriction:
            # an earlier forbidden selection must not hide an unknown ID.
            if any(item.source_id not in by_source for item in self.evidence):
                raise error("evidence_source_unavailable")
            support_issues = source_support_issues(
                self, catalog, fixed_rules_version="waf-system-v2.34")
            defer_support = ((info.context or {}).get("defer_source_support_validation") is True
                             and bool(support_issues))
            if support_issues and not defer_support:
                raise error("evidence_source_support_forbidden")
            # A separate forbidden counter-interpretation is not a reason to
            # waive an unrelated metadata-only decisive claim. The missing
            # anchor can be corrected only when a forbidden selection itself
            # carries that exact decisive direction.
            repairing_direction_anchor = defer_support and any(
                self.evidence[issue["index"]].supports == direction for issue in support_issues)
            if direction and not repairing_direction_anchor and not any(
                item.supports == direction and is_request_content(by_source[item.source_id]["field"])
                for item in self.evidence
            ):
                raise error("decisive_verdict_requires_directional_content")
        # Preserve established severity, condition and summary invariants. The
        # actual omission flag is supplied by the worker after model decoding.
        self.to_selection(input_truncated=False)
        return self


DECISION_REVIEW_VERSION = "evidence-decision-review-v1"


class EvidenceDecisionReviewOutput(StrictContract):
    """Reconsider a decision without generating, editing or omitting evidence."""

    retained_evidence_ids: list[EvidenceId] = Field(max_length=5)
    verdict: AgentVerdict
    confidence_score: float = Field(ge=0, le=1)
    summary_ko: Annotated[str, Field(min_length=1, max_length=3000), AfterValidator(nonblank)]
    threat_analysis: ThreatAnalysis
    decision_issue: DecisionCondition | None
    interpretation_conflict: bool = Field(strict=True)

    _analysis: SecurityAnalysisOutputV234 = PrivateAttr()

    @model_validator(mode="after")
    def validate_retained_decision(self, info: ValidationInfo):
        context = info.context or {}
        catalog = context.get("source_catalog")
        original = context.get("review_original_output")
        eligible = context.get("review_eligible_evidence_ids")
        if (type(catalog) is not dict or catalog.get("version") != "evidence-selection-v5"
                or type(catalog.get("items")) is not list
                or type(eligible) is not list or len(eligible) > 5
                or any(type(key) is not str or key not in _EVIDENCE_IDS for key in eligible)
                or len(eligible) != len(set(eligible))
                or not (type(original) is dict or type(original) is SecurityAnalysisOutputV234)):
            raise error("decision_review_context_invalid")
        original_document = (original.model_dump(mode="json") if type(original) is SecurityAnalysisOutputV234
                             else deepcopy(original))
        try:
            # The worker supplies only the safe neutral base. Deferral is never
            # enabled here, even if the outer caller's context requests it.
            base = SecurityAnalysisOutputV234.model_validate(
                original_document, context={"source_catalog": catalog})
        except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
            raise error("decision_review_context_invalid") from None
        original_ids = {item.evidence_id for item in base.evidence}
        eligible_ids = set(eligible)
        if not eligible_ids <= original_ids:
            raise error("decision_review_context_invalid")
        for item in base.evidence:
            if item.evidence_id in eligible_ids and not set(item.related_evidence_ids) <= eligible_ids:
                raise error("review_evidence_dependency_missing")
        # A trusted safe base contains every surviving independent source.
        # Inconsistent metadata must not let the caller hide a counterargument.
        if eligible_ids != original_ids:
            raise error("decision_review_context_invalid")
        retained = self.retained_evidence_ids
        if len(retained) != len(set(retained)):
            raise error("review_evidence_repeated")
        if not set(retained) <= eligible_ids:
            raise error("review_evidence_not_eligible")
        if set(retained) != eligible_ids:
            raise error("review_evidence_omitted")
        candidate = base.model_dump(mode="json")
        # Restore the original order, never the model's selection ordering.
        candidate["evidence"] = [item for item in candidate["evidence"]
                                 if item["evidence_id"] in eligible_ids]
        candidate.update(self.model_dump(mode="json", exclude={"retained_evidence_ids"}))
        self._analysis = SecurityAnalysisOutputV234.model_validate(
            candidate, context={"source_catalog": catalog})
        return self

    def to_analysis(self) -> SecurityAnalysisOutputV234:
        """Return an independent, strict validated full result for grounding."""
        if not hasattr(self, "_analysis"):
            raise ValueError("decision_review_context_invalid")
        return self._analysis.model_copy(deep=True)

