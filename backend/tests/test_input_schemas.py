"""Synthetic, offline checks for versioned input contracts and history isolation."""
import copy
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.input_schema_schemas import FieldDefinition, InputSchemaCreate
from app.models import AccessAudit, InputSchemaActivation, InputSchemaVersion
from app.services.input_schemas import (
    DEFAULT_VERSION_ID, InputSchemaError, activate_schema_version, content_hash, create_schema_version,
    default_definition, definition_document, get_active_schema, get_schema_state, load_definition,
    pin_schema, read_schema_snapshot, schema_metadata, to_json_schema, validate_definition, validate_event,
)

PREFIX = "/api/v1/admin/input-schemas"


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def payload(**kwargs):
    return {"name": "Synthetic schema", "change_note": "Synthetic contract change", "fields": definition_document(default_definition()), **kwargs}


def fields_with(name, **changes):
    data = definition_document(default_definition())
    next(item for item in data if item["name"] == name).update(changes)
    return data


def test_default_preserves_legacy_acceptance_and_unknown_values(event_payload):
    fields = default_definition()
    assert len(fields) == 11
    assert validate_event(fields, event_payload) == []
    minimal = {key: value for key, value in event_payload.items() if key not in {"src_port", "dest_port", "signature", "event_name"}}
    minimal.update(waf_action="a", extra_vendor={"anything": [1, True, None]})
    assert validate_event(fields, minimal) == []
    minimal["dest_port"] = "443"
    assert validate_event(fields, minimal) == []
    assert minimal["dest_port"] == "443"  # original input is never modified


@pytest.mark.parametrize("name,change", [
    ("event_id", {"required": False}), ("payload", {"nullable": True}),
    ("src_ip", {"type": "integer", "min_length": None, "max_length": None}),
    ("event_id", {"max_length": 256}), ("payload", {"min_length": 0}),
    ("src_port", {"minimum": -1}), ("dest_port", {"maximum": None}),
    ("event_name", {"max_length": 501}), ("waf_action", {"enum": ["D", "A", "X"]}),
])
def test_builtin_constraints_cannot_be_weakened(name, change):
    with pytest.raises(InputSchemaError):
        validate_definition(InputSchemaCreate(**payload(fields=fields_with(name, **change))).fields)


@pytest.mark.parametrize("field", ["expected_verdict", "label", "input_schema_version_id", "source_system", "System_Prompt"])
def test_reserved_fields_cannot_be_defined_even_nested(field):
    for extra in ({"name": field, "type": "string"}, {"name": "vendor", "type": "object", "properties": [{"name": field, "type": "string"}]}):
        with pytest.raises(InputSchemaError, match="input_schema_reserved_field"):
            validate_definition(InputSchemaCreate(**payload(fields=payload()["fields"] + [extra])).fields)


@pytest.mark.parametrize("extra", [
    {"name": "bad-name", "type": "string"}, {"name": "constructor", "type": "string"},
    {"name": "array", "type": "array"}, {"name": "x", "type": "string", "pattern": "a+"},
    {"name": "x", "type": "string", "$ref": "https://example.invalid/schema"},
    {"name": "x", "type": "string", "minimum": 1}, {"name": "x", "type": "integer", "enum": [True]},
    {"name": "x", "type": "number", "minimum": float("nan")},
    {"name": "x", "type": "string", "min_length": 4, "max_length": 3},
    {"name": "x", "type": "string", "enum": ["a", "a"]},
    {"name": "x", "type": "array", "items": {"type": "string"}, "max_items": 1001},
])
def test_unsafe_or_inconsistent_definition_rejected(extra):
    with pytest.raises(ValidationError):
        InputSchemaCreate(**payload(fields=payload()["fields"] + [extra]))


def test_optional_presence_null_and_additional_types(event_payload):
    fields = fields_with("signature", required=True, nullable=False, min_length=2)
    fields += [{"name": "vendor_score", "type": "number", "required": True, "minimum": 0, "maximum": 1},
               {"name": "vendor", "type": "object", "properties": [{"name": "tags", "type": "array", "required": True,
                    "min_items": 1, "max_items": 2, "items": {"type": "string", "enum": ["one", "two"]}}]}]
    value = {**event_payload, "vendor_score": .5, "vendor": {"tags": ["one"], "unknown": "preserved"}}
    assert validate_event(fields, value) == []
    value["vendor"]["tags"] = ["invalid-sensitive-fixture"]
    value["vendor_score"] = True
    value["signature"] = None
    issues = validate_event(fields, value)
    assert {item["field"] for item in issues} == {"signature", "vendor_score", "vendor.tags.0"}
    assert "sensitive" not in json.dumps(issues)
    del value["signature"]
    assert any(item["field"] == "signature" and item["type"] == "missing" for item in validate_event(fields, value))


def test_complexity_and_duplicate_bounds():
    with pytest.raises(ValidationError):
        InputSchemaCreate(**payload(fields=payload()["fields"] + [{"name": "event_id", "type": "string"}]))
    child = {"name": "leaf", "type": "string"}
    for depth in range(5):
        child = {"name": f"level{depth}", "type": "object", "properties": [child]}
    with pytest.raises(ValidationError):
        InputSchemaCreate(**payload(fields=payload()["fields"] + [child]))


def test_admin_endpoints_deny_service_and_anonymous(client, service_headers):
    for method, path, data in [("GET", "", None), ("POST", "", payload()), ("GET", "/activation-history", None),
                               ("GET", "/unknown", None), ("POST", "/unknown/validate", {"event": {}}),
                               ("POST", "/unknown/activate", {"expected_revision": 1, "validation_token": "x"})]:
        assert client.request(method, PREFIX + path, json=data).status_code == 401
        assert client.request(method, PREFIX + path, json=data, headers=service_headers).status_code == 403


def test_immutable_versions_activation_and_reversion(client, event_payload):
    login(client)
    listing = client.get(PREFIX).json()
    assert listing["active_version_id"] == DEFAULT_VERSION_ID and listing["revision"] == 1
    fields = fields_with("event_id", description="Synthetic revised meaning", max_length=100)
    created = client.post(PREFIX, json=payload(parent_id=DEFAULT_VERSION_ID, fields=fields))
    assert created.status_code == 201, created.text
    version = created.json()
    assert version["version_number"] == 2
    assert client.get(PREFIX).json()["active_version_id"] == DEFAULT_VERSION_ID
    assert client.patch(PREFIX + "/" + version["id"], json=payload()).status_code == 405
    assert client.delete(PREFIX + "/" + version["id"]).status_code == 405
    validate = client.post(PREFIX + f"/{version['id']}/validate", json={"event": event_payload}).json()
    assert validate["valid"] and validate["expires_in_seconds"] == 300
    activation = {"expected_revision": 1, "validation_token": validate["validation_token"]}
    response = client.post(PREFIX + f"/{version['id']}/activate", json=activation)
    assert response.status_code == 200, response.text
    assert response.json() == {"active_version_id": version["id"], "revision": 2}
    assert client.post(PREFIX + f"/{version['id']}/activate", json=activation).status_code == 409
    # Reverting is a new activation, not deleting newer history.
    token = client.post(PREFIX + f"/{DEFAULT_VERSION_ID}/validate", json={"event": event_payload}).json()["validation_token"]
    assert client.post(PREFIX + f"/{DEFAULT_VERSION_ID}/activate", json={"expected_revision": 2, "validation_token": token}).json()["revision"] == 3
    assert len(client.get(PREFIX).json()["items"]) == 2
    history = client.get(PREFIX + "/activation-history").json()["items"]
    assert [item["revision"] for item in history] == [3, 2, 1]
    assert client.get(PREFIX + "/" + version["id"]).json() == version


def test_sample_never_saved_and_bad_sample_never_authorizes(client, event_payload):
    login(client)
    client.get(PREFIX)
    sample = {**event_payload, "payload": "synthetic-private-sample-unique", "event_id": "x" * 300}
    checked = client.post(PREFIX + f"/{DEFAULT_VERSION_ID}/validate", json={"event": sample})
    assert checked.status_code == 200
    assert checked.json()["valid"] is False and checked.json()["validation_token"] is None
    assert sample["payload"] not in checked.text
    with client.app.state.session_factory() as db:
        versions = db.scalars(select(InputSchemaVersion)).all()
        assert all(sample["payload"] not in json.dumps(to_plain(row)) for row in versions)
        audits = db.scalars(select(AccessAudit)).all()
        assert all(sample["payload"] not in json.dumps(to_plain(row)) for row in audits)


def to_plain(row):
    return {column.name: str(getattr(row, column.name)) for column in row.__table__.columns}


def test_validation_token_bound_to_version_session_and_expiration(client, event_payload, monkeypatch):
    login(client)
    client.get(PREFIX)
    checked = client.post(PREFIX + f"/{DEFAULT_VERSION_ID}/validate", json={"event": event_payload}).json()
    version2 = client.post(PREFIX, json=payload()).json()["id"]
    body = {"expected_revision": 1, "validation_token": checked["validation_token"]}
    assert client.post(PREFIX + f"/{version2}/activate", json=body).status_code == 422
    # Reuse the already-running app without entering/disposal of its lifespan.
    other = TestClient(client.app, headers={"Origin": "http://testserver"})
    login(other)
    assert other.post(PREFIX + f"/{DEFAULT_VERSION_ID}/activate", json=body).status_code == 422
    other.close()
    from itsdangerous.timed import TimestampSigner
    timestamp = TimestampSigner.get_timestamp
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: timestamp(self) + 301)
    assert client.post(PREFIX + f"/{DEFAULT_VERSION_ID}/activate", json=body).status_code == 422


def test_snapshot_pins_metadata_not_event_and_legacy_default(client):
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        first = SimpleNamespace(input_schema_version_id=None, input_schema_snapshot_ciphertext=None)
        snapshot = pin_schema(db, crypto, first)
        original_ciphertext = first.input_schema_snapshot_ciphertext
        assert snapshot["version_id"] == DEFAULT_VERSION_ID
        updated = create_schema_version(db, crypto, InputSchemaCreate(**payload(fields=fields_with("signature", description="Revised synthetic meaning"))), "synthetic")
        activate_schema_version(db, crypto, updated.id, 1, "synthetic")
        assert pin_schema(db, crypto, first) == snapshot
        assert first.input_schema_snapshot_ciphertext == original_ciphertext
        legacy = SimpleNamespace(input_schema_version_id=None, input_schema_snapshot_ciphertext=None)
        assert pin_schema(db, crypto, legacy, selection_origin="legacy_default")["version_id"] == DEFAULT_VERSION_ID
        new = SimpleNamespace(input_schema_version_id=None, input_schema_snapshot_ciphertext=None)
        assert pin_schema(db, crypto, new)["version_id"] == updated.id
        assert "Revised synthetic meaning" not in json.dumps(schema_metadata(read_schema_snapshot(crypto, new)))
        assert schema_metadata(snapshot)["field_metadata_sent_to_model"] is False
        assert "수집" not in original_ciphertext
        corrupt = copy.deepcopy(snapshot)
        corrupt["fields"][0]["description"] = "tampered"
        first.input_schema_snapshot_ciphertext = crypto.encrypt_text(json.dumps(corrupt))
        with pytest.raises(InputSchemaError, match="input_schema_snapshot_invalid"):
            read_schema_snapshot(crypto, first)


def test_schema_content_integrity_and_description_hash(client):
    with client.app.state.session_factory() as db:
        crypto = client.app.state.crypto
        version = get_active_schema(db, crypto)
        original = content_hash(load_definition(version, crypto))
        updated = default_definition()
        updated[0].description = "Different meaning"
        assert content_hash(updated) != original
        version.definition_ciphertext = crypto.encrypt_text(json.dumps(definition_document(updated)))
        with pytest.raises(InputSchemaError, match="input_schema_content_unavailable"):
            load_definition(version, crypto)


def test_bootstrap_rollback_does_not_commit_unrelated_work(client):
    with client.app.state.session_factory() as db:
        get_schema_state(db, client.app.state.crypto)
        db.rollback()
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(InputSchemaVersion)) == 0
        assert db.scalar(select(func.count()).select_from(InputSchemaActivation)) == 0


def test_openapi_reflects_nested_nullable_enum_and_bounds():
    fields = payload()["fields"] + [{"name": "vendor", "type": "object", "nullable": True,
        "properties": [{"name": "values", "type": "array", "required": True, "items": {"type": "string", "nullable": True, "enum": ["x"]}}]}]
    schema = to_json_schema(fields)
    assert schema["additionalProperties"] is True
    assert "expected_verdict" in schema["propertyNames"]["not"]["enum"]
    assert "source_system" in schema["propertyNames"]["not"]["enum"]
    assert "input_schema_version_id" in schema["propertyNames"]["not"]["enum"]
    vendor = schema["properties"]["vendor"]
    assert vendor["type"] == ["object", "null"]
    assert "propertyNames" not in vendor
    assert vendor["required"] == ["values"]
    assert vendor["properties"]["values"]["maxItems"] == 1000
    assert vendor["properties"]["values"]["items"]["enum"] == ["x", None]


def test_number_enum_equivalence_does_not_accept_booleans(event_payload):
    fields = payload()["fields"] + [{"name": "score", "type": "number", "enum": [1]}]
    assert validate_event(fields, {**event_payload, "score": 1.0}) == []
    assert validate_event(fields, {**event_payload, "score": True})[0]["type"] == "type_mismatch"
    with pytest.raises(ValidationError):
        InputSchemaCreate(**payload(fields=payload()["fields"] + [{"name": "score", "type": "number", "enum": [1, 1.0]}]))
