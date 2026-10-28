"""Closed stored-result contract; legacy decoding is deliberately separate.

Only server-declared fields may enter a new result. A failed v3 validation must
never fall through to the permissive historical-result reader.
"""
from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator

from .agent.contracts import (AgentVerdict, SignatureAssessmentV2, StrictContract,
                              ThreatAnalysis, ThreatSeverity)


class StoredResultInvalid(ValueError):
    """Payload-free read error; never expose a Pydantic input/error document."""
    def __init__(self):
        super().__init__("analysis_result_invalid")


class SourceSpan(StrictContract):
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("result_source_span_invalid")
        return self


class EvidenceOrigin(StrictContract):
    role: Literal["primary", "verifier"]
    index: int = Field(ge=0, le=4, strict=True)
    source_id: str = Field(pattern=r"^c[1-9][0-9]{0,2}$")


class ResultEvidence(StrictContract):
    evidence_id: str = Field(pattern=r"^e[1-9][0-9]?$")
    field: str = Field(min_length=1, max_length=120)
    excerpt: str = Field(min_length=1, max_length=300)
    interpretation_ko: str = Field(min_length=1, max_length=2000)
    supports: Literal["attack_request", "normal_request", "context"]
    origins: list[EvidenceOrigin] = Field(min_length=1, max_length=10)
    source_spans: list[SourceSpan] = Field(default_factory=list, max_length=10)


class ResultIssue(StrictContract):
    point_ko: str = Field(min_length=1, max_length=400)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)
    missing_condition_ko: str | None = Field(max_length=500)
    origins: list[Literal["primary", "verifier"]] = Field(min_length=1, max_length=2)


class ResultCore(StrictContract):
    verdict: AgentVerdict
    confidence_score: float = Field(ge=0, le=1)
    summary_ko: str = Field(min_length=1, max_length=3000)
    threat_analysis: ThreatAnalysis
    signature_assessment: SignatureAssessmentV2
    input_truncated: bool
    interpretation_conflict: bool

    @model_validator(mode="after")
    def severity_matches_verdict(self):
        allowed = {
            AgentVerdict.true_positive: {ThreatSeverity.CRITICAL, ThreatSeverity.HIGH,
                                        ThreatSeverity.MEDIUM, ThreatSeverity.LOW},
            AgentVerdict.false_positive: {ThreatSeverity.NONE},
            AgentVerdict.inconclusive: {ThreatSeverity.UNKNOWN},
        }
        if self.threat_analysis.severity not in allowed[self.verdict]:
            raise ValueError("result_verdict_severity_invalid")
        return self


class RoleResult(ResultCore):
    evidence_ids: list[str] = Field(max_length=5)
    decision_issues: list[ResultIssue] = Field(default_factory=list, max_length=2)


class VerifierResult(StrictContract):
    executed: bool
    agreement: bool | None
    reasons: list[str]
    failure_id: str | None
    error: str | None
    output: RoleResult | None
    framework_run_id: str | None


class RoleDiagnostics(StrictContract):
    initial_model_verdict: AgentVerdict | None
    validated_verdict: AgentVerdict | None
    grounding_rejected_count: int = Field(ge=0)
    grounding_downgraded: bool
    repair_attempted: bool
    repair_recovered: bool
    correction_requires_review: bool
    resolved_source_count: int = Field(ge=0)
    failure_category: str | None


class DiagnosticRoles(StrictContract):
    primary: RoleDiagnostics
    verifier: RoleDiagnostics | None = None


class IntegrityDiagnostics(StrictContract):
    version: str
    issue_codes: list[str]
    limitation_codes: list[str]
    omitted_issues: bool
    downgraded_to_inconclusive: bool
    affected_issue_codes: list[str]


class FollowupDiagnostics(StrictContract):
    version: str
    suppressed_count: int = Field(ge=0)
    reason_codes: list[str]


class ResultDiagnostics(StrictContract):
    version: str
    inconclusive_reasons: list[str]
    input_signals: list[str]
    roles: DiagnosticRoles
    request_integrity: IntegrityDiagnostics | None = None
    followup_policy: FollowupDiagnostics | None = None


class ResultPolicy(StrictContract):
    policy_version_id: str
    policy_version_number: int = Field(ge=1)
    policy_hash: str
    prompt_version: str
    fixed_rules_version: str
    fixed_rules_hash: str
    instructions_hash: str
    reserved_tokens: int = Field(ge=0)
    selection_origin: Literal["enqueue", "legacy_execution"]
    verifier_confidence_threshold: float = Field(ge=0, le=1)
    disagreement_or_failure_becomes_inconclusive: Literal[True]
    decision_evidence_ids: list[str] = Field(max_length=5)
    verifier_policy_version: str
    retired_verifier_triggers: list[Literal["tuning_recommended"]]


class ProfileMetadata(StrictContract):
    model_profile_id: str
    model_profile: str
    model_name: str
    llm_provider: Literal["vllm", "openai"]
    profile_fingerprint: str
    external_data_approved: bool


class RoleProfiles(StrictContract):
    primary: ProfileMetadata
    verifier: ProfileMetadata


class SchemaMetadata(StrictContract):
    version_id: str | None
    version_number: int | None
    content_hash: str
    selection_origin: str
    field_metadata_usage: Literal["history_only"]
    field_count: int = Field(ge=0)
    field_metadata_sent_to_model: Literal[False]


class EditorMetadata(StrictContract):
    editor_version: str
    instructions_hash: str
    model_profile_id: str
    profile_fingerprint: str
    presentation_status: Literal["skipped", "completed", "fallback"]


class ResultAgent(StrictContract):
    role_profiles: RoleProfiles
    framework: Literal["moduagent"]
    input_schema: SchemaMetadata
    framework_version: str | None
    execution: Literal["standard"]
    tools: list[str] = Field(max_length=0)
    preprocessors: list[str]
    llm_provider: Literal["vllm", "openai"]
    model_name: str
    model_profile_id: str
    profile_fingerprint: str
    external_data_approved: bool
    thinking_enabled: bool | None
    evidence_editor: EditorMetadata | None = None


class AnalystGuidanceV3(StrictContract):
    summary_ko: str
    limitations: list[str]


class PresentationGroup(StrictContract):
    member_ids: list[str] = Field(min_length=1, max_length=10)
    representative_id: str


class EvidencePresentation(StrictContract):
    version: Literal["evidence-editor-v1"]
    status: Literal["skipped", "completed", "fallback"]
    reason: str | None
    groups: list[PresentationGroup] | None = None


class AnalysisResultV3(ResultCore):
    schema_version: Literal["waf-analysis-v3"]
    evidence: list[ResultEvidence] = Field(max_length=10)
    decision_issues: list[ResultIssue] = Field(max_length=5)
    omitted_issue_count: int = Field(ge=0)
    diagnostics: ResultDiagnostics
    analyst_guidance: AnalystGuidanceV3
    primary: RoleResult
    verifier: VerifierResult
    policy: ResultPolicy
    agent: ResultAgent
    evidence_presentation: EvidencePresentation | None = None

    @model_validator(mode="after")
    def references_are_closed(self):
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("result_evidence_ids_not_unique")
        known = set(ids)
        roles = [self.primary, *([self.verifier.output] if self.verifier.output else [])]
        references = [self.policy.decision_evidence_ids, *(role.evidence_ids for role in roles),
                      *(issue.evidence_ids for issue in self.decision_issues),
                      *(issue.evidence_ids for role in roles for issue in role.decision_issues)]
        if any(len(refs) != len(set(refs)) or not set(refs) <= known for refs in references):
            raise ValueError("result_evidence_reference_invalid")
        for name, role in (("primary", self.primary), ("verifier", self.verifier.output)):
            contributed = {item.evidence_id for item in self.evidence
                           if any(origin.role == name for origin in item.origins)}
            if contributed != (set(role.evidence_ids) if role is not None else set()):
                raise ValueError("result_role_evidence_reference_invalid")
        if not self.verifier.executed and self.verifier.output is not None:
            raise ValueError("result_unexecuted_verifier_output")
        for issue in self.decision_issues:
            if any(not any(origin.role == role for origin in item.origins)
                   for role in issue.origins for item in self.evidence if item.evidence_id in issue.evidence_ids):
                raise ValueError("result_issue_origin_invalid")
        if self.verdict != AgentVerdict.inconclusive and not self.policy.decision_evidence_ids:
            raise ValueError("result_decisive_evidence_required")
        presentation = self.evidence_presentation
        if presentation and presentation.status == "completed":
            from .agent.evidence_editor import validate_groups
            validate_groups({"evidence": [item.model_dump(mode="json") for item in self.evidence]},
                            {"groups": [group.model_dump() for group in presentation.groups or []]})
        return self


def validate_new_result(value: dict) -> dict:
    return AnalysisResultV3.model_validate(value).model_dump(mode="json", exclude_unset=True)


def legacy_result_projection(value: dict) -> dict:
    """An explicit v2 service response, never persisted or used for inference."""
    if value.get("schema_version") != "waf-analysis-v3":
        return value
    data = AnalysisResultV3.model_validate(value).model_dump(mode="json", exclude_unset=True)
    indexed = {item["evidence_id"]: item for item in data["evidence"]}
    def core(record, ids):
        return {**{key: deepcopy(record[key]) for key in ("verdict", "confidence_score", "summary_ko",
                    "threat_analysis", "signature_assessment", "input_truncated")},
                "evidence": [{key: indexed[identifier][key] for key in ("field", "excerpt", "interpretation_ko")}
                             for identifier in ids[:5]],
                "recommended_checks": [], "analyst_checks": [], "conflicting_evidence": [],
                "tuning_recommendation": {"recommended": False, "scope": None, "proposal_ko": None,
                                          "risk_ko": None, "validation_ko": None}}
    projected = core(data, data["policy"]["decision_evidence_ids"])
    projected.update(schema_version="waf-analysis-v2", diagnostics=data["diagnostics"], agent=data["agent"],
                     policy={key: val for key, val in data["policy"].items() if key not in {
                         "decision_evidence_ids", "verifier_policy_version", "retired_verifier_triggers"}},
                     analyst_guidance={**data["analyst_guidance"], "checks": []},
                     primary=core(data["primary"], data["primary"]["evidence_ids"]),
                     verifier={**data["verifier"], "output": core(data["verifier"]["output"],
                         data["verifier"]["output"]["evidence_ids"]) if data["verifier"].get("output") else None})
    directions = {"attack_request": "true_positive", "normal_request": "false_positive", "context": "context"}
    projected["analyst_assessment"] = {"version": "analyst-assessment-v1",
        "evidence": [{**item, "supports": directions[item["supports"]]} for item in data["evidence"]],
        "decision_issues": data["decision_issues"], "omitted_issue_count": data["omitted_issue_count"]}
    if data.get("evidence_presentation"):
        projected["evidence_presentation"] = data["evidence_presentation"]
    return projected
