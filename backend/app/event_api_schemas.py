"""Event-keyed service contracts; internal review identity stays server-side."""

from datetime import datetime
from typing import get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import FeedbackCategory, UTCResponse


class EventReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=255)
    feedback_categories: list[FeedbackCategory] = Field(
        default_factory=list,
        max_length=6,
        json_schema_extra={"uniqueItems": True, "default": []},
        description="선택형 피드백 항목. 정답·판정 변경과 별개이며 중복·null은 허용하지 않습니다.",
    )
    analyst_id: str | None = Field(default=None, max_length=255)
    comment: str | None = None

    @field_validator("event_id", "analyst_id", "comment")
    @classmethod
    def require_unicode_text(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("invalid_unicode_text") from None
        return value

    @field_validator("feedback_categories")
    @classmethod
    def normalize_categories(cls, value: list[FeedbackCategory]) -> list[FeedbackCategory]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate_feedback_category")
        return sorted(value, key=get_args(FeedbackCategory).index)


class EventReviewResponse(UTCResponse):
    event_id: str
    feedback_categories: list[FeedbackCategory]
    analyst_id: str | None
    comment: str | None
    created_at: datetime
