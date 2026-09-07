from datetime import UTC, datetime
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..models import Analysis, Review
from ..schemas import AnalysisDetail, AnalysisInput, AnalysisSummary
from .crypto import CryptoService
from .timing import analysis_timings
from .prompt_snapshots import pin_analysis_prompt
from .prompt_policies import PromptPolicyError
from .evaluation import attach_evaluations
from .input_schemas import InputSchemaError, pin_schema, read_schema_snapshot, schema_metadata, validate_event
from ..evaluation_schemas import EvaluationMetadata


class AnalysisIngestError(ValueError):
    def __init__(self, code: str, status_code: int, issues: list[dict] | None = None):
        self.code = code
        self.status_code = status_code
        self.issues = issues
        super().__init__(code)


def review_state(analysis: Analysis) -> str:
    if not analysis.reviews:
        return "unreviewed"
    latest = max(analysis.reviews, key=lambda review: (review.created_at, review.id))
    return "deferred" if latest.decision == "deferred" else "confirmed"


def to_summary(analysis: Analysis) -> AnalysisSummary:
    return AnalysisSummary(
        id=analysis.id,
        source_system=analysis.source_system,
        analysis_purpose=analysis.analysis_purpose,
        ingest_channel=analysis.ingest_channel,
        event_id=analysis.event_id,
        company_name=analysis.company_name,
        src_ip=analysis.src_ip,
        dest_ip=analysis.dest_ip,
        src_port=analysis.src_port,
        dest_port=analysis.dest_port,
        waf_vendor=analysis.waf_vendor,
        waf_action=analysis.waf_action,
        signature=analysis.signature,
        event_name=analysis.event_name,
        status=analysis.status,
        verdict=analysis.verdict,
        severity=analysis.severity,
        threat_category=analysis.threat_category,
        confidence_score=analysis.confidence_score,
        summary_ko=analysis.summary_ko,
        input_truncated=analysis.input_truncated,
        created_at=analysis.created_at,
        started_at=analysis.started_at,
        completed_at=analysis.completed_at,
        model_profile=analysis.model_profile,
        **analysis_timings(analysis),
        review_state=review_state(analysis),
        evaluation=getattr(analysis, "_evaluation", EvaluationMetadata()),
        test_run_id=getattr(analysis, "_test_run_id", None),
    )


def to_detail(analysis: Analysis, crypto: CryptoService | None = None) -> AnalysisDetail:
    metadata = None
    if crypto and (analysis.input_schema_version_id or analysis.input_schema_snapshot_ciphertext):
        try:
            metadata = schema_metadata(read_schema_snapshot(crypto, analysis))
        except InputSchemaError:
            metadata = {"version_id": analysis.input_schema_version_id, "status": "unavailable"}
    return AnalysisDetail(
        **to_summary(analysis).model_dump(),
        # Preserve the existing object-or-null public contract even when an old
        # row contains malformed JSON shapes. Never rewrite the stored value;
        # evaluation separately excludes these as unknown provenance.
        result=analysis.result_json if isinstance(analysis.result_json, dict) else None,
        prompt_version=analysis.prompt_version,
        prompt_policy_version_id=analysis.prompt_policy_version_id,
        input_schema_metadata=metadata,
        error_code=analysis.error_code,
        error_message=analysis.error_message,
    )


def enqueue_analysis(
    db: Session,
    crypto: CryptoService,
    source_system: str,
    payload: AnalysisInput,
    *,
    analysis_purpose: str = "production",
    ingest_channel: str = "service_api",
    payload_max_bytes: int = 2 * 1024 * 1024,
    commit: bool = True,
    schema_snapshot: dict | None = None,
) -> tuple[Analysis, bool]:
    """Enqueue an event; commit=False leaves the entire transaction to its caller."""
    try:
        payload_bytes = len(payload.payload.encode("utf-8"))
    except UnicodeError as exc:
        raise AnalysisIngestError("invalid_event_json", 422) from exc
    if payload_bytes > payload_max_bytes:
        raise AnalysisIngestError("payload_too_large", 413)
    fingerprint = event_fingerprint(payload.model_dump(mode="json"))
    existing = db.scalar(
        select(Analysis)
        .options(selectinload(Analysis.reviews))
        .where(Analysis.source_system == source_system, Analysis.event_id == payload.event_id)
    )
    if existing:
        check_duplicate(existing, crypto, fingerprint, analysis_purpose)
        attach_evaluations(db, [existing])
        return existing, True

    row = Analysis(
        source_system=source_system,
        analysis_purpose=analysis_purpose,
        ingest_channel=ingest_channel,
        event_fingerprint=fingerprint,
        event_id=payload.event_id,
        company_name=payload.company_name,
        src_ip=payload.src_ip,
        dest_ip=payload.dest_ip,
        src_port=payload.src_port,
        dest_port=payload.dest_port,
        signature=payload.signature,
        event_name=payload.event_name,
        waf_vendor=payload.waf_vendor,
        waf_action=payload.waf_action,
        payload_ciphertext=crypto.encrypt_text(payload.payload),
        encryption_key_version=crypto.key_version,
        extra_fields=payload.extra_values(),
    )
    # Pin the complete instructions with the new event. Duplicate submissions
    # above retain the original version and do not acquire the current policy.
    try:
        if schema_snapshot is None:
            snapshot = pin_schema(db, crypto, row)
        else:
            row.input_schema_version_id = schema_snapshot["version_id"]
            row.input_schema_snapshot_ciphertext = crypto.encrypt_text(json.dumps(schema_snapshot, ensure_ascii=False))
            snapshot = read_schema_snapshot(crypto, row)
        issues = validate_event(snapshot["fields"], payload.model_dump(mode="json", exclude_unset=True))
        if issues:
            if commit:
                db.rollback()
            raise AnalysisIngestError("input_schema_validation_failed", 422, issues)
        pin_analysis_prompt(db, crypto, row)
    except InputSchemaError as exc:
        if commit:
            db.rollback()
        raise AnalysisIngestError(exc.code, exc.status_code) from None
    except PromptPolicyError as exc:
        if commit:
            db.rollback()
        # A policy-store failure is not an invalid WAF event and must never
        # silently fall back to whichever instructions happen to be in code.
        raise AnalysisIngestError(exc.code, 503) from None
    db.add(row)
    try:
        if commit:
            db.commit()
        else:
            db.flush()
    except IntegrityError:
        if not commit:
            # A larger atomic operation must not lose its preceding writes.
            # Its owner handles rollback and, if appropriate, retries.
            raise
        db.rollback()
        existing = db.scalar(
            select(Analysis)
            .options(selectinload(Analysis.reviews))
            .where(Analysis.source_system == source_system, Analysis.event_id == payload.event_id)
        )
        if existing is None:
            raise
        check_duplicate(existing, crypto, fingerprint, analysis_purpose)
        attach_evaluations(db, [existing])
        return existing, True
    db.refresh(row)
    row.reviews = []
    return row, False


def event_fingerprint(document: dict) -> str:
    try:
        serialized = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AnalysisIngestError("invalid_event_json", 422) from exc


def check_duplicate(existing: Analysis, crypto: CryptoService, fingerprint: str, purpose: str) -> None:
    if existing.analysis_purpose not in {purpose, "legacy_unknown"}:
        raise AnalysisIngestError("event_purpose_conflict", 409)
    stored = existing.event_fingerprint
    if stored is None:
        try:
            document = dict(existing.extra_fields)
            document.update({name: getattr(existing, name) for name in AnalysisInput.model_fields if name != "payload"})
            document["payload"] = crypto.decrypt_text(existing.payload_ciphertext)
        except ValueError as exc:
            raise AnalysisIngestError("event_content_unavailable", 500) from exc
        stored = event_fingerprint(document)
    if stored != fingerprint:
        raise AnalysisIngestError("event_id_conflict", 409)


def fetch_analysis(db: Session, analysis_id: str, source_system: str | None = None) -> Analysis | None:
    query = select(Analysis).options(selectinload(Analysis.reviews)).where(Analysis.id == analysis_id)
    if source_system is not None:
        query = query.where(Analysis.source_system == source_system)
    row = db.scalar(query)
    if row is not None:
        attach_evaluations(db, [row])
        from .test_runs import attach_test_run_ids
        attach_test_run_ids(db, [row])
    return row


def count_analyses(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Analysis)) or 0)


def now_utc() -> datetime:
    return datetime.now(UTC)
