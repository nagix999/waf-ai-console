from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import ChangeEvent
from ..security import Principal, require_scope
from ..services.runtime_status import status_document, deployment_document
from ..services.production_configurations import ensure_baseline
from ..services.analysis import AnalysisIngestError

router = APIRouter(prefix="/admin", tags=["runtime"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


@router.get("/runtime/status")
def runtime_status(request: Request, response: Response, db: Db, _admin: Admin,
                   window: Literal["1h", "6h", "24h", "7d"] = "24h",
                   purpose: Literal["all", "production", "test"] = "all",
                   service_api_key_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None):
    response.headers["Cache-Control"] = "no-store"
    ensure_baseline(db, request.app.state.crypto, request.app.state.settings)
    try:
        result = status_document(db, request.app.state.crypto, request.app.state.settings, window, purpose, service_api_key_id)
    except AnalysisIngestError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None
    db.commit()
    return result


@router.get("/runtime/deployment")
def deployment(request: Request, response: Response, db: Db, _admin: Admin):
    response.headers["Cache-Control"] = "no-store"
    return deployment_document(db, request.app.state.settings)


@router.get("/activity")
def activity(response: Response, db: Db, _admin: Admin,
             category: Literal["all", "promotion", "runtime", "configuration", "integration", "deployment"] = "all",
             limit: Annotated[int, Query(ge=1, le=100)] = 30, offset: Annotated[int, Query(ge=0)] = 0):
    response.headers["Cache-Control"] = "no-store"
    condition = [ChangeEvent.category == category] if category != "all" else []
    rows = db.scalars(select(ChangeEvent).where(*condition).order_by(ChangeEvent.created_at.desc(), ChangeEvent.id.desc()).offset(offset).limit(limit))
    return {"total": db.scalar(select(func.count()).select_from(ChangeEvent).where(*condition)), "limit": limit, "offset": offset,
        "items": [{"id": r.id, "category": r.category, "actor": r.actor, "action": r.action,
            "resource_type": r.resource_type, "resource_id": r.resource_id, "before": r.before_json,
            "after": r.after_json, "created_at": r.created_at} for r in rows]}
