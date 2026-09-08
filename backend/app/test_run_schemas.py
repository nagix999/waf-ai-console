"""Administrator test metadata, never part of an AnalysisInput."""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .evaluation_schemas import EvaluationMetadata, EvaluationSummary
from .schemas import UTCResponse


class TestRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    event: dict[str, Any]
    expected_verdict: Literal["true_positive", "false_positive", "inconclusive"] | None = None
    difficulty: str | None = Field(default=None, max_length=80)
    test_category: str | None = Field(default=None, max_length=120)
    case_name: str | None = Field(default=None, max_length=240)

    @field_validator("name")
    @classmethod
    def require_name(cls, value):
        if not value.strip() or not value.isprintable():
            raise ValueError("test_name_required")
        return value.strip()


class TestRunItemResponse(BaseModel):
    id: str
    row_number: int
    analysis_id: str | None
    event_id: str | None
    difficulty: str | None
    test_category: str | None
    case_name: str | None
    ingest_status: str
    error_code: str | None
    status: str | None
    verdict: str | None
    summary_ko: str | None
    evaluation: EvaluationMetadata = Field(default_factory=EvaluationMetadata)


class TestRunSummary(UTCResponse):
    id: str
    name: str
    kind: str
    source_system: str
    created_at: datetime
    status: str
    total: int
    accepted: int
    duplicates: int
    rejected: int
    pending: int
    processing: int
    completed: int
    failed: int
    execution_mode: str
    profile_metadata: dict[str, Any]
    prompt_version: str
    prompt_policy_version_id: str | None = None
    model_test_run_id: str | None
    evaluation_summary: EvaluationSummary
    started_at: datetime | None
    completed_at: datetime | None
    total_elapsed_ms: int


class TestRunDetail(TestRunSummary):
    items: list[TestRunItemResponse]
    total_items: int
    limit: int
    offset: int
    facets: dict[str, list[str]]
    missing_difficulty_count: int = 0
    missing_test_category_count: int = 0


class TestRunList(BaseModel):
    items: list[TestRunSummary]
    total: int
    limit: int
    offset: int
