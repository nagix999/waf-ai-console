"""Administrator test metadata, never part of an AnalysisInput."""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .evaluation_schemas import EvaluationMetadata, EvaluationSummary
from .schemas import UTCResponse
from .candidate_schemas import CandidateConfiguration


class TestRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    event: dict[str, Any]
    candidate_configuration: CandidateConfiguration | None = None
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
    ground_truth_source: dict[str, Any] | None = None
    id: str
    row_number: int
    analysis_id: str | None
    original_analysis_id: str | None = None
    retry_count: int = 0
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
    test_purpose: Literal["official_evaluation", "development", "legacy_unknown"] = "legacy_unknown"
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
    configuration_snapshot: dict[str, Any] | None = None
    configuration_hash: str | None = None
    evaluation_mode: Literal["reference", "ground_truth"] = "reference"
    ground_truth: dict[str, Any] | None = None
    official_evaluation_pending: bool = False
    prompt_version: str
    prompt_policy_version_id: str | None = None
    model_test_run_id: str | None
    evaluation_summary: EvaluationSummary
    evaluation_id: str | None = None
    evaluation_revision: int = 0
    reference_basis: Literal["initial", "latest", "saved", "ground_truth"] = "initial"
    dataset_version_id: str | None = None
    accepting_items: bool = False
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
