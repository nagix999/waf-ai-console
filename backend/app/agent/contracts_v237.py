"""Flat, bounded interpretations and explicitly neutral source contributions.

The only wire change is the required contributes_to_evidence_ids field. Empty
means an interpretation root, not necessarily a directional one. Contributions
point to roots; the checked bridge reverses these memberships into the existing
root-to-source dependency representation without adding or relabelling items.
"""
from copy import deepcopy
from typing import Annotated

from pydantic import AfterValidator, Field, PrivateAttr, ValidationInfo, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from .contracts import AgentVerdict, RequestEvidenceSupport, StrictContract, ThreatAnalysis
from .contracts_v224 import CaptureAssessment
from .contracts_v234 import (
    CONTRACT_ERRORS as PREVIOUS_ERRORS, DecisionCondition, EvidenceId, Interpretation,
    SecurityAnalysisOutputV234, SignatureComparison, SourceId, has_directional_content,
    is_request_content, nonblank,
)


VERSION = "evidence-interpretation-v7"
DECISION_REVIEW_VERSION = "evidence-decision-review-v2"
REFERENCE_NORMALIZATION_VERSION = "evidence-contribution-normalization-v1"
CONTRIBUTION_REFERENCE_DUPLICATES = "evidence_contribution_duplicates_removed"
DECISION_REFERENCE_DUPLICATES = "decision_evidence_duplicates_removed"
NORMALIZATION_CODES = frozenset({CONTRIBUTION_REFERENCE_DUPLICATES, DECISION_REFERENCE_DUPLICATES})
_IDS = frozenset({"e1", "e2", "e3", "e4", "e5"})
CONTRACT_ERRORS = {
    **PREVIOUS_ERRORS,
    "evidence_link_repeated": ("evidence.contributes_to_evidence_ids", "같은 기여 대상 번호를 중복하지 마세요."),
    "evidence_link_unknown": ("evidence.contributes_to_evidence_ids", "이번 근거 목록의 해석 번호만 기여 대상으로 사용하세요."),
    "evidence_link_self": ("evidence.contributes_to_evidence_ids", "자기 자신을 기여 대상으로 사용하지 마세요."),
    "evidence_contribution_support_invalid": ("evidence.supports", "원문 기여는 실제 중립 자료로 작성하고 context로 분류하세요. 독립적인 공격/정상 해석은 빈 기여 대상 목록의 해석으로 보존하세요."),
    "evidence_contribution_target_not_root": ("evidence.contributes_to_evidence_ids", "원문 기여의 대상은 기여 대상 목록이 빈 해석이어야 합니다. 기여 항목끼리 연결하거나 순환 연결하지 마세요."),
}


def error(code):
    return PydanticCustomError(code, CONTRACT_ERRORS[code][1])


def normalize_duplicate_references(value):
    """Deduplicate only exact, already selected identities, never graph roles."""
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
        if type(identifier) is not str or identifier not in _IDS or identifier in selected:
            return value, ()
        selected.add(identifier)
    updates = {}
    for index, item in enumerate(evidence):
        refs = item.get("contributes_to_evidence_ids")
        if (type(refs) is not list or len(refs) > 4
                or any(type(key) is not str or key not in selected
                       or key == item["evidence_id"] for key in refs)):
            return value, ()
        unique = list(dict.fromkeys(refs))
        if unique != refs:
            updates[index] = unique
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
    if not updates and decision_update is None:
        return value, ()
    document = deepcopy(value)
    for index, refs in updates.items():
        document["evidence"][index]["contributes_to_evidence_ids"] = refs
    if decision_update is not None:
        document["decision_issue"]["evidence_ids"] = decision_update
    codes = (CONTRIBUTION_REFERENCE_DUPLICATES,) if updates else ()
    return document, (*codes, DECISION_REFERENCE_DUPLICATES) if decision_update is not None else codes


normalize_contribution_references = normalize_duplicate_references


class FlatEvidenceInterpretation(StrictContract):
    evidence_id: EvidenceId
    source_id: SourceId
    interpretation_ko: Interpretation
    supports: RequestEvidenceSupport
    contributes_to_evidence_ids: list[EvidenceId] = Field(max_length=4)

    @model_validator(mode="after")
    def distinct_targets(self):
        if len(self.contributes_to_evidence_ids) != len(set(self.contributes_to_evidence_ids)):
            raise error("evidence_link_repeated")
        if self.evidence_id in self.contributes_to_evidence_ids:
            raise error("evidence_link_self")
        if self.contributes_to_evidence_ids and self.supports != RequestEvidenceSupport.context:
            raise error("evidence_contribution_support_invalid")
        return self


class SecurityAnalysisOutputV237(StrictContract):
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

    def _bridge_document(self):
        by_id = {item.evidence_id: item for item in self.evidence}
        if len(by_id) != len(self.evidence):
            raise error("evidence_id_repeated")
        dependencies = {key: [] for key in by_id}
        for item in self.evidence:
            refs = item.contributes_to_evidence_ids
            if not set(refs) <= by_id.keys():
                raise error("evidence_link_unknown")
            if refs and item.supports != RequestEvidenceSupport.context:
                raise error("evidence_contribution_support_invalid")
            for target in refs:
                if by_id[target].contributes_to_evidence_ids:
                    raise error("evidence_contribution_target_not_root")
                dependencies[target].append(item.evidence_id)
        document = self.model_dump(mode="json")
        document["evidence"] = [
            {**item.model_dump(mode="json", exclude={"contributes_to_evidence_ids"}),
             "related_evidence_ids": dependencies[item.evidence_id]}
            for item in self.evidence
        ]
        return document

    def to_interpretations(self, *, context=None):
        """Return the strict legacy bridge, never a permissive model_construct."""
        return SecurityAnalysisOutputV234.model_validate(self._bridge_document(), context=context)

    def expanded_evidence(self):
        return self.to_interpretations().expanded_evidence()

    def to_selection(self, *, input_truncated):
        return self.to_interpretations().to_selection(input_truncated=input_truncated)

    @model_validator(mode="after")
    def consistent_output(self, info: ValidationInfo):
        # Every directional entry must be a root. The archived strict bridge
        # therefore checks same-direction actual request anchors, source roles,
        # verdict/severity, issue references and all other established rules.
        self.to_interpretations(context=info.context)
        return self


class EvidenceDecisionReviewOutput(StrictContract):
    """Review verdict fields only; every surviving evidence field is frozen."""
    retained_evidence_ids: list[EvidenceId] = Field(max_length=5)
    verdict: AgentVerdict
    confidence_score: float = Field(ge=0, le=1)
    summary_ko: Annotated[str, Field(min_length=1, max_length=3000), AfterValidator(nonblank)]
    threat_analysis: ThreatAnalysis
    decision_issue: DecisionCondition | None
    interpretation_conflict: bool = Field(strict=True)
    _analysis: SecurityAnalysisOutputV237 = PrivateAttr()

    @model_validator(mode="after")
    def validate_retained_decision(self, info: ValidationInfo):
        context = info.context or {}
        catalog = context.get("source_catalog")
        original = context.get("review_original_output")
        eligible = context.get("review_eligible_evidence_ids")
        if (type(catalog) is not dict or catalog.get("version") != "evidence-selection-v5"
                or type(catalog.get("items")) is not list
                or type(eligible) is not list or len(eligible) > 5
                or any(type(key) is not str or key not in _IDS for key in eligible)
                or len(eligible) != len(set(eligible))
                or not (type(original) is dict or type(original) is SecurityAnalysisOutputV237)):
            raise error("decision_review_context_invalid")
        original_document = (original.model_dump(mode="json") if type(original) is SecurityAnalysisOutputV237
                             else deepcopy(original))
        try:
            base = SecurityAnalysisOutputV237.model_validate(original_document, context={"source_catalog": catalog})
        except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
            raise error("decision_review_context_invalid") from None
        original_ids = {item.evidence_id for item in base.evidence}
        eligible_ids = set(eligible)
        if not eligible_ids <= original_ids:
            raise error("decision_review_context_invalid")
        dependencies = base.to_interpretations().evidence
        for item in dependencies:
            if item.evidence_id in eligible_ids and not set(item.related_evidence_ids) <= eligible_ids:
                raise error("review_evidence_dependency_missing")
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
        candidate.update(self.model_dump(mode="json", exclude={"retained_evidence_ids"}))
        self._analysis = SecurityAnalysisOutputV237.model_validate(candidate, context={"source_catalog": catalog})
        return self

    def to_analysis(self):
        if not hasattr(self, "_analysis"):
            raise ValueError("decision_review_context_invalid")
        return self._analysis.model_copy(deep=True)


EvidenceDecisionReviewOutputV237 = EvidenceDecisionReviewOutput
