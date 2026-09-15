from typing import Annotated
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from ..database import get_db
from ..security import Principal, require_scope
from ..validation_data_schemas import TestSessionCreate
from ..services.test_api import new_session, owned_run
from ..services.test_runs import describe_run, write_lock
from .validation_datasets import errors

router = APIRouter(prefix="/test-sessions", tags=["test-sessions"])
DbSession = Annotated[Session, Depends(get_db)]
TestClient = Annotated[Principal, Depends(require_scope("ingest"))]


@router.post("", status_code=201)
def create(payload: TestSessionCreate, request: Request, db: DbSession, principal: TestClient):
    with errors(db):
        return describe_run(db, new_session(db, request.app.state.crypto, request.app.state.settings, principal, payload))


@router.get("/{identifier}")
def detail(identifier: str, db: DbSession, principal: TestClient):
    with errors(db):
        return describe_run(db, owned_run(db, identifier, principal))


@router.post("/{identifier}/close")
def close(identifier: str, db: DbSession, principal: TestClient):
    with errors(db):
        write_lock(db)
        run = owned_run(db, identifier, principal)
        run.accepting_items = False
        db.commit()
        return describe_run(db, run)
