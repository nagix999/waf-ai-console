"""Immutable dataset copies. No raw data or reference values are logged."""
import hashlib
import json
import uuid
from pydantic import ValidationError
from sqlalchemy import select
from ..models import Analysis, AnalysisLabel, TestRun, TestRunItem, ValidationDataset, ValidationDatasetItem, ValidationDatasetVersion, VLLMProfile, utcnow
from ..schemas import AnalysisInput
from .analysis import AnalysisIngestError
from .input_schemas import pin_schema, validate_event
from .manual_references import audit, latest_reference, utc_datetime
from .test_runs import MAX_TEST_ITEMS, add_run_items, create_run_record, write_lock


def digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def input_digest(event):
    return digest({key: value for key, value in event.items() if key != "event_id"})


def dataset(db, identifier, revision=None):
    row = db.get(ValidationDataset, identifier)
    if row is None or row.deleted_at:
        raise AnalysisIngestError("dataset_not_found", 404)
    if revision is not None and row.revision != revision:
        raise AnalysisIngestError("dataset_changed_reload", 409)
    version = db.scalar(select(ValidationDatasetVersion).where(
        ValidationDatasetVersion.dataset_id == row.id, ValidationDatasetVersion.revision == row.revision))
    return row, version


def version_items(db, version):
    if not version.item_version_ids:
        return []
    rows = {row.id: row for row in db.scalars(select(ValidationDatasetItem)
        .where(ValidationDatasetItem.id.in_(version.item_version_ids)))}
    if len(rows) != len(version.item_version_ids):
        raise AnalysisIngestError("dataset_version_unavailable", 503)
    return [rows[identifier] for identifier in version.item_version_ids]


def save_version(db, row, ids, actor, *, initial=False):
    if not initial:
        row.revision += 1
    version = ValidationDatasetVersion(dataset_id=row.id, revision=row.revision, name=row.name,
        description=row.description, item_version_ids=list(ids), created_by=actor)
    db.add(version)
    db.flush()
    audit(db, actor, "save_dataset_version", "validation_dataset_version", version.id)
    return version


def dataset_summary(db, row, version=None):
    version = version or db.scalar(select(ValidationDatasetVersion).where(
        ValidationDatasetVersion.dataset_id == row.id, ValidationDatasetVersion.revision == row.revision))
    items = version_items(db, version)
    return {"id": row.id, "name": version.name, "description": version.description, "revision": version.revision,
        "version_id": version.id, "total": len(items), "labeled": sum(item.reference_verdict is not None for item in items),
        "review_counts": {status: sum(item.review_status == status for item in items)
                          for status in ("draft", "reviewed", "approved")},
        "internal_only": any(item.internal_only for item in items), "created_at": utc_datetime(row.created_at)}


def create_dataset(db, payload, actor):
    write_lock(db)
    row = ValidationDataset(name=payload.name.strip(), description=payload.description, created_by=actor, revision=1)
    db.add(row)
    db.flush()
    version = save_version(db, row, [], actor, initial=True)
    db.commit()
    return dataset_summary(db, row, version)


def item_summary(item):
    return {**{key: getattr(item, key) for key in ("id", "item_id", "revision", "reference_verdict", "difficulty",
        "test_category", "case_name", "internal_only", "original_analysis_id", "original_analysis_deleted",
        "review_status", "created_by", "source_kind", "source_ref", "source_label_id", "source_created_by",
        "ai_visible")}, "created_at": utc_datetime(item.created_at)}


def read_item(db, crypto, dataset_id, item_id, actor, version_id=None):
    _, version = dataset(db, dataset_id)
    item = next((entry for entry in version_items(db, version) if entry.item_id == item_id), None)
    if version_id:
        item = db.scalar(select(ValidationDatasetItem).where(ValidationDatasetItem.id == version_id,
            ValidationDatasetItem.dataset_id == dataset_id, ValidationDatasetItem.item_id == item_id))
    if item is None:
        raise AnalysisIngestError("dataset_item_not_found", 404)
    result = {**item_summary(item), "event": json.loads(crypto.decrypt_text(item.event_ciphertext)),
        "comment": crypto.decrypt_text(item.comment_ciphertext) if item.comment_ciphertext else "",
        "field_metadata": json.loads(crypto.decrypt_text(item.schema_snapshot_ciphertext)),
        "history": [item_summary(old) for old in db.scalars(select(ValidationDatasetItem).where(
            ValidationDatasetItem.dataset_id == dataset_id, ValidationDatasetItem.item_id == item_id)
            .order_by(ValidationDatasetItem.revision.desc()))]}
    audit(db, actor, "view_dataset_item", "validation_dataset_item", item.id)
    db.commit()
    return result


def validated_copy(db, crypto, settings, event):
    try:
        payload = AnalysisInput.model_validate(event)
        document = payload.model_dump(mode="json")
        # Same per-event limit as ingestion. Metadata and answers are separate.
        if len(payload.payload.encode()) > settings.payload_max_bytes:
            raise AnalysisIngestError("payload_too_large", 413)
        target = Analysis()
        snapshot = pin_schema(db, crypto, target)
        issues = validate_event(snapshot["fields"], document)
        if issues:
            raise AnalysisIngestError("dataset_event_schema_invalid", 422, issues)
        input_digest(document)
        return document, target.input_schema_snapshot_ciphertext
    except (ValidationError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AnalysisIngestError):
            raise
        raise AnalysisIngestError("invalid_dataset_event", 422) from None


def new_item(db, crypto, row, event, schema, *, actor, old=None, original=None, **metadata):
    item = ValidationDatasetItem(dataset_id=row.id, item_id=old.item_id if old else str(uuid.uuid4()),
        revision=old.revision + 1 if old else 1, event_ciphertext=crypto.encrypt_text(json.dumps(event, ensure_ascii=False)),
        schema_snapshot_ciphertext=schema, encryption_key_version=crypto.key_version,
        input_hash=input_digest(event), reference_verdict=metadata.get("reference_verdict"),
        review_status="draft",
        source_kind=metadata.get("source_kind", old.source_kind if old else "reference"),
        source_ref=old.source_ref if old else metadata.get("source_ref"),
        source_label_id=old.source_label_id if old else metadata.get("source_label_id"),
        source_created_by=old.source_created_by if old else metadata.get("source_created_by"),
        ai_visible=metadata.get("ai_visible", old.ai_visible if old else True),
        comment_ciphertext=crypto.encrypt_text(metadata.get("comment", "")),
        difficulty=metadata.get("difficulty"), test_category=metadata.get("test_category"), case_name=metadata.get("case_name"),
        internal_only=bool((old and old.internal_only) or (original and (
            original.internal_only or original.analysis_purpose != "test"))),
        original_analysis_id=old.original_analysis_id if old else original.id if original else None,
        original_analysis_deleted=bool(old and old.original_analysis_deleted), created_by=actor)
    db.add(item)
    db.flush()
    return item


def write_item(db, crypto, settings, identifier, payload, actor, item_id=None):
    write_lock(db)
    row, version = dataset(db, identifier, payload.expected_revision)
    items = version_items(db, version)
    old = next((item for item in items if item.item_id == item_id), None)
    if item_id and old is None:
        raise AnalysisIngestError("dataset_item_not_found", 404)
    event, schema = validated_copy(db, crypto, settings, payload.event)
    if any(item.input_hash == input_digest(event) and item != old for item in items):
        raise AnalysisIngestError("dataset_duplicate_input", 409)
    if old is None and len(items) >= MAX_TEST_ITEMS:
        raise AnalysisIngestError("dataset_item_limit", 422)
    entry = new_item(db, crypto, row, event, schema, actor=actor, old=old,
        **payload.model_dump(exclude={"event", "expected_revision"}))
    ids = [entry.id if old and item.id == old.id else item.id for item in items]
    if old is None:
        ids.append(entry.id)
    save_version(db, row, ids, actor)
    db.commit()
    return {"item": item_summary(entry), "revision": row.revision}


def review_item(db, identifier, item_id, payload, actor):
    """Review creates immutable revisions; it never rewrites inputs or labels."""
    write_lock(db)
    row, version = dataset(db, identifier, payload.expected_revision)
    items = version_items(db, version)
    old = next((item for item in items if item.item_id == item_id), None)
    if old is None:
        raise AnalysisIngestError("dataset_item_not_found", 404)
    status = payload.review_status
    if status == old.review_status:
        return {"item": item_summary(old), "revision": row.revision}
    allowed = {"draft": {"reviewed"}, "reviewed": {"draft", "approved"}, "approved": {"draft"}}
    if status not in allowed[old.review_status]:
        raise AnalysisIngestError("dataset_review_transition_invalid", 409)
    if status != "draft" and old.reference_verdict is None:
        raise AnalysisIngestError("dataset_review_verdict_required", 422)
    # Copy ciphertext unchanged, including its original key version. Review
    # does not silently revalidate/rewrite the case against today's schema.
    entry = ValidationDatasetItem(**{key: getattr(old, key) for key in (
        "dataset_id", "item_id", "event_ciphertext", "schema_snapshot_ciphertext", "encryption_key_version",
        "input_hash", "reference_verdict", "source_kind", "source_ref", "source_label_id", "source_created_by",
        "ai_visible", "comment_ciphertext", "difficulty", "test_category", "case_name", "internal_only",
        "original_analysis_id", "original_analysis_deleted")},
        revision=old.revision + 1, review_status=status, created_by=actor)
    db.add(entry)
    db.flush()
    save_version(db, row, [entry.id if item.id == old.id else item.id for item in items], actor)
    audit(db, actor, f"review_dataset_item_{status}", "validation_dataset_item", entry.id)
    db.commit()
    return {"item": item_summary(entry), "revision": row.revision}


def import_analyses(db, crypto, settings, identifier, payload, actor):
    write_lock(db)
    row, version = dataset(db, identifier, payload.expected_revision)
    items = version_items(db, version)
    seen = {item.input_hash: item for item in items}
    added, duplicate, conflicts, rejected = [], [], [], []
    ids = list(version.item_version_ids)
    for analysis_id in dict.fromkeys(payload.analysis_ids):
        original = db.get(Analysis, analysis_id)
        if original is None:
            raise AnalysisIngestError("analysis_not_found", 404)
        event = {field: getattr(original, field) for field in AnalysisInput.model_fields if field != "payload"}
        event.update(original.extra_fields)
        event["payload"] = crypto.decrypt_text(original.payload_ciphertext)
        try:
            event, schema = validated_copy(db, crypto, settings, event)
        except AnalysisIngestError as exc:
            rejected.append({"analysis_id": analysis_id, "code": exc.code})
            continue
        reference = latest_reference(db, analysis_id)
        expected = reference.verdict if reference else None
        matching = seen.get(input_digest(event))
        if matching:
            (duplicate if matching.reference_verdict == expected else conflicts).append(analysis_id)
            # Importing real data must not make an existing matching copy less
            # restricted. Do not silently promote an unrestricted entry: report
            # the conflict so it can be deliberately removed and imported again.
            if (original.analysis_purpose != "test" or original.internal_only) and not matching.internal_only:
                if analysis_id in duplicate:
                    duplicate.remove(analysis_id)
                if analysis_id not in conflicts:
                    conflicts.append(analysis_id)
            continue
        if len(ids) >= MAX_TEST_ITEMS:
            rejected.append({"analysis_id": analysis_id, "code": "dataset_item_limit"})
            continue
        source_item = db.scalar(select(TestRunItem).where(TestRunItem.analysis_id == analysis_id,
                                                       TestRunItem.ingest_status == "accepted"))
        entry = new_item(db, crypto, row, event, schema, actor=actor, original=original,
            reference_verdict=expected, source_kind=reference.source_kind if reference else "reference",
            source_ref=reference.source_ref if reference else None,
            source_label_id=reference.id if reference else None,
            source_created_by=reference.created_by if reference else None,
            ai_visible=reference.ai_visible if reference else None,
            comment=crypto.decrypt_text(reference.comment_ciphertext) if reference and reference.comment_ciphertext else "",
            **{key: getattr(source_item, key, None) for key in ("difficulty", "test_category", "case_name")})
        ids.append(entry.id)
        seen[entry.input_hash] = entry
        added.append(analysis_id)
    if added:
        save_version(db, row, ids, actor)
    audit(db, actor, "copy_analyses_to_dataset", "validation_dataset", row.id)
    db.commit()
    return {"added": len(added), "duplicates": len(duplicate), "conflicts": conflicts, "rejected": rejected, "revision": row.revision}


def require_internal_profiles(db, crypto, run):
    if run.execution_mode == "stub":
        return
    ids = [run.profile_id, run.profile_metadata.get("verifier_profile", {}).get("model_profile_id")]
    if run.evidence_editor_snapshot_ciphertext:
        ids.append(json.loads(crypto.decrypt_text(run.evidence_editor_snapshot_ciphertext))["profile_id"])
    for identifier in set(filter(None, ids)):
        profile = db.get(VLLMProfile, identifier)
        if profile is None or profile.provider != "vllm":
            raise AnalysisIngestError("dataset_internal_models_required", 409)


def execute_dataset(db, crypto, settings, identifier, payload, actor):
    write_lock(db)
    # Check a replay before the latest revision: a retry of an accepted request
    # must return its original run even if the dataset has since been edited.
    key = digest(["dataset-run", actor, identifier, payload.idempotency_key])
    request_parts = [identifier, payload.expected_revision, payload.name]
    if payload.candidate_configuration is not None:
        request_parts.append(payload.candidate_configuration.model_dump(mode="json"))
    if payload.evaluation_mode == "ground_truth":
        request_parts.append({"evaluation_mode": "ground_truth"})
    request_hash = digest(request_parts)
    existing = db.scalar(select(TestRun).where(TestRun.idempotency_key == key))
    if existing:
        if existing.request_hash != request_hash:
            raise AnalysisIngestError("test_run_idempotency_conflict", 409)
        return existing
    row, version = dataset(db, identifier, payload.expected_revision)
    entries = version_items(db, version)
    if payload.evaluation_mode == "ground_truth":
        if settings.agent_mode != "moduagent":
            raise AnalysisIngestError("official_evaluation_requires_llm", 409)
        entries = [entry for entry in entries if entry.review_status == "approved"]
        if not entries:
            raise AnalysisIngestError("approved_ground_truth_required", 422)
    if not entries:
        raise AnalysisIngestError("dataset_empty", 422)
    run, _ = create_run_record(db, crypto, settings, name=(payload.name or "").strip() or str(uuid.uuid4()),
        idempotency_key=key, request_hash=request_hash, kind="dataset", actor=actor, dataset_hash=digest([entry.id for entry in entries]),
        candidate_configuration=payload.candidate_configuration)
    run.dataset_version_id = version.id
    run.evaluation_mode = payload.evaluation_mode
    if payload.evaluation_mode == "ground_truth":
        run.approved_item_version_ids = [entry.id for entry in entries]
        from .official_evaluations import METRICS_VERSION
        run.metrics_version = METRICS_VERSION
        run.official_evaluation_pending = True
    if any(entry.internal_only for entry in entries):
        require_internal_profiles(db, crypto, run)
    rows, trusted = [], []
    for entry in entries:
        event = json.loads(crypto.decrypt_text(entry.event_ciphertext))
        # Preserve the entire observation, including the original event ID.
        # Case-scoped namespaces allow distinct inputs with the same event ID.
        event.update({key: getattr(entry, key) for key in ("difficulty", "test_category", "case_name")})
        if entry.reference_verdict:
            event["expected_verdict"] = entry.reference_verdict
        rows.append(event)
        trusted.append({"source_kind": entry.source_kind, "ai_visible": entry.ai_visible,
            "dataset_item_version_id": entry.id,
            "internal_only": entry.internal_only, "source_system": f"waf-internal-dataset-{run.id}-{entry.item_id}",
            "comment": crypto.decrypt_text(entry.comment_ciphertext) if entry.comment_ciphertext else ""})
    add_run_items(db, crypto, settings, run, rows, trusted_items=trusted, source_ref="validation-dataset")
    rejected = list(db.scalars(select(TestRunItem).where(TestRunItem.test_run_id == run.id, TestRunItem.ingest_status == "rejected")))
    if rejected:
        raise AnalysisIngestError("dataset_current_schema_invalid", 422,
            [{"row_number": item.row_number, "code": item.error_code} for item in rejected])
    audit(db, actor, "execute_validation_dataset", "test_run", run.id)
    db.commit()
    return run
