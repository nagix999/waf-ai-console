"""Candidate-only capture interpretation; public result contracts stay unchanged.

Capture assessment is an uncertain model interpretation, not proof that a WAF
discarded request bytes. It never changes the existing verdict dependencies.
"""

from typing import Literal

from pydantic import Field, field_validator

from .contracts import SecurityAnalysisOutputV216, SecurityAnalysisOutputV220, StrictContract


class CaptureAssessment(StrictContract):
    status: Literal["possible", "no_clear_signs", "unknown"]
    reason_ko: str = Field(min_length=1, max_length=240, strict=True)

    @field_validator("reason_ko")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("capture_assessment_reason_required")
        return value


class SecurityAnalysisOutputV224(SecurityAnalysisOutputV220):
    """Internal wire extension, deliberately absent from the legacy bridge."""

    capture_assessment: CaptureAssessment

    def legacy_bridge(self):
        # The inherited full validators still run on the original wire output.
        # Only private capture interpretation and relationship metadata are
        # excluded from the established grounding/policy/public-result adapter.
        values = self.model_dump(mode="json", exclude={"evidence_relations", "capture_assessment"})
        return SecurityAnalysisOutputV216.model_validate(values).legacy_bridge()
