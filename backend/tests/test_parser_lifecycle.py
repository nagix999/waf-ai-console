"""Parser lifecycle safety with synthetic observations and no model transport."""
import copy
import hashlib
import json
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import worker
from app.models import Analysis, ParserJob, ProductionPromotion, TestRun as Run
from app.services.analysis_retries import RetryError, load_execution_snapshot
from app.services.candidate_configurations import configuration_digest, verify_configuration
from app.services.parser_engine import CAPTURE_ENGINE, DEFAULT_OPTIONS
from app.services.parser_versions import (BASELINE_ID, activate_parser_version, create_version,
                                         legacy_parser_snapshot, parser_metadata, pin_parser, read_parser_snapshot)
from app.result_contracts import validate_new_result
from test_candidate_configurations import candidate, direct
from test_production_promotion import BASE, qualified
from test_validation_data import login
from test_analysis_retries_keys import failed_fixture, profile_fixture, retry
from test_result_contract_v3 import result_fixture

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def version(client):
    with client.app.state.session_factory() as db:
        row = create_version(db, client.app.state.crypto, name="Serialized capture fixture",
            engine_version=CAPTURE_ENGINE, options={**DEFAULT_OPTIONS, "line_endings": "escaped_crlf"},
            parent_id=BASELINE_ID, actor="synthetic-fixture")
        db.commit()
        return row.id


def review_record(client, identifier, *, reviewed=True, valid=True, kind="comparison"):
    with client.app.state.session_factory() as db:
        db.add(ParserJob(kind=kind, status="completed", candidate_version_id=identifier,
            baseline_version_id=BASELINE_ID, request_ciphertext=client.app.state.crypto.encrypt_text("{}"),
            encryption_key_version=client.app.state.crypto.key_version, created_by="synthetic-fixture",
            summary_json={"structure_reviewed": reviewed, "all_valid_source_spans": valid, "sample_count": 1}))
        db.commit()


def test_candidate_parser_is_pinned_per_run_and_item_not_applied(client, event_payload, candidate):
    before = client.get(BASE).json()
    identifier = version(client)
    response = direct(client, {**event_payload, "vendor_score": 2}, {**candidate, "parser_version_id": identifier})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["configuration_snapshot"]["schema_version"] == 2
    assert run["configuration_snapshot"]["parser"]["version_id"] == identifier
    assert client.get(BASE).json()["configuration_hash"] == before["configuration_hash"]
    with client.app.state.session_factory() as db:
        saved = db.get(Run, run["id"])
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        assert parser_metadata(read_parser_snapshot(None, saved)) == parser_metadata(read_parser_snapshot(None, analysis))
        assert verify_configuration(saved, client.app.state.crypto, analysis) == run["configuration_snapshot"]
        assert not {"parser", "parser_version_id", "candidate_configuration"}.intersection(analysis.extra_fields)
        analysis.parser_snapshot_json = {**analysis.parser_snapshot_json, "content_hash": "0" * 64}
        with pytest.raises(ValueError, match="candidate_configuration_invalid"):
            verify_configuration(saved, client.app.state.crypto, analysis)


def test_parser_selection_is_part_of_hash_and_legacy_request_hash_is_unchanged(client, event_payload, candidate):
    event = {**event_payload, "vendor_score": 2}
    first = direct(client, event, candidate).json()
    second = direct(client, event, {**candidate, "parser_version_id": version(client)}).json()
    assert first["configuration_hash"] != second["configuration_hash"]
    with client.app.state.session_factory() as db:
        saved = db.get(Run, first["id"])
        document = {"name": "candidate fixture", "kind": "direct", "rows": [{**event, "expected_verdict": "inconclusive"}],
                    "filename": None, "candidate_configuration": candidate}
        # Direct route has historically stored expected_verdict in the row.
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        assert saved.request_hash == digest


def test_legacy_configuration_hash_survives_new_registry_and_rejects_hidden_parser(client, event_payload, candidate):
    run = direct(client, {**event_payload, "vendor_score": 2}, candidate).json()
    new_id = version(client)
    with client.app.state.session_factory() as db:
        saved = db.get(Run, run["id"])
        original = copy.deepcopy(saved.configuration_snapshot_json)
        original.pop("parser")
        original["schema_version"] = 1
        saved.configuration_snapshot_json, saved.configuration_hash = original, configuration_digest(original)
        saved.parser_version_id = saved.parser_snapshot_json = None
        item = db.get(Analysis, run["items"][0]["analysis_id"])
        item.parser_version_id = item.parser_snapshot_json = None
        assert verify_configuration(saved, client.app.state.crypto, item) == original
        pin_parser(db, None, saved, version_id=new_id, selection_origin="candidate")
        with pytest.raises(ValueError, match="candidate_configuration_invalid"):
            verify_configuration(saved, client.app.state.crypto)


@pytest.mark.parametrize("kind,reviewed,valid,eligible", [
    ("comparison", True, True, True), ("comparison", False, True, False),
    ("comparison", True, False, False), ("suggestion", True, True, False),
])
def test_changed_parser_requires_explicit_structural_review_and_official_evaluation(
    client, event_payload, candidate, kind, reviewed, valid, eligible,
):
    identifier = version(client)
    run = qualified(client, event_payload, {**candidate, "parser_version_id": identifier})
    review_record(client, identifier, reviewed=reviewed, valid=valid, kind=kind)
    preflight = client.get(f"{BASE}/preflight/{run['id']}").json()
    gate = next(item for item in preflight["checks"] if item["code"] == "parser_structure_reviewed")
    assert gate["passed"] is eligible
    assert preflight["eligible"] is eligible
    before = client.get(BASE).json()
    response = client.post(BASE + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == (200 if eligible else 409), response.text
    after = client.get(BASE).json()
    assert after["snapshot"]["parser"]["version_id"] == (identifier if eligible else BASELINE_ID)
    if eligible:
        assert after["configuration_hash"] == run["configuration_hash"] and not after["drifted"]


def test_existing_production_hash_remains_legacy_until_a_new_promotion(client, candidate):
    current = client.get(BASE).json()
    with client.app.state.session_factory() as db:
        # Simulate a real prior five-component baseline, preserving its hash.
        baseline = db.scalar(select(ProductionPromotion).limit(1))
        old = copy.deepcopy(current["snapshot"])
        old.pop("parser")
        old["schema_version"] = 1
        baseline.snapshot_json, baseline.configuration_hash = old, configuration_digest(old)
        db.commit()
    legacy = client.get(BASE).json()
    assert legacy["snapshot"] == old and legacy["configuration_hash"] == configuration_digest(old)
    assert not legacy["drifted"]


def test_new_admission_uses_production_parser_but_previously_queued_and_legacy_do_not(client, event_payload):
    login(client)
    original = client.post("/api/v1/analyses", json=event_payload).json()["id"]
    identifier = version(client)
    with client.app.state.session_factory() as db:
        activate_parser_version(db, identifier)  # Synthetic fixture, not an API bypass.
        db.commit()
    newer = client.post("/api/v1/analyses", json={**event_payload, "event_id": "new-capture"}).json()["id"]
    with client.app.state.session_factory() as db:
        old = db.get(Analysis, original)
        new = db.get(Analysis, newer)
        assert old.parser_version_id == BASELINE_ID and new.parser_version_id == identifier
        old.parser_version_id = old.parser_snapshot_json = None
        assert read_parser_snapshot(None, old)["version_id"] == BASELINE_ID
        worker.process_stub(db, client.app.state.crypto, old)
        assert old.result_json["agent"]["parser"]["version_id"] == BASELINE_ID


def test_retry_copies_original_parser_not_new_production_selection(client, event_payload):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    profile_id = profile_fixture(client)
    original = failed_fixture(client, event_payload, profile_id)
    selected = version(client)
    with client.app.state.session_factory() as db:
        activate_parser_version(db, selected)  # Synthetic fixture only.
        db.commit()
    response = retry(client, original)
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        parent = db.get(Analysis, original)
        child = db.get(Analysis, response.json()["analysis_id"])
        assert child.parser_snapshot_json == parent.parser_snapshot_json
        assert child.parser_version_id == parent.parser_version_id == BASELINE_ID
        snapshot = load_execution_snapshot(child, client.app.state.crypto)
        assert snapshot.schema_version == 4 and snapshot.parser["version_id"] == BASELINE_ID
        child.parser_version_id = child.parser_snapshot_json = None
        # Removing a genuinely non-default selected parser must fail closed.
        pin_parser(db, None, child, version_id=selected, selection_origin="candidate")
        with pytest.raises(RetryError, match="retry_execution_snapshot_invalid"):
            load_execution_snapshot(child, client.app.state.crypto)


def test_legacy_execution_snapshot_never_claims_a_new_parser(client, event_payload):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    original = failed_fixture(client, event_payload, profile_fixture(client))
    selected = version(client)
    with client.app.state.session_factory() as db:
        parent = db.get(Analysis, original)
        document = json.loads(client.app.state.crypto.decrypt_text(parent.execution_snapshot_ciphertext))
        document["schema_version"] = 3
        document.pop("parser")
        parent.execution_snapshot_ciphertext = client.app.state.crypto.encrypt_text(json.dumps(document))
        assert load_execution_snapshot(parent, client.app.state.crypto).schema_version == 3
        parent.parser_version_id = parent.parser_snapshot_json = None
        pin_parser(db, None, parent, version_id=selected, selection_origin="candidate")
        with pytest.raises(RetryError, match="retry_execution_snapshot_invalid"):
            load_execution_snapshot(parent, client.app.state.crypto)


def test_result_parser_metadata_is_optional_and_closed():
    historical = result_fixture()
    assert "parser" not in validate_new_result(historical)["agent"]
    historical["agent"]["parser"] = parser_metadata(legacy_parser_snapshot())
    assert validate_new_result(historical)["agent"]["parser"]["version_id"] == BASELINE_ID
    for mutate in (lambda x: x.update(code="synthetic"),
                   lambda x: x["options"].update(pattern=".*"),
                   lambda x: x["options"].update(line_endings="automatic"),
                   lambda x: x["options"].update(line_endings="escaped_crlf")):
        invalid = copy.deepcopy(historical)
        mutate(invalid["agent"]["parser"])
        with pytest.raises(ValidationError):
            validate_new_result(invalid)

