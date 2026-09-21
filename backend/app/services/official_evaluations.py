"""Approved answer evaluations. No model calls, label edits, or runtime changes."""
import hashlib
import json
import logging

from sqlalchemy import func, select

from ..models import AccessAudit, Analysis, TestEvaluation, TestRun, TestRunItem, ValidationDatasetVersion
from .test_attempts import EVALUATION_RETRY_AUDIT_ACTION, test_attempts
from .test_runs import describe_run, write_lock
from .test_runs import fixed_reference_relation
from .evaluation import summarize_evaluation_rows

METRICS_VERSION = "waf-three-way-v1"


def frozen_breakdowns(db, run):
    """Two grouped queries, no raw log reads and no per-category N+1 queries."""
    _, current = test_attempts(run.id)
    relation = fixed_reference_relation(run.id)
    fields = [relation.c[key] for key in ("outcome", "source_kind", "ai_visible", "reference_verdict", "prediction_verdict")]
    result = {}
    for key in ("test_category", "difficulty"):
        column = getattr(TestRunItem, key)
        rows = db.execute(select(column.label("group_name"), *fields, func.count().label("count"))
            .select_from(TestRunItem).join(current, current.c.item_id == TestRunItem.id)
            .join(relation, relation.c.analysis_id == current.c.analysis_id)
            .where(TestRunItem.test_run_id == run.id, TestRunItem.ingest_status == "accepted")
            .group_by(column, *fields)).mappings()
        grouped = {}
        for row in rows:
            grouped.setdefault(row["group_name"], []).append(row)
        result[key] = [{"name": name, "evaluation_summary": summarize_evaluation_rows(values).model_dump(mode="json")}
            for name, values in sorted(grouped.items(), key=lambda item: (item[0] is None, item[0] or ""))]
    return result


def ground_truth_metadata(db, run):
    if run.evaluation_mode != "ground_truth":
        return None
    version = db.get(ValidationDatasetVersion, run.dataset_version_id)
    approved = run.approved_item_version_ids or []
    metadata = {"dataset_id": version.dataset_id, "dataset_name": version.name,
            "dataset_revision": version.revision, "dataset_version_id": version.id,
            "approved_count": len(approved), "excluded_count": len(version.item_version_ids) - len(approved),
            "membership_hash": hashlib.sha256(json.dumps(sorted(approved)).encode()).hexdigest(),
            "metrics_version": run.metrics_version} if version else None
    if metadata and version.is_published:
        from .validation_datasets import digest
        scope_hash = digest({"scope": "published_membership", "filters": {}})
        metadata.update({"published": True, "dataset_revision_id": version.id,
            "sample_count": len(version.item_version_ids), "evaluation_scope_hash": scope_hash,
            "comparison_key": digest([version.dataset_id, version.id, scope_hash, run.metrics_version]),
            "publish_metadata": {k: v for k, v in (version.publish_metadata or {}).items() if k != "cases"}})
    return metadata


def finalize_official_evaluation(db, run_id):
    """Atomically freeze terminal attempts and initial approved labels, once.

    A failed run gets a record too; it is not a successful qualification. Retry
    admission marks the run pending again without modifying earlier records.
    """
    write_lock(db)
    run = db.get(TestRun, run_id)
    if not run or run.evaluation_mode != "ground_truth" or not run.official_evaluation_pending or run.accepting_items:
        return None
    if run.metrics_version != METRICS_VERSION:
        raise ValueError("official_evaluation_metrics_version_unsupported")
    _, current = test_attempts(run.id)
    if db.scalar(select(Analysis.id).join(current, current.c.analysis_id == Analysis.id).where(
            Analysis.status.in_(["pending", "processing"])).limit(1)):
        return None
    items = list(db.execute(select(TestRunItem, current.c.analysis_id).join(
        current, current.c.item_id == TestRunItem.id).where(TestRunItem.test_run_id == run.id)))
    approved = run.approved_item_version_ids or []
    version = db.get(ValidationDatasetVersion, run.dataset_version_id)
    if version and version.is_published and sorted(approved) != sorted(version.item_version_ids):
        raise ValueError("official_evaluation_membership_invalid")
    if not approved or sorted(item.dataset_item_version_id or "" for item, _ in items) != sorted(approved):
        raise ValueError("official_evaluation_membership_invalid")
    ids = sorted(identifier for _, identifier in items if identifier)
    labels = sorted(item.label_id for item, _ in items if item.label_id)
    key = hashlib.sha256(json.dumps([run.id, ids, labels, run.configuration_hash, METRICS_VERSION]).encode()).hexdigest()
    record = db.scalar(select(TestEvaluation).where(TestEvaluation.idempotency_key == key))
    if record is None:
        summary = describe_run(db, run, detail=False, reference_basis="initial")
        record = TestEvaluation(test_run_id=run.id,
            revision=(db.scalar(select(func.max(TestEvaluation.revision)).where(TestEvaluation.test_run_id == run.id)) or 0) + 1,
            label_ids=labels, analysis_ids_json=ids, evaluation_kind="ground_truth",
            metrics_version=METRICS_VERSION, configuration_hash=run.configuration_hash,
            summary_json={**summary.model_dump(mode="json"), "breakdowns": frozen_breakdowns(db, run)}, idempotency_key=key, created_by=run.created_by)
        db.add(record)
        db.flush()
        db.add(AccessAudit(actor_kind="worker", actor_id="official-evaluation",
            action=EVALUATION_RETRY_AUDIT_ACTION, resource_type="test_evaluation", resource_id=record.id))
    run.official_evaluation_pending = False
    db.commit()
    return record


def finalize_for_analysis(session_factory, analysis_id):
    from .test_attempts import reference_ancestors
    with session_factory() as db:
        if db.scalar(select(Analysis.analysis_purpose).where(Analysis.id == analysis_id)) != "test":
            return
        # Follow only this attempt's ancestors, not every run in the database.
        ancestors = reference_ancestors([analysis_id])
        identifier = db.scalar(select(TestRun.id).join(TestRunItem, TestRunItem.test_run_id == TestRun.id)
            .join(ancestors, ancestors.c.ancestor_id == TestRunItem.analysis_id).where(
                TestRunItem.ingest_status == "accepted", TestRun.official_evaluation_pending.is_(True)))
    if identifier:
        with session_factory() as db:
            finalize_official_evaluation(db, identifier)


def recover_pending_evaluations(session_factory, *, after=None, limit=5):
    """Bounded round-robin scan also recovers a crash after result commit."""
    with session_factory() as db:
        query = select(TestRun.id).where(TestRun.official_evaluation_pending.is_(True))
        if after:
            query = query.where(TestRun.id > after)
        identifiers = list(db.scalars(query.order_by(TestRun.id).limit(limit)))
    for identifier in identifiers:
        try:
            with session_factory() as db:
                finalize_official_evaluation(db, identifier)
        except Exception as exc:
            # A corrupt/incompatible record must not starve later runs.
            logging.getLogger("waf-worker").error("evaluation recovery failed error_type=%s", type(exc).__name__)
    return identifiers[-1] if len(identifiers) == limit else None
