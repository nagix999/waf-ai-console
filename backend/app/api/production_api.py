from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..security import Principal, require_scope
from ..services.input_schemas import InputSchemaError
from ..services.production_api import active_contract, markdown_contract

router = APIRouter(tags=["production-api"])


class ProductionAPIDocument(BaseModel):
    markdown: str
    input_schema: dict[str, Any]


@router.get("/production-api", response_model=ProductionAPIDocument)
def production_api_document(request: Request, response: Response,
                            db: Annotated[Session, Depends(get_db)],
                            _principal: Annotated[Principal, Depends(require_scope("ingest"))]):
    try:
        metadata, fields, schema = active_contract(db, request.app.state.crypto)
        markdown = markdown_contract(metadata, fields, schema, request.app.state.settings.payload_max_bytes)
        db.commit()  # Only first-use default initialization writes data.
    except InputSchemaError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code) from None
    response.headers["Cache-Control"] = "no-store"
    return ProductionAPIDocument(markdown=markdown, input_schema=metadata)
