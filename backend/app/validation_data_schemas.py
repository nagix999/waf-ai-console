from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from .evaluation_schemas import ReferenceVerdict
from .candidate_schemas import CandidateConfiguration


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class AnalysisSelection(StrictModel):
    analysis_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(min_length=1, max_length=500)


class LabelTarget(StrictModel):
    analysis_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=0)


class BulkReference(StrictModel):
    targets: list[LabelTarget] = Field(min_length=1, max_length=500)
    verdict: ReferenceVerdict
    comment: str = Field(default="", max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")


class DatasetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    description: str = Field(default="", max_length=1000)


class DatasetUpdate(DatasetCreate):
    expected_revision: int = Field(ge=1)


class RevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)


class DatasetImport(AnalysisSelection):
    expected_revision: int = Field(ge=1)


class DatasetItemWrite(RevisionRequest):
    event: dict[str, Any]
    reference_verdict: ReferenceVerdict | None = None
    comment: str = Field(default="", max_length=4000)
    difficulty: str | None = Field(default=None, max_length=80)
    test_category: str | None = Field(default=None, max_length=120)
    case_name: str | None = Field(default=None, max_length=240)


DatasetReviewStatus = Literal["draft", "reviewed", "approved"]


class DatasetItemReview(RevisionRequest):
    review_status: DatasetReviewStatus


class DatasetRun(RevisionRequest):
    name: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    candidate_configuration: CandidateConfiguration | None = None
    evaluation_mode: Literal["reference", "ground_truth"] = "reference"


class EvaluationCreate(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")


class DatasetSearch(StrictModel):
    query: str = Field(default="", max_length=120)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class TestSessionCreate(EvaluationCreate):
    name: str | None = Field(default=None, max_length=120)
