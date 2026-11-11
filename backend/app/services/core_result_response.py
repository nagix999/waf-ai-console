"""Read-only public detail projection; stored v3 and execution history stay intact.

HTTP/export callers authorize their source and audit before passing an analysis.
An injected display view permits batched exports without per-analysis queries.
"""
from ..result_contracts import StoredResultInvalid, legacy_result_projection
from .analysis import to_detail

UNSET = object()
VERSION = "core-evidence-v1"


def to_core_detail(db, crypto, analysis, *, result_version="v3", display_view=UNSET, include_field_metadata=True):
    """Return (AnalysisDetail, safe presentation metadata), never enqueue work."""
    detail = to_detail(analysis, crypto if include_field_metadata else None, result_version="v3")
    result = analysis.result_json
    metadata = {"version": VERSION, "status": "skipped", "original_count": 0, "returned_count": 0}
    if not isinstance(result, dict) or result.get("schema_version") != "waf-analysis-v3":
        if result is None and analysis.status in {"pending", "processing"}:
            metadata["status"] = "pending"
        else:
            # An immutable historical object was not processed by core-v1.
            # Never label all of its old evidence as a compact response.
            metadata["version"] = None
        return detail, metadata
    metadata["original_count"] = len(result.get("evidence", []))
    if display_view is UNSET:
        from .evidence_display_access import read_display_view
        display_view = read_display_view(db, crypto, analysis)
    if not isinstance(display_view, dict):
        raise StoredResultInvalid()
    if display_view.get("status") in {"pending", "running"}:
        # The DB decision is complete, but the response promised by this API
        # is not ready. Do not leak that decision as a ready public result.
        evaluation = detail.evaluation.model_copy(update={"outcome": "pending"})
        detail = type(detail).model_validate({**detail.model_dump(mode="json"), "status": "processing", "result": None,
            "verdict": None, "severity": None, "threat_category": None, "confidence_score": None,
            "summary_ko": None, "completed_at": None, "total_elapsed_ms": None,
            "processing_duration_ms": None, "evaluation": evaluation.model_dump(mode="json")})
        metadata["status"] = "pending"
        return detail, metadata
    from .core_evidence_v3 import build_core_result
    projected = build_core_result(result, relations=display_view.get("relations"), display_view=display_view)
    selected = projected["result"]
    if result_version == "v2":
        selected = legacy_result_projection(selected)
    # Re-run the public union validator rather than model_copy bypassing it.
    detail = type(detail).model_validate({**detail.model_dump(mode="json"), "result": selected})
    return detail, projected["metadata"]


def set_core_headers(response, metadata):
    response.headers["Cache-Control"] = "no-store"
    if metadata.get("version") != VERSION:
        return
    response.headers["X-Evidence-View"] = "core-v1"
    response.headers["X-Evidence-Status"] = metadata["status"]
    response.headers["X-Evidence-Original-Count"] = str(metadata["original_count"])
    response.headers["X-Evidence-Returned-Count"] = str(metadata["returned_count"])
