"""Provider-neutral, immutable policy versions. Callers own the transaction."""
import hashlib
import hmac

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AccessAudit, ModelProfileStatus, PromptPolicyState, PromptPolicyVersion, VLLMProfile
from ..prompt_schemas import PromptPolicyCreate, PromptPolicyDetail, PromptPolicySummary
from .crypto import CryptoService


DEFAULT_VERSION_ID = "00000000-0000-4000-8000-000000000001"


class PromptPolicyError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def content_hash(policy_text: str) -> str:
    return hashlib.sha256(policy_text.encode("utf-8")).hexdigest()


def _sqlite_write_transaction(db: Session) -> None:
    # SQLite legacy mode does not BEGIN for SELECT or SAVEPOINT. An actual
    # outer transaction prevents releasing our savepoint from committing a
    # bootstrap separately from the caller's analysis/settings transaction.
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def get_policy_state(db: Session, crypto: CryptoService) -> PromptPolicyState:
    state = db.get(PromptPolicyState, 1)
    if state is not None:
        return state
    from ..agent.prompts import DEFAULT_POLICY_NAME, DEFAULT_POLICY_TEXT

    _sqlite_write_transaction(db)
    # Re-read after taking SQLite's writer lock: another worker may have
    # initialized the default while this connection was waiting.
    state = db.get(PromptPolicyState, 1, populate_existing=True)
    if state is not None:
        return state
    try:
        with db.begin_nested():
            version = PromptPolicyVersion(
                id=DEFAULT_VERSION_ID,
                version_number=1,
                name=DEFAULT_POLICY_NAME,
                change_note="기본 판정 지침 등록. 실제 모델 품질 검증을 의미하지 않습니다.",
                policy_ciphertext=crypto.encrypt_text(DEFAULT_POLICY_TEXT),
                encryption_key_version=crypto.key_version,
                content_hash=content_hash(DEFAULT_POLICY_TEXT),
                created_by="system",
            )
            db.add(version)
            db.flush()
            state = PromptPolicyState(id=1, active_version_id=version.id, revision=1)
            db.add(state)
            db.add(AccessAudit(
                actor_kind="system", actor_id="prompt_policy_bootstrap", action="initialize_prompt_policy",
                resource_type="prompt_policy_version", resource_id=version.id,
            ))
            db.flush()
        return state
    except IntegrityError:
        # Non-SQLite concurrent bootstrap is resolved by the fixed IDs and
        # singleton constraint; do not roll back unrelated caller work.
        state = db.get(PromptPolicyState, 1, populate_existing=True)
        if state is None:
            raise PromptPolicyError("prompt_policy_initialization_conflict") from None
        return state


def get_policy_version(db: Session, version_id: str) -> PromptPolicyVersion:
    version = db.get(PromptPolicyVersion, version_id)
    if version is None:
        raise PromptPolicyError("prompt_policy_version_not_found", 404)
    return version


def read_policy_text(version: PromptPolicyVersion, crypto: CryptoService) -> str:
    try:
        policy_text = crypto.decrypt_text(version.policy_ciphertext)
        actual_hash = content_hash(policy_text)
    except (ValueError, UnicodeError):
        raise PromptPolicyError("prompt_policy_content_unavailable", 503) from None
    if not isinstance(version.content_hash, str) or not version.content_hash.isascii() or not hmac.compare_digest(actual_hash, version.content_hash):
        raise PromptPolicyError("prompt_policy_integrity_failed", 503)
    return policy_text


def get_active_policy(db: Session, crypto: CryptoService) -> PromptPolicyVersion:
    state = get_policy_state(db, crypto)
    version = get_policy_version(db, state.active_version_id)
    read_policy_text(version, crypto)
    return version


def create_policy_version(
    db: Session, crypto: CryptoService, payload: PromptPolicyCreate, created_by: str,
) -> PromptPolicyVersion:
    _sqlite_write_transaction(db)
    get_policy_state(db, crypto)
    if payload.parent_version_id:
        get_policy_version(db, payload.parent_version_id)
    next_number = int(db.scalar(select(func.max(PromptPolicyVersion.version_number))) or 0) + 1
    version = PromptPolicyVersion(
        version_number=next_number,
        name=payload.name,
        change_note=payload.change_note,
        parent_version_id=payload.parent_version_id,
        policy_ciphertext=crypto.encrypt_text(payload.policy_text),
        encryption_key_version=crypto.key_version,
        content_hash=content_hash(payload.policy_text),
        created_by=created_by,
    )
    db.add(version)
    db.flush()
    return version


def activate_policy_version(
    db: Session, crypto: CryptoService, version_id: str, expected_revision: int,
) -> PromptPolicyState:
    from ..agent.input_builder import ESTIMATED_CHARS_PER_TOKEN, MIN_INPUT_CHARS
    from ..agent.prompts import policy_reserved_tokens

    _sqlite_write_transaction(db)
    get_policy_state(db, crypto)
    policy_text = read_policy_text(get_policy_version(db, version_id), crypto)
    profile = db.scalar(select(VLLMProfile).where(VLLMProfile.status == ModelProfileStatus.production.value))
    if profile is not None:
        input_tokens = profile.context_window - profile.max_output_tokens - policy_reserved_tokens(policy_text)
        if profile.max_output_tokens < 0 or input_tokens * ESTIMATED_CHARS_PER_TOKEN < MIN_INPUT_CHARS:
            raise PromptPolicyError("prompt_policy_context_budget_too_small", 422)
    changed = db.execute(
        update(PromptPolicyState)
        .where(PromptPolicyState.id == 1, PromptPolicyState.revision == expected_revision)
        .values(active_version_id=version_id, revision=expected_revision + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise PromptPolicyError("prompt_policy_changed_concurrently")
    return db.get(PromptPolicyState, 1, populate_existing=True)


def to_policy_summary(version: PromptPolicyVersion) -> PromptPolicySummary:
    return PromptPolicySummary(**{
        name: getattr(version, name)
        for name in ("id", "version_number", "name", "change_note", "parent_version_id", "content_hash", "created_by", "created_at")
    })


def to_policy_detail(version: PromptPolicyVersion, crypto: CryptoService) -> PromptPolicyDetail:
    return PromptPolicyDetail(**to_policy_summary(version).model_dump(), policy_text=read_policy_text(version, crypto))
