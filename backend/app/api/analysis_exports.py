"""Audited admin downloads; raw access only for explicitly requested decoding."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..security import Principal, require_scope
from ..database import get_db
from ..services.analysis import fetch_analysis
from ..services.core_result_response import to_core_detail, set_core_headers
from ..services.analysis_exports import ReportExportError, build_report, render_xlsx
from ..services.report_pdf import pdf_document, render_report_pdf
from ..services.payload_decoding import decode_payload
from .analyses import begin_analysis_read_snapshot, record_access

router = APIRouter(tags=["analysis reports"])
DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


def _download(analysis_id, request, db, principal, format, *, include_appendix=False, include_decoding=False, theme="light"):
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    allowed = {"include_appendix", "include_decoding", "theme"} if format == "pdf" else set()
    if set(request.query_params) - allowed:
        raise HTTPException(422, "unsupported_query_parameter", headers=headers)
    begin_analysis_read_snapshot(db)
    row = fetch_analysis(db, str(analysis_id))
    if row is None:
        raise HTTPException(404, "analysis_not_found", headers=headers)
    if isinstance(row.result_json, dict) and row.result_json.get("schema_version") == "waf-analysis-v3":
        # Source-link and display history are privileged, even for a download
        # that later fails eligibility or renderer limits.
        db.rollback()
        record_access(db, principal, "view_evidence_display_for_export", "analysis", str(analysis_id))
        begin_analysis_read_snapshot(db)
        db.refresh(row)
    try:
        projected, metadata = to_core_detail(db, request.app.state.crypto, row, include_field_metadata=False)
        detail = projected.model_dump(mode="json")
        evidence_view = "core-v1"
        # Downloads follow the same readiness and evidence list as GET/POST.
        # A model deadline is handled by the worker, never by a download read.
        if metadata["status"] == "pending":
            raise ReportExportError("report_not_final", 409)
        if format == "pdf":
            build_report(detail, evidence_view=evidence_view)  # Eligibility before any optional raw read.
            decoding = decode_payload(request.app.state.crypto.decrypt_text(row.payload_ciphertext)) if include_decoding else None
            report = pdf_document(detail, decoding=decoding, include_appendix=include_appendix, theme=theme, evidence_view=evidence_view)
        else:
            report = build_report(detail, evidence_view=evidence_view)
    except ReportExportError as exc:
        raise HTTPException(exc.status_code, exc.code, headers=headers) from None
    except Exception:
        raise HTTPException(503, "report_export_unavailable", headers=headers) from None
    # Release the consistent read before the short audit write. Export records
    # represent issuance requests, not proof the user saved a file to disk.
    db.rollback()
    record_access(db, principal, f"export_analysis_{format}", "analysis", str(analysis_id))
    if format == "pdf" and include_decoding:
        record_access(db, principal, "export_analysis_pdf_decoding", "analysis", str(analysis_id))
    try:
        body = render_report_pdf(report) if format == "pdf" else render_xlsx(report)
    except ReportExportError as exc:
        raise HTTPException(exc.status_code, exc.code, headers=headers) from None
    except Exception:
        raise HTTPException(503, "report_export_unavailable", headers=headers) from None
    headers["Content-Disposition"] = f'attachment; filename="waf-analysis-{analysis_id}.{format}"'
    media_type = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    response = Response(body, media_type=media_type, headers=headers)
    set_core_headers(response, metadata)
    return response


@router.get("/analyses/{analysis_id}/report.pdf", response_class=Response)
def pdf_report(analysis_id: UUID, request: Request, db: DbSession, principal: Admin,
               include_appendix: bool = False, include_decoding: bool = False, theme: Literal["light", "dark"] = "light"):
    return _download(analysis_id, request, db, principal, "pdf", include_appendix=include_appendix, include_decoding=include_decoding, theme=theme)


@router.get("/analyses/{analysis_id}/report.xlsx", response_class=Response)
def xlsx_report(analysis_id: UUID, request: Request, db: DbSession, principal: Admin):
    return _download(analysis_id, request, db, principal, "xlsx")
