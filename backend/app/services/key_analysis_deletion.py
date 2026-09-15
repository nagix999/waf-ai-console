"""Explicit key-scoped purge; never expand by source_system or remove audits."""
import hashlib
import json

from sqlalchemy import delete, exists, func, select, update

from ..api_key_schemas import ServiceApiKeyDeletionPreview
from ..models import AccessAudit, Analysis, AnalysisLabel, TestRunItem, ValidationDatasetItem
from .service_api_keys import ServiceApiKeyError, get_key


def targets(key_id):
    return select(Analysis.id).where(Analysis.service_api_key_id == key_id, Analysis.analysis_purpose == "production")


def preview(db, key_id):
    key = get_key(db, key_id)
    scope = targets(key_id)
    rows = db.execute(select(Analysis.id, Analysis.status).where(Analysis.id.in_(scope)).order_by(Analysis.id)).all()
    # Status participates too: a preview taken before processing completed must
    # not become a silently enabled destructive confirmation.
    fingerprint = hashlib.sha256(json.dumps([key.id, key.name, list(map(tuple, rows))], separators=(",", ":")).encode()).hexdigest()
    linked = db.scalar(select(exists().where(TestRunItem.analysis_id.in_(scope))))
    label_linked = db.scalar(select(exists().where(TestRunItem.label_id.in_(select(AnalysisLabel.id).where(AnalysisLabel.analysis_id.in_(scope))))))
    foreign_retry = db.scalar(select(exists().where(Analysis.retry_of_analysis_id.in_(scope), Analysis.id.not_in(scope))))
    copies = db.scalar(select(func.count()).select_from(ValidationDatasetItem).where(ValidationDatasetItem.original_analysis_id.in_(scope)))
    return ServiceApiKeyDeletionPreview(name=key.name, purpose=key.purpose, analyses=len(rows),
        active_analyses=sum(status not in {"completed", "failed"} for _, status in rows),
        dataset_copies=copies, blocked_references=bool(linked or label_linked or foreign_retry), scope=fingerprint)


def purge(db, key_id, request, actor):
    current = preview(db, key_id)
    if current.purpose != "production":
        raise ServiceApiKeyError("analysis_deletion_production_only", 422)
    if request.expected_scope != current.scope:
        raise ServiceApiKeyError("service_api_key_deletion_changed", 409)
    if current.active_analyses:
        raise ServiceApiKeyError("service_api_key_analyses_active", 409)
    if current.blocked_references:
        raise ServiceApiKeyError("service_api_key_analyses_referenced", 409)
    scope = targets(key_id)
    # Only the source link changes, not copied content, answers, version IDs,
    # membership or the internal-only restriction.
    for item_id in db.scalars(select(ValidationDatasetItem.id).where(ValidationDatasetItem.original_analysis_id.in_(scope))):
        db.add(AccessAudit(actor_kind="admin_session", actor_id=actor,
            action="detach_deleted_analysis_source", resource_type="validation_dataset_item", resource_id=item_id))
    db.execute(update(ValidationDatasetItem).where(ValidationDatasetItem.original_analysis_id.in_(scope))
        .values(original_analysis_id=None, original_analysis_deleted=True))
    # SQLite RESTRICT is immediate. Remove terminal retries first, in bounded
    # chunks; no other key's child is pulled into this operation.
    child = Analysis.__table__.alias("child")
    removed = 0
    while True:
        leaves = list(db.scalars(select(Analysis.id).where(Analysis.id.in_(scope),
            ~exists(select(child.c.id).where(child.c.retry_of_analysis_id == Analysis.id))).limit(400)))
        if not leaves:
            break
        for identifier in leaves:
            db.add(AccessAudit(actor_kind="admin_session", actor_id=actor,
                action="delete_analysis_with_service_key", resource_type="analysis", resource_id=identifier))
        db.execute(delete(Analysis).where(Analysis.id.in_(leaves)))
        removed += len(leaves)
    if removed != current.analyses:
        raise ServiceApiKeyError("service_api_key_analyses_referenced", 409)
    return removed
