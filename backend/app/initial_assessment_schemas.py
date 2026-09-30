"""External first-pass metadata, not evidence or an evaluation answer."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class InitialAssessmentInput(BaseModel):
    initial_verdict: Literal["true_positive", "false_positive"] | None = None
    initial_probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False, strict=True)
    initial_model_version: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def initial_pair(self):
        if (self.initial_verdict is None) != (self.initial_probability is None):
            raise ValueError("initial_assessment_pair_required")
        if self.initial_model_version is not None and self.initial_verdict is None:
            raise ValueError("initial_assessment_pair_required")
        return self
