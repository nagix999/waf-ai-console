"""SQLite read model for draft lists. No event/comment/schema decryption here.

Published membership and case hashes remain authoritative. The SQL union keeps
removed cases visible, but excludes them from the current-draft denominators.
"""
import json
from collections import OrderedDict
from threading import RLock
from types import SimpleNamespace

from sqlalchemy import JSON, case, func, literal, select, true, type_coerce, union_all

from ..models import (ValidationDataset as Dataset, ValidationDatasetVersion as Version,
    ValidationDatasetItem as Snapshot, ValidationDatasetWorkingItem as Item,
    ValidationDatasetWorkingState as State)
from .analysis import AnalysisIngestError
from .manual_references import utc_datetime

STATES = ("ready", "needs_attention", "excluded")
CHANGES = ("added", "changed", "removed", "unchanged")
_legacy_lock = RLock()
FIELDS = ("dataset_id", "item_id", "reference_verdict", "difficulty", "test_category", "case_name",
    "source_kind", "internal_only", "original_analysis_id", "source_label_id", "original_analysis_deleted")


def published_versions():
    ranked = select(Version.id, Version.dataset_id, Version.revision,
        func.row_number().over(partition_by=Version.dataset_id, order_by=Version.revision.desc()).label("position")
        ).where(Version.is_published.is_(True)).subquery()
    return select(ranked.c.id, ranked.c.dataset_id).where(ranked.c.position == 1).subquery()


def legacy_validation(db, versions, crypto):
    """Bounded per-key/process cache of immutable validation metadata, NOT logs.

    Old versions have no persisted validation result. Their first read must
    validate, not guess that an answer makes an event valid. No GET writes a
    WorkingState, changes provenance or approves historic data. Cold reads use
    bounded chunks; subsequent reads never select/decrypt the snapshot blobs.
    The cache holds at most 10k small metadata records and no input or comment.
    """
    from .ground_truth_working import from_snapshot
    with _legacy_lock:
        cache = getattr(crypto, "_legacy_gt_validation", None)
        if cache is None:
            cache = crypto._legacy_gt_validation = OrderedDict()
        result = []
        for version in versions:
            cached = cache.get(version.id)
            if cached is None:
                cached = []
                ids = version.item_version_ids
                for start in range(0, len(ids), 200):
                    originals = db.execute(select(Snapshot.__table__).where(Snapshot.id.in_(ids[start:start + 200]))).mappings().all()
                    if len(originals) != len(ids[start:start + 200]):
                        raise AnalysisIngestError("dataset_version_unavailable", 503)
                    for original in originals:
                        item = from_snapshot(SimpleNamespace(**original), crypto)
                        cached.append({"id": original["id"], "state": item.validation_state, "issues": item.validation_issues_json})
                cache[version.id] = cached
                while sum(len(rows) for rows in cache.values()) > 10000 or len(cache) > 64:
                    cache.popitem(last=False)
            if version.id in cache:
                cache.move_to_end(version.id)
            result.extend(cached)
        return json.dumps(result, ensure_ascii=False)


def relation(dataset_ids, legacy_metadata="[]", *, summary_only=False):
    latest = published_versions()
    entries = func.json_each(Version.publish_metadata, "$.cases").table_valued("key", "value")
    # Materialize the *metadata only* expansion once. Without this, SQLite can
    # reparse a whole 5k-case JSON document for every working item.
    baseline = select(Version.dataset_id, entries.c.key.label("item_id"), entries.c.value.label("metadata"))\
        .join(latest, latest.c.id == Version.id).join(entries, true())\
        .where(Version.dataset_id.in_(dataset_ids)).cte("published_cases").prefix_with("MATERIALIZED")
    current = select(*(getattr(Item, k) for k in FIELDS), Item.excluded,
        case((Item.excluded.is_(True), "excluded"), else_=Item.validation_state).label("state"),
        Item.validation_issues_json.label("issues"), Item.tags, Item.reference_origin, Item.reference_origin_ref_id,
        Item.provenance_json.label("provenance"), Item.updated_at,
        case((baseline.c.item_id.is_(None), "added"),
             (func.json_extract(baseline.c.metadata, "$.content_hash") == Item.content_hash, "unchanged"),
             else_="changed").label("change"), literal(0).label("section"),
        Item.created_at.label("sort_at"), Item.item_id.label("sort_id"))\
        .select_from(Item).outerjoin(baseline, (baseline.c.dataset_id == Item.dataset_id) & (baseline.c.item_id == Item.item_id))\
        .where(Item.dataset_id.in_(dataset_ids))
    members = func.json_each(Version.item_version_ids).table_valued("key", "value")
    metadata = baseline.c.metadata
    removed = select(*(getattr(Snapshot, k) for k in FIELDS), literal(False).label("excluded"),
        literal("ready").label("state"), literal([], type_=JSON).label("issues"),
        type_coerce(func.coalesce(func.json_extract(metadata, "$.tags"), "[]"), JSON).label("tags"),
        func.coalesce(func.json_extract(metadata, "$.reference_origin"), case((Snapshot.source_label_id.is_not(None), "reference_label"), else_="none")).label("reference_origin"),
        case((func.json_type(metadata, "$.reference_origin_ref_id").is_not(None),
              func.json_extract(metadata, "$.reference_origin_ref_id")), else_=Snapshot.source_label_id).label("reference_origin_ref_id"),
        type_coerce(func.coalesce(func.json_extract(metadata, "$.provenance"), "{}"), JSON).label("provenance"),
        Snapshot.created_at.label("updated_at"), literal("removed").label("change"), literal(1).label("section"),
        Version.created_at.label("sort_at"), members.c.key.label("sort_id"))\
        .select_from(Version).join(latest, latest.c.id == Version.id).join(members, true())\
        .join(Snapshot, Snapshot.id == members.c.value)\
        .outerjoin(baseline, (baseline.c.dataset_id == Snapshot.dataset_id) & (baseline.c.item_id == Snapshot.item_id))\
        .where(Version.dataset_id.in_(dataset_ids), ~select(Item.id).where(
            Item.dataset_id == Snapshot.dataset_id, Item.item_id == Snapshot.item_id).exists())
    def combine(queries):
        if summary_only:
            queries = [query.with_only_columns(*(query.selected_columns[key] for key in (
                "dataset_id", "state", "change", "reference_origin"))) for query in queries]
        return union_all(*queries).subquery()
    if legacy_metadata == "[]":
        return combine([current, removed])
    legacy_json = func.json_each(legacy_metadata).table_valued("value")
    legacy_rows = select(func.json_extract(legacy_json.c.value, "$.id").label("id"), legacy_json.c.value).cte(
        "legacy_validation").prefix_with("MATERIALIZED")
    legacy_members = func.json_each(Version.item_version_ids).table_valued("key", "value")
    old = select(*(getattr(Snapshot, k) for k in FIELDS), literal(False).label("excluded"),
        func.json_extract(legacy_rows.c.value, "$.state").label("state"),
        type_coerce(func.json_extract(legacy_rows.c.value, "$.issues"), JSON).label("issues"),
        literal([], type_=JSON).label("tags"),
        case((Snapshot.source_label_id.is_not(None), "reference_label"), else_="none").label("reference_origin"),
        Snapshot.source_label_id.label("reference_origin_ref_id"), literal({}, type_=JSON).label("provenance"),
        Snapshot.created_at.label("updated_at"), literal("added").label("change"), literal(0).label("section"),
        Version.created_at.label("sort_at"), legacy_members.c.key.label("sort_id"))\
        .select_from(Dataset).join(Version, (Version.dataset_id == Dataset.id) & (Version.revision == Dataset.revision))\
        .join(legacy_members, true()).join(Snapshot, Snapshot.id == legacy_members.c.value)\
        .join(legacy_rows, Snapshot.id == legacy_rows.c.id)\
        .where(Dataset.id.in_(dataset_ids), ~select(State.dataset_id).where(State.dataset_id == Dataset.id).exists())
    return combine([current, removed, old])


def summaries(db, dataset_ids, legacy_metadata="[]"):
    if not dataset_ids:
        return {}
    rows = relation(dataset_ids, legacy_metadata, summary_only=True)
    grouped = db.execute(select(rows.c.dataset_id, rows.c.state, rows.c.change, rows.c.reference_origin,
        func.count().label("count")).group_by(rows.c.dataset_id, rows.c.state, rows.c.change, rows.c.reference_origin)).mappings()
    result = {identifier: {"counts": dict.fromkeys(STATES, 0), "changes": dict.fromkeys(CHANGES, 0),
        "total": 0, "included_reference_origin_counts": {}} for identifier in dataset_ids}
    for row in grouped:
        summary = result[row["dataset_id"]]
        summary["changes"][row["change"]] += row["count"]
        if row["change"] != "removed":
            summary["counts"][row["state"]] += row["count"]
            summary["total"] += row["count"]
            if row["state"] == "ready":
                origins = summary["included_reference_origin_counts"]
                origins[row["reference_origin"]] = origins.get(row["reference_origin"], 0) + row["count"]
    for summary in result.values():
        summary["working_changes_count"] = sum(summary["changes"][k] for k in CHANGES if k != "unchanged")
    return result


def published_metadata(db, dataset_ids):
    values = db.execute(select(Version.dataset_id, Version.id, Version.revision, Version.created_at,
        func.json_array_length(Version.item_version_ids).label("total"),
        func.json_remove(Version.publish_metadata, "$.cases").label("metadata"))
        .where(Version.dataset_id.in_(dataset_ids), Version.is_published.is_(True))
        .order_by(Version.revision.desc())).mappings()
    result = {identifier: [] for identifier in dataset_ids}
    for row in values:
        result[row["dataset_id"]].append({"id": row["id"], "revision": row["revision"], "total": row["total"],
            "created_at": utc_datetime(row["created_at"]), "metadata": json.loads(row["metadata"] or "{}")})
    return result


def serialize_item(row):
    return {"id": row["item_id"], **{key: row[key] for key in (
        "item_id", "reference_verdict", "difficulty", "test_category", "case_name", "source_kind",
        "internal_only", "original_analysis_id", "excluded", "state", "issues", "tags", "reference_origin",
        "reference_origin_ref_id", "provenance", "change")}, "updated_at": utc_datetime(row["updated_at"]),
        "original_analysis_deleted": bool(row["original_analysis_deleted"] or (row["source_label_id"] and not row["original_analysis_id"]))}


def legacy_metadata_for(db, dataset_ids, crypto):
    versions = db.scalars(select(Version).join(Dataset,
        (Version.dataset_id == Dataset.id) & (Version.revision == Dataset.revision)).where(
        Dataset.id.in_(dataset_ids), ~select(State.dataset_id).where(State.dataset_id == Dataset.id).exists())).all()
    return legacy_validation(db, versions, crypto) if versions else "[]"


def document(db, row, state, query, crypto):
    from ..validation_data_schemas import WorkingSearch
    query = query or WorkingSearch()
    legacy_metadata = legacy_metadata_for(db, [row.id], crypto) if state is None else "[]"
    values = relation([row.id], legacy_metadata)
    conditions = []
    for key in ("state", "change", "reference_verdict", "source_kind"):
        if getattr(query, key):
            conditions.append(values.c[key] == getattr(query, key))
    if query.query.strip():
        # Python casefold and list repr preserve the previous Unicode/tag search
        # contract. Only safe metadata reaches the SQLite scalar, never raw data.
        conditions.append(func.waf_case_search(values.c.case_name, values.c.test_category,
            values.c.difficulty, values.c.tags, query.query.strip()) == 1)
    summary = summaries(db, [row.id], legacy_metadata)[row.id]
    total = db.scalar(select(func.count()).select_from(values).where(*conditions)) if conditions else sum(summary["changes"].values())
    items = db.execute(select(values).where(*conditions).order_by(values.c.section, values.c.sort_at, values.c.sort_id)
        .limit(query.limit).offset(query.offset)).mappings()
    published = published_metadata(db, [row.id])[row.id]
    return {"id": row.id, "name": row.name, "description": row.description, "revision": row.revision,
        "working_revision": state.working_revision if state else 0, **summary,
        "filtered_total": total, "items": [serialize_item(item) for item in items], "limit": query.limit, "offset": query.offset,
        "latest_published_revision_id": published[0]["id"] if published else None, "published_revisions": published}


def dataset_summaries(db, datasets, crypto):
    """One grouped read per data class, not full documents per dataset."""
    identifiers = [row.id for row in datasets]
    if not identifiers:
        return {}
    legacy_metadata = legacy_metadata_for(db, identifiers, crypto)
    result = summaries(db, identifiers, legacy_metadata)
    published = published_metadata(db, identifiers)
    revisions = dict(db.execute(select(State.dataset_id, State.working_revision).where(State.dataset_id.in_(identifiers))).all())
    for row in datasets:
        result[row.id].update(id=row.id, name=row.name, working_revision=revisions.get(row.id, 0),
            published_revisions=published[row.id], latest_published_revision_id=published[row.id][0]["id"] if published[row.id] else None)
    return result


def catalog(db, datasets, crypto):
    identifiers = [row.id for row in datasets]
    if not identifiers:
        return []
    summaries_by_id = dataset_summaries(db, datasets, crypto)
    members = func.json_each(Version.item_version_ids).table_valued("value")
    memberships = select(Version.dataset_id, Version.id.label("version_id"), members.c.value.label("item_id"))\
        .join(Dataset, (Dataset.id == Version.dataset_id) & (Dataset.revision == Version.revision))\
        .outerjoin(members, true()).where(Dataset.id.in_(identifiers)).cte("catalog_members").prefix_with("MATERIALIZED")
    grouped = db.execute(select(memberships.c.dataset_id, memberships.c.version_id,
        func.count(Snapshot.id).label("total"), func.count(memberships.c.item_id).label("expected_total"),
        func.count(Snapshot.reference_verdict).label("labeled"),
        *(func.sum(case((Snapshot.review_status == state, 1), else_=0)).label(state) for state in ("draft", "reviewed", "approved")),
        func.max(Snapshot.internal_only).label("internal_only"))
        .select_from(memberships).outerjoin(Snapshot, Snapshot.id == memberships.c.item_id)
        .group_by(memberships.c.version_id)).mappings()
    versions = {row["dataset_id"]: row for row in grouped}
    result = []
    for row in datasets:
        version, data = versions[row.id], summaries_by_id[row.id]
        if version["total"] != version["expected_total"]:
            raise AnalysisIngestError("dataset_version_unavailable", 503)
        latest = data["published_revisions"][0] if data["published_revisions"] else None
        result.append({"id": row.id, "name": row.name, "description": row.description, "revision": row.revision,
            "version_id": version["version_id"], "total": version["total"], "labeled": version["labeled"],
            "review_counts": {state: version[state] or 0 for state in ("draft", "reviewed", "approved")},
            "internal_only": bool(version["internal_only"]), "created_at": utc_datetime(row.created_at),
            "latest_published_revision": latest, "published_case_count": latest["total"] if latest else 0,
            "working_revision": data["working_revision"], "working_change_count": data["working_changes_count"],
            **{f"{key}_count": value for key, value in data["counts"].items()}})
    return result
