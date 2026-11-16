"""Encrypted, immutable rule bundles behind the existing parser snapshot API.

Only ciphertext and content-free identities enter JSON snapshots. A decrypted
document is attached to a local runtime object, never persisted or serialized.
Legacy snapshots and their engine hashes are deliberately untouched.

This module is the versioned vendor-http-v2 integration artifact. After release,
add a new engine in a separate module rather than changing this implementation.
The shared dispatch module may gain new engines without invalidating this one.
"""
from copy import deepcopy
import hashlib
import hmac
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


RULE_ENGINE_VERSION = "vendor-http-v2"
DOCUMENT_VERSION = "encrypted-parser-bundle-v1"


class RuleSnapshotOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    definition_ciphertext: StrictStr = Field(min_length=80, max_length=524288)
    definition_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    engine_artifact_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    rule_count: StrictInt = Field(ge=1, le=20)


class RuntimeRuleSnapshot(dict):
    """Private per-call attachment; json.dumps only sees encrypted values."""
    def __init__(self, value, document):
        super().__init__(value)
        self.document = document


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def rule_engine_artifact_hash():
    root = Path(__file__).parent
    files = (root / "parser_rule_versions.py", root / "parser_rule_engine.py",
             root.parent / "parser_rule_schemas.py", root / "http_parser.py",
             root / "parser_engine_v1.py")
    # The mutable shared dispatcher is not part of a versioned engine artifact.
    # Its v2 branch stays compatible; adding a v3 branch must not break v2 jobs.
    try:
        dependency = version("google-re2")
    except PackageNotFoundError:
        dependency = "unavailable"
    return hashlib.sha256(b"\0".join([*(path.read_bytes() for path in files), dependency.encode()])).hexdigest()


def validate_rule_options(options):
    from .parser_versions import ParserVersionError
    try:
        clean = RuleSnapshotOptions.model_validate(options).model_dump()
        if not hmac.compare_digest(clean["engine_artifact_hash"], rule_engine_artifact_hash()):
            raise ValueError()
        return clean
    except (TypeError, ValueError):
        raise ParserVersionError("parser_definition_invalid") from None


def decrypt_rule_document(options, crypto):
    from .parser_versions import ParserVersionError, definition_hash
    from .parser_rule_engine import validate_bundle
    if crypto is None:
        raise ParserVersionError("parser_rule_decryption_required")
    options = validate_rule_options(options)
    try:
        document = json.loads(crypto.decrypt_text(options["definition_ciphertext"]))
        if (not isinstance(document, dict) or set(document) != {"schema_version", "bundle", "fallback"}
                or document["schema_version"] != DOCUMENT_VERSION
                or not hmac.compare_digest(digest(document), options["definition_hash"])):
            raise ValueError()
        document["bundle"] = validate_bundle(document["bundle"])
        if len(document["bundle"]["rules"]) != options["rule_count"]:
            raise ValueError()
        fallback = document["fallback"]
        if (not isinstance(fallback, dict) or set(fallback) != {"engine_version", "options", "content_hash"}
                or fallback["engine_version"] not in {"generic-http-v3", "http-capture-v1"}
                or not hmac.compare_digest(fallback["content_hash"], definition_hash(fallback["engine_version"], fallback["options"]))):
            raise ValueError()
        return document
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ParserVersionError("parser_rule_snapshot_invalid") from None


def read_rule_bundle_definition(row, crypto):
    if row.engine_version != RULE_ENGINE_VERSION:
        return None
    from .parser_versions import ParserVersionError, definition_hash
    if not hmac.compare_digest(definition_hash(row.engine_version, row.options_json), row.content_hash):
        raise ParserVersionError("parser_definition_invalid")
    return decrypt_rule_document(row.options_json, crypto)["bundle"]


def create_rule_bundle_version(db, crypto, definition, *, name, parent_id=None, description=None, actor="admin"):
    from .parser_rule_engine import validate_bundle
    from .parser_versions import (BASELINE_ID, ParserVersionError, create_version, definition_hash,
                                  ensure_parser_state, get_version)
    state = ensure_parser_state(db)
    parent = get_version(db, parent_id or state.active_version_id or BASELINE_ID)
    incoming = validate_bundle(definition)
    if parent.engine_version == RULE_ENGINE_VERSION:
        previous = decrypt_rule_document(parent.options_json, crypto)
        # New samples do not authorize deleting old formats or another vendor.
        rules = {rule["rule_id"]: rule for rule in previous["bundle"]["rules"]}
        for rule in incoming["rules"]:
            old = rules.get(rule["rule_id"])
            if old and (old["waf_vendor"].strip().casefold() != rule["waf_vendor"].strip().casefold()
                        or rule["rule_version"] <= old["rule_version"] and rule != old):
                raise ParserVersionError("parser_rule_revision_conflict")
            rules[rule["rule_id"]] = rule
        incoming = validate_bundle({**incoming, "rules": list(rules.values())})
        fallback = previous["fallback"]
    else:
        fallback = {"engine_version": parent.engine_version, "options": deepcopy(parent.options_json),
                    "content_hash": definition_hash(parent.engine_version, parent.options_json)}
    document = {"schema_version": DOCUMENT_VERSION, "bundle": incoming, "fallback": fallback}
    options = {"definition_ciphertext": crypto.encrypt_text(canonical(document)),
               "definition_hash": digest(document), "engine_artifact_hash": rule_engine_artifact_hash(),
               "rule_count": len(incoming["rules"])}
    return create_version(db, crypto, name=name, engine_version=RULE_ENGINE_VERSION,
                          options=options, parent_id=parent.id, description=description, actor=actor)


def bind_runtime(snapshot, crypto):
    return RuntimeRuleSnapshot(snapshot, decrypt_rule_document(snapshot["options"], crypto))


def apply_rule_snapshot(raw, snapshot, *, waf_vendor=None):
    from .parser_versions import ParserVersionError
    from .parser_rule_engine import parse_rule_bundle
    from .parser_engine import parse_capture_payload
    document = getattr(snapshot, "document", None)
    if document is None or not hmac.compare_digest(digest(document), snapshot["options"]["definition_hash"]):
        raise ParserVersionError("parser_rule_decryption_required")
    parsed = parse_rule_bundle(raw, document["bundle"], waf_vendor=waf_vendor)
    selection = parsed.get("capture_selection", {})
    if selection.get("status") == "generic_fallback":
        fallback = document["fallback"]
        # Preserve the installation's previous vendor capture adapters too.
        result = parse_capture_payload(raw, fallback["engine_version"], fallback["options"], waf_vendor=waf_vendor)
        previous = result.get("capture_selection")
        result["capture_selection"] = {**selection, "fallback_engine": fallback["engine_version"]}
        if previous:
            result["capture_selection"]["fallback_selection"] = previous
        for warning in parsed.get("warnings", []):
            if warning.startswith("rule_") or warning.startswith("parser_rule_"):
                result["warnings"] = list(dict.fromkeys([*result["warnings"], warning]))
        return result
    return parsed
