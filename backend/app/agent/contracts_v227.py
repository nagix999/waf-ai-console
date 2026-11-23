"""Interpretation-first internal output; exact quotes and public v3 stay fixed.

The model selects sources directly. The server expands groups and assigns all
array indexes, constant signature fields and the actual input omission flag.
These checks prove consistency and provenance, never semantic correctness.
"""
from typing import Annotated

from pydantic import AfterValidator, Field, ValidationInfo, model_validator
from pydantic_core import PydanticCustomError

from .contracts import (
    AgentVerdict, RequestEvidenceSupport, SignatureAssessmentV2, SignatureLimit,
    SignaturePoint, SignatureRelation, StrictContract, ThreatAnalysis,
)
from .contracts_v224 import CaptureAssessment, SecurityAnalysisOutputV224
from .evidence_source_policy_v226 import is_reference_source_field

VERSION = "evidence-interpretation-v1"
CONTRACT_ERRORS = {
    "evidence_text_required": ("evidence", "관측의 보안상 의미와 관련 원문의 기여를 공백 없이 작성하세요."),
    "evidence_source_repeated": ("evidence", "한 해석 안에서 같은 source_id를 중복하지 마세요."),
    "evidence_expansion_limit": ("evidence", "대표와 관련 원문을 합쳐 역할당 최대 5개입니다. 필요한 연결은 유지하세요."),
    "evidence_support_conflict": ("evidence", "같은 해석에 공격과 정상 방향을 섞지 마세요. 반대 해석은 별도 근거로 유지하세요."),
    "evidence_source_unavailable": ("evidence", "실제 제공된 원문 후보의 source_id만 선택하세요."),
    "evidence_source_support_forbidden": ("evidence", "시그니처와 등록 설명은 context 전용입니다. 해당 설명만으로 요청의 공격/정상 근거를 만들지 마세요."),
    "decisive_verdict_requires_directional_content": ("evidence", "확정 판정과 같은 방향의 요청 내용 해석이 필요합니다. 원문으로 해석과 supports 및 판정을 함께 재검토하세요. 분류만 바꾸거나 근거를 만들어 채우지 마세요."),
    "decision_source_unselected": ("decision_issue.source_ids", "확인 조건에는 이번 근거에서 선택한 source_id만 연결하세요."),
    "decision_source_repeated": ("decision_issue.source_ids", "확인 조건의 source_id를 중복하지 마세요."),
}


def error(code):
    return PydanticCustomError(code, CONTRACT_ERRORS[code][1])


def nonblank(value):
    if not value.strip():
        raise error("evidence_text_required")
    return value


SourceId = Annotated[str, Field(pattern=r"^c[1-9][0-9]{0,2}$", strict=True)]
Interpretation = Annotated[str, Field(min_length=1, max_length=2000, strict=True), AfterValidator(nonblank)]


class RelatedSource(StrictContract):
    source_id: SourceId
    contribution_ko: Interpretation
    supports: RequestEvidenceSupport


class EvidenceInterpretation(StrictContract):
    # Source roles/contributions precede the integrated interpretation and its
    # direction. This is output order, not a request for private reasoning.
    source_id: SourceId
    related_sources: list[RelatedSource] = Field(max_length=4)
    interpretation_ko: Interpretation
    supports: RequestEvidenceSupport

    @model_validator(mode="after")
    def source_links(self):
        ids = [self.source_id, *(item.source_id for item in self.related_sources)]
        if len(ids) != len(set(ids)):
            raise error("evidence_source_repeated")
        directions = {self.supports, *(item.supports for item in self.related_sources)}
        if {RequestEvidenceSupport.attack_request, RequestEvidenceSupport.normal_request} <= directions:
            raise error("evidence_support_conflict")
        return self


class SignatureComparison(StrictContract):
    relation: SignatureRelation
    matched_points: list[SignaturePoint] = Field(max_length=5)
    mismatched_points: list[SignaturePoint] = Field(max_length=5)
    uncertainty_ko: SignatureLimit | None

    def expanded(self):
        return SignatureAssessmentV2.model_validate({
            **self.model_dump(mode="json"), "version": "signature-assessment-v2", "explanation_ko": None,
        })

    @model_validator(mode="after")
    def validate_comparison(self):
        self.expanded()
        return self


class DecisionCondition(StrictContract):
    point_ko: Annotated[str, Field(min_length=1, max_length=400), AfterValidator(nonblank)]
    source_ids: list[SourceId] = Field(min_length=1, max_length=5)
    missing_condition_ko: Annotated[str, Field(max_length=500), AfterValidator(nonblank)] | None

    @model_validator(mode="after")
    def unique_sources(self):
        if len(self.source_ids) != len(set(self.source_ids)):
            raise error("decision_source_repeated")
        return self


class SecurityAnalysisOutputV227(StrictContract):
    evidence: list[EvidenceInterpretation] = Field(max_length=5)
    signature_assessment: SignatureComparison
    capture_assessment: CaptureAssessment
    interpretation_conflict: bool = Field(strict=True)
    summary_ko: Annotated[str, Field(min_length=1, max_length=3000), AfterValidator(nonblank)]
    verdict: AgentVerdict
    confidence_score: float = Field(ge=0, le=1)
    threat_analysis: ThreatAnalysis
    decision_issue: DecisionCondition | None

    def expanded_evidence(self):
        evidence, relations = [], []
        for claim in self.evidence:
            representative = len(evidence)
            evidence.append({"source_id": claim.source_id, "interpretation_ko": claim.interpretation_ko,
                             "supports": claim.supports.value})
            related = []
            for item in claim.related_sources:
                related.append(len(evidence))
                evidence.append({"source_id": item.source_id, "interpretation_ko": item.contribution_ko,
                                 "supports": item.supports.value})
            if related:
                relations.append({"representative_index": representative, "related_indexes": related})
        return evidence, relations

    def to_selection(self, *, input_truncated):
        evidence, relations = self.expanded_evidence()
        issue = None
        if self.decision_issue is not None:
            issue = {"point_ko": self.decision_issue.point_ko,
                     "missing_condition_ko": self.decision_issue.missing_condition_ko,
                     "evidence_indexes": [index for index, item in enumerate(evidence)
                                          if item["source_id"] in self.decision_issue.source_ids]}
        return SecurityAnalysisOutputV224.model_validate({
            **self.model_dump(mode="json", exclude={"evidence", "signature_assessment", "decision_issue"}),
            "evidence": evidence, "evidence_relations": relations, "decision_issue": issue,
            "signature_assessment": self.signature_assessment.expanded().model_dump(mode="json"),
            "input_truncated": input_truncated,
        })

    @model_validator(mode="after")
    def consistent_output(self, info: ValidationInfo):
        evidence, _ = self.expanded_evidence()
        if len(evidence) > 5:
            raise error("evidence_expansion_limit")
        selected = {item["source_id"] for item in evidence}
        if self.decision_issue and not set(self.decision_issue.source_ids) <= selected:
            raise error("decision_source_unselected")
        direction = {AgentVerdict.true_positive: "attack_request", AgentVerdict.false_positive: "normal_request"}.get(self.verdict)
        # A contribution alone cannot rescue a conclusion whose integrated
        # interpretations are all neutral or in the opposite direction.
        if direction and not any(item.supports == direction for item in self.evidence):
            raise error("decisive_verdict_requires_directional_content")
        catalog = (info.context or {}).get("source_catalog")
        if catalog is not None:
            by_id = {item["source_id"]: item for item in catalog["items"]}
            for item in evidence:
                source = by_id.get(item["source_id"])
                if source is None:
                    raise error("evidence_source_unavailable")
                if ((is_reference_source_field(source["field"]) and item["supports"] != "context")
                        or ("allowed_supports" in source and item["supports"] not in source["allowed_supports"])):
                    raise error("evidence_source_support_forbidden")
            if direction and not any(
                claim.supports == direction and is_request_content(by_id[claim.source_id]["field"])
                for claim in self.evidence
            ):
                raise error("decisive_verdict_requires_directional_content")
        # Reuse all established severity, summary, signature, condition and
        # relation constraints. Actual omission is supplied only by the worker.
        self.to_selection(input_truncated=False)
        return self


def is_request_content(field):
    field = field.strip().removeprefix("event.")
    return (not is_reference_source_field(field)
            and (field in {"payload", "raw_payload"} or field.startswith(("payload.", "extra_fields."))))


def has_directional_content(output, selection, resolutions):
    """Recheck surviving direction; the worker separately fences broken groups."""
    direction = {AgentVerdict.true_positive: "true_positive", AgentVerdict.false_positive: "false_positive"}.get(output.verdict)
    if direction is None:
        return True
    return any(selection.evidence[source["index"]].supports == direction and is_request_content(item.field)
               for source, item in zip(resolutions, output.evidence, strict=True))
