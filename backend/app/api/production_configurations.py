from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import ProductionPromotion, TestEvaluation
from ..security import Principal, require_scope
from ..services.analysis import AnalysisIngestError
from ..services import production_configurations as service

router = APIRouter(prefix="/admin/production-configurations", tags=["production-configurations"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


class PromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate_test_run_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    expected_production_configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledge_schema_change: bool = False


def prepare(request, db, response):
    response.headers["Cache-Control"] = "no-store"
    service.ensure_baseline(db, request.app.state.crypto, request.app.state.settings)


@router.get("")
def current(request: Request, db: Db, response: Response, _admin: Admin):
    prepare(request, db, response)
    result = service.current_configuration(db, request.app.state.crypto, request.app.state.settings)
    result["evaluation"] = service.official_document(service.official_for_hash(db, result["configuration_hash"]))
    db.commit()
    return result


@router.get("/history")
def history(request: Request, db: Db, response: Response, _admin: Admin):
    prepare(request, db, response)
    rows = db.scalars(select(ProductionPromotion).order_by(ProductionPromotion.created_at.desc()).limit(100))
    result = {"items": [{"id": r.id, "kind": r.kind, "source_test_run_id": r.source_test_run_id,
        "configuration_hash": r.configuration_hash, "created_at": r.created_at, "actor": r.actor_id} for r in rows]}
    db.commit()
    return result


@router.get("/evaluations")
def evaluations(request: Request, db: Db, response: Response, _admin: Admin):
    prepare(request, db, response)
    config = service.current_configuration(db, request.app.state.crypto, request.app.state.settings)
    rows = db.scalars(select(TestEvaluation).where(TestEvaluation.configuration_hash == config["configuration_hash"],
        TestEvaluation.evaluation_kind == "ground_truth").order_by(TestEvaluation.created_at.desc()).limit(100))
    result = {"configuration": config, "items": [service.official_document(row) for row in rows]}
    db.commit()
    return result


@router.get("/preflight/{test_run_id}")
def preflight(test_run_id: str, request: Request, db: Db, response: Response, _admin: Admin):
    prepare(request, db, response)
    try:
        result = service.preflight(db, request.app.state.crypto, request.app.state.settings, test_run_id)
        db.commit()
        return result
    except AnalysisIngestError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None


@router.post("/promote")
def promote(payload: PromotionRequest, request: Request, db: Db, response: Response, admin: Admin):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.promote(db, request.app.state.crypto, request.app.state.settings, payload, admin.username or "admin")
    except AnalysisIngestError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None
