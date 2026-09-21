"""Solo Ground Truth: mutable encrypted draft -> atomic immutable publication.

No review-status decision, model call, or mutation of historical results here.
Legacy datasets are copied lazily under a write lock, not auto-published.
"""
import json
import uuid

from sqlalchemy import func, select

from ..models import (Analysis, TestRunItem, ValidationDatasetItem, ValidationDatasetVersion,
                      ValidationDatasetWorkingItem as Item, ValidationDatasetWorkingState as State, utcnow)
from ..schemas import AnalysisInput
from . import validation_datasets as legacy
from .analysis import AnalysisIngestError
from .change_events import record_change
from .input_schemas import pin_schema, validate_event
from .manual_references import audit, latest_reference, utc_datetime
from .test_runs import MAX_TEST_ITEMS, write_lock

COPY_FIELDS = ("dataset_id", "item_id", "event_ciphertext", "schema_snapshot_ciphertext",
    "encryption_key_version", "input_hash", "reference_verdict", "source_kind", "source_ref",
    "source_label_id", "source_created_by", "ai_visible", "comment_ciphertext", "difficulty",
    "test_category", "case_name", "internal_only", "original_analysis_id", "original_analysis_deleted")
EDIT_FIELDS = ("reference_verdict", "difficulty", "test_category", "case_name", "excluded", "tags")


def latest_published(db, dataset_id):
    return db.scalar(select(ValidationDatasetVersion).where(ValidationDatasetVersion.dataset_id == dataset_id,
        ValidationDatasetVersion.is_published.is_(True)).order_by(ValidationDatasetVersion.revision.desc()).limit(1))


def content_hash(item, crypto):
    # Include the original observation ID: duplicate detection deliberately does
    # not, but editing any part of the observation is a visible working change.
    return legacy.digest({"event": json.loads(crypto.decrypt_text(item.event_ciphertext)),
        "comment": crypto.decrypt_text(item.comment_ciphertext) if item.comment_ciphertext else "",
        **{key: getattr(item, key) for key in EDIT_FIELDS},
        "source_kind": item.source_kind, "source_ref": item.source_ref, "internal_only": item.internal_only})


def validate(item, crypto):
    # A duplicate import must not silently choose between contradictory
    # answers. Keep that issue until the analyst explicitly saves the case.
    issues = [issue for issue in (item.validation_issues_json or []) if issue.get("code") == "reference_conflict"]
    if item.reference_verdict not in {"true_positive", "false_positive", "inconclusive"}:
        issues.append({"code": "reference_verdict_required"})
    try:
        event = json.loads(crypto.decrypt_text(item.event_ciphertext))
        AnalysisInput.model_validate(event)
        snapshot = json.loads(crypto.decrypt_text(item.schema_snapshot_ciphertext))
        # Do not retain values or exception messages in validation metadata.
        if validate_event(snapshot["fields"], event):
            issues.append({"code": "event_schema_invalid"})
    except (ValueError, KeyError, TypeError):
        issues.append({"code": "event_invalid"})
    item.validation_issues_json = issues
    item.validation_state = "needs_attention" if issues else "ready"
    item.content_hash = content_hash(item, crypto)


def from_snapshot(old, crypto, metadata=None):
    item = Item(**{key: getattr(old, key) for key in COPY_FIELDS}, id=str(uuid.uuid4()),
        created_by=old.created_by, created_at=old.created_at, updated_at=old.created_at,
        excluded=False, tags=(metadata or {}).get("tags", []))
    validate(item, crypto)
    return item


def rows(db, identifier, crypto):
    row, version = legacy.dataset(db, identifier)
    state = db.get(State, identifier)
    if state:
        return row, state, list(db.scalars(select(Item).where(Item.dataset_id == identifier)
            .order_by(Item.created_at, Item.item_id)))
    # A read does not allocate a draft or write audit/data. Legacy latest inputs
    # are visible as un-published draft copies until the first edit/publication.
    return row, None, [from_snapshot(old, crypto) for old in legacy.version_items(db, version)]


def locked(db, identifier, crypto, expected):
    write_lock(db)
    row, state, items = rows(db, identifier, crypto)
    if (state.working_revision if state else 0) != expected:
        raise AnalysisIngestError("ground_truth_working_changed", 409)
    if state is None:
        state = State(dataset_id=identifier, working_revision=0)
        db.add(state)
        db.add_all(items)
        db.flush()
    return row, state, items


def changed(db, state, actor, action):
    state.working_revision += 1
    state.updated_at = utcnow()
    audit(db, actor, action, "validation_dataset", state.dataset_id)
    db.flush()


def update_metadata(db, identifier, payload, crypto, actor):
    row, state, _ = locked(db, identifier, crypto, payload.expected_working_revision)
    row.name, row.description = payload.name.strip(), payload.description
    changed(db, state, actor, "rename_ground_truth_draft")
    db.commit()
    return {"id": row.id, "working_revision": state.working_revision}


def item_summary(item, change):
    return {"id": item.item_id, "item_id": item.item_id, "change": change,
        "state": "excluded" if item.excluded else item.validation_state,
        "issues": item.validation_issues_json, "updated_at": utc_datetime(item.updated_at),
        **{key: getattr(item, key) for key in (*EDIT_FIELDS, "source_kind", "internal_only", "original_analysis_id")},
        "original_analysis_deleted": bool(item.original_analysis_deleted or
            (item.source_label_id and not item.original_analysis_id))}


def document(db, identifier, crypto, query=None):
    row, state, items = rows(db, identifier, crypto)
    published = latest_published(db, identifier)
    baseline = (published.publish_metadata or {}).get("cases", {}) if published else {}
    current = {item.item_id: item for item in items}
    counts = {key: 0 for key in ("ready", "needs_attention", "excluded")}
    changes = {key: 0 for key in ("added", "changed", "removed", "unchanged")}
    listing = []
    for item in items:
        old = baseline.get(item.item_id)
        change = "added" if old is None else "unchanged" if old["content_hash"] == item.content_hash else "changed"
        entry = item_summary(item, change)
        counts[entry["state"]] += 1
        changes[change] += 1
        listing.append(entry)
    if published:
        for old in legacy.version_items(db, published):
            if old.item_id not in current:
                changes["removed"] += 1
                listing.append(item_summary(from_snapshot(old, crypto, baseline.get(old.item_id)), "removed"))
    if query:
        listing = [entry for entry in listing if
            (not query.state or entry["state"] == query.state) and
            (not query.change or entry["change"] == query.change) and
            (not query.reference_verdict or entry["reference_verdict"] == query.reference_verdict) and
            (not query.source_kind or entry["source_kind"] == query.source_kind) and
            (not query.query.strip() or query.query.strip().casefold() in " ".join(str(entry.get(k) or "")
                for k in ("case_name", "test_category", "difficulty", "tags")).casefold())]
    offset, limit = (query.offset, query.limit) if query else (0, 50)
    return {"id": row.id, "name": row.name, "description": row.description, "revision": row.revision,
        "working_revision": state.working_revision if state else 0, "counts": counts, "changes": changes,
        "working_changes_count": sum(changes[k] for k in ("added", "changed", "removed")),
        "total": len(items), "filtered_total": len(listing), "items": listing[offset:offset + limit],
        "limit": limit, "offset": offset, "latest_published_revision_id": published.id if published else None,
        "published_revisions": [{"id": v.id, "revision": v.revision, "total": len(v.item_version_ids),
            "created_at": utc_datetime(v.created_at), "metadata": {k: val for k, val in (v.publish_metadata or {}).items() if k != "cases"}}
            for v in db.scalars(select(ValidationDatasetVersion).where(ValidationDatasetVersion.dataset_id == row.id,
                ValidationDatasetVersion.is_published.is_(True)).order_by(ValidationDatasetVersion.revision.desc()))]}


def read_item(db, identifier, case_id, crypto, actor):
    _, _, items = rows(db, identifier, crypto)
    item = next((i for i in items if i.item_id == case_id), None)
    removed = False
    if item is None:
        published = latest_published(db, identifier)
        old = next((i for i in legacy.version_items(db, published) if i.item_id == case_id), None) if published else None
        if old:
            item, removed = from_snapshot(old, crypto, (published.publish_metadata or {}).get("cases", {}).get(case_id)), True
    if item is None:
        raise AnalysisIngestError("dataset_item_not_found", 404)
    result = {**item_summary(item, "removed" if removed else None),
        "event": json.loads(crypto.decrypt_text(item.event_ciphertext)),
        "comment": crypto.decrypt_text(item.comment_ciphertext) if item.comment_ciphertext else "",
        "field_metadata": json.loads(crypto.decrypt_text(item.schema_snapshot_ciphertext))}
    audit(db, actor, "view_dataset_item", "validation_dataset_working_item", case_id)
    db.commit()
    return result


def write_item(db, identifier, payload, crypto, settings, actor, case_id=None):
    row, state, items = locked(db, identifier, crypto, payload.expected_working_revision)
    item = next((i for i in items if i.item_id == case_id), None)
    if case_id and not item:
        raise AnalysisIngestError("dataset_item_not_found", 404)
    event = payload.event
    try:
        encoded = json.dumps(event, ensure_ascii=False, allow_nan=False)
        fingerprint = legacy.input_digest(event)
        encoded_size = len(encoded.encode())
        payload_size = len(event["payload"].encode()) if isinstance(event.get("payload"), str) else 0
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise AnalysisIngestError("invalid_dataset_event", 422) from None
    if encoded_size > settings.payload_max_bytes + 65536 or payload_size > settings.payload_max_bytes:
        raise AnalysisIngestError("payload_too_large", 413)
    if any(i.input_hash == fingerprint and i is not item for i in items):
        raise AnalysisIngestError("dataset_duplicate_input", 409)
    if item is None:
        if len(items) >= MAX_TEST_ITEMS:
            raise AnalysisIngestError("dataset_item_limit", 422)
        item = Item(dataset_id=row.id, item_id=str(uuid.uuid4()), created_by=actor,
            source_kind="reference", internal_only=False, original_analysis_deleted=False)
    target = Analysis()
    pin_schema(db, crypto, target)
    item.schema_snapshot_ciphertext = target.input_schema_snapshot_ciphertext
    item.event_ciphertext = crypto.encrypt_text(encoded)
    item.encryption_key_version = crypto.key_version
    item.input_hash = fingerprint
    item.comment_ciphertext = crypto.encrypt_text(payload.comment)
    for key in EDIT_FIELDS:
        setattr(item, key, getattr(payload, key))
    item.updated_at = utcnow()
    item.validation_issues_json = []
    validate(item, crypto)
    db.add(item)
    changed(db, state, actor, "edit_ground_truth_draft")
    db.commit()
    return {"item": item_summary(item, None), "working_revision": state.working_revision}


def bulk(db, identifier, payload, crypto, actor):
    _, state, items = locked(db, identifier, crypto, payload.expected_working_revision)
    selected = [item for item in items if item.item_id in set(payload.case_ids)]
    if len(selected) != len(set(payload.case_ids)):
        raise AnalysisIngestError("dataset_item_not_found", 404)
    for item in selected:
        if payload.action == "delete":
            db.delete(item)
            continue
        if payload.action in {"include", "exclude"}:
            item.excluded = payload.action == "exclude"
        elif payload.action == "categorize":
            item.test_category = payload.test_category
        elif payload.action == "tag":
            item.tags = list(dict.fromkeys([*(item.tags or []), *payload.tags]))[:30]
        item.updated_at = utcnow()
        validate(item, crypto)
    changed(db, state, actor, "edit_ground_truth_draft")
    db.commit()
    return {"working_revision": state.working_revision}


def restore(db, identifier, payload, crypto, actor, case_id=None):
    _, state, items = locked(db, identifier, crypto, payload.expected_working_revision)
    published = latest_published(db, identifier)
    if not published:
        raise AnalysisIngestError("ground_truth_published_revision_required", 409)
    originals = legacy.version_items(db, published)
    if case_id:
        originals = [i for i in originals if i.item_id == case_id]
        if not originals:
            raise AnalysisIngestError("ground_truth_case_not_in_published_revision", 404)
    for item in items:
        if not case_id or item.item_id == case_id:
            db.delete(item)
    db.flush()
    for original in originals:
        db.add(from_snapshot(original, crypto, published.publish_metadata["cases"].get(original.item_id)))
    changed(db, state, actor, "restore_ground_truth_draft")
    db.commit()
    return {"working_revision": state.working_revision}


def publish(db, identifier, payload, crypto, actor):
    row, state, items = locked(db, identifier, crypto, payload.expected_working_revision)
    for item in items:
        validate(item, crypto)
    included = [item for item in items if not item.excluded and item.validation_state == "ready"]
    excluded = sum(i.excluded for i in items)
    attention = len(items) - len(included) - excluded
    if not included:
        raise AnalysisIngestError("ground_truth_ready_cases_required", 422)
    if len(included) != len(items) and not payload.acknowledge_exclusions:
        raise AnalysisIngestError("ground_truth_exclusion_ack_required", 409)
    previous = latest_published(db, identifier)
    cases = {item.item_id: {"content_hash": item.content_hash, "tags": item.tags} for item in included}
    metadata = {"working_total_at_publish": len(items), "included_ready_count": len(included),
        "needs_attention_count": attention, "excluded_count": excluded, "inclusion_rate": len(included) / len(items),
        "working_revision_at_publish": state.working_revision, "cases": cases}
    if previous and previous.publish_metadata == metadata:
        db.commit()
        return {"id": previous.id, "revision": previous.revision, "metadata": {k: v for k, v in metadata.items() if k != "cases"}}
    ids = []
    # Every member is immutable and fixed at this transaction. The approved
    # storage value is solely compatibility with the old table constraint.
    for item in included:
        revision = (db.scalar(select(func.max(ValidationDatasetItem.revision)).where(
            ValidationDatasetItem.item_id == item.item_id)) or 0) + 1
        snapshot = ValidationDatasetItem(**{key: getattr(item, key) for key in COPY_FIELDS},
            revision=revision, review_status="approved", created_by=actor)
        db.add(snapshot)
        db.flush()
        ids.append(snapshot.id)
    row.revision += 1
    version = ValidationDatasetVersion(dataset_id=row.id, revision=row.revision, name=row.name,
        description=row.description, item_version_ids=ids, is_published=True,
        membership_hash=legacy.digest(sorted(ids)), publish_metadata=metadata, created_by=actor)
    db.add(version)
    db.flush()
    record_change(db, category="configuration", actor=actor, action="publish_ground_truth_revision",
        resource_type="validation_dataset_version", resource_id=version.id,
        after={"dataset_id": row.id, "revision": version.revision, **{k: v for k, v in metadata.items() if k != "cases"}})
    audit(db, actor, "publish_ground_truth_revision", "validation_dataset_version", version.id)
    db.commit()
    return {"id": version.id, "revision": version.revision, "metadata": {k: v for k, v in metadata.items() if k != "cases"}}


def import_analyses(db, identifier, payload, crypto, settings, actor):
    row, state, items = locked(db, identifier, crypto, payload.expected_working_revision)
    seen = {i.input_hash: i for i in items}
    added, duplicates, conflicts, rejected = 0, 0, [], []
    conflict_changed = False
    for analysis_id in dict.fromkeys(payload.analysis_ids):
        original = db.get(Analysis, analysis_id)
        if original is None:
            raise AnalysisIngestError("analysis_not_found", 404)
        event = {key: getattr(original, key) for key in AnalysisInput.model_fields if key != "payload"}
        event.update(original.extra_fields)
        event["payload"] = crypto.decrypt_text(original.payload_ciphertext)
        fingerprint = legacy.input_digest(event)
        reference = latest_reference(db, analysis_id)
        verdict = reference.verdict if reference else None
        match = seen.get(fingerprint)
        internal = original.internal_only or original.analysis_purpose != "test"
        if match:
            if match.reference_verdict != verdict or internal and not match.internal_only:
                conflicts.append(analysis_id)
                new_issue = {"code": "reference_conflict"} not in (match.validation_issues_json or [])
                if new_issue or internal and not match.internal_only:
                    if new_issue:
                        match.validation_issues_json = [*(match.validation_issues_json or []), {"code": "reference_conflict"}]
                    # A duplicate cannot weaken the strongest known restriction.
                    match.internal_only = bool(match.internal_only or internal)
                    match.updated_at = utcnow()
                    validate(match, crypto)
                    conflict_changed = True
            else:
                duplicates += 1
            continue
        if len(seen) >= MAX_TEST_ITEMS:
            rejected.append({"analysis_id": analysis_id, "code": "dataset_item_limit"})
            continue
        source = db.scalar(select(TestRunItem).where(TestRunItem.analysis_id == analysis_id).limit(1))
        target = Analysis()
        pin_schema(db, crypto, target)
        item = Item(dataset_id=row.id, item_id=str(uuid.uuid4()), event_ciphertext=crypto.encrypt_text(json.dumps(event, ensure_ascii=False)),
            schema_snapshot_ciphertext=original.input_schema_snapshot_ciphertext or target.input_schema_snapshot_ciphertext,
            encryption_key_version=crypto.key_version, input_hash=fingerprint, reference_verdict=verdict,
            source_kind=reference.source_kind if reference else "reference", source_ref=reference.source_ref if reference else None,
            source_label_id=reference.id if reference else None, source_created_by=reference.created_by if reference else None,
            ai_visible=reference.ai_visible if reference else None, internal_only=bool(internal),
            original_analysis_id=original.id, original_analysis_deleted=False, excluded=False, tags=[], created_by=actor,
            comment_ciphertext=crypto.encrypt_text(crypto.decrypt_text(reference.comment_ciphertext) if reference and reference.comment_ciphertext else ""),
            **{key: getattr(source, key, None) for key in ("difficulty", "test_category", "case_name")})
        validate(item, crypto)
        db.add(item)
        seen[fingerprint] = item
        added += 1
    if added or conflict_changed:
        changed(db, state, actor, "import_ground_truth_draft")
    audit(db, actor, "copy_analyses_to_dataset", "validation_dataset", identifier)
    db.commit()
    return {"added": added, "duplicates": duplicates, "conflicts": conflicts, "rejected": rejected,
        "working_revision": state.working_revision}
