"""Flat, bounded model evidence with server-resolved interpretation links.

Related references may form cycles; a bounded visited-set traversal preserves
all original edges while checking every reachable support classification.
Local evidence IDs identify interpretations, not source excerpts. The same
excerpt may support distinct interpretations without losing either one. Model
IDs are removed by the existing internal bridge before public v3 adaptation.
"""
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, ValidationInfo, model_validator
from pydantic_core import PydanticCustomError

from .contracts import AgentVerdict, RequestEvidenceSupport, StrictContract, ThreatAnalysis
from .contracts_v224 import CaptureAssessment, SecurityAnalysisOutputV224
from .contracts_v227 import (
    CONTRACT_ERRORS as V227_CONTRACT_ERRORS, SignatureComparison, SourceId,
    has_directional_content, is_request_content,
)
from .evidence_source_policy_v226 import is_reference_source_field

VERSION = "evidence-interpretation-v3"
CONTRACT_ERRORS = {
    **V227_CONTRACT_ERRORS,
    "evidence_text_required": ("evidence", "보안상 의미와 관련 원문의 기여를 공백 없이 작성하세요."),
    "evidence_id_repeated": ("evidence", "근거마다 서로 다른 evidence_id를 사용하세요."),
    "evidence_link_repeated": ("evidence.related_evidence_ids", "같은 관련 근거 번호를 중복하지 마세요."),
    "evidence_link_unknown": ("evidence.related_evidence_ids", "이번 evidence 목록에 있는 근거 번호만 연결하세요."),
    "evidence_link_self": ("evidence.related_evidence_ids", "근거를 자기 자신에게 연결하지 마세요."),
    "evidence_support_conflict": ("evidence", "같은 연결에 공격과 정상 방향을 섞지 마세요. 반대 해석은 별도 근거로 유지하세요."),
    "evidence_source_unavailable": ("evidence", "실제 제공된 원문 후보의 source_id만 선택하세요."),
    "evidence_source_support_forbidden": ("evidence", "시그니처와 등록 설명은 context 전용입니다. 해당 설명만으로 요청의 공격/정상 근거를 만들지 마세요."),
    "decisive_verdict_requires_directional_content": ("evidence", "확정 판정과 같은 방향의 요청 내용 해석이 필요합니다. 원문으로 해석과 supports 및 판정을 함께 재검토하세요. 분류만 바꾸거나 근거를 만들어 채우지 마세요."),
    "decision_evidence_unselected": ("decision_issue.evidence_ids", "확인 조건에는 이번 evidence 목록에 있는 근거 번호만 연결하세요."),
    "decision_evidence_repeated": ("decision_issue.evidence_ids", "확인 조건의 근거 번호를 중복하지 마세요."),
}


def error(code):
    return PydanticCustomError(code, CONTRACT_ERRORS[code][1])


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


class SecurityAnalysisOutputV229(StrictContract):
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
            seen, pending = set(), [key]
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(by_id[current].related_evidence_ids)
            return seen

        for key in by_id:
            directions = {by_id[related].supports for related in visit(key)}
            if {RequestEvidenceSupport.attack_request, RequestEvidenceSupport.normal_request} <= directions:
                raise error("evidence_support_conflict")

        direction = {AgentVerdict.true_positive: "attack_request", AgentVerdict.false_positive: "normal_request"}.get(self.verdict)
        if direction and not any(item.supports == direction for item in self.evidence):
            raise error("decisive_verdict_requires_directional_content")
        catalog = (info.context or {}).get("source_catalog")
        if catalog is not None:
            by_source = {item["source_id"]: item for item in catalog["items"]}
            for item in self.evidence:
                source = by_source.get(item.source_id)
                if source is None:
                    raise error("evidence_source_unavailable")
                if ((is_reference_source_field(source["field"]) and item.supports != "context")
                        or ("allowed_supports" in source and item.supports not in source["allowed_supports"])):
                    raise error("evidence_source_support_forbidden")
            if direction and not any(
                item.supports == direction and is_request_content(by_source[item.source_id]["field"])
                for item in self.evidence
            ):
                raise error("decisive_verdict_requires_directional_content")
        # Preserve established severity, condition and summary invariants. The
        # actual omission flag is supplied by the worker after model decoding.
        self.to_selection(input_truncated=False)
        return self
