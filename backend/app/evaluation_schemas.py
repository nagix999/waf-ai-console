from datetime import UTC, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ReferenceVerdict = Literal["true_positive", "false_positive", "inconclusive"]
LabelSourceKind = Literal["synthetic_expected", "reference"]
AIVisibility = Literal["unknown", "true", "false"]
EvaluationOutcome = Literal[
    "unlabeled", "pending", "failed", "stub", "unknown_provenance", "input_contaminated",
    "match", "false_negative", "false_positive", "abstained",
    "expected_abstention_match", "expected_abstention_mismatch",
]
OUTCOMES = (
    "unlabeled", "pending", "failed", "stub", "unknown_provenance", "input_contaminated",
    "match", "false_negative", "false_positive", "abstained",
    "expected_abstention_match", "expected_abstention_mismatch",
)
MAX_PREVIEW_TOKEN_CHARS = 2 * 1024 * 1024


class ReferenceLabel(BaseModel):
    id: str
    revision: int
    verdict: ReferenceVerdict
    source_kind: LabelSourceKind
    source_ref: str
    ai_visible: bool | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_created_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class EvaluationMetadata(BaseModel):
    outcome: EvaluationOutcome = "unlabeled"
    reference_label: ReferenceLabel | None = None


class EvaluationConfusionMatrix(BaseModel):
    """Reference rows: attack/normal; prediction columns: attack/normal/hold.

    Positive means the reference verdict is ``true_positive`` (an attack).
    The application's ``false_positive`` verdict means normal, not this matrix's FP.
    Only comparable, binary-reference cases enter these six cells.
    """

    tp: int = Field(default=0, ge=0)
    fn: int = Field(default=0, ge=0)
    fp: int = Field(default=0, ge=0)
    tn: int = Field(default=0, ge=0)
    abstained_positive: int = Field(default=0, ge=0)
    abstained_negative: int = Field(default=0, ge=0)


class EvaluationMetrics(BaseModel):
    """Scores use decided binary cases, except the three explicit coverage rates.

    Fractions are 0..1 (MCC is -1..1), not percentages. Undefined denominators
    are null. Balanced accuracy and macro F1 require decided reference support
    for both classes; expected-hold references never enter binary metrics.
    """

    basis: Literal["decided_binary"] = "decided_binary"
    accuracy: float | None = Field(default=None, ge=0, le=1)
    precision: float | None = Field(default=None, ge=0, le=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    f1: float | None = Field(default=None, ge=0, le=1)
    specificity: float | None = Field(default=None, ge=0, le=1)
    false_positive_rate: float | None = Field(default=None, ge=0, le=1)
    false_negative_rate: float | None = Field(default=None, ge=0, le=1)
    balanced_accuracy: float | None = Field(default=None, ge=0, le=1)
    macro_f1: float | None = Field(default=None, ge=0, le=1)
    mcc: float | None = Field(default=None, ge=-1, le=1)
    coverage: float | None = Field(default=None, ge=0, le=1)
    abstention_rate: float | None = Field(default=None, ge=0, le=1)
    overall_binary_correct_rate: float | None = Field(default=None, ge=0, le=1)


class EvaluationBinarySummary(BaseModel):
    binary_evaluable: int = 0
    binary_decided: int = 0
    binary_correct: int = 0
    support_positive: int = 0
    support_negative: int = 0
    decided_support_positive: int = 0
    decided_support_negative: int = 0
    confusion_matrix: EvaluationConfusionMatrix = Field(default_factory=EvaluationConfusionMatrix)
    metrics: EvaluationMetrics = Field(default_factory=EvaluationMetrics)


class EvaluationSourceGroup(EvaluationBinarySummary):
    source_kind: LabelSourceKind
    ai_visible: bool | None
    labeled: int = 0
    evaluable: int = 0
    matches: int = 0
    false_negatives: int = 0
    false_positives: int = 0
    abstained: int = 0
    expected_abstention_matches: int = 0
    expected_abstention_mismatches: int = 0


class EvaluationSummary(EvaluationBinarySummary):
    total: int = 0
    labeled: int = 0
    evaluable: int = 0
    matches: int = 0
    label_coverage: float | None = Field(default=None, ge=0, le=1)
    outcomes: dict[str, int] = Field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    source_groups: list[EvaluationSourceGroup] = Field(default_factory=list)


class LabelPreviewRow(BaseModel):
    row_number: int
    event_id: str
    analysis_id: str
    current_revision: int
    current_label: ReferenceVerdict | None
    proposed_label: ReferenceVerdict
    change: bool


class LabelPreviewIssue(BaseModel):
    row_number: int
    code: str
    field: str | None = None


class LabelPreviewResponse(BaseModel):
    preview_token: str | None
    expires_at: datetime | None
    can_confirm: bool
    total_rows: int
    matched_count: int
    unchanged_count: int
    change_count: int
    source_system: str
    source_kind: LabelSourceKind
    source_ref: str
    ai_visible: bool | None
    rows: list[LabelPreviewRow]
    errors: list[LabelPreviewIssue]


class LabelConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_token: str = Field(min_length=1, max_length=MAX_PREVIEW_TOKEN_CHARS)


class LabelConfirmResponse(BaseModel):
    attachment_id: str
    applied_count: int
    unchanged_count: int
    duplicate: bool


class LabelHistoryItem(ReferenceLabel):
    created_by: str


class LabelHistoryResponse(BaseModel):
    items: list[LabelHistoryItem]
