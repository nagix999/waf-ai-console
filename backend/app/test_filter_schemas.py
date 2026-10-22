"""Read-only POST bodies; filters never enter analysis or evaluation writes."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GroupField = Literal["signature", "company_name", "waf_vendor", "waf_action", "event_name", "src_ip", "dest_ip", "difficulty", "test_category"]
Verdict = Literal["true_positive", "false_positive", "inconclusive"]


class TestReadScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_filter: Any = None  # bounded AST validator returns payload-free errors
    evaluation_id: str | None = Field(None, max_length=36)
    reference_basis: Literal["initial", "latest"] = "latest"
    difficulty: str | None = Field(None, max_length=80)
    test_category: str | None = Field(None, max_length=120)
    difficulty_missing: bool = False
    test_category_missing: bool = False

    @model_validator(mode="after")
    def no_scope_conflict(self):
        if (self.difficulty_missing and self.difficulty is not None) or (self.test_category_missing and self.test_category is not None):
            raise ValueError("conflicting_test_group_filter")
        return self


class FilteredTestRead(TestReadScope):
    offset: int = Field(0, ge=0)
    sort_order: Literal["asc", "desc"] = "desc"


class FilteredStatisticsRead(FilteredTestRead):
    group_by: str = Field("signature", max_length=120)
    limit: int = Field(25, ge=1, le=100)
    q: str | None = Field(None, max_length=500)
    sort_by: Literal["cases", "value", "accuracy", "inconclusive"] = "cases"


class TestCaseFilters(TestReadScope):
    sort_by: Literal["row_number", "case_name", "status"] = "row_number"
    sort_order: Literal["asc", "desc"] = "asc"
    status: Literal["pending", "processing", "completed", "failed", "canceled"] | None = None
    evaluation_outcome: str | None = Field(None, max_length=80)
    reference_verdict: Verdict | None = None
    verdict: Verdict | None = None
    group_field: GroupField | None = None
    group_value: str | None = Field(None, max_length=4096)
    group_missing: bool = False
    group_filters: str | None = Field(None, max_length=32768)


class FilteredCasesRead(TestCaseFilters):
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class TestExportPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: TestCaseFilters = Field(default_factory=TestCaseFilters)


class TestExportRequest(TestExportPreview):
    columns: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(min_length=1, max_length=512)
    expected_count: int = Field(ge=0, le=5000, strict=True)
    preview_scope: str = Field(pattern=r"^[0-9a-f]{64}$")
