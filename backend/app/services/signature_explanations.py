"""Versioned reference knowledge, pinned before inference and never event truth.

Callers own transactions. Production activation is an internal promotion hook,
not exposed as an administrator shortcut. No network or model calls occur here.
"""
import hashlib
import hmac
import json
import uuid

from pydantic import ValidationError
from sqlalchemy import func, select

from ..models import SignatureExplanationConfiguration, SignatureExplanationVersion
from ..signature_explanation_schemas import (
    MAX_CATALOG_BYTES, MAX_ENTRIES, SignatureExplanationCreate, SignatureExplanationEntry,
)
from .prompt_policies import _sqlite_write_transaction

SNAPSHOT_FIELDS = {"schema_version", "version_id", "version_no", "content_hash", "entry_count",
                   "selection_origin", "scope", "match_key", "entries", "snapshot_hash"}
ORIGINS = {"legacy_default", "production", "test", "candidate", "retry", "baseline"}


class SignatureExplanationError(ValueError):
    def __init__(self, code, status_code=409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _key(waf_vendor, signature):
    return {"waf_vendor": waf_vendor.strip().casefold() if isinstance(waf_vendor, str) else "",
            "signature": signature.strip() if isinstance(signature, str) else None}


def _entries(values):
    if not isinstance(values, list) or len(values) > MAX_ENTRIES:
        raise SignatureExplanationError("signature_explanation_definition_invalid", 422)
    try:
        result = [SignatureExplanationEntry.model_validate(value).model_dump() for value in values]
        keys = [_json(_key(entry["waf_vendor"], entry["signature"])) for entry in result]
        if len(set(keys)) != len(keys):
            raise SignatureExplanationError("duplicate_signature_explanation", 422)
        if len(_json(result).encode("utf-8")) > MAX_CATALOG_BYTES:
            raise SignatureExplanationError("signature_explanation_catalog_size_limit", 413)
        # Match ordering has no semantic effect; canonicalize before hashing.
        return sorted(result, key=lambda entry: (entry["waf_vendor"].casefold(), entry["signature"]))
    except (ValidationError, TypeError, UnicodeError):
        raise SignatureExplanationError("signature_explanation_definition_invalid", 422) from None


def _seal(document):
    return {**document, "snapshot_hash": _digest(document)}


def default_snapshot(selection_origin="legacy_default"):
    return _seal({"schema_version": 1, "version_id": None, "version_no": None,
        "content_hash": _digest([]), "entry_count": 0, "selection_origin": selection_origin,
        "scope": "catalog", "match_key": None, "entries": []})


empty_snapshot = default_snapshot


def validate_snapshot(snapshot):
    """Strictly validate encrypted historical bindings; never fall back to latest."""
    try:
        if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
            raise ValueError()
        if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
            raise ValueError()
        if snapshot["selection_origin"] not in ORIGINS or snapshot["scope"] not in {"catalog", "event"}:
            raise ValueError()
        identifier, number = snapshot["version_id"], snapshot["version_no"]
        if identifier is not None and (not isinstance(identifier, str) or not 1 <= len(identifier) <= 36):
            raise ValueError()
        if identifier is None and number is not None:
            raise ValueError()
        if identifier is not None and (type(number) is not int or number < 1):
            raise ValueError()
        if type(snapshot["entry_count"]) is not int or not 0 <= snapshot["entry_count"] <= MAX_ENTRIES:
            raise ValueError()
        entries = _entries(snapshot["entries"])
        if entries != snapshot["entries"]:
            raise ValueError()
        hash_value = snapshot["content_hash"]
        if (not isinstance(hash_value, str) or len(hash_value) != 64
                or any(character not in "0123456789abcdef" for character in hash_value)):
            raise ValueError()
        if snapshot["scope"] == "catalog":
            if (snapshot["match_key"] is not None or len(entries) != snapshot["entry_count"]
                    or not hmac.compare_digest(hash_value, _digest(entries))):
                raise ValueError()
        else:
            key = snapshot["match_key"]
            if (not isinstance(key, dict) or set(key) != {"waf_vendor", "signature"}
                    or not isinstance(key["waf_vendor"], str)
                    or (key["signature"] is not None and not isinstance(key["signature"], str))
                    or key != _key(key["waf_vendor"], key["signature"]) or len(entries) > min(1, snapshot["entry_count"])
                    or any(_key(entry["waf_vendor"], entry["signature"]) != key for entry in entries)):
                raise ValueError()
        if identifier is None and (snapshot["entry_count"] != 0 or entries or hash_value != _digest([])):
            raise ValueError()
        document = {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
        if not isinstance(snapshot["snapshot_hash"], str) or not hmac.compare_digest(snapshot["snapshot_hash"], _digest(document)):
            raise ValueError()
        return snapshot
    except (SignatureExplanationError, ValueError, TypeError, KeyError, UnicodeError):
        raise SignatureExplanationError("signature_explanation_snapshot_invalid") from None


def ensure_state(db):
    _sqlite_write_transaction(db)
    state = db.get(SignatureExplanationConfiguration, 1, populate_existing=True)
    if state is None:
        state = SignatureExplanationConfiguration(id=1, active_version_id=None, revision=0)
        db.add(state)
        db.flush()
    return state


def get_version(db, version_id):
    row = db.get(SignatureExplanationVersion, version_id)
    if row is None:
        raise SignatureExplanationError("signature_explanation_version_not_found", 404)
    return row


def _definition(row, crypto):
    try:
        entries = _entries(json.loads(crypto.decrypt_text(row.definition_ciphertext)))
        if len(entries) != row.entry_count or not hmac.compare_digest(_digest(entries), row.fingerprint):
            raise ValueError()
        return entries
    except (ValueError, TypeError, UnicodeError):
        raise SignatureExplanationError("signature_explanation_definition_invalid") from None


def create_version(db, crypto, *, name, change_reason, entries, parent_id=None, actor="admin"):
    try:
        payload = SignatureExplanationCreate.model_validate({"name": name, "change_reason": change_reason,
            "entries": entries, "parent_id": parent_id})
    except ValidationError:
        raise SignatureExplanationError("signature_explanation_definition_invalid", 422) from None
    ensure_state(db)
    if parent_id is not None:
        _definition(get_version(db, parent_id), crypto)
    clean = _entries([entry.model_dump() for entry in payload.entries])
    version_no = (db.scalar(select(func.max(SignatureExplanationVersion.version_no))) or 0) + 1
    row = SignatureExplanationVersion(id=str(uuid.uuid4()), version_no=version_no, name=payload.name,
        change_reason=payload.change_reason, parent_id=parent_id, entry_count=len(clean), fingerprint=_digest(clean),
        definition_ciphertext=crypto.encrypt_text(_json(clean)), encryption_key_version=crypto.key_version,
        created_by=actor)
    db.add(row)
    db.flush()
    return row


def version_summary(row):
    return {key: getattr(row, key) for key in ("id", "version_no", "name", "change_reason", "parent_id",
        "fingerprint", "entry_count", "created_by", "created_at")} | {"content_hash": row.fingerprint}


def version_detail(row, crypto):
    return {**version_summary(row), "entries": _definition(row, crypto)}


def snapshot_for_version(db, crypto, version_id=None, selection_origin="candidate"):
    if version_id is None:
        return validate_snapshot(default_snapshot(selection_origin))
    row = get_version(db, version_id)
    snapshot = {"schema_version": 1, "version_id": row.id, "version_no": row.version_no,
        "content_hash": row.fingerprint, "entry_count": row.entry_count, "selection_origin": selection_origin,
        "scope": "catalog", "match_key": None, "entries": _definition(row, crypto)}
    return validate_snapshot(_seal(snapshot))


snapshot_catalog = snapshot_for_version


def load_snapshot(crypto, ciphertext):
    try:
        return validate_snapshot(json.loads(crypto.decrypt_text(ciphertext)))
    except (TypeError, ValueError, UnicodeError):
        raise SignatureExplanationError("signature_explanation_snapshot_invalid") from None


def read_snapshot(crypto, target, allow_legacy=True):
    ciphertext = getattr(target, "signature_explanation_snapshot_ciphertext", None)
    identifier = getattr(target, "signature_explanation_version_id", None)
    if ciphertext is None:
        if identifier is not None or not allow_legacy:
            raise SignatureExplanationError("signature_explanation_snapshot_missing")
        return default_snapshot()
    snapshot = load_snapshot(crypto, ciphertext)
    if snapshot["version_id"] != identifier:
        raise SignatureExplanationError("signature_explanation_snapshot_invalid")
    if snapshot["scope"] == "event" and hasattr(target, "waf_vendor"):
        if snapshot["match_key"] != _key(target.waf_vendor, getattr(target, "signature", None)):
            raise SignatureExplanationError("signature_explanation_snapshot_invalid")
    return snapshot


def match_snapshot(snapshot, waf_vendor, signature):
    value = validate_snapshot(snapshot)
    match_key = _key(waf_vendor, signature)
    if value["scope"] == "event" and value["match_key"] != match_key:
        raise SignatureExplanationError("signature_explanation_snapshot_match_invalid")
    for entry in value["entries"]:
        if _key(entry["waf_vendor"], entry["signature"]) == match_key:
            return dict(entry)
    return None


lookup = match_snapshot


def event_snapshot(snapshot, waf_vendor, signature):
    value = validate_snapshot(snapshot)
    entry = match_snapshot(value, waf_vendor, signature)
    document = {key: item for key, item in value.items() if key != "snapshot_hash"}
    document.update(scope="event", match_key=_key(waf_vendor, signature), entries=[entry] if entry else [])
    return validate_snapshot(_seal(document))


def pin_snapshot(db, crypto, target, version_id=None, selection_origin="production", *, waf_vendor=None, signature=None):
    if (getattr(target, "signature_explanation_snapshot_ciphertext", None) is not None
            or getattr(target, "signature_explanation_version_id", None) is not None):
        return read_snapshot(crypto, target, allow_legacy=False)
    snapshot = snapshot_for_version(db, crypto, version_id, selection_origin)
    if hasattr(target, "waf_vendor") or waf_vendor is not None:
        snapshot = event_snapshot(snapshot, waf_vendor if waf_vendor is not None else target.waf_vendor,
                                  signature if signature is not None else getattr(target, "signature", None))
    target.signature_explanation_version_id = snapshot["version_id"]
    target.signature_explanation_snapshot_ciphertext = crypto.encrypt_text(_json(snapshot))
    return snapshot


def copy_snapshot(source, target, *, crypto=None):
    ciphertext = getattr(source, "signature_explanation_snapshot_ciphertext", None)
    identifier = getattr(source, "signature_explanation_version_id", None)
    if ciphertext is None and identifier is not None:
        raise SignatureExplanationError("signature_explanation_snapshot_missing")
    if ciphertext is not None and crypto is not None:
        snapshot = read_snapshot(crypto, source, allow_legacy=False)
        if hasattr(target, "waf_vendor"):
            snapshot = event_snapshot(snapshot, target.waf_vendor, getattr(target, "signature", None))
            ciphertext = crypto.encrypt_text(_json(snapshot))
    elif ciphertext is not None and not hasattr(source, "waf_vendor") and hasattr(target, "waf_vendor"):
        raise SignatureExplanationError("signature_explanation_copy_requires_crypto")
    target.signature_explanation_version_id = identifier
    target.signature_explanation_snapshot_ciphertext = ciphertext


def metadata(snapshot):
    value = validate_snapshot(snapshot)
    return {key: value[key] for key in ("version_id", "version_no", "content_hash", "entry_count")}


def activate_version(db, crypto, version_id):
    """Internal hook; caller must first enforce official evaluation and review."""
    if version_id is not None:
        _definition(get_version(db, version_id), crypto)
    state = ensure_state(db)
    state.active_version_id = version_id
    state.revision += 1
    db.flush()
    return state
