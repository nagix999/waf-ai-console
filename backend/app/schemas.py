from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .agent.contracts import ThreatSeverity, WAFAnalysisOutput
from .evaluation_schemas import EvaluationMetadata, EvaluationSummary
from .services.label_fields import LABEL_FIELDS
from .services.input_field_policy import SERVER_CONTROL_FIELDS
from .models import AnalysisPurpose, AnalysisStatus, IngestChannel, ModelProfileStatus, ModelProvider, ModelTestMode, ModelTestStatus, ReviewDecision, Verdict


class UTCResponse(BaseModel):
    @field_validator("*", mode="after")
    @classmethod
    def utc_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=255)
    company_name: str = Field(min_length=1, max_length=255)
    src_ip: str = Field(min_length=1, max_length=64)
    dest_ip: str = Field(min_length=1, max_length=64)
    src_port: int | None = Field(default=None, ge=0, le=65535)
    dest_port: int | None = Field(default=None, ge=0, le=65535)
    payload: str = Field(min_length=1)
    signature: str | None = None
    event_name: str | None = Field(default=None, max_length=500)
    waf_vendor: str = Field(min_length=1, max_length=120)
    waf_action: Literal["D", "A"]

    @model_validator(mode="before")
    @classmethod
    def reject_server_fields(cls, value: Any) -> Any:
        if isinstance(value, dict) and LABEL_FIELDS.intersection(value):
            raise ValueError("evaluation_labels_require_separate_attachment")
        if isinstance(value, dict) and SERVER_CONTROL_FIELDS.intersection(value):
            raise ValueError("server_control_fields_not_allowed")
        return value

    @field_validator("waf_action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    def extra_values(self) -> dict[str, Any]:
        return self.model_extra or {}


class AnalysisSummary(UTCResponse):
    id: str
    source_system: str
    analysis_purpose: AnalysisPurpose
    ingest_channel: IngestChannel
    event_id: str
    company_name: str
    src_ip: str
    dest_ip: str
    src_port: int | None
    dest_port: int | None
    waf_vendor: str
    waf_action: str
    signature: str | None
    event_name: str | None
    status: AnalysisStatus
    verdict: Verdict | None
    severity: ThreatSeverity | None
    threat_category: str | None
    confidence_score: float | None
    summary_ko: str | None
    input_truncated: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    total_elapsed_ms: int | None
    queue_wait_ms: int | None
    processing_duration_ms: int | None
    model_profile: str | None
    review_state: Literal["unreviewed", "confirmed", "deferred"] = "unreviewed"
    evaluation: EvaluationMetadata = Field(default_factory=EvaluationMetadata)
    test_run_id: str | None = None
    service_api_key_id: str | None = None
    retry_of_analysis_id: str | None = None
    retry_analysis_id: str | None = None


class AnalysisResultV2(WAFAnalysisOutput):
    """Typed current result; server metadata and future additions remain intact."""
    model_config = ConfigDict(extra="allow")
    schema_version: Literal["waf-analysis-v2"]


class AnalysisDetail(AnalysisSummary):
    result: AnalysisResultV2 | dict[str, Any] | None = Field(
        description="waf-analysis-v2 result, or an unchanged legacy result object.",
        union_mode="left_to_right",
    )
    prompt_version: str | None
    prompt_policy_version_id: str | None = None
    input_schema_metadata: dict[str, Any] | None = None
    error_code: str | None
    error_message: str | None


class RawEventResponse(BaseModel):
    analysis_id: str
    event_id: str
    payload: str
    extra_fields: dict[str, Any]
    encryption_key_version: str
    decoding: dict[str, Any] | None = None


class AnalysisListResponse(BaseModel):
    items: list[AnalysisSummary]
    total: int
    limit: int
    offset: int
    evaluation_summary: EvaluationSummary = Field(default_factory=EvaluationSummary)


class ReviewCreate(BaseModel):
    external_review_id: str = Field(min_length=1, max_length=255)
    event_id: str = Field(min_length=1, max_length=255)
    decision: ReviewDecision
    analyst_id: str | None = Field(default=None, max_length=255)
    comment: str | None = None
    ai_visible: bool = False


class ReviewResponse(UTCResponse):
    id: str
    analysis_id: str
    source_system: str
    external_review_id: str
    event_id: str
    decision: ReviewDecision
    analyst_id: str | None
    comment: str | None
    ai_visible: bool
    created_at: datetime


class AgentStepResponse(UTCResponse):
    id: str
    sequence: int
    step_type: str
    name: str
    status: str
    input: str | None
    output: str | None
    metadata: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None


class AgentRunResponse(UTCResponse):
    id: str
    analysis_id: str
    framework_run_id: str | None
    fingerprint: str | None
    status: str
    failure_id: str | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    steps: list[AgentStepResponse]


class LoginRequest(BaseModel):
    username: str
    password: str


class PrincipalResponse(BaseModel):
    kind: str
    username: str | None = None
    source_system: str | None = None
    scopes: list[str]


class UploadResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    analysis_ids: list[str]
    errors: list[dict[str, Any]]
    label_attached: int = 0
    label_unchanged: int = 0
    test_run_id: str | None = None


class AccessAuditResponse(BaseModel):
    id: str
    actor_kind: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    created_at: datetime


class VLLMProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    provider: ModelProvider = ModelProvider.vllm
    external_data_approved: bool = Field(default=False, strict=True)
    base_url: str = Field(min_length=1, max_length=500)
    model_name: str = Field(default="google/gemma-4-26B-A4B-it", min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    timeout_seconds: int = Field(default=120, ge=5, le=600)
    context_window: int = Field(default=32768, ge=4096, le=131072)
    max_output_tokens: int = Field(default=3072, ge=256, le=16384)
    test_concurrency: int = Field(default=3, ge=1, le=10)
    tls_verify: bool = True

    @field_validator("model_name")
    @classmethod
    def model_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model_name_must_not_be_blank")
        return value

    @model_validator(mode="after")
    def output_fits_context(self):
        if self.max_output_tokens >= self.context_window:
            raise ValueError("max_output_tokens_must_be_less_than_context_window")
        return self


class VLLMProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    provider: ModelProvider | None = None
    external_data_approved: bool | None = Field(default=None, strict=True)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    context_window: int | None = Field(default=None, ge=4096, le=131072)
    max_output_tokens: int | None = Field(default=None, ge=256, le=16384)
    test_concurrency: int | None = Field(default=None, ge=1, le=10)
    tls_verify: bool | None = None

    @field_validator("model_name")
    @classmethod
    def model_name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("model_name_must_not_be_blank")
        return value

    @model_validator(mode="after")
    def reject_null_configuration(self):
        nullable = {"api_key"}
        for field in self.model_fields_set - nullable:
            if getattr(self, field) is None:
                raise ValueError(f"{field}_cannot_be_null")
        return self


class VLLMProfileResponse(BaseModel):
    id: str
    profile_fingerprint: str
    name: str
    provider: ModelProvider
    external_data_approved: bool
    base_url: str
    model_name: str
    has_api_key: bool
    timeout_seconds: int
    context_window: int
    max_output_tokens: int
    test_concurrency: int
    tls_verify: bool
    thinking_enabled: Literal[False] | None = False
    status: ModelProfileStatus
    is_test: bool = False
    can_assign: bool = False
    assignment_block_reason: Literal["profile_not_verified", "matching_full_test_required"] | None = None
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ModelProfileAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$", max_length=64)


class VLLMTestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ModelTestMode
    include_dataset: bool = Field(default=False, strict=True)
    expected_profile_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def full_dataset_only(self):
        if self.include_dataset and self.mode != ModelTestMode.full:
            raise ValueError("dataset_requires_full_test")
        if self.name is not None:
            if not self.name.strip() or not self.name.isprintable():
                raise ValueError("test_name_required")
            self.name = self.name.strip()
        if self.include_dataset and not self.name:
            raise ValueError("test_name_required")
        if self.include_dataset and not self.idempotency_key:
            raise ValueError("test_idempotency_key_required")
        return self


class ModelDatasetEvaluation(BaseModel):
    dataset_version: str
    dataset_hash: str
    source_system: str
    total: int
    pending: int
    processing: int
    completed: int
    failed: int
    status: Literal["waiting", "running", "completed", "failed", "skipped"]
    summary: EvaluationSummary


class VLLMTestRunResponse(BaseModel):
    id: str
    profile_id: str
    mode: ModelTestMode
    status: ModelTestStatus
    profile_fingerprint: str
    include_dataset: bool = False
    dataset_evaluation: ModelDatasetEvaluation | None = None
    name: str | None = None
    test_run_id: str | None = None
    checks: list[dict[str, Any]]
    metrics: dict[str, Any]
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
