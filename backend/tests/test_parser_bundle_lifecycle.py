"""One frozen parser bundle dispatches synthetic WAF vendors per observation."""
import copy
import json
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import worker
from app.models import AgentRun, AgentStep, Analysis, TestRun as Run
from app.result_contracts import validate_new_result
from app.services.analysis_retries import load_execution_snapshot
from app.services.parser_engine import CAPTURE_ENGINE, DEFAULT_OPTIONS
from app.services.parser_versions import (BASELINE_ID, activate_parser_version, copy_parser_snapshot,
                                         create_version, parser_metadata, pin_parser, read_parser_snapshot)
from test_analysis_retries_keys import failed_fixture, profile_fixture, retry
from test_result_contract_v3 import result_fixture
from test_validation_data import login
from test_agent_configuration import install_calls

pytestmark = pytest.mark.usefixtures("registered_vllm_target")


def bundle(client):
    options = {**DEFAULT_OPTIONS, "profiles": [
        {**DEFAULT_OPTIONS, "name": "Serialized A", "waf_vendor": "Fixture-A", "line_endings": "escaped_crlf"},
        {**DEFAULT_OPTIONS, "name": "Serialized B", "waf_vendor": "Fixture-B", "line_endings": "escaped_lf"},
    ]}
    with client.app.state.session_factory() as db:
        row = create_version(db, client.app.state.crypto, name="Mixed vendor capture fixture", options=options,
            engine_version=CAPTURE_ENGINE, parent_id=BASELINE_ID, actor="synthetic-fixture")
        identifier = row.id
        db.commit()
    return identifier, options


def test_one_test_run_dispatches_bundle_per_vendor_and_preserves_raw_fallback(client, event_payload, monkeypatch):
    login(client)
    identifier, options = bundle(client)
    actual = "GET /search?q=test HTTP/1.1\r\nHost: example.internal\r\n\r\n"
    rows = [{**event_payload, "event_id": f"bundle-{index}", "waf_vendor": vendor, "payload": raw}
            for index, (vendor, raw) in enumerate([
                ("FIXTURE-a", actual.replace("\r\n", "\\r\\n")),
                ("Fixture-B", actual.replace("\r\n", "\\n")),
                ("Unknown-Fixture", actual),
                ("Unknown-Fixture", actual.replace("\r\n", "\\r\\n")),
            ])]
    response = client.post("/api/v1/test-runs/uploads", data={"name": "Mixed capture fixture", "idempotency_key": str(uuid.uuid4())},
                           files={"file": ("fixture.json", json.dumps(rows).encode())})
    assert response.status_code == 202, response.text
    document = response.json()
    assert document["accepted"] == 4
    original_apply = worker.apply_parser_snapshot
    vendors = []

    def observe(raw, snapshot, *, waf_vendor=None):
        vendors.append(waf_vendor)
        return original_apply(raw, snapshot, waf_vendor=waf_vendor)

    monkeypatch.setattr(worker, "apply_parser_snapshot", observe)
    with client.app.state.session_factory() as db:
        run = db.get(Run, document["id"])
        run.parser_version_id = run.parser_snapshot_json = None
        pin_parser(db, None, run, version_id=identifier, selection_origin="test")
        for position, item in enumerate(document["items"]):
            analysis = db.get(Analysis, item["analysis_id"])
            copy_parser_snapshot(run, analysis)
            worker.process_stub(db, client.app.state.crypto, analysis)
            step = db.scalar(select(AgentStep).join(AgentRun).where(AgentRun.analysis_id == analysis.id, AgentStep.step_type == "parser"))
            parsed = json.loads(client.app.state.crypto.decrypt_text(step.output_ciphertext))
            assert client.app.state.crypto.decrypt_text(analysis.payload_ciphertext) == rows[position]["payload"]
            assert bool(parsed["headers"].get("host")) is (position != 3)
            assert analysis.result_json["agent"]["parser"]["version_id"] == identifier
            assert "options" not in analysis.result_json["agent"]["parser"]
            assert step.metadata_json["parser"]["options"] == options
            if position < 2:
                assert parsed["capture_selection"]["status"] == "matched"
                assert parsed["capture_selection"]["profile_index"] == position
                assert parsed["parse_status"] == "partial"  # No silent removal of the Verifier trigger.
                assert "capture_serialization_unverified" in parsed["warnings"]
            else:
                assert parsed["capture_selection"]["status"] == "generic_fallback"
                assert parsed["capture_selection"]["reason_code"] == "capture_profile_unmatched"
        assert vendors == [row["waf_vendor"] for row in rows]


def test_retry_preserves_entire_bundle_after_production_selection_changes(client, event_payload):
    login(client)
    client.app.state.settings.agent_mode = "moduagent"
    identifier, options = bundle(client)
    profile_id = profile_fixture(client)
    with client.app.state.session_factory() as db:
        activate_parser_version(db, identifier)  # Synthetic setup, not a user-facing bypass.
        db.commit()
    original = failed_fixture(client, {**event_payload, "waf_vendor": "Fixture-A"}, profile_id)
    with client.app.state.session_factory() as db:
        activate_parser_version(db, BASELINE_ID)
        db.commit()
    response = retry(client, original)
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        child = db.get(Analysis, response.json()["analysis_id"])
        parent = db.get(Analysis, original)
        assert child.parser_snapshot_json == parent.parser_snapshot_json
        assert read_parser_snapshot(None, child)["options"] == options
        assert load_execution_snapshot(child, client.app.state.crypto).parser["options"] == options


def test_result_contract_retains_bundle_and_rejects_arbitrary_or_duplicate_profile_fields(client):
    identifier, options = bundle(client)
    with client.app.state.session_factory() as db:
        target = Run()
        pin_parser(db, None, target, version_id=identifier, selection_origin="test")
        metadata = parser_metadata(read_parser_snapshot(None, target))
    result = result_fixture()
    result["agent"]["parser"] = metadata
    assert "options" not in validate_new_result(result)["agent"]["parser"]
    for mutate in (lambda x: x[0].update(pattern=".*"), lambda x: x.append(copy.deepcopy(x[0])),
                   lambda x: x[0].update(selector="event.raw")):
        invalid = copy.deepcopy(result)
        mutate(invalid["agent"]["parser"]["options"]["profiles"])
        with pytest.raises(ValidationError):
            validate_new_result(invalid)


@pytest.mark.parametrize("mode", ["stub", "moduagent"])
def test_service_results_hide_other_vendor_routes_but_admin_trace_keeps_them(
    client, event_payload, service_headers, monkeypatch, mode,
):
    client.app.state.settings.agent_mode = mode
    identifier, options = bundle(client)
    if mode == "moduagent":
        profile_fixture(client)
        install_calls(monkeypatch)
    with client.app.state.session_factory() as db:
        activate_parser_version(db, identifier)  # Synthetic setup only.
        db.commit()
    created = client.post("/api/v1/analyses", json=event_payload, headers=service_headers)
    assert created.status_code == 202, created.text
    analysis_id = created.json()["id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        if mode == "stub":
            worker.process_stub(db, client.app.state.crypto, row)
        else:
            worker.process_moduagent(db, client.app.state.crypto, row, "", .75)
        assert set(row.result_json["agent"]["parser"]) == {"version_id", "engine_version", "content_hash"}
    for query in ("", "?result_version=v3"):
        response = client.get(f"/api/v1/analyses/{analysis_id}{query}", headers=service_headers)
        assert response.status_code == 200, response.text
        public = response.json()["result"]["agent"]["parser"]
        assert set(public) == {"version_id", "engine_version", "content_hash"}
        assert "Fixture-A" not in response.text and "Fixture-B" not in response.text
        assert "Serialized A" not in response.text and "Serialized B" not in response.text
    assert client.get(f"/api/v1/analyses/{analysis_id}/agent-runs", headers=service_headers).status_code == 403
    login(client)
    trace = client.get(f"/api/v1/analyses/{analysis_id}/agent-runs")
    assert trace.status_code == 200, trace.text
    assert "Fixture-A" in trace.text and "Fixture-B" in trace.text
    parser_step = next(step for run in trace.json() for step in run["steps"] if step["step_type"] == "parser")
    assert parser_step["metadata"]["parser"]["options"] == options
