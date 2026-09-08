from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..security import Principal, require_scope
from ..services.input_schemas import InputSchemaError
from ..services.production_api import active_contract, markdown_contract
from ..services.production_api_pdf import ProductionAPIPDFError, render_production_api_pdf

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


@router.get("/production-api.pdf", response_class=Response,
            responses={200: {"content": {"application/pdf": {}}},
                       409: {"description": "Input schema changed since the displayed document"},
                       413: {"description": "Complete document exceeds PDF limits"},
                       503: {"description": "PDF document unavailable"}})
def production_api_pdf(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _principal: Annotated[Principal, Depends(require_scope("ingest"))],
    expected_schema_hash: str | None = Query(default=None, pattern=r"^[0-9a-fA-F]{64}$"),
    expected_schema_version_id: UUID | None = Query(default=None),
):
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    allowed = {"expected_schema_hash", "expected_schema_version_id"}
    if set(request.query_params) - allowed or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise HTTPException(422, "unsupported_query_parameter", headers=headers)
    try:
        metadata, fields, schema = active_contract(db, request.app.state.crypto)
        if ((expected_schema_hash is not None and expected_schema_hash.lower() != metadata["content_hash"])
                or (expected_schema_version_id is not None and str(expected_schema_version_id) != metadata["version_id"])):
            db.rollback()
            raise HTTPException(409, "input_schema_document_changed", headers=headers)
        markdown = markdown_contract(metadata, fields, schema, request.app.state.settings.payload_max_bytes)
        db.commit()  # Same first-use initialization as the existing document.
        # Release the read/initialization transaction before CPU-only layout.
        body = render_production_api_pdf(markdown, metadata)
    except HTTPException:
        raise
    except InputSchemaError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.code, headers=headers) from None
    except ProductionAPIPDFError as exc:
        raise HTTPException(exc.status_code, exc.code, headers=headers) from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "production_api_pdf_unavailable", headers=headers) from None
    headers["Content-Disposition"] = f'attachment; filename="Production_API_v0.2.0_schema-v{metadata["version_number"]}.pdf"'
    headers["X-Input-Schema-Hash"] = metadata["content_hash"]
    headers["X-Input-Schema-Version-Id"] = metadata["version_id"]
    return Response(body, media_type="application/pdf", headers=headers)
