from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AnalysisStatus, ModelProfileStatus, ModelTestMode, ModelTestStatus, ReviewDecision, Verdict


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

    @field_validator("waf_action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    def extra_values(self) -> dict[str, Any]:
        return self.model_extra or {}


class AnalysisSummary(BaseModel):
    id: str
    source_system: str
    event_id: str
    company_name: str
    waf_vendor: str
    waf_action: str
    signature: str | None
    event_name: str | None
    status: AnalysisStatus
    verdict: Verdict | None
    confidence_score: float | None
    summary_ko: str | None
    input_truncated: bool
    created_at: datetime
    completed_at: datetime | None
    review_state: Literal["unreviewed", "confirmed", "deferred"] = "unreviewed"


class AnalysisDetail(AnalysisSummary):
    src_ip: str
    dest_ip: str
    src_port: int | None
    dest_port: int | None
    result: dict[str, Any] | None
    prompt_version: str | None
    model_profile: str | None
    error_code: str | None
    error_message: str | None


class RawEventResponse(BaseModel):
    analysis_id: str
    event_id: str
    payload: str
    extra_fields: dict[str, Any]
    encryption_key_version: str


class AnalysisListResponse(BaseModel):
    items: list[AnalysisSummary]
    total: int
    limit: int
    offset: int


class ReviewCreate(BaseModel):
    external_review_id: str = Field(min_length=1, max_length=255)
    event_id: str = Field(min_length=1, max_length=255)
    decision: ReviewDecision
    analyst_id: str | None = Field(default=None, max_length=255)
    comment: str | None = None
    ai_visible: bool = False


class ReviewResponse(BaseModel):
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


class AgentStepResponse(BaseModel):
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


class AgentRunResponse(BaseModel):
    id: str
    analysis_id: str
    framework_run_id: str | None
    fingerprint: str | None
    status: str
    failure_id: str | None
    started_at: datetime
    completed_at: datetime | None
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
    base_url: str = Field(min_length=1, max_length=500)
    model_name: str = Field(default="google/gemma-4-26B-A4B-it", min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    timeout_seconds: int = Field(default=120, ge=5, le=600)
    context_window: int = Field(default=32768, ge=4096, le=131072)
    max_output_tokens: int = Field(default=3072, ge=256, le=16384)
    test_concurrency: int = Field(default=3, ge=1, le=10)
    tls_verify: bool = True

    @model_validator(mode="after")
    def output_fits_context(self):
        if self.max_output_tokens >= self.context_window:
            raise ValueError("max_output_tokens_must_be_less_than_context_window")
        return self


class VLLMProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    context_window: int | None = Field(default=None, ge=4096, le=131072)
    max_output_tokens: int | None = Field(default=None, ge=256, le=16384)
    test_concurrency: int | None = Field(default=None, ge=1, le=10)
    tls_verify: bool | None = None

    @model_validator(mode="after")
    def reject_null_configuration(self):
        nullable = {"api_key"}
        for field in self.model_fields_set - nullable:
            if getattr(self, field) is None:
                raise ValueError(f"{field}_cannot_be_null")
        return self


class VLLMProfileResponse(BaseModel):
    id: str
    name: str
    base_url: str
    model_name: str
    has_api_key: bool
    timeout_seconds: int
    context_window: int
    max_output_tokens: int
    test_concurrency: int
    tls_verify: bool
    thinking_enabled: Literal[False] = False
    status: ModelProfileStatus
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VLLMTestCreate(BaseModel):
    mode: ModelTestMode


class VLLMTestRunResponse(BaseModel):
    id: str
    profile_id: str
    mode: ModelTestMode
    status: ModelTestStatus
    profile_fingerprint: str
    checks: list[dict[str, Any]]
    metrics: dict[str, Any]
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
