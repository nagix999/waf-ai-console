"""Audited administrator downloads; never decrypt raw/Agent data."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..security import Principal, require_scope
from ..database import get_db
from ..services.analysis import fetch_analysis, to_detail
from ..services.analysis_exports import ReportExportError, build_report, render_pdf, render_xlsx
from .analyses import begin_analysis_read_snapshot, record_access

router = APIRouter(tags=["analysis reports"])
DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


def _download(analysis_id, request, db, principal, format):
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if request.query_params:
        raise HTTPException(422, "unsupported_query_parameter", headers=headers)
    begin_analysis_read_snapshot(db)
    row = fetch_analysis(db, str(analysis_id))
    if row is None:
        raise HTTPException(404, "analysis_not_found", headers=headers)
    try:
        # No crypto argument: field-definition history is not report content.
        report = build_report(to_detail(row).model_dump())
    except ReportExportError as exc:
        raise HTTPException(exc.status_code, exc.code, headers=headers) from None
    except Exception:
        raise HTTPException(503, "report_export_unavailable", headers=headers) from None
    # Release the consistent read before the short audit write. Export records
    # represent issuance requests, not proof the user saved a file to disk.
    db.rollback()
    record_access(db, principal, f"export_analysis_{format}", "analysis", str(analysis_id))
    try:
        body = render_pdf(report) if format == "pdf" else render_xlsx(report)
    except ReportExportError as exc:
        raise HTTPException(exc.status_code, exc.code, headers=headers) from None
    except Exception:
        raise HTTPException(503, "report_export_unavailable", headers=headers) from None
    headers["Content-Disposition"] = f'attachment; filename="waf-analysis-{analysis_id}.{format}"'
    media_type = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(body, media_type=media_type, headers=headers)


@router.get("/analyses/{analysis_id}/report.pdf", response_class=Response)
def pdf_report(analysis_id: UUID, request: Request, db: DbSession, principal: Admin):
    return _download(analysis_id, request, db, principal, "pdf")


@router.get("/analyses/{analysis_id}/report.xlsx", response_class=Response)
def xlsx_report(analysis_id: UUID, request: Request, db: DbSession, principal: Admin):
    return _download(analysis_id, request, db, principal, "xlsx")
