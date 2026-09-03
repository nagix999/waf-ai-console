from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentVerdict(str, Enum):
    true_positive = "true_positive"
    false_positive = "false_positive"
    inconclusive = "inconclusive"


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
    interpretation_ko: str = Field(min_length=1, max_length=1000)


class TuningRecommendation(StrictContract):
    recommended: bool
    scope: TuningScope | None = None
    proposal_ko: str | None = Field(default=None, max_length=2000)
    risk_ko: str | None = Field(default=None, max_length=2000)
    validation_ko: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_details_when_recommended(self):
        if self.recommended and not all((self.scope, self.proposal_ko, self.risk_ko, self.validation_ko)):
            raise ValueError("recommended_tuning_requires_scope_proposal_risk_and_validation")
        return self


class WAFAnalysisOutput(StrictContract):
    verdict: AgentVerdict
    confidence_score: float = Field(ge=0, le=1)
    summary_ko: str = Field(min_length=1, max_length=3000)
    threat_analysis: ThreatAnalysis
    signature_assessment: SignatureAssessment
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=5)
    uncertainties: list[str] = Field(default_factory=list, max_length=10)
    recommended_checks: list[str] = Field(default_factory=list, max_length=10)
    tuning_recommendation: TuningRecommendation
    conflicting_evidence: list[str] = Field(default_factory=list, max_length=10)
    input_truncated: bool = False

    @model_validator(mode="after")
    def decisive_verdict_requires_evidence(self):
        if self.verdict != AgentVerdict.inconclusive and not self.evidence:
            raise ValueError("decisive_verdict_requires_evidence")
        return self
