"""Administrator single-case copies. No model invocation or Ground Truth promotion."""
import json

from sqlalchemy import select

from ..models import Analysis, TestRun, TestRunItem, VLLMProfile
from ..schemas import AnalysisInput
from .analysis import AnalysisIngestError
from .initial_assessment import FIELDS
from .manual_references import latest_reference


def source_analysis(db, identifier):
    row = db.get(Analysis, identifier)
    if row is None:
        raise AnalysisIngestError("analysis_not_found", 404)
    return row


def internal_only(row):
    # Production input is internal even if the original service admission did
    # not need the dataset-specific flag. An edited copy never clears it.
    return bool(row.internal_only or row.analysis_purpose != "test")


def _source_item(db, row):
    # Retry results are displayed in place of the original membership. Resolve
    # only its existing ancestry; never infer membership by event ID.
    seen = set()
    while row is not None and row.id not in seen:
        seen.add(row.id)
        item = db.scalar(select(TestRunItem).where(TestRunItem.analysis_id == row.id))
        if item is not None:
            return item
        row = db.get(Analysis, row.retry_of_analysis_id) if row.retry_of_analysis_id else None
    return None


def template(db, crypto, identifier):
    row = source_analysis(db, identifier)
    try:
        # Preserve JSON value types and nested extra_fields exactly. Only
        # original event columns override colliding legacy additional values.
        event = dict(row.extra_fields or {})
        event.update({key: getattr(row, key) for key in AnalysisInput.model_fields if key != "payload"})
        event["payload"] = crypto.decrypt_text(row.payload_ciphertext)
    except (ValueError, TypeError, UnicodeError):
        raise AnalysisIngestError("single_test_source_unavailable", 503) from None
    item = _source_item(db, row)
    run = db.get(TestRun, item.test_run_id) if item is not None else None
    reference = latest_reference(db, row.id)
    return {"source_analysis_id": row.id, "source_analysis_purpose": row.analysis_purpose,
        "internal_only": internal_only(row), "event": event,
        "initial_assessment": {key: getattr(row, key) for key in FIELDS},
        # Only separately registered answers are copied. Never use row.verdict,
        # a review decision or a Deep Assessment result as an answer.
        "expected_verdict": reference.verdict if reference else None,
        "name": run.name if run else None, "source_test_run_id": run.id if run else None,
        **{key: getattr(item, key) if item else None for key in ("difficulty", "test_category", "case_name")}}


def require_internal_copy_profiles(db, crypto, run):
    if run.execution_mode == "stub":
        return
    identifiers = {run.profile_id, run.profile_metadata.get("verifier_profile", {}).get("model_profile_id")}
    try:
        if run.evidence_editor_snapshot_ciphertext:
            identifiers.add(json.loads(crypto.decrypt_text(run.evidence_editor_snapshot_ciphertext))["profile_id"])
        if run.display_editor_snapshot_ciphertext:
            from .evidence_display_jobs_v4 import load_snapshot
            identifiers.add(load_snapshot(crypto, run.display_editor_snapshot_ciphertext).profile_id)
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise AnalysisIngestError("single_test_configuration_unavailable", 409) from None
    for identifier in identifiers:
        if not identifier:
            continue
        profile = db.get(VLLMProfile, identifier)
        if profile is None or profile.provider != "vllm":
            raise AnalysisIngestError("single_test_internal_models_required", 409)
