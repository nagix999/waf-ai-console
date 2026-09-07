"""Immutable input contract versions. Callers own transactions and payloads."""
import hashlib
import hmac
import json
import math
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..input_schema_schemas import (
    FieldDefinition, InputSchemaCreate, InputSchemaDetail, InputSchemaSummary,
    MAX_ARRAY_ITEMS, MAX_DEPTH, MAX_DEFINITION_BYTES, MAX_ENUM_ITEMS, MAX_FIELDS,
    MAX_STRING_LENGTH, MAX_VALIDATION_ISSUES, TypeDefinition, check_definition_bounds,
)
from ..models import AccessAudit, InputSchemaActivation, InputSchemaState, InputSchemaVersion
from .crypto import CryptoService
from .input_field_policy import SERVER_CONTROL_FIELDS
from .label_fields import LABEL_FIELDS
from .prompt_policies import _sqlite_write_transaction

DEFAULT_VERSION_ID = "00000000-0000-4000-8000-000000000002"
SNAPSHOT_VERSION = "input-schema-snapshot-v1"
CORE_FIELDS = frozenset({"event_id", "company_name", "src_ip", "dest_ip", "payload", "waf_vendor", "waf_action"})


class InputSchemaError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


def default_definition() -> list[FieldDefinition]:
    return [
        FieldDefinition(name="event_id", description="수집 시스템 내 이벤트 식별자. 중복 접수와 답안 연결에 사용합니다.", type="string", required=True, min_length=1, max_length=255),
        FieldDefinition(name="company_name", description="이벤트가 발생한 회사 또는 조직 이름입니다.", type="string", required=True, min_length=1, max_length=255),
        FieldDefinition(name="src_ip", description="WAF가 기록한 요청 출발지 IP 문자열입니다. 실제 공격 주체를 보장하지 않습니다.", type="string", required=True, min_length=1, max_length=64),
        FieldDefinition(name="dest_ip", description="WAF가 기록한 요청 목적지 IP 문자열입니다.", type="string", required=True, min_length=1, max_length=64),
        FieldDefinition(name="src_port", description="요청 출발지 포트입니다.", type="integer", nullable=True, minimum=0, maximum=65535),
        FieldDefinition(name="dest_port", description="요청 목적지 포트입니다.", type="integer", nullable=True, minimum=0, maximum=65535),
        FieldDefinition(name="payload", description="분석할 HTTP 원문입니다. 내용과 Cookie를 마스킹하지 않습니다.", type="string", required=True, min_length=1),
        FieldDefinition(name="signature", description="WAF 탐지 시그니처입니다. 정답을 의미하지 않습니다.", type="string", nullable=True),
        FieldDefinition(name="event_name", description="WAF 탐지 이벤트 이름입니다.", type="string", nullable=True, max_length=500),
        FieldDefinition(name="waf_vendor", description="WAF 제품 또는 벤더 이름입니다.", type="string", required=True, min_length=1, max_length=120),
        FieldDefinition(name="waf_action", description="WAF 관측 조치: D는 차단, A는 허용입니다. 정오탐 정답이 아닙니다.", type="string", required=True, enum=["D", "A"]),
    ]


def schema_bounds() -> dict:
    return {
        "max_fields": MAX_FIELDS, "max_depth": MAX_DEPTH, "max_definition_bytes": MAX_DEFINITION_BYTES,
        "max_enum_items": MAX_ENUM_ITEMS, "max_string_length": MAX_STRING_LENGTH,
        "max_array_items": MAX_ARRAY_ITEMS, "core_required_fields": sorted(CORE_FIELDS),
        "reserved_fields": sorted(SERVER_CONTROL_FIELDS | LABEL_FIELDS),
        "unknown_fields": "preserve", "field_metadata_usage": "history_only",
    }


def definition_document(fields: list[FieldDefinition]) -> list[dict]:
    return [field.model_dump(exclude_none=True) for field in fields]


def _canonical(fields: list[FieldDefinition]) -> str:
    return json.dumps(definition_document(fields), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(fields: list[FieldDefinition]) -> str:
    return hashlib.sha256(_canonical(fields).encode("utf-8")).hexdigest()


def validate_definition(fields: list[FieldDefinition]) -> None:
    try:
        check_definition_bounds(fields)
        by_name = {field.name: field for field in fields}
        reserved = {name.lower() for name in SERVER_CONTROL_FIELDS | LABEL_FIELDS}
        pending = list(fields)
        while pending:
            field = pending.pop()
            if isinstance(field, FieldDefinition) and field.name.lower() in reserved:
                raise ValueError("input_schema_reserved_field")
            pending.extend(field.properties or [])
            if field.items is not None:
                pending.append(field.items)
        for baseline in default_definition():
            field = by_name.get(baseline.name)
            if field is None:
                raise ValueError("input_schema_builtin_field_required")
            if field.type != baseline.type or (baseline.required and not field.required) or (not baseline.nullable and field.nullable):
                raise ValueError("input_schema_builtin_contract_protected")
            for low in ("min_length", "minimum"):
                limit, chosen = getattr(baseline, low), getattr(field, low)
                if limit is not None and (chosen is None or chosen < limit):
                    raise ValueError("input_schema_builtin_constraint_protected")
            for high in ("max_length", "maximum"):
                limit, chosen = getattr(baseline, high), getattr(field, high)
                if limit is not None and (chosen is None or chosen > limit):
                    raise ValueError("input_schema_builtin_constraint_protected")
            if baseline.enum is not None and (field.enum is None or any(value not in baseline.enum for value in field.enum)):
                raise ValueError("input_schema_builtin_constraint_protected")
    except ValueError as exc:
        code = str(exc) if str(exc).startswith("input_schema_") else "input_schema_definition_invalid"
        raise InputSchemaError(code, 422) from None


def get_schema_state(db: Session, crypto: CryptoService) -> InputSchemaState:
    state = db.get(InputSchemaState, 1)
    if state is not None:
        return state
    _sqlite_write_transaction(db)
    state = db.get(InputSchemaState, 1, populate_existing=True)
    if state is not None:
        return state
    try:
        with db.begin_nested():
            fields = default_definition()
            version = InputSchemaVersion(
                id=DEFAULT_VERSION_ID, version_number=1, name="기본 입력 스키마",
                change_note="기존 11개 입력 필드와 호환되는 기본 정의입니다.",
                content_hash=content_hash(fields), definition_ciphertext=crypto.encrypt_text(_canonical(fields)),
                encryption_key_version=crypto.key_version, created_by="system")
            db.add(version)
            db.flush()
            state = InputSchemaState(id=1, active_version_id=version.id, revision=1)
            db.add(state)
            db.add(InputSchemaActivation(active_version_id=version.id, revision=1, actor_id="system"))
            db.add(AccessAudit(actor_kind="system", actor_id="input_schema_bootstrap", action="initialize_input_schema", resource_type="input_schema_version", resource_id=version.id))
            db.flush()
        return state
    except IntegrityError:
        state = db.get(InputSchemaState, 1, populate_existing=True)
        if state is None:
            raise InputSchemaError("input_schema_initialization_conflict") from None
        return state


def get_schema_version(db: Session, version_id: str) -> InputSchemaVersion:
    version = db.get(InputSchemaVersion, version_id)
    if version is None:
        raise InputSchemaError("input_schema_version_not_found", 404)
    return version


def load_definition(version: InputSchemaVersion, crypto: CryptoService) -> list[FieldDefinition]:
    try:
        raw = crypto.decrypt_text(version.definition_ciphertext)
        if len(raw.encode("utf-8")) > MAX_DEFINITION_BYTES:
            raise ValueError()
        fields = [FieldDefinition.model_validate(field) for field in json.loads(raw)]
        validate_definition(fields)
        if not isinstance(version.content_hash, str) or not version.content_hash.isascii() or not hmac.compare_digest(content_hash(fields), version.content_hash):
            raise ValueError()
        return fields
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise InputSchemaError("input_schema_content_unavailable", 503) from None


def get_active_schema(db: Session, crypto: CryptoService) -> InputSchemaVersion:
    state = get_schema_state(db, crypto)
    version = get_schema_version(db, state.active_version_id)
    load_definition(version, crypto)
    return version


def get_default_schema(db: Session, crypto: CryptoService) -> InputSchemaVersion:
    get_schema_state(db, crypto)
    return get_schema_version(db, DEFAULT_VERSION_ID)


def create_schema_version(db: Session, crypto: CryptoService, payload: InputSchemaCreate, created_by: str) -> InputSchemaVersion:
    validate_definition(payload.fields)
    _sqlite_write_transaction(db)
    get_schema_state(db, crypto)
    if payload.parent_id is not None:
        get_schema_version(db, payload.parent_id)
    version = InputSchemaVersion(
        version_number=int(db.scalar(select(func.max(InputSchemaVersion.version_number))) or 0) + 1,
        name=payload.name, change_note=payload.change_note, parent_id=payload.parent_id,
        content_hash=content_hash(payload.fields), definition_ciphertext=crypto.encrypt_text(_canonical(payload.fields)),
        encryption_key_version=crypto.key_version, created_by=created_by)
    db.add(version)
    db.flush()
    return version


def activate_schema_version(db: Session, crypto: CryptoService, version_id: str, expected_revision: int, actor_id: str) -> InputSchemaState:
    _sqlite_write_transaction(db)
    state = get_schema_state(db, crypto)
    previous = state.active_version_id
    load_definition(get_schema_version(db, version_id), crypto)
    changed = db.execute(update(InputSchemaState).where(InputSchemaState.id == 1, InputSchemaState.revision == expected_revision)
                         .values(active_version_id=version_id, revision=expected_revision + 1).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise InputSchemaError("input_schema_changed_concurrently")
    db.add(InputSchemaActivation(previous_version_id=previous, active_version_id=version_id, revision=expected_revision + 1, actor_id=actor_id))
    db.flush()
    return db.get(InputSchemaState, 1, populate_existing=True)


def to_schema_summary(version: InputSchemaVersion) -> InputSchemaSummary:
    return InputSchemaSummary(**{name: getattr(version, name) for name in ("id", "version_number", "name", "change_note", "parent_id", "content_hash", "created_by", "created_at")})


def to_schema_detail(version: InputSchemaVersion, crypto: CryptoService) -> InputSchemaDetail:
    return InputSchemaDetail(**to_schema_summary(version).model_dump(), fields=load_definition(version, crypto))


def validate_event(definition: list[FieldDefinition] | list[dict], document: dict) -> list[dict[str, str]]:
    """Validate without reflecting input values. Built-ins retain legacy coercion."""
    from ..schemas import AnalysisInput

    fields = [item if isinstance(item, FieldDefinition) else FieldDefinition.model_validate(item) for item in definition]
    validate_definition(fields)
    issues: list[dict[str, str]] = []
    def issue(path: str, kind: str, message: str):
        if len(issues) < MAX_VALIDATION_ISSUES:
            issues.append({"field": path, "type": kind, "message": message})
    if not isinstance(document, dict):
        issue("body", "object_required", "이벤트는 JSON 객체여야 합니다.")
        return issues
    try:
        normalized = AnalysisInput.model_validate(document).model_dump()
    except ValidationError as exc:
        builtin = {item.name for item in default_definition()}
        for error in exc.errors(include_input=False, include_context=False, include_url=False)[:MAX_VALIDATION_ISSUES]:
            loc = error.get("loc", ())
            name = loc[0] if loc and loc[0] in builtin else "body"
            issue(name, str(error["type"]), "기본 입력 계약에 맞지 않습니다. 필드의 필수 여부·타입·범위를 확인하세요.")
        return issues
    builtin = {item.name for item in default_definition()}
    visited = 0
    def check(value: Any, spec: TypeDefinition, path: str):
        nonlocal visited
        if len(issues) >= MAX_VALIDATION_ISSUES:
            return
        visited += 1
        if visited > 10000:
            if not any(item["type"] == "validation_complexity_limit" for item in issues):
                issue("body", "validation_complexity_limit", "입력 검증의 처리 한도를 초과했습니다.")
            return
        if value is None:
            if not spec.nullable:
                issue(path, "null_not_allowed", "null 값을 허용하지 않는 필드입니다.")
            return
        matches = {"string": isinstance(value, str), "integer": isinstance(value, int) and not isinstance(value, bool),
                   "number": isinstance(value, (int, float)) and not isinstance(value, bool), "boolean": isinstance(value, bool),
                   "array": isinstance(value, list), "object": isinstance(value, dict)}[spec.type]
        if not matches:
            issue(path, "type_mismatch", "설정된 필드 타입과 일치하지 않습니다.")
            return
        if spec.enum is not None and not any(
            value == item and (type(value) is type(item) or (spec.type == "number" and type(item) in {int, float}))
            for item in spec.enum
        ):
            issue(path, "enum_mismatch", "설정된 허용값 목록에 포함되지 않습니다.")
        if spec.type == "string":
            if spec.min_length is not None and len(value) < spec.min_length:
                issue(path, "string_too_short", "설정된 최소 문자열 길이보다 짧습니다.")
            if spec.max_length is not None and len(value) > spec.max_length:
                issue(path, "string_too_long", "설정된 최대 문자열 길이를 초과했습니다.")
        if spec.type in {"integer", "number"}:
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                issue(path, "number_not_finite", "유한한 숫자만 허용합니다.")
            elif (spec.minimum is not None and value < spec.minimum) or (spec.maximum is not None and value > spec.maximum):
                issue(path, "number_out_of_range", "설정된 숫자 범위를 벗어났습니다.")
        if spec.type == "array":
            if len(value) > (spec.max_items if spec.max_items is not None else MAX_ARRAY_ITEMS):
                issue(path, "array_too_long", "설정 또는 시스템의 최대 배열 항목 수를 초과했습니다.")
                return
            if spec.min_items is not None and len(value) < spec.min_items:
                issue(path, "array_too_short", "설정된 최소 배열 항목 수보다 적습니다.")
            for index, item in enumerate(value):
                check(item, spec.items, f"{path}.{index}")
                if visited > 10000 or len(issues) >= MAX_VALIDATION_ISSUES:
                    break
        if spec.type == "object":
            for child in spec.properties or []:
                if child.name not in value:
                    if child.required:
                        issue(f"{path}.{child.name}", "missing", "필수 필드가 누락되었습니다.")
                else:
                    check(value[child.name], child, f"{path}.{child.name}")
    for field in fields:
        if field.name not in document:
            if field.required:
                issue(field.name, "missing", "필수 필드가 누락되었습니다.")
            continue
        check(normalized[field.name] if field.name in builtin else document[field.name], field, field.name)
    return issues


def pin_schema(db: Session, crypto: CryptoService, target, version: InputSchemaVersion | None = None, selection_origin: str = "ingest") -> dict:
    if target.input_schema_snapshot_ciphertext:
        return read_schema_snapshot(crypto, target)
    if target.input_schema_version_id is not None:
        raise InputSchemaError("input_schema_snapshot_invalid", 503)
    if selection_origin not in {"ingest", "legacy_default", "test_run", "model_validation"}:
        raise InputSchemaError("input_schema_snapshot_origin_invalid", 422)
    version = version or (get_default_schema(db, crypto) if selection_origin == "legacy_default" else get_active_schema(db, crypto))
    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION, "version_id": version.id, "version_number": version.version_number,
        "content_hash": version.content_hash, "selection_origin": selection_origin,
        "field_metadata_usage": "history_only", "fields": definition_document(load_definition(version, crypto)),
    }
    target.input_schema_version_id = version.id
    target.input_schema_snapshot_ciphertext = crypto.encrypt_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
    return snapshot


def read_schema_snapshot(crypto: CryptoService, target) -> dict:
    try:
        raw = crypto.decrypt_text(target.input_schema_snapshot_ciphertext)
        if len(raw.encode("utf-8")) > MAX_DEFINITION_BYTES + 2048:
            raise ValueError()
        snapshot = json.loads(raw)
        if not isinstance(snapshot, dict) or set(snapshot) != {"snapshot_version", "version_id", "version_number", "content_hash", "selection_origin", "field_metadata_usage", "fields"}:
            raise ValueError()
        fields = [FieldDefinition.model_validate(item) for item in snapshot["fields"]]
        validate_definition(fields)
        if (snapshot["snapshot_version"] != SNAPSHOT_VERSION or snapshot["version_id"] != target.input_schema_version_id
                or type(snapshot["version_number"]) is not int or snapshot["version_number"] < 1
                or snapshot["selection_origin"] not in {"ingest", "legacy_default", "test_run", "model_validation"}
                or snapshot["field_metadata_usage"] != "history_only" or content_hash(fields) != snapshot["content_hash"]):
            raise ValueError()
        return snapshot
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        raise InputSchemaError("input_schema_snapshot_invalid", 503) from None


def schema_metadata(snapshot: dict) -> dict:
    return {**{name: snapshot[name] for name in ("version_id", "version_number", "content_hash", "selection_origin", "field_metadata_usage")},
            "field_count": len(snapshot["fields"]), "field_metadata_sent_to_model": False}


def to_json_schema(definition: list[FieldDefinition] | list[dict]) -> dict:
    """OpenAPI 3.1 representation of the same bounded validator rules."""
    fields = [item if isinstance(item, FieldDefinition) else FieldDefinition.model_validate(item) for item in definition]
    validate_definition(fields)
    def convert(spec: TypeDefinition) -> dict:
        result: dict = {"type": [spec.type, "null"] if spec.nullable else spec.type}
        if isinstance(spec, FieldDefinition) and spec.description:
            result["description"] = spec.description
        for local, wire in (("min_length", "minLength"), ("max_length", "maxLength"), ("minimum", "minimum"),
                            ("maximum", "maximum"), ("min_items", "minItems"), ("max_items", "maxItems"), ("enum", "enum")):
            value = getattr(spec, local)
            if value is not None:
                result[wire] = value
        if spec.enum is not None and spec.nullable and None not in spec.enum:
            # nullable is independent of scalar allow-lists in the validator.
            result["enum"] = [*spec.enum, None]
        if spec.type == "array":
            result["items"] = convert(spec.items)
            result.setdefault("maxItems", MAX_ARRAY_ITEMS)
        if spec.type == "object":
            result.update(object_definition(spec.properties or []))
            result["type"] = ["object", "null"] if spec.nullable else "object"
        return result
    def object_definition(properties: list[FieldDefinition]) -> dict:
        result = {"type": "object", "properties": {item.name: convert(item) for item in properties}, "additionalProperties": True}
        required = [item.name for item in properties if item.required]
        if required:
            result["required"] = required
        return result
    result = object_definition(fields)
    # The application rejects these exact top-level keys even when their value
    # is null. Unknown nested vendor values keep the existing preservation rule.
    result["propertyNames"] = {"not": {"enum": sorted(SERVER_CONTROL_FIELDS | LABEL_FIELDS)}}
    result["title"] = "ActiveAnalysisInput"
    result["description"] = "Versioned admission schema; unknown vendor fields are preserved. Field definitions are historical metadata, not LLM instructions. Built-in waf_action is case-normalized and built-in ports preserve existing numeric coercion."
    return result
