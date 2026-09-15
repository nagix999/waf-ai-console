"""Service Test ingress: source isolation, pinned roles, bounded append sessions."""
import hashlib
import uuid
from pydantic import ValidationError
from sqlalchemy import select
from ..models import Analysis, AnalysisLabel, TestRun, TestRunItem
from ..schemas import AnalysisInput
from .analysis import AnalysisIngestError, check_duplicate, event_fingerprint
from .test_runs import add_run_items, create_run_record, split_test_metadata, write_lock
from .uploads import UploadFormatError, normalize_upload_row
from .validation_datasets import digest


def require_test_key(principal):
    if principal.kind != "service_api_key" or principal.purpose != "test":
        raise AnalysisIngestError("test_api_key_required", 403)


def owned_run(db, identifier, principal):
    require_test_key(principal)
    run = db.get(TestRun, identifier)
    if run is None or run.api_source_system != principal.source_system:
        raise AnalysisIngestError("test_run_not_found", 404)
    return run


def new_session(db, crypto, settings, principal, payload):
    require_test_key(principal)
    write_lock(db)
    key = digest(["api-session", principal.source_system, payload.idempotency_key])
    run, duplicate = create_run_record(db, crypto, settings, name=(payload.name or "").strip() or str(uuid.uuid4()),
        idempotency_key=key, request_hash=digest([payload.name]), kind="api", actor=principal.source_system)
    if not duplicate:
        run.api_source_system, run.accepting_items = principal.source_system, True
    db.commit()
    return run


def ingest(db, crypto, settings, principal, rows, *, run_id=None, upload=False):
    require_test_key(principal)
    if not rows or len(rows) > 5000:
        raise AnalysisIngestError("test_item_count_out_of_range", 422)
    write_lock(db)
    if run_id:
        run = owned_run(db, run_id, principal)
    else:
        identity_rows = rows
        if not upload:
            # Older /analyses admission hashed the normalized event including
            # default fields. Preserve retries across the request-model change,
            # but keep the submitted field presence for schema validation below.
            try:
                event, expected, _ = split_test_metadata(rows[0])
                identity_rows = [AnalysisInput.model_validate(event).model_dump(mode="json")]
                if expected is not None:
                    identity_rows[0]["expected_verdict"] = expected
            except (ValidationError, UploadFormatError):
                pass
        key = digest(["api-upload" if upload else "api-event", principal.source_system,
                      rows if upload else rows[0].get("event_id")])
        run, duplicate = create_run_record(db, crypto, settings, name=str(uuid.uuid4()), idempotency_key=key,
            request_hash=digest(identity_rows), kind="api", actor=principal.source_system)
        if duplicate:
            items = list(db.scalars(select(TestRunItem).where(TestRunItem.test_run_id == run.id).order_by(TestRunItem.row_number)))
            return run, [(item, item.ingest_status == "accepted") for item in items]
        run.api_source_system = principal.source_system
    result = []
    for raw in rows:
        try:
            event, expected, _ = split_test_metadata(raw)
            payload = AnalysisInput.model_validate(normalize_upload_row(event))
            old = db.scalar(select(TestRunItem).where(TestRunItem.test_run_id == run.id,
                TestRunItem.event_id == payload.event_id, TestRunItem.ingest_status == "accepted"))
            if old:
                analysis = db.get(Analysis, old.analysis_id)
                check_duplicate(analysis, crypto, event_fingerprint(payload.model_dump(mode="json")), "test")
                label = db.scalar(select(AnalysisLabel).where(AnalysisLabel.analysis_id == analysis.id)
                                  .order_by(AnalysisLabel.revision.desc()).limit(1))
                if expected is not None and (label.verdict if label else None) != expected:
                    raise AnalysisIngestError("expected_verdict_conflict", 409)
                result.append((old, True))
                continue
        except (ValidationError, UploadFormatError):
            pass  # add_run_items records a sanitized rejected row.
        if run_id and not run.accepting_items:
            raise AnalysisIngestError("test_session_closed", 409)
        add_run_items(db, crypto, settings, run, [raw], source_kind="reference",
                      source_ref="analysis-request:expected_verdict", actor_kind=principal.kind)
        item = db.scalar(select(TestRunItem).where(TestRunItem.test_run_id == run.id)
                         .order_by(TestRunItem.row_number.desc()).limit(1))
        if item.analysis_id:
            analysis = db.get(Analysis, item.analysis_id)
            analysis.service_api_key_id = principal.service_api_key_id
            analysis.ingest_channel = "file_upload" if upload else "service_api"
        result.append((item, False))
    db.commit()
    return run, result
