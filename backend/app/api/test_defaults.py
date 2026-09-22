from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from ..database import get_db
from ..security import Principal, require_scope
from ..candidate_schemas import CandidateConfiguration
from ..services import test_defaults
from .validation_datasets import errors

router = APIRouter(prefix="/admin", tags=["test-configuration"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


class DefaultsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    candidate_configuration: CandidateConfiguration


@router.get("/test-configuration-defaults")
def read(request: Request, response: Response, db: Db, admin: Admin):
    response.headers["Cache-Control"] = "no-store"
    return test_defaults.document(db, request.app.state.crypto)


@router.patch("/test-configuration-defaults")
def save(payload: DefaultsWrite, request: Request, db: Db, admin: Admin):
    with errors(db):
        return test_defaults.update(db, request.app.state.crypto, payload, admin.username or "admin")


@router.get("/setup-status")
def setup(request: Request, response: Response, db: Db, admin: Admin):
    from ..services.setup_status import document
    response.headers["Cache-Control"] = "no-store"
    with errors(db):
        result = document(db, request.app.state.crypto, request.app.state.settings)
        db.commit()
        return result
