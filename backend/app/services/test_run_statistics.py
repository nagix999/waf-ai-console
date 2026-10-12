"""Count-only Test statistics, with the same attempts/references as evaluation."""
import json

from sqlalchemy import and_, case, func, or_, select

from ..models import Analysis, TestRunItem
from .analysis import AnalysisIngestError
from .analysis_query import contains_text
from .evaluation import COMPARABLE, MATCHES
from .test_runs import run_cohort, test_read_context

GROUP_FIELDS = {name: getattr(Analysis, name) for name in (
    "signature", "company_name", "waf_vendor", "waf_action", "event_name", "src_ip", "dest_ip")}
GROUP_FIELDS.update(difficulty=TestRunItem.difficulty, test_category=TestRunItem.test_category)


def group_fields(group_by):
    fields = [field.strip() for field in group_by.split(",")]
    if not 1 <= len(fields) <= 3 or len(set(fields)) != len(fields) or not all(fields):
        raise AnalysisIngestError("invalid_test_statistics_grouping", 422)
    if any(field not in GROUP_FIELDS for field in fields):
        raise AnalysisIngestError("invalid_test_statistics_field", 422)
    return fields


def group_condition(field, value=None, missing=False):
    if field is None:
        if value is not None or missing:
            raise AnalysisIngestError("test_group_field_required", 422)
        return None
    if field not in GROUP_FIELDS or (missing and value is not None) or (not missing and value is None):
        raise AnalysisIngestError("invalid_test_group_filter", 422)
    column = GROUP_FIELDS[field]
    return (column.is_(None) | (column == "")) if missing else column == value


def group_conditions(filters=None, *, field=None, value=None, missing=False):
    """Strict bounded tuple filter; legacy single-field requests still work."""
    if filters is None:
        return group_condition(field, value, missing)
    if field is not None or value is not None or missing:
        raise AnalysisIngestError("conflicting_test_group_filter", 422)
    try:
        entries = json.loads(filters)
    except (ValueError, TypeError, RecursionError):
        raise AnalysisIngestError("invalid_test_group_filter", 422) from None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 3:
        raise AnalysisIngestError("invalid_test_group_filter", 422)
    seen, conditions = set(), []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"field", "value"}:
            raise AnalysisIngestError("invalid_test_group_filter", 422)
        key, text = entry["field"], entry["value"]
        if (not isinstance(key, str) or key not in GROUP_FIELDS or key in seen or
                (text is not None and (not isinstance(text, str) or not 1 <= len(text) <= 4096))):
            raise AnalysisIngestError("invalid_test_group_filter", 422)
        seen.add(key)
        conditions.append(group_condition(key, text, text is None))
    return and_(*conditions)


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
    fields = group_fields(group_by)
    evaluation, _nodes, current, relation = test_read_context(db, run, evaluation_id=evaluation_id, reference_basis=reference_basis)
    cohort = run_cohort(run.id, difficulty, test_category, difficulty_missing, test_category_missing)
    names = ["value" if index == 0 else f"value_{index}" for index in range(len(fields))]
    dimensions = [func.nullif(GROUP_FIELDS[field], "").label(name) for field, name in zip(fields, names)]
    base = select(*dimensions, Analysis.status, Analysis.verdict,
        relation.c.outcome, relation.c.reference_verdict).select_from(TestRunItem).join(
        current, current.c.item_id == TestRunItem.id).join(Analysis, Analysis.id == current.c.analysis_id).outerjoin(
        relation, relation.c.analysis_id == Analysis.id).where(*cohort, TestRunItem.ingest_status == "accepted").subquery()
    grouped = select(*[base.c[name] for name in names], *_counts(base.c)).group_by(*[base.c[name] for name in names]).subquery()
    query = select(grouped)
    if q:
        query = query.where(or_(*[contains_text(grouped.c[name], q) for name in names]))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    accuracy = grouped.c.matches * 1.0 / func.nullif(grouped.c.comparable, 0)
    column = {"cases": grouped.c.cases, "value": grouped.c.value, "accuracy": accuracy,
              "inconclusive": grouped.c.inconclusive}.get(sort_by, grouped.c.cases)
    order = [column.desc().nulls_last() if sort_order == "desc" else column.asc().nulls_last()]
    # Value sorting applies to the whole tuple; count sorting has stable tuple tie breakers.
    if sort_by == "value":
        order.extend(grouped.c[name].desc().nulls_last() if sort_order == "desc" else grouped.c[name].asc().nulls_last()
                     for name in names[1:])
    else:
        order.extend(grouped.c[name].asc().nulls_last() for name in names)
    items = [{"value": row["value"], "values": {field: row[name] for field, name in zip(fields, names)},
              **present_counts(row)} for row in db.execute(
        query.order_by(*order).limit(limit).offset(offset)).mappings()]
    summary = present_counts(db.execute(select(*_counts(base.c))).mappings().one())
    admissions = dict(db.execute(select(TestRunItem.ingest_status, func.count()).where(*cohort)
        .group_by(TestRunItem.ingest_status)).all())
    return {"test_run_id": run.id, "group_by": ",".join(fields), "group_fields": fields, "items": items, "total_groups": total,
        "limit": limit, "offset": offset, "summary": summary,
        "rejected": admissions.get("rejected", 0), "duplicates": admissions.get("duplicate", 0),
        "reference_basis": "saved" if evaluation else "ground_truth" if run.evaluation_mode == "ground_truth" else reference_basis,
        "evaluation_id": evaluation.id if evaluation else None, "accuracy_includes_inconclusive": True}
