"""Preview/confirm reuse of frozen test observations, not model answers."""
import json
import uuid
from collections import Counter
from datetime import timedelta
from sqlalchemy import select
from ..models import (Analysis, AnalysisLabel, GroundTruthImportPreview, TestRun, TestRunItem,
    ValidationDataset, ValidationDatasetItem, ValidationDatasetVersion, ValidationDatasetWorkingItem as Item,
    ValidationDatasetWorkingState as State, utcnow)
from ..schemas import AnalysisInput
from . import ground_truth_working as working, validation_datasets as legacy
from .analysis import AnalysisIngestError
from .change_events import record_change
from .manual_references import utc_datetime
from .test_runs import MAX_TEST_ITEMS, describe_run, write_lock
from .input_schemas import pin_schema


def source(db, crypto, run_id, selected_ids=None):
    run = db.get(TestRun, run_id)
    if run is None:
        raise AnalysisIngestError("test_run_not_found", 404)
    status = describe_run(db, run, detail=False).status
    if run.accepting_items or status not in {"completed", "failed"}:
        raise AnalysisIngestError("test_must_finish_before_import", 409)
    version = db.get(ValidationDatasetVersion, run.dataset_version_id) if run.dataset_version_id else None
    result = []
    for item in db.scalars(select(TestRunItem).where(TestRunItem.test_run_id == run.id).order_by(TestRunItem.row_number)):
        if selected_ids is not None and item.id not in selected_ids:
            continue
        entry = {"test_run_item_id": item.id, "row_number": item.row_number, "case_name": item.case_name,
                 "analysis_id": item.analysis_id, "category": "unavailable"}
        analysis = db.get(Analysis, item.analysis_id) if item.analysis_id and item.ingest_status == "accepted" else None
        if analysis:
            try:
                event = {**analysis.extra_fields, **{key: getattr(analysis, key) for key in AnalysisInput.model_fields if key != "payload"},
                    "payload": crypto.decrypt_text(analysis.payload_ciphertext)}
                AnalysisInput.model_validate(event)
            except (ValueError, TypeError):
                entry["reason"] = "stored_event_unavailable"
                result.append(entry)
                continue
            fixed = db.get(ValidationDatasetItem, item.dataset_item_version_id) if version and version.is_published and item.dataset_item_version_id in version.item_version_ids else None
            label = db.get(AnalysisLabel, item.label_id) if item.label_id else None
            verdict = fixed.reference_verdict if fixed else label.verdict if label else None
            origin = "published_ground_truth" if fixed else "reference_label" if label else "none"
            entry.update(input_hash=legacy.input_digest(event), observation_hash=legacy.digest(event),
                reference_verdict=verdict, reference_origin=origin,
                reference_origin_ref_id=fixed.id if fixed else label.id if label else None,
                internal_only=bool(analysis.internal_only or analysis.analysis_purpose != "test"),
                category="new" if verdict else "missing_reference", reason=None if verdict else "reference_verdict_missing",
                provenance={"source_test_run_id": run.id, "source_test_run_item_id": item.id,
                    "source_analysis_id": analysis.id, "source_dataset_id": version.dataset_id if version else None,
                    "source_dataset_revision_id": version.id if version else None,
                    "source_dataset_item_version_id": fixed.id if fixed else None,
                    "source_reference_label_id": label.id if label else None, "source_kind": origin})
        else:
            entry["reason"] = "rejected_or_unavailable"
        result.append(entry)
    if selected_ids is not None and {entry["test_run_item_id"] for entry in result} != set(selected_ids):
        raise AnalysisIngestError("test_import_membership_mismatch", 422)
    return run, result


def preview(db, crypto, payload, run_id, actor):
    write_lock(db)
    run, entries = source(db, crypto, run_id, payload.test_run_item_ids)
    target = None
    if payload.target == "append_to_existing_dataset":
        if not payload.dataset_id or payload.expected_working_revision is None:
            raise AnalysisIngestError("ground_truth_target_required", 422)
        dataset, state, items = working.rows(db, payload.dataset_id, crypto)
        revision = state.working_revision if state else 0
        if revision != payload.expected_working_revision:
            raise AnalysisIngestError("ground_truth_working_changed", 409)
        target = {"id": dataset.id, "name": dataset.name, "working_revision": revision}
    else:
        if payload.dataset_id is not None or payload.expected_working_revision is not None:
            raise AnalysisIngestError("invalid_ground_truth_target", 422)
        items = []
    seen = {i.input_hash: i.reference_verdict for i in items}
    manifest = {"source_hash": legacy.digest(entries), "target": payload.model_dump(), "entries": entries}
    for entry in entries:
        if entry["category"] == "unavailable":
            continue
        fingerprint = entry["input_hash"]
        if fingerprint in seen:
            entry["category"] = "duplicate" if seen[fingerprint] == entry["reference_verdict"] else "reference_conflict"
            entry["reason"] = "same_input_and_reference" if entry["category"] == "duplicate" else "reference_conflict"
        else:
            seen[fingerprint] = entry["reference_verdict"]
    counts = Counter(i["category"] for i in entries)
    if len(seen) > MAX_TEST_ITEMS:
        raise AnalysisIngestError("dataset_item_limit", 422)
    row = GroundTruthImportPreview(test_run_id=run_id, actor=actor,
        manifest_ciphertext=crypto.encrypt_text(json.dumps(manifest, ensure_ascii=False)), expires_at=utcnow() + timedelta(minutes=15))
    db.add(row)
    db.flush()
    # No raw observation/answer/comment in the preview response or audit.
    result = {"preview_token": row.id, "expires_at": utc_datetime(row.expires_at), "source_test_run_id": run_id,
        "source_total": len(entries), "importable": len(entries) - counts["unavailable"],
        "new_count": counts["new"] + counts["missing_reference"], "duplicate_count": counts["duplicate"],
        "reference_conflict_count": counts["reference_conflict"], "missing_reference_count": counts["missing_reference"],
        "unavailable_count": counts["unavailable"], "target_dataset_summary": target,
        "items": [{k: i[k] for k in ("test_run_item_id", "row_number", "case_name", "analysis_id", "category", "reason")} for i in entries]}
    db.commit()
    return result


def confirm(db, crypto, payload, run_id, actor):
    write_lock(db)
    preview_row = db.get(GroundTruthImportPreview, payload.preview_token)
    if not preview_row or preview_row.test_run_id != run_id or preview_row.actor != actor:
        raise AnalysisIngestError("ground_truth_preview_not_found", 404)
    if preview_row.confirmed_at:
        if preview_row.idempotency_key != payload.idempotency_key:
            raise AnalysisIngestError("ground_truth_import_already_confirmed", 409)
        return preview_row.result_json
    if utc_datetime(preview_row.expires_at) < utcnow():
        raise AnalysisIngestError("ground_truth_preview_expired", 409)
    if db.scalar(select(GroundTruthImportPreview.id).where(GroundTruthImportPreview.idempotency_key == payload.idempotency_key)):
        raise AnalysisIngestError("ground_truth_import_idempotency_conflict", 409)
    manifest = json.loads(crypto.decrypt_text(preview_row.manifest_ciphertext))
    run, current = source(db, crypto, run_id, manifest["target"].get("test_run_item_ids"))
    if legacy.digest(current) != manifest["source_hash"]:
        raise AnalysisIngestError("ground_truth_import_source_changed", 409)
    target = manifest["target"]
    if target["target"] == "create_new_dataset":
        dataset = ValidationDataset(name=((target.get("new_dataset_name") or "").strip() or f"{run.name} Ground Truth")[:120],
            description="", revision=1, created_by=actor)
        db.add(dataset)
        db.flush()
        legacy.save_version(db, dataset, [], actor, initial=True)
        state = State(dataset_id=dataset.id, working_revision=0)
        db.add(state)
        items = []
    else:
        dataset, state, items = working.locked(db, target["dataset_id"], crypto, target["expected_working_revision"])
    seen = {i.input_hash: i for i in items}
    added, conflicts, duplicates = 0, 0, 0
    for entry in current:
        if entry["category"] == "unavailable":
            continue
        match = seen.get(entry["input_hash"])
        if match:
            if match.reference_verdict != entry["reference_verdict"]:
                conflicts += 1
                match.validation_issues_json = [{"code": "reference_conflict"}]
            else:
                duplicates += 1
            match.internal_only = bool(match.internal_only or entry["internal_only"])
            working.validate(match, crypto)
            continue
        if len(seen) >= MAX_TEST_ITEMS:
            raise AnalysisIngestError("dataset_item_limit", 422)
        analysis = db.get(Analysis, entry["analysis_id"])
        source_item = db.get(TestRunItem, entry["test_run_item_id"])
        reference = db.get(ValidationDatasetItem, entry["reference_origin_ref_id"]) if entry["reference_origin"] == "published_ground_truth" else db.get(AnalysisLabel, entry["reference_origin_ref_id"]) if entry["reference_origin"] == "reference_label" else None
        event = {**analysis.extra_fields, **{key: getattr(analysis, key) for key in AnalysisInput.model_fields if key != "payload"},
            "payload": crypto.decrypt_text(analysis.payload_ciphertext)}
        schema = Analysis()
        pin_schema(db, crypto, schema)
        item = Item(dataset_id=dataset.id, item_id=str(uuid.uuid4()), event_ciphertext=crypto.encrypt_text(json.dumps(event, ensure_ascii=False)),
            schema_snapshot_ciphertext=schema.input_schema_snapshot_ciphertext, encryption_key_version=crypto.key_version,
            input_hash=entry["input_hash"], reference_verdict=entry["reference_verdict"], reference_origin=entry["reference_origin"],
            reference_origin_ref_id=entry["reference_origin_ref_id"], provenance_json={**entry["provenance"], "imported_at": utcnow().isoformat()},
            source_kind=reference.source_kind if reference else "reference", source_ref=reference.source_ref if reference else f"test:{run.id}",
            ai_visible=reference.ai_visible if reference else None,
            source_created_by=(getattr(reference, "source_created_by", None) or reference.created_by) if reference else None,
            comment_ciphertext=reference.comment_ciphertext if reference else None,
            source_label_id=entry["provenance"]["source_reference_label_id"],
            original_analysis_id=analysis.id, original_analysis_deleted=False, internal_only=entry["internal_only"],
            excluded=False, tags=[], created_by=actor,
            **{key: getattr(source_item, key) for key in ("case_name", "difficulty", "test_category")})
        working.validate(item, crypto)
        db.add(item)
        seen[item.input_hash] = item
        added += 1
    working.changed(db, state, actor, "import_test_ground_truth_draft")
    result = {"dataset_id": dataset.id, "working_revision": state.working_revision, "added": added,
        "duplicates": duplicates, "conflicts": conflicts, "published": False}
    record_change(db, category="configuration", actor=actor, action="import_test_ground_truth_draft",
        resource_type="validation_dataset", resource_id=dataset.id, after=result)
    preview_row.confirmed_at, preview_row.idempotency_key, preview_row.result_json = utcnow(), payload.idempotency_key, result
    db.commit()
    return result
