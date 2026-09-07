"""Read-side reference comparison. The SQL expression is shared by all views.

No labels, comparison results, or references are copied into Analysis/event JSON.
"""
from datetime import UTC
from math import sqrt

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from ..evaluation_schemas import (
    EvaluationBinarySummary, EvaluationConfusionMatrix, EvaluationMetadata,
    EvaluationMetrics, EvaluationSourceGroup, EvaluationSummary, ReferenceLabel,
)
from ..models import Analysis, AnalysisLabel
from .label_fields import LABEL_FIELDS

COMPARABLE = ("match", "false_negative", "false_positive", "abstained", "expected_abstention_match", "expected_abstention_mismatch")
MATCHES = ("match", "expected_abstention_match")
BINARY = ("true_positive", "false_positive")


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def calculate_binary_metrics(matrix: EvaluationConfusionMatrix) -> EvaluationMetrics:
    """Calculate once on counts, never average percentages across source groups.

    Standard formulas: https://scikit-learn.org/stable/modules/model_evaluation.html
    (balanced-accuracy-score, matthews-correlation-coefficient, and
    multilabel-confusion-matrix), and the official f1_score reference. No sklearn
    runtime dependency is needed. Our explicit undefined-value policy differs
    from library defaults: zero denominators are null, and class-averaged scores
    require decided reference support for *both* classes.
    """
    tp, fn, fp, tn = matrix.tp, matrix.fn, matrix.fp, matrix.tn
    positive, negative = tp + fn, tn + fp
    decided = positive + negative
    abstained = matrix.abstained_positive + matrix.abstained_negative
    evaluable = decided + abstained
    recall, specificity = _ratio(tp, positive), _ratio(tn, negative)
    f1 = _ratio(2 * tp, 2 * tp + fp + fn)
    negative_f1 = _ratio(2 * tn, 2 * tn + fp + fn)
    both_classes = positive > 0 and negative > 0
    mcc_denominator = sqrt((tp + fp) * positive * negative * (tn + fn))
    mcc = _ratio(tp * tn - fp * fn, mcc_denominator)
    return EvaluationMetrics(
        accuracy=_ratio(tp + tn, decided), precision=_ratio(tp, tp + fp),
        recall=recall, f1=f1, specificity=specificity,
        false_positive_rate=_ratio(fp, negative), false_negative_rate=_ratio(fn, positive),
        balanced_accuracy=(recall + specificity) / 2 if both_classes else None,
        macro_f1=(f1 + negative_f1) / 2 if both_classes else None,
        mcc=max(-1.0, min(1.0, mcc)) if mcc is not None else None,
        coverage=_ratio(decided, evaluable), abstention_rate=_ratio(abstained, evaluable),
        overall_binary_correct_rate=_ratio(tp + tn, evaluable),
    )


def _add_binary_outcome(summary: EvaluationBinarySummary, reference: str, outcome: str, count: int) -> None:
    cell = {
        ("true_positive", "match"): "tp", ("false_positive", "match"): "tn",
        ("true_positive", "false_negative"): "fn", ("false_positive", "false_positive"): "fp",
        ("true_positive", "abstained"): "abstained_positive",
        ("false_positive", "abstained"): "abstained_negative",
    }.get((reference, outcome))
    if cell is not None:
        setattr(summary.confusion_matrix, cell, getattr(summary.confusion_matrix, cell) + count)


def _finalize_binary_summary(summary: EvaluationBinarySummary) -> None:
    matrix = summary.confusion_matrix
    summary.decided_support_positive = matrix.tp + matrix.fn
    summary.decided_support_negative = matrix.tn + matrix.fp
    summary.support_positive = summary.decided_support_positive + matrix.abstained_positive
    summary.support_negative = summary.decided_support_negative + matrix.abstained_negative
    summary.binary_evaluable = summary.support_positive + summary.support_negative
    summary.binary_decided = summary.decided_support_positive + summary.decided_support_negative
    summary.binary_correct = matrix.tp + matrix.tn
    summary.metrics = calculate_binary_metrics(matrix)


def latest_labels():
    revisions = select(
        AnalysisLabel.analysis_id, func.max(AnalysisLabel.revision).label("revision"),
    ).group_by(AnalysisLabel.analysis_id).subquery()
    return select(AnalysisLabel).join(revisions, and_(
        AnalysisLabel.analysis_id == revisions.c.analysis_id,
        AnalysisLabel.revision == revisions.c.revision,
    )).subquery()


def evaluation_relation(labels=None):
    labels = latest_labels() if labels is None else labels
    # Invalid legacy JSON must not crash a whole list/aggregate operation.
    result = case((func.json_valid(Analysis.result_json) == 1, Analysis.result_json), else_=literal("{}"))
    extras = case((func.json_valid(Analysis.extra_fields) == 1, Analysis.extra_fields), else_=literal("{}"))
    value = lambda path: func.json_extract(result, path)
    stub = or_(
        Analysis.model_profile.in_(("stub", "stub-no-llm")),
        Analysis.prompt_version.in_(("stub", "stub-v0")),
        value("$.policy.prompt_version").in_(("stub", "stub-v0")),
        value("$.agent.framework").in_(("stub", "local-stub")),
        value("$.agent.execution") == "stub",
        value("$.agent.agent_mode") == "stub",
        value("$.agent.llm_called") == 0,
    )
    contaminated = and_(func.json_type(extras) == "object", or_(*(
        func.json_type(extras, f'$."{key}"').is_not(None) for key in sorted(LABEL_FIELDS)
    )))
    real = and_(
        Analysis.status == "completed", Analysis.completed_at.is_not(None),
        func.json_type(result) == "object", func.json_type(result, "$.agent") == "object",
        value("$.agent.framework") == "moduagent",
        value("$.verdict").in_((*BINARY, "inconclusive")), value("$.verdict") == Analysis.verdict,
    )
    outcome = case(
        (labels.c.id.is_(None), "unlabeled"),
        (Analysis.status.in_(("pending", "processing")), "pending"),
        (Analysis.status == "failed", "failed"),
        (stub, "stub"),
        (contaminated, "input_contaminated"),
        (func.coalesce(real, False).is_(False), "unknown_provenance"),
        (and_(labels.c.verdict == "inconclusive", Analysis.verdict == "inconclusive"), "expected_abstention_match"),
        (labels.c.verdict == "inconclusive", "expected_abstention_mismatch"),
        (Analysis.verdict == "inconclusive", "abstained"),
        (labels.c.verdict == Analysis.verdict, "match"),
        (labels.c.verdict == "true_positive", "false_negative"),
        else_="false_positive",
    )
    return select(
        Analysis.id.label("analysis_id"), outcome.label("outcome"),
        labels.c.id.label("label_id"), labels.c.revision, labels.c.verdict.label("reference_verdict"),
        labels.c.source_kind, labels.c.source_ref, labels.c.ai_visible, labels.c.created_at,
    ).select_from(Analysis).outerjoin(labels, labels.c.analysis_id == Analysis.id).subquery()


def metadata_from_row(row) -> EvaluationMetadata:
    reference = None
    if row["label_id"] is not None:
        created_at = row["created_at"]
        reference = ReferenceLabel(
            id=row["label_id"], revision=row["revision"], verdict=row["reference_verdict"],
            source_kind=row["source_kind"], source_ref=row["source_ref"], ai_visible=row["ai_visible"],
            created_at=created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at,
        )
    return EvaluationMetadata(outcome=row["outcome"], reference_label=reference)


def attach_evaluations(db: Session, analyses: list[Analysis]) -> None:
    if not analyses:
        return
    relation = evaluation_relation()
    values = db.execute(select(relation).where(relation.c.analysis_id.in_([row.id for row in analyses]))).mappings()
    indexed = {row["analysis_id"]: metadata_from_row(row) for row in values}
    for row in analyses:
        row._evaluation = indexed.get(row.id, EvaluationMetadata())


def summarize_evaluations(db: Session, relation, conditions) -> EvaluationSummary:
    # Group counts only, not a page of ORM results or raw result/payload content.
    rows = db.execute(select(
        relation.c.outcome, relation.c.source_kind, relation.c.ai_visible, relation.c.reference_verdict,
        func.count().label("count"),
    ).select_from(Analysis).join(relation, relation.c.analysis_id == Analysis.id).where(*conditions)
        .group_by(relation.c.outcome, relation.c.source_kind, relation.c.ai_visible, relation.c.reference_verdict)).mappings()
    summary = EvaluationSummary()
    groups = {}
    for row in rows:
        count, outcome = row["count"], row["outcome"]
        summary.total += count
        summary.outcomes[outcome] += count
        if row["source_kind"] is None:
            continue
        summary.labeled += count
        key = (row["source_kind"], row["ai_visible"])
        group = groups.setdefault(key, EvaluationSourceGroup(source_kind=key[0], ai_visible=key[1]))
        group.labeled += count
        if outcome not in COMPARABLE:
            continue
        group.evaluable += count
        summary.evaluable += count
        if outcome in MATCHES:
            group.matches += count
            summary.matches += count
        _add_binary_outcome(group, row["reference_verdict"], outcome, count)
        _add_binary_outcome(summary, row["reference_verdict"], outcome, count)
        counter = {
            "false_negative": "false_negatives", "false_positive": "false_positives", "abstained": "abstained",
            "expected_abstention_match": "expected_abstention_matches",
            "expected_abstention_mismatch": "expected_abstention_mismatches",
        }.get(outcome)
        if counter:
            setattr(group, counter, getattr(group, counter) + count)
    _finalize_binary_summary(summary)
    summary.label_coverage = _ratio(summary.labeled, summary.total)
    for group in groups.values():
        _finalize_binary_summary(group)
    summary.source_groups = sorted(groups.values(), key=lambda group: (group.source_kind, str(group.ai_visible)))
    return summary
