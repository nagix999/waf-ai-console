import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class AnalysisStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class AnalysisPurpose(str, enum.Enum):
    production = "production"
    test = "test"
    legacy_unknown = "legacy_unknown"


class IngestChannel(str, enum.Enum):
    service_api = "service_api"
    file_upload = "file_upload"
    test_lab = "test_lab"
    model_validation = "model_validation"
    legacy_unknown = "legacy_unknown"


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


class ModelProvider(str, enum.Enum):
    vllm = "vllm"
    openai = "openai"


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
        Index("uq_analysis_source_event_original", "source_system", "event_id", unique=True,
              sqlite_where=text("retry_of_analysis_id IS NULL"), postgresql_where=text("retry_of_analysis_id IS NULL")),
        Index("ix_analyses_status_created", "status", "created_at"),
        Index("ix_analyses_purpose_created", "analysis_purpose", "created_at"),
        Index("ix_analyses_severity_created", "severity", "created_at"),
        Index("ix_analyses_category_created", "threat_category", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    analysis_purpose: Mapped[str] = mapped_column(String(32), default="legacy_unknown", server_default="legacy_unknown", nullable=False)
    ingest_channel: Mapped[str] = mapped_column(String(32), default="legacy_unknown", server_default="legacy_unknown", nullable=False)
    event_fingerprint: Mapped[str | None] = mapped_column(String(64))
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
    severity: Mapped[str | None] = mapped_column(String(16))
    threat_category: Mapped[str | None] = mapped_column(String(120))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    initial_verdict: Mapped[str | None] = mapped_column(String(32))
    initial_probability: Mapped[float | None] = mapped_column(Float)
    initial_model_version: Mapped[str | None] = mapped_column(String(255))
    summary_ko: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(120))
    prompt_policy_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_policy_versions.id", ondelete="RESTRICT"))
    prompt_snapshot_ciphertext: Mapped[str | None] = mapped_column(Text)
    input_schema_version_id: Mapped[str | None] = mapped_column(ForeignKey("input_schema_versions.id", ondelete="RESTRICT"))
    input_schema_snapshot_ciphertext: Mapped[str | None] = mapped_column(Text)
    model_profile: Mapped[str | None] = mapped_column(String(120))
    model_test_run_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_test_runs.id", ondelete="RESTRICT"), index=True)
    service_api_key_id: Mapped[str | None] = mapped_column(ForeignKey("service_api_keys.id", ondelete="RESTRICT"), index=True)
    retry_of_analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analyses.id", ondelete="RESTRICT"), unique=True)
    retry_idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    execution_snapshot_ciphertext: Mapped[str | None] = mapped_column(Text)
    internal_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

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


class AnalysisLabel(Base):
    """Append-only references, never part of event/model input."""
    __tablename__ = "analysis_labels"
    __table_args__ = (
        UniqueConstraint("analysis_id", "revision", name="uq_analysis_label_revision"),
        Index("ix_analysis_labels_attachment", "attachment_id"),
        CheckConstraint("revision > 0", name="ck_analysis_label_revision"),
        CheckConstraint("verdict IN ('true_positive', 'false_positive', 'inconclusive')", name="ck_analysis_label_verdict"),
        CheckConstraint("source_kind IN ('synthetic_expected', 'reference')", name="ck_analysis_label_source"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    ai_visible: Mapped[bool | None] = mapped_column(Boolean)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    attachment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    comment_ciphertext: Mapped[str | None] = mapped_column(Text)
    encryption_key_version: Mapped[str | None] = mapped_column(String(64))


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


class PromptPolicyVersion(Base):
    """Immutable saved policy content; activation lives in the singleton state."""
    __tablename__ = "prompt_policy_versions"
    __table_args__ = (
        UniqueConstraint("version_number", name="uq_prompt_policy_version_number"),
        CheckConstraint("version_number > 0", name="ck_prompt_policy_version_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    change_note: Mapped[str] = mapped_column(String(1000), nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey("prompt_policy_versions.id", ondelete="RESTRICT"))
    policy_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PromptPolicyState(Base):
    __tablename__ = "prompt_policy_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_prompt_policy_state_singleton"),
        CheckConstraint("revision >= 1", name="ck_prompt_policy_state_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_policy_versions.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class InputSchemaVersion(Base):
    __tablename__ = "input_schema_versions"
    __table_args__ = (
        UniqueConstraint("version_number", name="uq_input_schema_version_number"),
        CheckConstraint("version_number > 0", name="ck_input_schema_version_number"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    change_note: Mapped[str] = mapped_column(String(1000), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("input_schema_versions.id", ondelete="RESTRICT"))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InputSchemaState(Base):
    __tablename__ = "input_schema_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_input_schema_state_singleton"),
        CheckConstraint("revision >= 1", name="ck_input_schema_state_revision"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active_version_id: Mapped[str] = mapped_column(ForeignKey("input_schema_versions.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class InputSchemaActivation(Base):
    __tablename__ = "input_schema_activations"
    __table_args__ = (UniqueConstraint("revision", name="uq_input_schema_activation_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    previous_version_id: Mapped[str | None] = mapped_column(ForeignKey("input_schema_versions.id", ondelete="RESTRICT"))
    active_version_id: Mapped[str] = mapped_column(ForeignKey("input_schema_versions.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InternalEgressTarget(Base):
    __tablename__ = "internal_egress_targets"
    __table_args__ = (
        UniqueConstraint("ip_address", "port", name="uq_internal_egress_ip_port"),
        CheckConstraint("port >= 1 AND port <= 65535", name="ck_internal_egress_port"),
        CheckConstraint("revision >= 1", name="ck_internal_egress_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ip_address: Mapped[str] = mapped_column(String(39), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


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
        Index("uq_vllm_single_test", "is_test", unique=True,
              sqlite_where=text("is_test = 1"), postgresql_where=text("is_test = true")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="vllm", server_default="vllm", nullable=False)
    external_data_approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
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
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    test_runs: Mapped[list["VLLMTestRun"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by=lambda: VLLMTestRun.created_at.desc(),
    )


class AgentConfiguration(Base):
    __tablename__ = "agent_configurations"
    __table_args__ = (CheckConstraint("id = 1", name="ck_agent_configuration_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    production_verifier_profile_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="RESTRICT"))
    test_verifier_profile_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="RESTRICT"))
    production_evidence_editor_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    test_evidence_editor_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    production_evidence_editor_profile_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="RESTRICT"))
    test_evidence_editor_profile_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="RESTRICT"))


class ConcurrencyConfiguration(Base):
    __tablename__ = "concurrency_configuration"
    __table_args__ = (CheckConstraint("id = 1", name="ck_concurrency_singleton"),
                     CheckConstraint("production >= 1 AND production <= 32 AND test >= 1 AND test <= 32", name="ck_concurrency_range"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    production: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    test: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    server_limits: Mapped[dict[str, int]] = mapped_column(JSON, default=dict, nullable=False)


class LLMCallSlot(Base):
    """Temporary, content-free reservations shared by every worker."""
    __tablename__ = "llm_call_slots"
    __table_args__ = (Index("ix_llm_slots_server_expires", "server_key", "expires_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_key: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VLLMTestRun(Base):
    __tablename__ = "vllm_test_runs"
    __table_args__ = (Index("ix_vllm_test_status_created", "status", "created_at"),
                     Index("uq_vllm_tests_idempotency", "idempotency_key", unique=True))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    profile_id: Mapped[str] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=ModelTestStatus.pending.value, nullable=False)
    profile_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    include_dataset: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    dataset_hash: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    input_schema_version_id: Mapped[str | None] = mapped_column(ForeignKey("input_schema_versions.id", ondelete="RESTRICT"))
    input_schema_snapshot_ciphertext: Mapped[str | None] = mapped_column(Text)
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


class TestConfigurationDefaults(Base):
    __tablename__ = "test_configuration_defaults"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    primary_profile_id: Mapped[str] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="RESTRICT"), nullable=False)
    verifier_profile_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="RESTRICT"))
    evidence_editor_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_editor_profile_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="RESTRICT"))
    prompt_policy_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_policy_versions.id", ondelete="RESTRICT"), nullable=False)
    input_schema_version_id: Mapped[str] = mapped_column(ForeignKey("input_schema_versions.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class GroundTruthImportPreview(Base):
    __tablename__ = "ground_truth_import_previews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    test_run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="RESTRICT"), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    manifest_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    result_json: Mapped[dict | None] = mapped_column(JSON)


class TestRun(Base):
    """Named, immutable submission boundary; analyses remain the work queue."""
    __tablename__ = "test_runs"
    __table_args__ = (Index("ix_test_runs_created", "created_at"),
                     Index("ix_test_runs_official_pending", "official_evaluation_pending", "id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    test_purpose: Mapped[str] = mapped_column(String(32), default="development", server_default="legacy_unknown", nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255))
    dataset_hash: Mapped[str | None] = mapped_column(String(64))
    dataset_version_id: Mapped[str | None] = mapped_column(ForeignKey("validation_dataset_versions.id", ondelete="RESTRICT"))
    api_source_system: Mapped[str | None] = mapped_column(String(120), index=True)
    accepting_items: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    model_test_run_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_test_runs.id", ondelete="RESTRICT"), unique=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("vllm_profiles.id", ondelete="RESTRICT"))
    profile_fingerprint: Mapped[str | None] = mapped_column(String(64))
    profile_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    configuration_snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    configuration_hash: Mapped[str | None] = mapped_column(String(64))
    evaluation_mode: Mapped[str] = mapped_column(String(24), default="reference", server_default="reference", nullable=False)
    approved_item_version_ids: Mapped[list[str] | None] = mapped_column(JSON)
    metrics_version: Mapped[str | None] = mapped_column(String(64))
    official_evaluation_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_snapshot_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_editor_snapshot_ciphertext: Mapped[str | None] = mapped_column(Text)
    prompt_policy_version_id: Mapped[str] = mapped_column(ForeignKey("prompt_policy_versions.id", ondelete="RESTRICT"), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    input_schema_version_id: Mapped[str | None] = mapped_column(ForeignKey("input_schema_versions.id", ondelete="RESTRICT"))
    input_schema_snapshot_ciphertext: Mapped[str | None] = mapped_column(Text)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TestRunItem(Base):
    __tablename__ = "test_run_items"
    __table_args__ = (
        UniqueConstraint("test_run_id", "row_number", name="uq_test_run_row"),
        Index("ix_test_run_items_analysis", "analysis_id"),
        Index("ix_test_run_items_run", "test_run_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    test_run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="RESTRICT"), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analyses.id", ondelete="RESTRICT"))
    label_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_labels.id", ondelete="RESTRICT"))
    dataset_item_version_id: Mapped[str | None] = mapped_column(ForeignKey("validation_dataset_items.id", ondelete="RESTRICT"))
    event_id: Mapped[str | None] = mapped_column(String(255))
    difficulty: Mapped[str | None] = mapped_column(String(80))
    test_category: Mapped[str | None] = mapped_column(String(120))
    case_name: Mapped[str | None] = mapped_column(String(240))
    ingest_status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))


class ServiceApiKey(Base):
    __tablename__ = "service_api_keys"
    __table_args__ = (
        UniqueConstraint("name", name="uq_service_api_key_name"),
        UniqueConstraint("key_hash", name="uq_service_api_key_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(48), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), default="production", server_default="production", nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(255))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(255))


class ValidationDataset(Base):
    __tablename__ = "validation_datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ValidationDatasetVersion(Base):
    __tablename__ = "validation_dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "revision", name="uq_validation_dataset_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("validation_datasets.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    item_version_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    membership_hash: Mapped[str | None] = mapped_column(String(64))
    publish_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ValidationDatasetItem(Base):
    """Immutable item revisions. A dataset version freezes membership by IDs."""
    __tablename__ = "validation_dataset_items"
    __table_args__ = (
        UniqueConstraint("item_id", "revision", name="uq_validation_item_revision"),
        CheckConstraint("review_status IN ('draft', 'reviewed', 'approved')", name="ck_dataset_review_status"),
        CheckConstraint("review_status = 'draft' OR reference_verdict IS NOT NULL", name="ck_dataset_review_verdict"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("validation_datasets.id", ondelete="RESTRICT"), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    event_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    schema_snapshot_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_verdict: Mapped[str | None] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft", nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), default="reference", nullable=False)
    # Frozen source-label provenance, not a live FK: dataset copies survive
    # explicit deletion of their source analysis and labels.
    source_ref: Mapped[str | None] = mapped_column(String(120))
    source_label_id: Mapped[str | None] = mapped_column(String(36))
    source_created_by: Mapped[str | None] = mapped_column(String(255))
    ai_visible: Mapped[bool | None] = mapped_column(Boolean)
    comment_ciphertext: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[str | None] = mapped_column(String(80))
    test_category: Mapped[str | None] = mapped_column(String(120))
    case_name: Mapped[str | None] = mapped_column(String(240))
    internal_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    original_analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analyses.id", ondelete="RESTRICT"))
    original_analysis_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ValidationDatasetWorkingState(Base):
    __tablename__ = "validation_dataset_working_states"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("validation_datasets.id", ondelete="RESTRICT"), primary_key=True)
    working_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ValidationDatasetWorkingItem(Base):
    __tablename__ = "validation_dataset_working_items"
    __table_args__ = (UniqueConstraint("dataset_id", "item_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("validation_datasets.id", ondelete="RESTRICT"), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    schema_snapshot_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_verdict: Mapped[str | None] = mapped_column(String(32))
    reference_origin: Mapped[str] = mapped_column(String(32), default="none", server_default="none", nullable=False)
    reference_origin_ref_id: Mapped[str | None] = mapped_column(String(36))
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_state: Mapped[str] = mapped_column(String(24), default="needs_attention", nullable=False)
    validation_issues_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), default="reference", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    source_label_id: Mapped[str | None] = mapped_column(String(36))
    source_created_by: Mapped[str | None] = mapped_column(String(255))
    ai_visible: Mapped[bool | None] = mapped_column(Boolean)
    comment_ciphertext: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[str | None] = mapped_column(String(80))
    test_category: Mapped[str | None] = mapped_column(String(120))
    case_name: Mapped[str | None] = mapped_column(String(240))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    internal_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    original_analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analyses.id", ondelete="SET NULL"))
    original_analysis_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TestEvaluation(Base):
    __tablename__ = "test_evaluations"
    __table_args__ = (UniqueConstraint("test_run_id", "revision", name="uq_test_evaluation_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    test_run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    label_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evaluation_kind: Mapped[str] = mapped_column(String(24), default="reference", server_default="reference", nullable=False)
    metrics_version: Mapped[str | None] = mapped_column(String(64))
    configuration_hash: Mapped[str | None] = mapped_column(String(64))
    analysis_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProductionPromotion(Base):
    """Immutable baseline/promotion records. Execution tables remain authoritative."""
    __tablename__ = "production_promotions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_test_run_id: Mapped[str | None] = mapped_column(ForeignKey("test_runs.id", ondelete="RESTRICT"))
    evaluation_id: Mapped[str | None] = mapped_column(ForeignKey("test_evaluations.id", ondelete="RESTRICT"))
    previous_configuration_hash: Mapped[str | None] = mapped_column(String(64))
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChangeEvent(Base):
    __tablename__ = "change_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    worker_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
