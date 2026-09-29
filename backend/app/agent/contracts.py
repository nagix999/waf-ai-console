from copy import deepcopy
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from pydantic_core import PydanticCustomError


# Fixed, payload-free diagnostics shared by validation and corrective feedback.
CONTRACT_ERRORS = {
    "decisive_verdict_requires_evidence": ("evidence", "확정 판정에는 원문 근거가 최소 1개 필요합니다."),
    "true_positive_requires_threat_severity": ("threat_analysis.severity", "정탐의 심각도는 CRITICAL/HIGH/MEDIUM/LOW 중 하나여야 합니다."),
    "false_positive_requires_none_severity": ("threat_analysis.severity", "오탐의 심각도는 NONE이어야 합니다."),
    "inconclusive_requires_unknown_severity": ("threat_analysis.severity", "보류의 심각도는 UNKNOWN이어야 합니다."),
    "recommended_tuning_requires_scope_proposal_risk_and_validation": (
        "tuning_recommendation", "튜닝을 제안하면 scope/proposal_ko/risk_ko/validation_ko를 모두 작성해야 합니다."
    ),
    "duplicate_correction_index": ("corrections", "같은 근거 인덱스는 한 번만 교정할 수 있습니다."),
    "signature_exact_requires_match": ("signature_assessment.matched_points", "exact에는 일치 지점을 1개 이상 작성하세요."),
    "signature_exact_disallows_mismatch": ("signature_assessment", "exact는 차이 지점이 빈 배열이고 uncertainty_ko가 null이어야 합니다."),
    "signature_partial_requires_match_and_mismatch": ("signature_assessment", "partial에는 일치 지점과 차이 지점을 각각 1개 이상 작성하세요."),
    "signature_mismatch_requires_difference": ("signature_assessment.mismatched_points", "mismatch에는 차이 지점을 1개 이상 작성하세요. 정탐과 함께 사용할 수 있습니다."),
    "signature_unknown_requires_reason": ("signature_assessment.uncertainty_ko", "unknown에는 관계를 비교할 수 없는 이유를 작성하세요."),
    "signature_v2_explanation_must_be_null": ("signature_assessment.explanation_ko", "v2의 explanation_ko는 null입니다. 세부 내용은 일치·차이 지점과 비교 제한에 작성하세요."),
    "signature_legacy_requires_explanation": ("signature_assessment.explanation_ko", "기존 계약에는 explanation_ko가 필요합니다."),
    "signature_v2_requires_version": ("signature_assessment.version", "구조화한 관계 설명에는 signature-assessment-v2 버전이 필요합니다."),
    "analyst_check_purpose_required": ("analyst_checks.purpose", "새 계약의 확인 항목에는 purpose가 필요합니다."),
    "decisive_verdict_disallows_decision_condition": ("analyst_checks.purpose", "확정 판정에는 decision_condition을 사용할 수 없습니다. 실제 영향 확인·튜닝 검증만 남기고, 판정을 가르는 조건이 빠졌다면 원문을 다시 검토하세요."),
    "recommended_checks_legacy_only": ("recommended_checks", "새 계약에서는 recommended_checks를 빈 배열로 두고 analyst_checks만 작성하세요."),
}


def contract_error(code: str) -> PydanticCustomError:
    return PydanticCustomError(code, CONTRACT_ERRORS[code][1])


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentVerdict(str, Enum):
    true_positive = "true_positive"
    false_positive = "false_positive"
    inconclusive = "inconclusive"


class ThreatSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class SignatureRelation(str, Enum):
    exact = "exact"
    partial = "partial"
    mismatch = "mismatch"
    unknown = "unknown"


class TuningScope(str, Enum):
    signature = "signature"
    uri = "uri"
    parameter = "parameter"
    header = "header"
    source = "source"
    other = "other"


class ThreatAnalysis(StrictContract):
    severity: ThreatSeverity
    category: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=500)
    technique_ko: str = Field(min_length=1, max_length=2000)
    obfuscations: list[str] = Field(default_factory=list, max_length=10)
    potential_impact_ko: str = Field(min_length=1, max_length=2000)


class SignatureAssessment(StrictContract):
    relation: SignatureRelation
    explanation_ko: str | None = Field(default=None, min_length=1, max_length=2000)
    version: Literal["signature-assessment-v2"] | None = None
    matched_points: list[Annotated[str, Field(min_length=1, max_length=400, pattern=r"\S")]] = Field(default_factory=list, max_length=5)
    mismatched_points: list[Annotated[str, Field(min_length=1, max_length=400, pattern=r"\S")]] = Field(default_factory=list, max_length=5)
    uncertainty_ko: str | None = Field(default=None, min_length=1, max_length=500, pattern=r"\S")

    @model_validator(mode="after")
    def validate_relation(self):
        if self.version is None:
            if self.matched_points or self.mismatched_points or self.uncertainty_ko is not None:
                raise contract_error("signature_v2_requires_version")
            if self.explanation_ko is None:
                raise contract_error("signature_legacy_requires_explanation")
            return self
        if self.explanation_ko is not None:
            raise contract_error("signature_v2_explanation_must_be_null")
        if self.relation == SignatureRelation.exact:
            if self.mismatched_points or self.uncertainty_ko is not None:
                raise contract_error("signature_exact_disallows_mismatch")
            if not self.matched_points:
                raise contract_error("signature_exact_requires_match")
        elif self.relation == SignatureRelation.partial and not (self.matched_points and self.mismatched_points):
            raise contract_error("signature_partial_requires_match_and_mismatch")
        elif self.relation == SignatureRelation.mismatch and not self.mismatched_points:
            raise contract_error("signature_mismatch_requires_difference")
        elif self.relation == SignatureRelation.unknown and self.uncertainty_ko is None:
            raise contract_error("signature_unknown_requires_reason")
        return self

    @model_serializer(mode="wrap")
    def serialize_compatible(self, handler):
        value = handler(self)
        if self.version is None:
            for key in ("version", "matched_points", "mismatched_points", "uncertainty_ko"):
                value.pop(key, None)
        return value


class SignatureAssessmentV2(SignatureAssessment):
    # New calls cannot opt out through a missing/null version; old snapshots
    # use the legacy wire schema, while stored results use the compatible base.
    version: Literal["signature-assessment-v2"]
    matched_points: list[Annotated[str, Field(min_length=1, max_length=400, pattern=r"\S")]] = Field(max_length=5)
    mismatched_points: list[Annotated[str, Field(min_length=1, max_length=400, pattern=r"\S")]] = Field(max_length=5)
    uncertainty_ko: str | None = Field(min_length=1, max_length=500, pattern=r"\S")
    explanation_ko: None


class EvidenceItem(StrictContract):
    field: str = Field(min_length=1, max_length=120)
    excerpt: str = Field(min_length=1, max_length=300)
    interpretation_ko: str = Field(min_length=1, max_length=2000)


class EvidenceCitationCorrection(StrictContract):
    index: int = Field(ge=0, le=4, strict=True)
    field: str = Field(min_length=1, max_length=120)
    excerpt: str = Field(min_length=1, max_length=300)


class EvidenceCorrectionOutput(StrictContract):
    """Citation edits only; the model cannot replace a verdict or interpretation."""

    corrections: list[EvidenceCitationCorrection] = Field(max_length=5)
    requires_reanalysis: bool = Field(strict=True)

    @model_validator(mode="after")
    def unique_indexes(self):
        if len({item.index for item in self.corrections}) != len(self.corrections):
            raise contract_error("duplicate_correction_index")
        return self


class AnalystCheckPurpose(str, Enum):
    decision_condition = "decision_condition"
    impact_followup = "impact_followup"
    tuning_validation = "tuning_validation"


class AnalystCheck(StrictContract):
    source_ko: str = Field(min_length=1, max_length=240)
    check_ko: str = Field(min_length=1, max_length=800)
    why_ko: str = Field(min_length=1, max_length=800)
    purpose: AnalystCheckPurpose | None = None

    @model_serializer(mode="wrap")
    def serialize_compatible(self, handler):
        value = handler(self)
        if self.purpose is None:
            value.pop("purpose", None)
        return value


class PurposeAnalystCheck(AnalystCheck):
    purpose: AnalystCheckPurpose


class TuningRecommendation(StrictContract):
    recommended: bool
    scope: TuningScope | None = None
    proposal_ko: str | None = Field(default=None, max_length=2000)
    risk_ko: str | None = Field(default=None, max_length=2000)
    validation_ko: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_details_when_recommended(self):
        if self.recommended and not all((self.scope, self.proposal_ko, self.risk_ko, self.validation_ko)):
            raise contract_error("recommended_tuning_requires_scope_proposal_risk_and_validation")
        return self


class WAFAnalysisOutput(StrictContract):
    verdict: AgentVerdict
    confidence_score: float = Field(ge=0, le=1)
    summary_ko: str = Field(min_length=1, max_length=3000)
    threat_analysis: ThreatAnalysis
    signature_assessment: SignatureAssessment
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=5)
    recommended_checks: list[str] = Field(default_factory=list, max_length=10)
    analyst_checks: list[AnalystCheck] = Field(default_factory=list, max_length=5)
    tuning_recommendation: TuningRecommendation
    conflicting_evidence: list[str] = Field(default_factory=list, max_length=10)
    input_truncated: bool = False

    @model_validator(mode="after")
    def validate_verdict_contract(self):
        if self.signature_assessment.version == "signature-assessment-v2":
            if self.recommended_checks:
                raise contract_error("recommended_checks_legacy_only")
            if any(check.purpose is None for check in self.analyst_checks):
                raise contract_error("analyst_check_purpose_required")
        if self.verdict != AgentVerdict.inconclusive and any(
            check.purpose == AnalystCheckPurpose.decision_condition for check in self.analyst_checks
        ):
            raise contract_error("decisive_verdict_disallows_decision_condition")
        if self.verdict != AgentVerdict.inconclusive and not self.evidence:
            raise contract_error("decisive_verdict_requires_evidence")
        severity = self.threat_analysis.severity
        if self.verdict == AgentVerdict.true_positive and severity not in {
            ThreatSeverity.CRITICAL,
            ThreatSeverity.HIGH,
            ThreatSeverity.MEDIUM,
            ThreatSeverity.LOW,
        }:
            raise contract_error("true_positive_requires_threat_severity")
        if self.verdict == AgentVerdict.false_positive and severity != ThreatSeverity.NONE:
            raise contract_error("false_positive_requires_none_severity")
        if self.verdict == AgentVerdict.inconclusive and severity != ThreatSeverity.UNKNOWN:
            raise contract_error("inconclusive_requires_unknown_severity")
        return self


class EvidenceSelection(StrictContract):
    source_id: str = Field(pattern=r"^c[1-9][0-9]{0,2}$")
    interpretation_ko: str = Field(min_length=1, max_length=2000)


class EvidenceSelectionOutput(WAFAnalysisOutput):
    """Internal model output only; public results retain field/excerpt evidence."""

    evidence: list[EvidenceSelection] = Field(default_factory=list, max_length=5)


class EvidenceSupport(str, Enum):
    true_positive = "true_positive"
    false_positive = "false_positive"
    context = "context"


class AssessedEvidenceSelection(EvidenceSelection):
    supports: EvidenceSupport


class DecisionIssue(StrictContract):
    point_ko: str = Field(min_length=1, max_length=400)
    evidence_indexes: list[Annotated[int, Field(ge=0, le=4, strict=True)]] = Field(min_length=1, max_length=5)
    missing_condition_ko: str | None = Field(max_length=500)


class EvidenceAssessmentOutput(EvidenceSelectionOutput):
    """v2.10 presentation metadata; never used as a second decision policy."""

    evidence: list[AssessedEvidenceSelection] = Field(default_factory=list, max_length=5)
    decision_issue: DecisionIssue | None


class SemanticAssessmentOutput(EvidenceAssessmentOutput):
    """v2.13 wire output: structured relation details and explicit check purpose."""

    signature_assessment: SignatureAssessmentV2
    analyst_checks: list[PurposeAnalystCheck] = Field(max_length=5)


def legacy_output_schema(schema: dict) -> dict:
    """Preserve the pre-v2.13 wire contract of pinned old executions.

    Compatibility fields belong to stored-result readers, not old LLM calls.
    model_json_schema() returns a fresh dict; never mutate saved snapshots.
    """
    schema = deepcopy(schema)
    definitions = schema.get("$defs", {})
    signature = definitions.get("SignatureAssessment")
    if signature:
        for key in ("version", "matched_points", "mismatched_points", "uncertainty_ko"):
            signature["properties"].pop(key, None)
        signature["properties"]["explanation_ko"] = {"title": "Explanation Ko", "type": "string", "minLength": 1, "maxLength": 2000}
        signature["required"] = ["relation", "explanation_ko"]
    check = definitions.get("AnalystCheck")
    if check:
        check["properties"].pop("purpose", None)
    definitions.pop("AnalystCheckPurpose", None)
    return schema


class EvidenceSelectionCorrection(StrictContract):
    index: int = Field(ge=0, le=4, strict=True)
    source_id: str = Field(pattern=r"^c[1-9][0-9]{0,2}$")


class EvidenceSelectionCorrectionOutput(EvidenceCorrectionOutput):
    corrections: list[EvidenceSelectionCorrection] = Field(max_length=5)
