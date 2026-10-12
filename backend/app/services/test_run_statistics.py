"""Count-only Test statistics, with the same attempts/references as evaluation."""
from sqlalchemy import case, func, select

from ..models import Analysis, TestRunItem
from .analysis import AnalysisIngestError
from .analysis_query import contains_text
from .evaluation import COMPARABLE, MATCHES
from .test_runs import run_cohort, test_read_context

GROUP_FIELDS = {name: getattr(Analysis, name) for name in (
    "signature", "company_name", "waf_vendor", "waf_action", "event_name", "src_ip", "dest_ip")}
GROUP_FIELDS.update(difficulty=TestRunItem.difficulty, test_category=TestRunItem.test_category)


def group_condition(field, value=None, missing=False):
    if field is None:
        if value is not None or missing:
            raise AnalysisIngestError("test_group_field_required", 422)
        return None
    if field not in GROUP_FIELDS or (missing and value is not None) or (not missing and value is None):
        raise AnalysisIngestError("invalid_test_group_filter", 422)
    column = GROUP_FIELDS[field]
    return (column.is_(None) | (column == "")) if missing else column == value


def _counts(columns):
    c = columns
    count = lambda predicate: func.coalesce(func.sum(case((predicate, 1), else_=0)), 0)
    return [func.count().label("cases"),
        *[count(c.status == status).label(status) for status in ("completed", "failed", "pending", "processing", "canceled")],
        *[count((c.status == "completed") & (c.verdict == verdict)).label(verdict)
          for verdict in ("true_positive", "false_positive", "inconclusive")],
        count(c.outcome.in_(COMPARABLE)).label("comparable"), count(c.outcome.in_(MATCHES)).label("matches"),
        count(c.reference_verdict.is_not(None)).label("labeled"),
        count(c.outcome == "false_negative").label("false_negatives"),
        count(c.outcome == "false_positive").label("false_positives"),
        count(c.outcome == "abstained").label("abstained"),
        count(c.outcome == "expected_abstention_mismatch").label("expected_hold_mismatches")]


def present_counts(row):
    values = {key: row[key] for key in (
        "cases", "completed", "failed", "pending", "processing", "canceled", "true_positive", "false_positive",
        "inconclusive", "comparable", "matches", "labeled", "false_negatives", "false_positives", "abstained", "expected_hold_mismatches")}
    denominator = sum(values[key] for key in ("true_positive", "false_positive", "inconclusive"))
    values.update(distribution_total=denominator,
        verdict_rates={key: values[key] / denominator if denominator else None
            for key in ("true_positive", "false_positive", "inconclusive")},
        accuracy=values["matches"] / values["comparable"] if values["comparable"] else None,
        label_coverage=values["labeled"] / values["cases"] if values["cases"] else None,
        unclassified_completed=values["completed"] - denominator)
    return values


def statistics(db, run, *, group_by="signature", limit=25, offset=0, q=None, sort_by="cases", sort_order="desc",
               evaluation_id=None, reference_basis="latest", difficulty=None, test_category=None,
               difficulty_missing=False, test_category_missing=False):
    if group_by not in GROUP_FIELDS:
        raise AnalysisIngestError("invalid_test_statistics_field", 422)
    evaluation, _nodes, current, relation = test_read_context(db, run, evaluation_id=evaluation_id, reference_basis=reference_basis)
    cohort = run_cohort(run.id, difficulty, test_category, difficulty_missing, test_category_missing)
    base = select(func.nullif(GROUP_FIELDS[group_by], "").label("value"), Analysis.status, Analysis.verdict,
        relation.c.outcome, relation.c.reference_verdict).select_from(TestRunItem).join(
        current, current.c.item_id == TestRunItem.id).join(Analysis, Analysis.id == current.c.analysis_id).outerjoin(
        relation, relation.c.analysis_id == Analysis.id).where(*cohort, TestRunItem.ingest_status == "accepted").subquery()
    grouped = select(base.c.value, *_counts(base.c)).group_by(base.c.value).subquery()
    query = select(grouped)
    if q:
        query = query.where(contains_text(grouped.c.value, q))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    accuracy = grouped.c.matches * 1.0 / func.nullif(grouped.c.comparable, 0)
    column = {"cases": grouped.c.cases, "value": grouped.c.value, "accuracy": accuracy,
              "inconclusive": grouped.c.inconclusive}.get(sort_by, grouped.c.cases)
    order = column.desc().nulls_last() if sort_order == "desc" else column.asc().nulls_last()
    items = [{"value": row["value"], **present_counts(row)} for row in db.execute(
        query.order_by(order, grouped.c.value.asc().nulls_last()).limit(limit).offset(offset)).mappings()]
    summary = present_counts(db.execute(select(*_counts(base.c))).mappings().one())
    admissions = dict(db.execute(select(TestRunItem.ingest_status, func.count()).where(*cohort)
        .group_by(TestRunItem.ingest_status)).all())
    return {"test_run_id": run.id, "group_by": group_by, "items": items, "total_groups": total,
        "limit": limit, "offset": offset, "summary": summary,
        "rejected": admissions.get("rejected", 0), "duplicates": admissions.get("duplicate", 0),
        "reference_basis": "saved" if evaluation else "ground_truth" if run.evaluation_mode == "ground_truth" else reference_basis,
        "evaluation_id": evaluation.id if evaluation else None, "accuracy_includes_inconclusive": True}
