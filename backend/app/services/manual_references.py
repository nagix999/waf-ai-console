"""Administrator references and frozen rescoring. Never imported by the agent."""
import hashlib
import json
import uuid
from datetime import UTC
from sqlalchemy import func, select
from ..models import AccessAudit, Analysis, AnalysisLabel, TestEvaluation, TestRun, TestRunItem
from .analysis import AnalysisIngestError
from .test_runs import write_lock


def audit(db, actor, action, kind, identifier):
    db.add(AccessAudit(actor_kind="admin_session", actor_id=actor, action=action,
                       resource_type=kind, resource_id=identifier))


def utc_datetime(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def latest_reference(db, analysis_id):
    from .evaluation import latest_labels
    labels = latest_labels([analysis_id])
    identifier = db.scalar(select(labels.c.id).where(labels.c.analysis_id == analysis_id))
    return db.get(AnalysisLabel, identifier) if identifier else None


def reference_selection(db, ids):
    ids = list(dict.fromkeys(ids))
    existing = set(db.scalars(select(Analysis.id).where(Analysis.id.in_(ids))))
    if existing != set(ids):
        raise AnalysisIngestError("analysis_not_found", 404)
    return [{"analysis_id": identifier, "expected_revision": label.revision if label and label.analysis_id == identifier else 0,
             "verdict": label.verdict if label else None}
            for identifier in ids for label in [latest_reference(db, identifier)]]


def save_references(db, crypto, payload, actor):
    write_lock(db)
    ids = [target.analysis_id for target in payload.targets]
    if len(set(ids)) != len(ids):
        raise AnalysisIngestError("duplicate_reference_target", 422)
    attachment = str(uuid.uuid5(uuid.NAMESPACE_URL, f"manual-reference:{actor}:{payload.idempotency_key}"))
    digest = hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existing = list(db.scalars(select(AnalysisLabel).where(AnalysisLabel.attachment_id == attachment)))
    if existing:
        if any(row.token_digest != digest for row in existing):
            raise AnalysisIngestError("reference_idempotency_conflict", 409)
        return {"applied_count": len(existing), "duplicate": True}
    current = reference_selection(db, ids)
    revisions = {item["analysis_id"]: item["expected_revision"] for item in current}
    if any(revisions[target.analysis_id] != target.expected_revision for target in payload.targets):
        raise AnalysisIngestError("reference_changed_reload", 409)
    for target in payload.targets:
        db.add(AnalysisLabel(analysis_id=target.analysis_id, revision=target.expected_revision + 1,
            verdict=payload.verdict, source_kind="reference", source_ref="analyst-reference",
            ai_visible=True, created_by=actor, attachment_id=attachment, token_digest=digest,
            comment_ciphertext=crypto.encrypt_text(payload.comment), encryption_key_version=crypto.key_version))
    audit(db, actor, "save_manual_references", "evaluation_label_attachment", attachment)
    db.commit()
    return {"applied_count": len(ids), "duplicate": False}


def evaluation_record(row):
    return {"id": row.id, "revision": row.revision, "created_by": row.created_by, "created_at": utc_datetime(row.created_at)}


def create_evaluation(db, run_id, payload, actor):
    write_lock(db)
    run = db.get(TestRun, run_id)
    if run is None:
        raise AnalysisIngestError("test_run_not_found", 404)
    key = hashlib.sha256(f"{actor}:{run_id}:{payload.idempotency_key}".encode()).hexdigest()
    old = db.scalar(select(TestEvaluation).where(TestEvaluation.idempotency_key == key))
    if old:
        return evaluation_record(old)
    from .test_attempts import EVALUATION_RETRY_AUDIT_ACTION, test_attempts
    _nodes, current = test_attempts(run_id)
    ids = list(db.scalars(select(current.c.analysis_id).join(TestRunItem, TestRunItem.id == current.c.item_id).where(
        TestRunItem.ingest_status == "accepted")))
    if run.accepting_items or db.scalar(select(func.count()).select_from(Analysis).where(
            Analysis.id.in_(ids), Analysis.status.in_(["pending", "processing"]))):
        raise AnalysisIngestError("test_must_finish_before_rescoring", 409)
    revision = (db.scalar(select(func.max(TestEvaluation.revision)).where(TestEvaluation.test_run_id == run_id)) or 0) + 1
    from .evaluation import latest_labels
    labels = latest_labels(ids)
    row = TestEvaluation(test_run_id=run_id, revision=revision,
        label_ids=list(db.scalars(select(labels.c.id).order_by(labels.c.analysis_id))),
        idempotency_key=key, created_by=actor)
    db.add(row)
    db.flush()
    audit(db, actor, EVALUATION_RETRY_AUDIT_ACTION, "test_evaluation", row.id)
    db.commit()
    return evaluation_record(row)
