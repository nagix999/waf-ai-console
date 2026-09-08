"""Read-only, paired comparison of two immutable named test submissions."""
from typing import Literal

from pydantic import BaseModel, Field

from .evaluation_schemas import EvaluationSummary, ReferenceVerdict
from .test_run_schemas import TestRunSummary


class ComparisonCounts(BaseModel):
    accepted_pairs: int = 0
    comparable_pairs: int = 0
    changed: int = 0
    improved: int = 0
    regressed: int = 0
    exclusions: dict[str, int] = Field(default_factory=dict)


class ComparisonItem(BaseModel):
    event_id: str
    case_name: str | None = None
    difficulty: str | None = None
    test_category: str | None = None
    baseline_analysis_id: str | None = None
    candidate_analysis_id: str | None = None
    baseline_verdict: ReferenceVerdict | None = None
    candidate_verdict: ReferenceVerdict | None = None
    baseline_outcome: str | None = None
    candidate_outcome: str | None = None
    reference_verdict: ReferenceVerdict | None = None
    comparison_status: str
    change: Literal["improved", "regressed", "changed", "unchanged", "not_comparable"]


class ComparisonTiming(BaseModel):
    count: int = 0
    missing_count: int = 0
    sum_ms: int = 0
    mean_ms: float | None = None
    p50_ms: int | None = None
    p95_ms: int | None = None


class ComparisonTokenCounter(BaseModel):
    """Sum only steps whose counter is known for every recorded repair attempt.

    A partially measured step is counted as missing, not summed incompletely.
    Zero measured_steps (or missing_agent_histories on its parent side) must
    never be presented as a measured zero-token/zero-cost execution.
    """
    known_sum: int = 0
    measured_steps: int = 0
    missing_steps: int = 0


class ComparisonTokens(BaseModel):
    input_tokens: ComparisonTokenCounter = Field(default_factory=ComparisonTokenCounter)
    output_tokens: ComparisonTokenCounter = Field(default_factory=ComparisonTokenCounter)
    total_tokens: ComparisonTokenCounter = Field(default_factory=ComparisonTokenCounter)


class ComparisonPerformanceSide(BaseModel):
    processing_ms: ComparisonTiming = Field(default_factory=ComparisonTiming)
    llm_step_ms: ComparisonTiming = Field(default_factory=ComparisonTiming)
    tokens: ComparisonTokens = Field(default_factory=ComparisonTokens)
    llm_steps: int = 0
    missing_agent_histories: int = Field(default=0, description="Paired analyses missing Primary or an expected Verifier step history")
    output_repair_steps: int = 0


class ComparisonPerformance(BaseModel):
    # Includes recorded failed/reclaimed attempts ONLY for the same eligible
    # analysis pairs. This is neither full-run consumption nor a billing total.
    scope: Literal["comparable_pairs_all_recorded_attempts"] = "comparable_pairs_all_recorded_attempts"
    baseline: ComparisonPerformanceSide
    candidate: ComparisonPerformanceSide


class TestComparisonResponse(BaseModel):
    baseline: TestRunSummary
    candidate: TestRunSummary
    baseline_evaluation: EvaluationSummary
    candidate_evaluation: EvaluationSummary
    counts: ComparisonCounts
    warnings: list[str]
    items: list[ComparisonItem]
    total_items: int
    limit: int
    offset: int
    changes_only: bool
    performance: ComparisonPerformance
