"""Atomic test-only ingestion and append-only reference attachment.

The answer never becomes part of AnalysisInput, the event fingerprint, encrypted
event content, or agent input. No worker or model is executed here. A caller using
commit=False owns commit/rollback for the complete surrounding transaction.
"""
import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..models import AccessAudit, Analysis, AnalysisLabel
from ..schemas import AnalysisInput
from .analysis import AnalysisIngestError, enqueue_analysis
from .crypto import CryptoService
from .prompt_snapshots import PromptSnapshot


UPLOAD_LABEL_SOURCE_REF = "test-upload:expected_verdict"
EXPECTED_VERDICTS = frozenset({"true_positive", "false_positive", "inconclusive"})


def enqueue_test_upload_row(
    db: Session,
    crypto: CryptoService,
    source_system: str,
    payload: AnalysisInput,
    *,
    expected_verdict: str | None,
    actor: str,
    payload_max_bytes: int = 2 * 1024 * 1024,
    commit: bool = True,
    label_source_ref: str = UPLOAD_LABEL_SOURCE_REF,
    attachment_id: str | None = None,
    ai_visible: bool | None = None,
    ingest_channel: str = "file_upload",
    schema_snapshot: dict | None = None,
    prompt_snapshot: PromptSnapshot | None = None,
) -> tuple[Analysis, bool, str | None]:
    """Return (analysis, duplicate, 'attached'/'unchanged'/None).

    Provenance overrides are trusted internal service arguments, not upload/API
    fields. Reupload preserves any current reference with the same verdict; a
    different verdict requires the separate preview/confirm workflow.
    """
    if expected_verdict is not None and (
        not isinstance(expected_verdict, str) or expected_verdict not in EXPECTED_VERDICTS
    ):
        raise AnalysisIngestError("invalid_expected_verdict", 422)
    try:
        connection = db.connection()
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            # Serializes against manual label confirmation and other uploads.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        analysis, duplicate = enqueue_analysis(
            db, crypto, source_system, payload,
            analysis_purpose="test", ingest_channel=ingest_channel,
            payload_max_bytes=payload_max_bytes, commit=False,
            schema_snapshot=schema_snapshot,
            prompt_snapshot=prompt_snapshot,
        )
        label_state = None
        if expected_verdict is not None:
            # Existing legacy/production submissions must never gain test-file
            # provenance merely because historical purpose data was incomplete.
            if analysis.analysis_purpose != "test":
                raise AnalysisIngestError("event_purpose_conflict", 409)
            if connection.dialect.name != "sqlite":
                db.execute(select(Analysis.id).where(Analysis.id == analysis.id).with_for_update())
            latest = db.scalar(select(AnalysisLabel).where(
                AnalysisLabel.analysis_id == analysis.id,
            ).order_by(AnalysisLabel.revision.desc()).limit(1))
            if latest is not None:
                if latest.verdict != expected_verdict:
                    raise AnalysisIngestError("expected_verdict_conflict", 409)
                label_state = "unchanged"
            else:
                identifier = attachment_id or str(uuid.uuid4())
                # This is attachment metadata, not a preview token or event hash.
                digest = hashlib.sha256(f"test-upload-reference-v1:{identifier}".encode("utf-8")).hexdigest()
                db.add(AnalysisLabel(
                    analysis_id=analysis.id, revision=1, verdict=expected_verdict,
                    source_kind="synthetic_expected", source_ref=label_source_ref,
                    ai_visible=ai_visible, created_by=actor,
                    attachment_id=identifier, token_digest=digest,
                ))
                db.add(AccessAudit(
                    actor_kind="admin_session", actor_id=actor,
                    action="attach_test_upload_expected_label",
                    resource_type="evaluation_label_attachment", resource_id=identifier,
                ))
                label_state = "attached"
        if commit:
            db.commit()
        else:
            db.flush()
        return analysis, duplicate, label_state
    except IntegrityError:
        if commit:
            db.rollback()
        raise AnalysisIngestError("test_upload_changed_concurrently", 409) from None
    except OperationalError:
        if commit:
            db.rollback()
        raise AnalysisIngestError("test_upload_busy", 503) from None
    except Exception:
        if commit:
            db.rollback()
        raise
