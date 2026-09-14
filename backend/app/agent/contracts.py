from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
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
    explanation_ko: str = Field(min_length=1, max_length=2000)


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


class AnalystCheck(StrictContract):
    source_ko: str = Field(min_length=1, max_length=240)
    check_ko: str = Field(min_length=1, max_length=800)
    why_ko: str = Field(min_length=1, max_length=800)


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


class EvidenceSelectionCorrection(StrictContract):
    index: int = Field(ge=0, le=4, strict=True)
    source_id: str = Field(pattern=r"^c[1-9][0-9]{0,2}$")


class EvidenceSelectionCorrectionOutput(EvidenceCorrectionOutput):
    corrections: list[EvidenceSelectionCorrection] = Field(max_length=5)
