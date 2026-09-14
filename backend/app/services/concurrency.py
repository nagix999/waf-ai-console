"""DB-wide admission, not a GPU scheduler. Never store credentials or payloads."""
import asyncio
import hashlib
import ipaddress
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..models import Analysis, ConcurrencyConfiguration, LLMCallSlot, VLLMProfile, utcnow

MAX_ANALYSES_PER_PURPOSE = 32
MAX_SERVER_CALLS = 64


def server_key(profile):
    """Aliases/models/path variations at one host:port share the same limit."""
    parsed = urlsplit(profile.base_url)
    host = parsed.hostname
    if not host or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("invalid_llm_server")
    try:
        host = ipaddress.ip_address(host).compressed
    except ValueError:
        host = host.lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"


def lock_configuration(db):
    # SQLite is the currently supported shared queue. Serialize count + claim
    # and settings changes under the same short writer transaction.
    db.execute(insert(ConcurrencyConfiguration).values(id=1, revision=0, production=1, test=1,
               server_limits={}).on_conflict_do_nothing(index_elements=["id"]))
    db.execute(update(ConcurrencyConfiguration).where(ConcurrencyConfiguration.id == 1)
               .values(revision=ConcurrencyConfiguration.revision))
    return db.get(ConcurrencyConfiguration, 1, populate_existing=True)


def purpose_filter(purpose):
    return Analysis.analysis_purpose == "test" if purpose == "test" else Analysis.analysis_purpose != "test"


def active_analyses(db, purpose):
    return db.scalar(select(func.count()).select_from(Analysis).where(
        Analysis.model_test_run_id.is_(None), purpose_filter(purpose), Analysis.status == "processing",
        Analysis.lease_expires_at > utcnow()))


def configuration_document(db):
    config = db.get(ConcurrencyConfiguration, 1)
    limits = dict(config.server_limits) if config else {}
    servers = {}
    for profile in db.scalars(select(VLLMProfile).order_by(VLLMProfile.name)):
        try:
            key = server_key(profile)
        except ValueError:
            continue
        entry = servers.setdefault(key, {"server_key": key, "max_calls": limits.get(key, 1), "profiles": []})
        entry["profiles"].append({"id": profile.id, "name": profile.name})
    state = {"revision": config.revision if config else 0,
             "production": config.production if config else 1, "test": config.test if config else 1,
             "servers": list(servers.values())}
    token = hashlib.sha256(json.dumps({**state, "saved_limits": limits}, sort_keys=True).encode()).hexdigest()
    return {**state, "state_token": token,
            "active": {purpose: active_analyses(db, purpose) for purpose in ("production", "test")}}


def configured_server_limit(engine, profile):
    with Session(engine) as db:
        config = db.get(ConcurrencyConfiguration, 1)
        return config.server_limits.get(server_key(profile), 1) if config else 1


def try_reserve(engine, key, hold_seconds):
    now = utcnow()
    with Session(engine) as db:
        # Avoid a write transaction when clearly full; repeat under the lock.
        count = lambda: db.scalar(select(func.count()).select_from(LLMCallSlot).where(
            LLMCallSlot.server_key == key, LLMCallSlot.expires_at > now))
        config = db.get(ConcurrencyConfiguration, 1)
        if count() >= (config.server_limits.get(key, 1) if config else 1):
            return None
        db.rollback()
        config = lock_configuration(db)
        now = utcnow()
        if count() >= config.server_limits.get(key, 1):
            return None
        db.execute(delete(LLMCallSlot).where(LLMCallSlot.expires_at <= now))
        token = str(uuid.uuid4())
        db.add(LLMCallSlot(id=token, server_key=key, expires_at=now + timedelta(seconds=hold_seconds)))
        db.commit()
        return token


def release(engine, token):
    with engine.begin() as connection:
        connection.execute(delete(LLMCallSlot).where(LLMCallSlot.id == token))


@asynccontextmanager
async def call_slot(engine, profile, check=None, *, timeout_seconds):
    """Wait outside model deadlines, then hold one slot across serial retries.

    A hard local deadline precedes reservation expiry by 30s. Process crashes
    release capacity after expiry. Remote GPU cancellation cannot be guaranteed.
    """
    if engine is None:  # Pure adapter/offline callers have no shared runtime.
        if check:
            check()  # Omitting capacity coordination must never omit egress.
        yield {"wait_ms": 0, "limited": False}
        return
    key, started, token = server_key(profile), time.monotonic(), None
    while token is None:
        if check:
            check()
        token = try_reserve(engine, key, timeout_seconds + 30)
        if token is None:
            await asyncio.sleep(0.25)
    reserved_at = time.monotonic()
    try:
        if check:
            check()  # Permissions/claim may have changed while waiting.
        remaining = timeout_seconds - (time.monotonic() - reserved_at)
        if remaining <= 0:
            raise TimeoutError("llm_call_deadline_exceeded")
        async with asyncio.timeout(remaining):
            yield {"wait_ms": round((time.monotonic() - started) * 1000), "limited": True}
    finally:
        release(engine, token)
