from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    cost_acknowledged: Literal[True]


class RetryEligibility(BaseModel):
    analysis_id: str
    allowed: bool
    blocked_reason: str | None = None
    existing_retry_id: str | None = None
    model_profile_id: str | None = None
    model_profile: str | None = None
    model_name: str | None = None
    provider: str | None = None
    verifier_model_profile: str | None = None
    verifier_model_name: str | None = None
    verifier_provider: str | None = None
    evidence_editor_enabled: bool = False
    evidence_editor_model_profile: str | None = None
    evidence_editor_model_name: str | None = None
    evidence_editor_provider: str | None = None
    prompt_version: str | None = None
    costs_may_apply: bool = True


class RetryResponse(BaseModel):
    analysis_id: str
    retry_of_analysis_id: str
    status: str
    duplicate: bool


class TestRetryRequest(RetryRequest):
    analysis_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(min_length=1, max_length=5000)
