import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class AnalysisStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Verdict(str, enum.Enum):
    true_positive = "true_positive"
    false_positive = "false_positive"
    inconclusive = "inconclusive"


class ReviewDecision(str, enum.Enum):
    true_positive = "true_positive"
    false_positive = "false_positive"
    deferred = "deferred"


class RunStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"


class ModelProfileStatus(str, enum.Enum):
    draft = "draft"
    verified = "verified"
    production = "production"
    disabled = "disabled"


class ModelTestMode(str, enum.Enum):
    quick = "quick"
    full = "full"


class ModelTestStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    passed = "passed"
    failed = "failed"


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (
        UniqueConstraint("source_system", "event_id", name="uq_analysis_source_event"),
        Index("ix_analyses_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    src_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    dest_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    src_port: Mapped[int | None] = mapped_column(Integer)
    dest_port: Mapped[int | None] = mapped_column(Integer)
    signature: Mapped[str | None] = mapped_column(Text)
    event_name: Mapped[str | None] = mapped_column(String(500))
    waf_vendor: Mapped[str] = mapped_column(String(120), nullable=False)
    waf_action: Mapped[str] = mapped_column(String(1), nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extra_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(32), default=AnalysisStatus.pending.value, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)

    verdict: Mapped[str | None] = mapped_column(String(32))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    summary_ko: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(120))
    model_profile: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    runs: Mapped[list["AgentRun"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")
    reviews: Mapped[list["Review"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_analysis_created", "analysis_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False)
    framework_run_id: Mapped[str | None] = mapped_column(String(255))
    fingerprint: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.running.value, nullable=False)
    failure_id: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    analysis: Mapped[Analysis] = relationship(back_populates="runs")
    steps: Mapped[list["AgentStep"]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="AgentStep.sequence")


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_agent_step_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_ciphertext: Mapped[str | None] = mapped_column(Text)
    output_ciphertext: Mapped[str | None] = mapped_column(Text)
    encryption_key_version: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    tool_calls_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("source_system", "external_review_id", name="uq_review_source_external"),
        Index("ix_reviews_analysis_created", "analysis_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    external_review_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    analyst_id: Mapped[str | None] = mapped_column(String(255))
    comment: Mapped[str | None] = mapped_column(Text)
    ai_visible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    analysis: Mapped[Analysis] = relationship(back_populates="reviews")


class AccessAudit(Base):
    __tablename__ = "access_audits"
    __table_args__ = (Index("ix_access_audits_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class VLLMProfile(Base):
    __tablename__ = "vllm_profiles"
    __table_args__ = (
        UniqueConstraint("name", name="uq_vllm_profile_name"),
        Index(
            "uq_vllm_single_production",
            "status",
            unique=True,
            sqlite_where=text("status = 'production'"),
            postgresql_where=text("status = 'production'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    encryption_key_version: Mapped[str | None] = mapped_column(String(64))
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    context_window: Mapped[int] = mapped_column(Integer, default=32768, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=3072, nullable=False)
    test_concurrency: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    tls_verify: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=ModelProfileStatus.draft.value, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    test_runs: Mapped[list["VLLMTestRun"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by=lambda: VLLMTestRun.created_at.desc(),
    )


class VLLMTestRun(Base):
    __tablename__ = "vllm_test_runs"
    __table_args__ = (Index("ix_vllm_test_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_id: Mapped[str] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=ModelTestStatus.pending.value, nullable=False)
    profile_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    checks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    profile: Mapped[VLLMProfile] = relationship(back_populates="test_runs")
