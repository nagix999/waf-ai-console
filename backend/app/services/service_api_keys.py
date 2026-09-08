"""High-entropy service credentials; only a one-way digest persists."""
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, timedelta

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from ..api_key_schemas import ServiceApiKeyCreate, ServiceApiKeyItem, valid_service_source
from ..models import ServiceApiKey, utcnow


TOKEN_PATTERN = re.compile(r"wafsvc_([0-9a-f]{32})_([A-Za-z0-9_-]{43})\Z", re.ASCII)
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
LAST_USED_INTERVAL_SECONDS = 60


class ServiceApiKeyError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def to_key_item(key: ServiceApiKey) -> ServiceApiKeyItem:
    return ServiceApiKeyItem(
        id=key.id, name=key.name, key_prefix=key.key_prefix, source_system=key.source_system,
        scopes=sorted(key.scopes_json), created_at=key.created_at,
        last_used_at=key.last_used_at, revoked_at=key.revoked_at,
    )


def issue_key(db: Session, payload: ServiceApiKeyCreate, actor: str) -> tuple[ServiceApiKey, str]:
    identity = uuid.uuid4()
    prefix = f"wafsvc_{identity.hex}"
    # The public identifier is not entropy. The secret contains 256 random bits.
    raw = f"{prefix}_{secrets.token_urlsafe(32)}"
    key = ServiceApiKey(
        id=str(identity), name=payload.name, key_prefix=prefix,
        key_hash=hashlib.sha256(raw.encode("ascii")).hexdigest(),
        source_system=payload.source_system, scopes_json=sorted(payload.scopes), created_by=actor,
    )
    db.add(key)
    db.flush()
    return key, raw


def get_key(db: Session, key_id: str) -> ServiceApiKey:
    key = db.get(ServiceApiKey, key_id, populate_existing=True)
    if key is None or key.deleted_at is not None:
        raise ServiceApiKeyError("service_api_key_not_found", 404)
    return key


def rename_key(db: Session, key_id: str, name: str) -> ServiceApiKey:
    get_key(db, key_id)
    changed = db.execute(update(ServiceApiKey)
                         .where(ServiceApiKey.id == key_id, ServiceApiKey.revoked_at.is_(None))
                         .values(name=name).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise ServiceApiKeyError("service_api_key_revoked")
    return get_key(db, key_id)


def revoke_key(db: Session, key_id: str, actor: str) -> tuple[ServiceApiKey, bool]:
    get_key(db, key_id)
    changed = db.execute(update(ServiceApiKey)
                         .where(ServiceApiKey.id == key_id, ServiceApiKey.revoked_at.is_(None))
                         .values(revoked_at=utcnow(), revoked_by=actor)
                         .execution_options(synchronize_session=False))
    return get_key(db, key_id), changed.rowcount == 1


def authenticate_key(db: Session, supplied: str) -> ServiceApiKey | None:
    match = TOKEN_PATTERN.fullmatch(supplied)
    if match is None:
        return None
    key = db.get(ServiceApiKey, str(uuid.UUID(hex=match[1])), populate_existing=True)
    if key is None or key.revoked_at is not None or key.deleted_at is not None:
        return None
    if not isinstance(key.key_hash, str) or DIGEST_PATTERN.fullmatch(key.key_hash) is None:
        return None
    digest = hashlib.sha256(supplied.encode("ascii")).hexdigest()
    if not hmac.compare_digest(key.key_hash, digest):
        return None
    # Directly modified database rows cannot grant admin or internal namespaces.
    scopes = key.scopes_json
    if not valid_service_source(key.source_system) or not isinstance(scopes, list) or not scopes or any(type(scope) is not str or scope not in {"ingest", "review"} for scope in scopes) or len(scopes) != len(set(scopes)):
        return None
    now = utcnow()
    previous = key.last_used_at
    if previous is not None and previous.tzinfo is None:
        previous = previous.replace(tzinfo=UTC)
    if previous is None or previous <= now - timedelta(seconds=LAST_USED_INTERVAL_SECONDS):
        # Authentication, not successful endpoint execution, is being counted.
        # Conditional updates never clear revocation or overwrite a newer time.
        db.execute(update(ServiceApiKey)
                   .where(ServiceApiKey.id == key.id, ServiceApiKey.revoked_at.is_(None),
                          or_(ServiceApiKey.last_used_at.is_(None), ServiceApiKey.last_used_at <= now - timedelta(seconds=LAST_USED_INTERVAL_SECONDS)))
                   .values(last_used_at=now).execution_options(synchronize_session=False))
    return key


def delete_key(db: Session, key_id: str, actor: str) -> bool:
    """Retain the tombstone for attribution/audit; never delete analyses."""
    key = db.get(ServiceApiKey, key_id, populate_existing=True)
    if key is None:
        raise ServiceApiKeyError("service_api_key_not_found", 404)
    if key.deleted_at is not None:
        return False
    now = utcnow()
    changed = db.execute(update(ServiceApiKey).where(
        ServiceApiKey.id == key_id, ServiceApiKey.deleted_at.is_(None),
    ).values(deleted_at=now, deleted_by=actor,
             revoked_at=key.revoked_at or now, revoked_by=key.revoked_by or actor)
      .execution_options(synchronize_session=False))
    return changed.rowcount == 1
