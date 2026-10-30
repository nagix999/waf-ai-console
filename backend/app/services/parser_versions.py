"""Immutable parser definitions and explicit legacy/runtime binding."""
import hashlib
import hmac
import json
import uuid
from functools import cache
from pathlib import Path

from sqlalchemy import select

from ..models import ParserConfiguration, ParserVersion
from .parser_engine import (CAPTURE_ENGINE, DEFAULT_OPTIONS, LEGACY_ENGINE,
                            parse_capture_payload, validate_options)
from .prompt_policies import _sqlite_write_transaction

BASELINE_ID = LEGACY_ENGINE


class ParserVersionError(ValueError):
    def __init__(self, code, status_code=409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


@cache
def engine_artifact_hash(engine):
    # Only fixed bundled files; never a path supplied by a user or model.
    files = [Path(__file__).with_name("http_parser.py")]
    if engine == CAPTURE_ENGINE:
        files.append(Path(__file__).with_name("parser_engine_v1.py"))
    return hashlib.sha256(b"\0".join(path.read_bytes() for path in files)).hexdigest()


def definition_hash(engine, options):
    document = {"engine_version": engine, "options": validate_options(engine, options),
                "engine_artifact_hash": engine_artifact_hash(engine)}
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def legacy_parser_snapshot():
    return {"schema_version": 1, "version_id": BASELINE_ID, "engine_version": LEGACY_ENGINE,
            "options": dict(DEFAULT_OPTIONS), "content_hash": definition_hash(LEGACY_ENGINE, DEFAULT_OPTIONS),
            "selection_origin": "legacy_default"}


def ensure_parser_state(db):
    _sqlite_write_transaction(db)
    baseline = db.get(ParserVersion, BASELINE_ID)
    if baseline is None:
        baseline = ParserVersion(id=BASELINE_ID, name="Generic HTTP v3", engine_version=LEGACY_ENGINE,
            options_json=dict(DEFAULT_OPTIONS), content_hash=definition_hash(LEGACY_ENGINE, DEFAULT_OPTIONS), created_by="system:parser-baseline")
        db.add(baseline)
        db.flush()
    state = db.get(ParserConfiguration, 1)
    if state is None:
        state = ParserConfiguration(id=1, active_version_id=BASELINE_ID, revision=0)
        db.add(state)
        db.flush()
    return state


def get_version(db, version_id):
    ensure_parser_state(db)
    row = db.get(ParserVersion, version_id)
    if row is None:
        raise ParserVersionError("parser_version_not_found", 404)
    try:
        digest = definition_hash(row.engine_version, row.options_json)
    except (TypeError, ValueError):
        raise ParserVersionError("parser_definition_invalid") from None
    if not hmac.compare_digest(digest, row.content_hash):
        raise ParserVersionError("parser_definition_invalid")
    return row


def create_version(db, crypto, *, name, engine_version=CAPTURE_ENGINE, options=None,
                   parent_id=None, description=None, actor="admin"):
    ensure_parser_state(db)
    if parent_id is not None:
        get_version(db, parent_id)
    try:
        clean = validate_options(engine_version, DEFAULT_OPTIONS if options is None else options)
    except (TypeError, ValueError):
        raise ParserVersionError("parser_definition_invalid", 422) from None
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ParserVersionError("parser_name_invalid", 422)
    row = ParserVersion(id=str(uuid.uuid4()), name=name.strip(), engine_version=engine_version,
        options_json=clean, content_hash=definition_hash(engine_version, clean), parent_id=parent_id,
        description_ciphertext=crypto.encrypt_text(description) if description else None, created_by=actor)
    db.add(row)
    db.flush()
    return row


def version_summary(row):
    return {"id": row.id, "name": row.name, "engine_version": row.engine_version,
            "options": row.options_json, "content_hash": row.content_hash, "parent_id": row.parent_id,
            "created_by": row.created_by, "created_at": row.created_at}


def version_detail(row, crypto):
    return {**version_summary(row), "description": crypto.decrypt_text(row.description_ciphertext) if row.description_ciphertext else None}


def _validated(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != {"schema_version", "version_id", "engine_version", "options", "content_hash", "selection_origin"}:
        raise ParserVersionError("parser_snapshot_invalid")
    try:
        if (type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1
                or not isinstance(snapshot["version_id"], str) or not 1 <= len(snapshot["version_id"]) <= 36
                or snapshot["selection_origin"] not in {"legacy_default", "production", "test", "candidate", "retry", "baseline"}
                or not isinstance(snapshot["content_hash"], str)
                or not hmac.compare_digest(snapshot["content_hash"], definition_hash(snapshot["engine_version"], snapshot["options"]))):
            raise ValueError()
        if snapshot["version_id"] == BASELINE_ID and snapshot["engine_version"] != LEGACY_ENGINE:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ParserVersionError("parser_snapshot_invalid") from None
    return {**snapshot, "options": validate_options(snapshot["engine_version"], snapshot["options"])}


def read_parser_snapshot(crypto, target, allow_legacy=True):
    value = getattr(target, "parser_snapshot_json", None)
    identifier = getattr(target, "parser_version_id", None)
    if value is None:
        if identifier is not None or not allow_legacy:
            raise ParserVersionError("parser_snapshot_missing")
        return legacy_parser_snapshot()
    snapshot = _validated(value)
    if snapshot["version_id"] != identifier:
        raise ParserVersionError("parser_snapshot_invalid")
    return snapshot


def pin_parser(db, crypto, target, version_id=None, selection_origin="production"):
    if getattr(target, "parser_snapshot_json", None) is not None or getattr(target, "parser_version_id", None) is not None:
        return read_parser_snapshot(crypto, target, allow_legacy=False)
    state = ensure_parser_state(db)
    row = get_version(db, version_id or (state.active_version_id if selection_origin == "production" else BASELINE_ID))
    snapshot = {"schema_version": 1, "version_id": row.id, "engine_version": row.engine_version,
        "options": dict(row.options_json), "content_hash": row.content_hash, "selection_origin": selection_origin}
    target.parser_version_id, target.parser_snapshot_json = row.id, _validated(snapshot)
    return snapshot


def copy_parser_snapshot(source, target):
    snapshot = read_parser_snapshot(None, source)
    # Baseline implicit history remains implicit unless it was already pinned.
    target.parser_version_id = getattr(source, "parser_version_id", None)
    target.parser_snapshot_json = _validated(snapshot) if getattr(source, "parser_snapshot_json", None) is not None else None


def parser_metadata(snapshot):
    value = _validated(snapshot)
    return {key: value[key] for key in ("version_id", "engine_version", "content_hash", "options")}


def apply_parser_snapshot(raw_payload, snapshot, *, waf_vendor=None):
    value = _validated(snapshot)
    return parse_capture_payload(raw_payload, value["engine_version"], value["options"], waf_vendor=waf_vendor)


def activate_parser_version(db, version_id):
    """Internal hook: caller must enforce the official promotion contract."""
    row, state = get_version(db, version_id), ensure_parser_state(db)
    state.active_version_id = row.id
    state.revision += 1
    db.flush()
    return state
