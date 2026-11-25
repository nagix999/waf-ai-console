"""Administrator feedback reads, separate from append-only service writes."""

from datetime import date, datetime
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import FeedbackCategory, UTCResponse


class FeedbackFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    analysis_purpose: Literal["production", "test", "all"] = "production"
    production_configuration_id: str | None = Field(default=None, min_length=1, max_length=36)
    category: FeedbackCategory | None = None
    q: str | None = Field(default=None, max_length=200)
    analysis_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("q")
    @classmethod
    def sanitize_search(cls, value):
        if value is None:
            return None
        value = " ".join("".join(char for char in value if not unicodedata.category(char).startswith("C")).split())
        return value or None

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("feedback_date_range_invalid")
        if self.date_from == date.min:
            # KST midnight would precede datetime's minimum UTC instant.
            raise ValueError("feedback_date_range_invalid")
        # The inclusive end is implemented as the following midnight.
        if self.date_to == date.max:
            raise ValueError("feedback_date_range_invalid")
        return self


class FeedbackItem(UTCResponse):
    id: str
    analysis_id: str
    event_id: str
    analysis_purpose: str
    source_system: str
    external_review_id: str
    company_name: str
    signature: str | None
    verdict: str | None
    severity: str | None
    production_configuration_id: str | None
    production_configuration_label: str | None
    test_run_id: str | None = None
    feedback_categories: list[FeedbackCategory]
    analyst_id: str | None
    comment: str | None
    created_at: datetime | None


class FeedbackList(BaseModel):
    items: list[FeedbackItem]
    page: int
    page_size: int
    total: int
    search_scope: Literal["metadata_only"] = "metadata_only"


class FeedbackCategoryCount(BaseModel):
    category: FeedbackCategory
    count: int
    percent: float


class FeedbackSummary(BaseModel):
    registration_count: int
    analysis_count: int
    multi_category_count: int
    comment_only_count: int
    category_counts: list[FeedbackCategoryCount]
    search_scope: Literal["metadata_only"] = "metadata_only"


class FeedbackVersion(UTCResponse):
    id: str
    label: str
    count: int
    analysis_count: int
    configuration_hash: str | None
    applied_at: datetime | None


class FeedbackVersions(BaseModel):
    items: list[FeedbackVersion]
    unrecorded_count: int
    unrecorded_analysis_count: int
