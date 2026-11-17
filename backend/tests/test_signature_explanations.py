"""Synthetic-only reference catalog storage, matching, and historical binding."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AccessAudit, SignatureExplanationVersion
from app.services.signature_explanations import (
    SignatureExplanationError, activate_version, copy_snapshot, default_snapshot, ensure_state,
    match_snapshot, metadata, pin_snapshot, read_snapshot, snapshot_for_version, validate_snapshot,
)

PREFIX = "/api/v1/admin/signature-explanations"
ENTRY = {"waf_vendor": "Example WAF", "signature": "Synthetic reference signature",
         "signature_explanation": "Synthetic reference description, not an observed exploit.",
         "reference_source": "Synthetic vendor document"}


def login(client):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 200


def create(client, **changes):
    response = client.post(PREFIX, json={"name": "Synthetic catalog", "change_reason": "Synthetic revision",
        "entries": [ENTRY], **changes})
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_only_no_store_and_no_implicit_activation(client, service_headers):
    assert client.get(PREFIX).status_code == 401
    assert client.get(PREFIX, headers=service_headers).status_code == 403
    login(client)
    response = client.get(PREFIX)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["active_version_id"] is None
    assert response.json()["total"] == 0
    assert response.json()["activation_requires_production_review"] is True


def test_create_encrypted_immutable_catalog_and_audited_reads(client):
    login(client)
    first = create(client)
    second = create(client, parent_id=first["id"], entries=[{**ENTRY, "signature_explanation": "Revised synthetic reference."}])
    assert (first["version_no"], second["version_no"]) == (1, 2)
    assert first["fingerprint"] != second["fingerprint"]
    assert first["entries"] == [ENTRY]
    listed = client.get(PREFIX).json()
    assert listed["active_version_id"] is None
    assert ENTRY["signature_explanation"] not in json.dumps(listed)
    assert ENTRY["reference_source"] not in json.dumps(listed)
    assert client.get(PREFIX + "/" + first["id"]).json()["entries"] == [ENTRY]
    assert client.patch(PREFIX + "/" + first["id"], json={"name": "Changed"}).status_code == 405
    assert client.delete(PREFIX + "/" + first["id"]).status_code == 405
    assert client.post(PREFIX + "/" + first["id"] + "/activate").json()["detail"] == "production_promotion_required"
    with client.app.state.session_factory() as db:
        row = db.get(SignatureExplanationVersion, first["id"])
        assert ENTRY["signature_explanation"] not in row.definition_ciphertext
        assert ENTRY["reference_source"] not in row.definition_ciphertext
        actions = set(db.scalars(select(AccessAudit.action)))
        assert {"create_signature_explanation", "list_signature_explanations", "view_signature_explanation"} <= actions


@pytest.mark.parametrize("vendor,signature,matched", [
    (" example waf ", ENTRY["signature"], True),
    ("EXAMPLE WAF", " " + ENTRY["signature"] + " ", True),
    ("Another WAF", ENTRY["signature"], False),
    (ENTRY["waf_vendor"], ENTRY["signature"].upper(), False),
    (ENTRY["waf_vendor"], ENTRY["signature"] + " suffix", False),
])
def test_exact_signature_and_normalized_vendor_match(client, vendor, signature, matched):
    login(client)
    version = create(client)
    response = client.post(PREFIX + "/match", json={"version_id": version["id"], "waf_vendor": vendor, "signature": signature})
    assert response.status_code == 200
    assert response.json()["matched"] is matched
    assert response.json()["entry"] == (ENTRY if matched else None)


@pytest.mark.parametrize("change", [
    {"entries": [ENTRY, {**ENTRY, "waf_vendor": " example waf "}]},
    {"entries": [{**ENTRY, "signature_explanation": " "}]},
    {"entries": [{**ENTRY, "signature_explanation": "x" * 2001}]},
    {"entries": [{**ENTRY, "waf_vendor": "example\nWAF"}]},
    {"entries": [{**ENTRY, "signature": "signature\x00"}]},
    {"entries": [{**ENTRY, "signature_explanation": 42}]},
    {"entries": [{**ENTRY, "unknown": "not accepted"}]},
    {"entries": [{**ENTRY, "signature": str(index)} for index in range(201)]},
    {"name": " "}, {"change_reason": ""}, {"unexpected": True},
])
def test_invalid_fields_limits_and_duplicates_rejected(client, change):
    login(client)
    response = client.post(PREFIX, json={"name": "Synthetic", "change_reason": "Test", "entries": [ENTRY], **change})
    assert response.status_code == 422


def test_total_utf8_limit_and_parent_not_found(client):
    login(client)
    response = client.post(PREFIX, json={"name": "Synthetic", "change_reason": "Test",
        "entries": [{**ENTRY, "signature": str(index), "signature_explanation": "가" * 2000} for index in range(50)]})
    assert response.status_code == 413
    assert response.json()["detail"] == "signature_explanation_catalog_size_limit"
    response = client.post(PREFIX, json={"name": "Synthetic", "change_reason": "Test", "parent_id": "missing", "entries": []})
    assert response.status_code == 404


def test_snapshot_empty_does_not_adopt_current_and_catalog_shrinks_for_analysis(client):
    login(client)
    second_entry = {**ENTRY, "signature": "Another synthetic signature", "signature_explanation": "Different reference"}
    version = create(client, entries=[ENTRY, second_entry])
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        activate_version(db, crypto, version["id"])
        assert ensure_state(db).active_version_id == version["id"]
        assert snapshot_for_version(db, crypto)["version_id"] is None
        run = SimpleNamespace(signature_explanation_version_id=None, signature_explanation_snapshot_ciphertext=None)
        full = pin_snapshot(db, crypto, run, version["id"], "candidate")
        target = SimpleNamespace(waf_vendor=" EXAMPLE WAF ", signature=ENTRY["signature"],
            signature_explanation_version_id=None, signature_explanation_snapshot_ciphertext=None)
        copy_snapshot(run, target, crypto=crypto)
        event = read_snapshot(crypto, target)
        assert full["scope"] == "catalog" and len(full["entries"]) == 2
        assert event["scope"] == "event" and event["entries"] == [ENTRY]
        assert event["entry_count"] == 2
        assert metadata(event) == metadata(full)
        assert match_snapshot(event, target.waf_vendor, target.signature) == ENTRY
        retry = SimpleNamespace(waf_vendor=target.waf_vendor, signature=target.signature)
        copy_snapshot(target, retry)
        assert retry.signature_explanation_snapshot_ciphertext == target.signature_explanation_snapshot_ciphertext
        assert read_snapshot(crypto, retry) == event
        assert read_snapshot(crypto, SimpleNamespace()) == default_snapshot()


def test_snapshot_pins_unmatched_event_and_empty_catalog_without_later_lookup(client):
    login(client)
    version = create(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        target = SimpleNamespace(waf_vendor="Unmatched WAF", signature=ENTRY["signature"])
        pinned = pin_snapshot(db, crypto, target, version["id"], "candidate")
        assert pinned["scope"] == "event" and pinned["entries"] == []
        assert pinned["entry_count"] == 1
        assert match_snapshot(pinned, target.waf_vendor, target.signature) is None
        activate_version(db, crypto, version["id"])
        assert pin_snapshot(db, crypto, target, None) == pinned
        empty = SimpleNamespace(waf_vendor=ENTRY["waf_vendor"], signature=ENTRY["signature"])
        snapshot = pin_snapshot(db, crypto, empty, None)
        assert snapshot["entries"] == [] and snapshot["version_id"] is None
        assert read_snapshot(crypto, empty) == snapshot


def test_snapshot_corruption_and_incorrect_binding_fail_closed(client):
    login(client)
    version = create(client)
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        snapshot = snapshot_for_version(db, crypto, version["id"])
        assert ENTRY["signature_explanation"] not in json.dumps(metadata(snapshot))
        with pytest.raises(SignatureExplanationError, match="snapshot_invalid"):
            validate_snapshot({**snapshot, "entries": []})
        with pytest.raises(SignatureExplanationError, match="snapshot_missing"):
            read_snapshot(crypto, SimpleNamespace(signature_explanation_version_id=version["id"]))
        target = SimpleNamespace(waf_vendor=ENTRY["waf_vendor"], signature=ENTRY["signature"])
        pin_snapshot(db, crypto, target, version["id"])
        target.signature = "Changed signature"
        with pytest.raises(SignatureExplanationError, match="snapshot_invalid"):
            read_snapshot(crypto, target)
        row = db.get(SignatureExplanationVersion, version["id"])
        row.definition_ciphertext = crypto.encrypt_text(json.dumps([{**ENTRY, "signature_explanation": "Changed"}]))
        with pytest.raises(SignatureExplanationError, match="definition_invalid"):
            snapshot_for_version(db, crypto, version["id"])


def test_removed_entry_is_new_version_and_older_snapshot_preserved(client):
    login(client)
    first = create(client)
    second = create(client, parent_id=first["id"], entries=[])
    assert second["entry_count"] == 0
    with client.app.state.session_factory() as db:
        crypto = client.app.state.crypto
        old = snapshot_for_version(db, crypto, first["id"])
        new = snapshot_for_version(db, crypto, second["id"])
        assert match_snapshot(old, ENTRY["waf_vendor"], ENTRY["signature"]) == ENTRY
        assert match_snapshot(new, ENTRY["waf_vendor"], ENTRY["signature"]) is None
        assert ensure_state(db).active_version_id is None
