from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..agent.contracts import ThreatSeverity
from ..models import Analysis, AnalysisPurpose, AnalysisStatus, IngestChannel, Review, Verdict, TestRunItem
from ..evaluation_schemas import AIVisibility, EvaluationOutcome, LabelSourceKind, ReferenceVerdict
from .evaluation import attach_evaluations, evaluation_relation, summarize_evaluations


SearchField = Literal[
    "all", "event_id", "company_name", "source_system", "src_ip", "dest_ip",
    "signature", "event_name", "threat_category",
]
TEXT_FIELDS = ("event_id", "company_name", "source_system", "signature", "event_name", "threat_category")


class AnalysisFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    service_api_key_id: str | None = Field(default=None, min_length=1, max_length=36)
    include_retries: bool = False
    analysis_purpose: AnalysisPurpose | None = None
    ingest_channel: IngestChannel | None = None
    test_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    test_difficulty: str | None = Field(default=None, min_length=1, max_length=80)
    test_category: str | None = Field(default=None, min_length=1, max_length=120)
    q: str | None = Field(default=None, min_length=1, max_length=500)
    search_field: SearchField = "all"
    event_id: str | None = Field(default=None, min_length=1, max_length=255)
    company_name: str | None = Field(default=None, min_length=1, max_length=255)
    source_system: str | None = Field(default=None, min_length=1, max_length=120)
    src_ip: str | None = Field(default=None, min_length=1, max_length=64)
    dest_ip: str | None = Field(default=None, min_length=1, max_length=64)
    signature: str | None = Field(default=None, min_length=1, max_length=500)
    event_name: str | None = Field(default=None, min_length=1, max_length=500)
    threat_category: str | None = Field(default=None, min_length=1, max_length=120)
    src_port: int | None = Field(default=None, ge=0, le=65535)
    dest_port: int | None = Field(default=None, ge=0, le=65535)
    status: AnalysisStatus | None = None
    verdict: Verdict | None = None
    severity: ThreatSeverity | None = None
    waf_vendor: str | None = Field(default=None, min_length=1, max_length=120)
    waf_action: Literal["D", "A"] | None = None
    review_state: Literal["unreviewed", "confirmed", "deferred"] | None = None
    label_presence: Literal["labeled", "unlabeled"] | None = None
    evaluation_outcome: EvaluationOutcome | None = None
    reference_label: ReferenceVerdict | None = None
    label_source_kind: LabelSourceKind | None = None
    label_source_ref: str | None = Field(default=None, min_length=1, max_length=120)
    label_ai_visible: AIVisibility | None = None
    model_profile: str | None = Field(default=None, min_length=1, max_length=120)
    input_truncated: bool | None = None
    confidence_min: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    confidence_max: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    created_from: datetime | None = None
    created_to: datetime | None = None

    @field_validator("created_from", "created_to")
    @classmethod
    def dates_have_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timezone_required")
            return value.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.confidence_min is not None and self.confidence_max is not None and self.confidence_min > self.confidence_max:
            raise ValueError("confidence_range_reversed")
        if self.created_from is not None and self.created_to is not None and self.created_from > self.created_to:
            raise ValueError("created_range_reversed")
        return self


def contains_text(column, value: str):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def analysis_conditions(filters: AnalysisFilters, source_system: str | None, evaluation):
    conditions = []
    if not filters.include_retries:
        conditions.append(Analysis.retry_of_analysis_id.is_(None))
    if filters.service_api_key_id is not None:
        conditions.append(Analysis.service_api_key_id == filters.service_api_key_id)
    if filters.test_run_id is not None or filters.test_difficulty is not None or filters.test_category is not None:
        members = select(TestRunItem.analysis_id).where(TestRunItem.ingest_status == "accepted")
        for name, value in (("test_run_id", filters.test_run_id), ("difficulty", filters.test_difficulty), ("test_category", filters.test_category)):
            if value is not None:
                members = members.where(getattr(TestRunItem, name) == value)
        conditions.append(Analysis.id.in_(members))
    if source_system is not None:
        conditions.append(Analysis.source_system == source_system)
    for field in TEXT_FIELDS:
        value = getattr(filters, field)
        if value is not None:
            conditions.append(contains_text(getattr(Analysis, field), value))
    for field in (
        "analysis_purpose", "ingest_channel", "src_ip", "dest_ip", "src_port", "dest_port",
        "status", "verdict", "severity", "waf_vendor", "waf_action", "model_profile", "input_truncated",
    ):
        value = getattr(filters, field)
        if value is not None:
            conditions.append(getattr(Analysis, field) == value)
    if filters.q is not None:
        fields = (*TEXT_FIELDS, "src_ip", "dest_ip") if filters.search_field == "all" else (filters.search_field,)
        conditions.append(or_(*(
            getattr(Analysis, field) == filters.q if field in {"src_ip", "dest_ip"}
            else contains_text(getattr(Analysis, field), filters.q)
            for field in fields
        )))
    if filters.confidence_min is not None:
        conditions.append(Analysis.confidence_score >= filters.confidence_min)
    if filters.confidence_max is not None:
        conditions.append(Analysis.confidence_score <= filters.confidence_max)
    if filters.created_from is not None:
        conditions.append(Analysis.created_at >= filters.created_from)
    if filters.created_to is not None:
        conditions.append(Analysis.created_at < filters.created_to)
    if filters.review_state is not None:
        latest_review = (
            select(Review.decision).where(Review.analysis_id == Analysis.id)
            .order_by(Review.created_at.desc(), Review.id.desc()).limit(1).correlate(Analysis).scalar_subquery()
        )
        state = case((latest_review.is_(None), "unreviewed"), (latest_review == "deferred", "deferred"), else_="confirmed")
        conditions.append(state == filters.review_state)
    if filters.label_presence is not None:
        conditions.append(evaluation.c.label_id.is_not(None) if filters.label_presence == "labeled" else evaluation.c.label_id.is_(None))
    for field, column in (
        ("evaluation_outcome", evaluation.c.outcome), ("reference_label", evaluation.c.reference_verdict),
        ("label_source_kind", evaluation.c.source_kind), ("label_source_ref", evaluation.c.source_ref),
    ):
        value = getattr(filters, field)
        if value is not None:
            conditions.append(column == value)
    if filters.label_ai_visible is not None:
        conditions.append(evaluation.c.label_id.is_not(None))
        conditions.append(evaluation.c.ai_visible.is_({"unknown": None, "true": True, "false": False}[filters.label_ai_visible]))
    return conditions


def find_analyses(db: Session, filters: AnalysisFilters, source_system: str | None = None) -> tuple[list[Analysis], int]:
    evaluation = evaluation_relation()
    conditions = analysis_conditions(filters, source_system, evaluation)
    total = int(db.scalar(select(func.count()).select_from(Analysis).join(evaluation, evaluation.c.analysis_id == Analysis.id).where(*conditions)) or 0)
    query = (
        select(Analysis).options(selectinload(Analysis.reviews)).join(evaluation, evaluation.c.analysis_id == Analysis.id).where(*conditions)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc()).offset(filters.offset).limit(filters.limit)
    )
    rows = list(db.scalars(query).all())
    attach_evaluations(db, rows)
    from .test_runs import attach_test_run_ids
    attach_test_run_ids(db, rows)
    from .analysis import attach_retry_ids
    attach_retry_ids(db, rows)
    return rows, total


def filtered_evaluation_summary(db: Session, filters: AnalysisFilters, source_system: str | None = None):
    evaluation = evaluation_relation()
    return summarize_evaluations(db, evaluation, analysis_conditions(filters, source_system, evaluation))
