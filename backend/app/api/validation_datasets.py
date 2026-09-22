from contextlib import contextmanager
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import ValidationDataset, ValidationDatasetVersion, utcnow
from ..security import Principal, require_scope
from ..validation_data_schemas import DatasetCreate, DatasetUpdate, DatasetImport, DatasetItemWrite, DatasetItemReview, DatasetReviewStatus, DatasetRun, RevisionRequest, DatasetSearch
from ..validation_data_schemas import WorkingSearch, WorkingItemWrite, WorkingRevision, WorkingBulk, WorkingImport, PublishRevision, WorkingMetadata
from ..services import ground_truth_working as working
from ..services import validation_datasets as service
from ..services.analysis import AnalysisIngestError
from ..services.input_schemas import InputSchemaError
from ..services.manual_references import audit, utc_datetime
from ..services.prompt_policies import PromptPolicyError
from ..services.prompt_snapshots import PromptSnapshotError
from ..services.test_runs import describe_run, read_snapshot, write_lock
from ..services.vllm_profiles import TargetNotAllowedError

router = APIRouter(prefix="/validation-datasets", tags=["validation-datasets"])
DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


@contextmanager
def errors(db):
    try:
        yield
    except (AnalysisIngestError, InputSchemaError, PromptPolicyError) as exc:
        db.rollback()
        raise HTTPException(exc.status_code, {"code": exc.code, "issues": exc.issues} if getattr(exc, "issues", None) else exc.code) from None
    except PromptSnapshotError as exc:
        db.rollback()
        raise HTTPException(503, exc.code) from None
    except TargetNotAllowedError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None


@router.get("")
def listing(request: Request, db: DbSession, _principal: Admin, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    read_snapshot(db)
    query = select(ValidationDataset).where(ValidationDataset.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {"items": [dataset_metadata(db, row, request.app.state.crypto) for row in db.scalars(query
        .order_by(ValidationDataset.created_at.desc(), ValidationDataset.id).limit(limit).offset(offset))], "total": total}


@router.post("", status_code=201)
def create(payload: DatasetCreate, db: DbSession, principal: Admin):
    with errors(db):
        return service.create_dataset(db, payload, principal.username or "admin")


@router.post("/search")
def search(payload: DatasetSearch, request: Request, db: DbSession, _principal: Admin):
    # Search text stays out of access-log URLs and browser history.
    read_snapshot(db)
    query = select(ValidationDataset).where(ValidationDataset.deleted_at.is_(None))
    if payload.query.strip():
        query = query.where(ValidationDataset.name.contains(payload.query.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    return {"items": [dataset_metadata(db, row, request.app.state.crypto) for row in db.scalars(query
        .order_by(ValidationDataset.created_at.desc(), ValidationDataset.id).limit(payload.limit).offset(payload.offset))],
        "total": total, "limit": payload.limit, "offset": payload.offset}


def dataset_metadata(db, row, crypto):
    summary = service.dataset_summary(db, row)
    data = working.document(db, row.id, crypto)
    latest = data["published_revisions"][0] if data["published_revisions"] else None
    return {**summary, "latest_published_revision": latest, "published_case_count": latest["total"] if latest else 0,
        "working_revision": data["working_revision"], "working_change_count": data["working_changes_count"],
        **{f"{key}_count": value for key, value in data["counts"].items()}}


@router.get("/{identifier}")
def detail(identifier: str, db: DbSession, _principal: Admin,
           revision: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
           review_status: DatasetReviewStatus | None = None):
    with errors(db):
        read_snapshot(db)
        row, version = service.dataset(db, identifier)
        if revision is not None:
            version = db.scalar(select(ValidationDatasetVersion).where(ValidationDatasetVersion.dataset_id == row.id,
                                                                      ValidationDatasetVersion.revision == revision))
            if version is None:
                raise HTTPException(404, "dataset_version_not_found")
        items = service.version_items(db, version)
        if review_status is not None:
            items = [item for item in items if item.review_status == review_status]
        return {**service.dataset_summary(db, row, version), "current_revision": row.revision,
            "filtered_total": len(items),
            "items": [service.item_summary(item) for item in items[offset:offset + limit]], "limit": limit, "offset": offset,
            "versions": [{"id": v.id, "revision": v.revision, "created_at": utc_datetime(v.created_at), "total": len(v.item_version_ids)}
                for v in db.scalars(select(ValidationDatasetVersion).where(ValidationDatasetVersion.dataset_id == row.id)
                    .order_by(ValidationDatasetVersion.revision.desc()))]}


@router.post("/{identifier}/working/search")
def working_search(identifier: str, payload: WorkingSearch, request: Request, db: DbSession, _principal: Admin):
    with errors(db):
        read_snapshot(db)
        return working.document(db, identifier, request.app.state.crypto, payload)


@router.get("/{identifier}/working")
def working_detail(identifier: str, request: Request, db: DbSession, _principal: Admin):
    with errors(db):
        read_snapshot(db)
        return working.document(db, identifier, request.app.state.crypto)


@router.get("/{identifier}/working/items/{case_id}")
def working_item(identifier: str, case_id: str, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.read_item(db, identifier, case_id, request.app.state.crypto, principal.username or "admin")


@router.post("/{identifier}/working/items", status_code=201)
def working_add(identifier: str, payload: WorkingItemWrite, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.write_item(db, identifier, payload, request.app.state.crypto, request.app.state.settings, principal.username or "admin")


@router.put("/{identifier}/working/items/{case_id}")
def working_edit(identifier: str, case_id: str, payload: WorkingItemWrite, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.write_item(db, identifier, payload, request.app.state.crypto, request.app.state.settings, principal.username or "admin", case_id)


@router.post("/{identifier}/working/bulk")
def working_bulk(identifier: str, payload: WorkingBulk, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.bulk(db, identifier, payload, request.app.state.crypto, principal.username or "admin")


@router.post("/{identifier}/working/imports")
def working_import(identifier: str, payload: WorkingImport, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.import_analyses(db, identifier, payload, request.app.state.crypto, request.app.state.settings, principal.username or "admin")


@router.post("/{identifier}/working/items/{case_id}/revert")
@router.post("/{identifier}/working/items/{case_id}/restore")
def working_revert(identifier: str, case_id: str, payload: WorkingRevision, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.restore(db, identifier, payload, request.app.state.crypto, principal.username or "admin", case_id)


@router.post("/{identifier}/working/discard")
def working_discard(identifier: str, payload: WorkingRevision, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.restore(db, identifier, payload, request.app.state.crypto, principal.username or "admin")


@router.post("/{identifier}/publish", status_code=201)
def publish(identifier: str, payload: PublishRevision, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.publish(db, identifier, payload, request.app.state.crypto, principal.username or "admin")


@router.patch("/{identifier}/working/metadata")
def working_metadata(identifier: str, payload: WorkingMetadata, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return working.update_metadata(db, identifier, payload, request.app.state.crypto, principal.username or "admin")


@router.patch("/{identifier}")
def update(identifier: str, payload: DatasetUpdate, db: DbSession, principal: Admin):
    with errors(db):
        write_lock(db)
        service.require_legacy_draft(db, identifier)
        row, version = service.dataset(db, identifier, payload.expected_revision)
        row.name, row.description = payload.name.strip(), payload.description
        service.save_version(db, row, version.item_version_ids, principal.username or "admin")
        db.commit()
        return service.dataset_summary(db, row)


@router.delete("/{identifier}")
def delete(identifier: str, payload: RevisionRequest, db: DbSession, principal: Admin):
    with errors(db):
        write_lock(db)
        row, _ = service.dataset(db, identifier, payload.expected_revision)
        row.deleted_at = utcnow()
        audit(db, principal.username or "admin", "delete_dataset", "validation_dataset", row.id)
        db.commit()
        return {"deleted": True}


@router.post("/{identifier}/imports")
def import_rows(identifier: str, payload: DatasetImport, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return service.import_analyses(db, request.app.state.crypto, request.app.state.settings,
            identifier, payload, principal.username or "admin")


@router.post("/{identifier}/items", status_code=201)
def add_item(identifier: str, payload: DatasetItemWrite, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return service.write_item(db, request.app.state.crypto, request.app.state.settings,
            identifier, payload, principal.username or "admin")


@router.get("/{identifier}/items/{item_id}")
def item_detail(identifier: str, item_id: str, request: Request, db: DbSession, principal: Admin,
                version_id: str | None = Query(None, max_length=36)):
    with errors(db):
        return service.read_item(db, request.app.state.crypto, identifier, item_id, principal.username or "admin", version_id)


@router.put("/{identifier}/items/{item_id}")
def update_item(identifier: str, item_id: str, payload: DatasetItemWrite, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        return service.write_item(db, request.app.state.crypto, request.app.state.settings,
            identifier, payload, principal.username or "admin", item_id)


@router.delete("/{identifier}/items/{item_id}")
def remove_item(identifier: str, item_id: str, payload: RevisionRequest, db: DbSession, principal: Admin):
    with errors(db):
        write_lock(db)
        service.require_legacy_draft(db, identifier)
        row, version = service.dataset(db, identifier, payload.expected_revision)
        items = service.version_items(db, version)
        ids = [item.id for item in items if item.item_id != item_id]
        if len(ids) == len(items):
            raise HTTPException(404, "dataset_item_not_found")
        service.save_version(db, row, ids, principal.username or "admin")
        db.commit()
        return {"revision": row.revision}


@router.post("/{identifier}/items/{item_id}/reviews")
def review(identifier: str, item_id: str, payload: DatasetItemReview, db: DbSession, principal: Admin):
    with errors(db):
        return service.review_item(db, identifier, item_id, payload, principal.username or "admin")


@router.post("/{identifier}/runs", status_code=202)
def execute(identifier: str, payload: DatasetRun, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        run = service.execute_dataset(db, request.app.state.crypto, request.app.state.settings,
            identifier, payload, principal.username or "admin")
        return describe_run(db, run)
