from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..models import Analysis, Review
from ..schemas import AnalysisDetail, AnalysisInput, AnalysisSummary
from .crypto import CryptoService


def review_state(analysis: Analysis) -> str:
    if not analysis.reviews:
        return "unreviewed"
    return "deferred" if analysis.reviews[-1].decision == "deferred" else "confirmed"


def to_summary(analysis: Analysis) -> AnalysisSummary:
    return AnalysisSummary(
        id=analysis.id,
        source_system=analysis.source_system,
        event_id=analysis.event_id,
        company_name=analysis.company_name,
        waf_vendor=analysis.waf_vendor,
        waf_action=analysis.waf_action,
        signature=analysis.signature,
        event_name=analysis.event_name,
        status=analysis.status,
        verdict=analysis.verdict,
        confidence_score=analysis.confidence_score,
        summary_ko=analysis.summary_ko,
        input_truncated=analysis.input_truncated,
        created_at=analysis.created_at,
        completed_at=analysis.completed_at,
        review_state=review_state(analysis),
    )


def to_detail(analysis: Analysis) -> AnalysisDetail:
    return AnalysisDetail(
        **to_summary(analysis).model_dump(),
        src_ip=analysis.src_ip,
        dest_ip=analysis.dest_ip,
        src_port=analysis.src_port,
        dest_port=analysis.dest_port,
        result=analysis.result_json,
        prompt_version=analysis.prompt_version,
        model_profile=analysis.model_profile,
        error_code=analysis.error_code,
        error_message=analysis.error_message,
    )


def enqueue_analysis(
    db: Session,
    crypto: CryptoService,
    source_system: str,
    payload: AnalysisInput,
) -> tuple[Analysis, bool]:
    existing = db.scalar(
        select(Analysis)
        .options(selectinload(Analysis.reviews))
        .where(Analysis.source_system == source_system, Analysis.event_id == payload.event_id)
    )
    if existing:
        return existing, True

    row = Analysis(
        source_system=source_system,
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
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Analysis)
            .options(selectinload(Analysis.reviews))
            .where(Analysis.source_system == source_system, Analysis.event_id == payload.event_id)
        )
        if existing is None:
            raise
        return existing, True
    db.refresh(row)
    row.reviews = []
    return row, False


def fetch_analysis(db: Session, analysis_id: str) -> Analysis | None:
    return db.scalar(
        select(Analysis)
        .options(selectinload(Analysis.reviews))
        .where(Analysis.id == analysis_id)
    )


def count_analyses(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Analysis)) or 0)


def now_utc() -> datetime:
    return datetime.now(UTC)
