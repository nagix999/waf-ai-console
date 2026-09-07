from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..evaluation_schemas import (
    AIVisibility, LabelConfirmRequest, LabelConfirmResponse, LabelHistoryItem, LabelHistoryResponse,
    LabelPreviewResponse, LabelSourceKind,
)
from ..models import Analysis, AnalysisLabel
from ..security import Principal, require_scope
from ..services.evaluation_labels import (
    MAX_LABEL_FILE_BYTES, LabelAttachmentError, confirm_labels, parse_answer_file, preview_labels,
)

router = APIRouter(tags=["evaluation-labels"])
DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


@router.post("/evaluation-labels/preview", response_model=LabelPreviewResponse)
async def preview(
    request: Request, db: DbSession, principal: Admin,
    file: UploadFile = File(...), source_system: str = Form(..., min_length=1, max_length=120),
    source_kind: LabelSourceKind = Form(...), source_ref: str = Form(..., min_length=1, max_length=120),
    ai_visible: AIVisibility = Form("unknown"),
) -> LabelPreviewResponse:
    try:
        content = await file.read(MAX_LABEL_FILE_BYTES + 1)
        answers = parse_answer_file(content, file.filename)
        return preview_labels(
            db, answers=answers, source_system=source_system, source_kind=source_kind, source_ref=source_ref,
            ai_visible={"unknown": None, "true": True, "false": False}[ai_visible],
            actor=principal.username or "admin", secret=request.app.state.settings.session_secret,
        )
    except LabelAttachmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
    finally:
        await file.close()


@router.post("/evaluation-labels/confirm", response_model=LabelConfirmResponse)
def confirm(payload: LabelConfirmRequest, request: Request, db: DbSession, principal: Admin) -> LabelConfirmResponse:
    try:
        return confirm_labels(db, token=payload.preview_token, actor=principal.username or "admin", secret=request.app.state.settings.session_secret)
    except LabelAttachmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


@router.get("/analyses/{analysis_id}/evaluation-labels", response_model=LabelHistoryResponse)
def history(analysis_id: str, db: DbSession, _principal: Admin) -> LabelHistoryResponse:
    if db.scalar(select(Analysis.id).where(Analysis.id == analysis_id)) is None:
        raise HTTPException(status_code=404, detail="analysis_not_found")
    rows = db.scalars(select(AnalysisLabel).where(AnalysisLabel.analysis_id == analysis_id).order_by(AnalysisLabel.revision.desc()))
    return LabelHistoryResponse(items=[LabelHistoryItem.model_validate(row, from_attributes=True) for row in rows])
