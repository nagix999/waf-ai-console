"""Bounded feedback reads and full-scope SQL aggregates.

Reviews are append-only registrations. Counts do not select a latest review or
turn feedback into evaluation labels. No event payload, model result or Agent
history is selected. Opinion text is read only after selecting the list page.
Production provenance identifies the approved configuration at admission, not
a guarantee that every runtime setting matched that complete snapshot.
"""

from datetime import UTC, datetime, time, timedelta, timezone
from typing import get_args

from sqlalchemy import and_, case, func, or_, select

from ..feedback_schemas import FeedbackFilters, FeedbackItem, FeedbackList, FeedbackSummary, FeedbackVersions
from ..models import Analysis, ProductionPromotion, Review, TestRunItem
from ..schemas import FeedbackCategory

CATEGORIES = get_args(FeedbackCategory)
UNRECORDED = "__unrecorded__"
KST = timezone(timedelta(hours=9))


def category_selected(category):
    # EXISTS gives one vote per registration even for malformed historical
    # arrays containing the same allowed category more than once.
    elements = func.json_each(Review.feedback_categories).table_valued("value")
    return select(1).select_from(elements).where(elements.c.value == category).correlate(Review).exists()


def predicates(filters: FeedbackFilters, *, include_version=True):
    scope = []
    if filters.analysis_purpose != "all":
        scope.append(Analysis.analysis_purpose == filters.analysis_purpose)
    if filters.date_from:
        scope.append(Review.created_at >= datetime.combine(filters.date_from, time.min, KST).astimezone(UTC))
    if filters.date_to:
        end = filters.date_to + timedelta(days=1)
        scope.append(Review.created_at < datetime.combine(end, time.min, KST).astimezone(UTC))
    if filters.analysis_id:
        scope.append(Review.analysis_id == filters.analysis_id)
    if include_version and filters.production_configuration_id:
        scope.append(Analysis.analysis_purpose == "production")
        scope.append(Analysis.production_configuration_id.is_(None) if filters.production_configuration_id == UNRECORDED
                     else Analysis.production_configuration_id == filters.production_configuration_id)
    if filters.category:
        scope.append(category_selected(filters.category))
    if filters.q:
        # The opinion field may contain sensitive text and is deliberately not
        # a search index. Escape SQL wildcard characters rather than treating
        # user input as LIKE syntax or SQL.
        scope.append(or_(*(column.contains(filters.q, autoescape=True) for column in (
            Review.analysis_id, Review.event_id, Review.external_review_id,
            Review.source_system, Review.analyst_id, Analysis.company_name, Analysis.signature,
        ))))
    return scope


def query_from_reviews(*columns):
    return select(*columns).select_from(Review).join(Analysis, Analysis.id == Review.analysis_id)


def version_label(rules, applied_at):
    if isinstance(rules, str) and rules.strip():
        label = rules.removeprefix("waf-system-")
        if isinstance(applied_at, datetime):
            recorded = applied_at.replace(tzinfo=UTC) if applied_at.tzinfo is None else applied_at
            return f"{label} / {recorded.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S')} KST"
        return label
    return "버전 정보 미기록"


def version_projection():
    return (ProductionPromotion.configuration_hash,
            ProductionPromotion.snapshot_json["prompt"]["fixed_rules_version"].as_string().label("fixed_rules_version"))


def test_run_map(db, analysis_ids):
    if not analysis_ids:
        return {}
    from .test_attempts import reference_ancestors
    nodes = reference_ancestors(analysis_ids)
    # Start at the bounded page, then walk only its original retry lineage.
    return dict(db.execute(select(nodes.c.target_id, TestRunItem.test_run_id).join(
        TestRunItem, TestRunItem.analysis_id == nodes.c.ancestor_id).where(
        TestRunItem.ingest_status == "accepted")).all())


def feedback_projection():
    return query_from_reviews(
        Review.id, Review.analysis_id, Review.event_id, Review.source_system, Review.external_review_id,
        Review.feedback_categories, Review.analyst_id, Review.comment, Review.created_at,
        Analysis.analysis_purpose, Analysis.company_name, Analysis.signature, Analysis.verdict, Analysis.severity,
        Analysis.production_configuration_id, *version_projection(),
        ProductionPromotion.created_at.label("production_applied_at"),
    ).outerjoin(ProductionPromotion, ProductionPromotion.id == Analysis.production_configuration_id)


def describe_rows(db, rows):
    run_ids = test_run_map(db, [row["analysis_id"] for row in rows if row["analysis_purpose"] == "test"])
    items = []
    for row in rows:
        production = row["analysis_purpose"] == "production"
        identifier = row["production_configuration_id"] if production else None
        items.append(FeedbackItem(
            **{key: row[key] for key in (
                "id", "analysis_id", "event_id", "source_system", "external_review_id", "analyst_id", "comment",
                "created_at", "analysis_purpose", "company_name", "signature", "verdict", "severity")},
            feedback_categories=[category for category in CATEGORIES if category in (row["feedback_categories"] or [])],
            production_configuration_id=identifier,
            production_configuration_label=(version_label(row["fixed_rules_version"], row["production_applied_at"])
                                            if identifier else "운영 버전 미기록") if production else None,
            test_run_id=run_ids.get(row["analysis_id"]),
        ))
    return items


def feedback_list(db, filters, *, page, page_size):
    scope = predicates(filters)
    total = db.scalar(query_from_reviews(func.count(Review.id)).where(*scope))
    # Select only this page's review text. No whole-history text processing.
    rows = db.execute(feedback_projection().where(*scope)
        .order_by(Review.created_at.desc(), Review.id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings().all()
    return FeedbackList(items=describe_rows(db, rows), total=total, page=page, page_size=page_size)


def feedback_detail(db, review_id):
    rows = db.execute(feedback_projection().where(Review.id == review_id)).mappings().all()
    return describe_rows(db, rows)[0] if rows else None


def feedback_summary(db, filters):
    selected = {category: category_selected(category) for category in CATEGORIES}
    category_count = sum(case((condition, 1), else_=0) for condition in selected.values())
    def count_if(condition):
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
    counts = db.execute(query_from_reviews(
        func.count(Review.id).label("registration_count"),
        func.count(func.distinct(Review.analysis_id)).label("analysis_count"),
        count_if(category_count > 1).label("multi_category_count"),
        count_if(and_(func.json_array_length(Review.feedback_categories) == 0, Review.comment.is_not(None),
                      func.length(func.trim(Review.comment, " \t\r\n")) > 0))
            .label("comment_only_count"),
        *(count_if(condition).label(category) for category, condition in selected.items()),
    ).where(*predicates(filters))).mappings().one()
    total = counts["registration_count"]
    return FeedbackSummary(**{name: counts[name] for name in (
        "registration_count", "analysis_count", "multi_category_count", "comment_only_count")},
        category_counts=[{"category": category, "count": counts[category],
                          "percent": counts[category] * 100 / total if total else 0} for category in CATEGORIES])


def feedback_versions(db, filters):
    # Facets honor every filter except their own selected version. A Test
    # registration is never folded into the missing Production version group.
    rows = db.execute(query_from_reviews(
        Analysis.production_configuration_id, *version_projection(), ProductionPromotion.created_at.label("applied_at"),
        func.count(Review.id).label("count"), func.count(func.distinct(Review.analysis_id)).label("analysis_count"),
    ).outerjoin(ProductionPromotion, ProductionPromotion.id == Analysis.production_configuration_id)
        .where(*predicates(filters, include_version=False), Analysis.analysis_purpose == "production")
        .group_by(Analysis.production_configuration_id, *version_projection(), ProductionPromotion.created_at)
        .order_by(ProductionPromotion.created_at.desc(), Analysis.production_configuration_id.desc())).mappings().all()
    items = []
    unrecorded_count = unrecorded_analysis_count = 0
    for row in rows:
        identifier = row["production_configuration_id"]
        if identifier is None:
            unrecorded_count, unrecorded_analysis_count = row["count"], row["analysis_count"]
        items.append({"id": identifier or UNRECORDED,
            "label": version_label(row["fixed_rules_version"], row["applied_at"]) if identifier else "운영 버전 미기록",
            "count": row["count"], "analysis_count": row["analysis_count"],
            "configuration_hash": row["configuration_hash"], "applied_at": row["applied_at"]})
    return FeedbackVersions(items=items, unrecorded_count=unrecorded_count,
                            unrecorded_analysis_count=unrecorded_analysis_count)
