"""Synthetic encrypted rule admission, retry and legacy preservation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import worker
from app.models import AgentRun, AgentStep, Analysis, ParserVersion
from app.result_contracts import validate_new_result
from app.services.parser_rule_versions import (create_rule_bundle_version, read_rule_bundle_definition,
    rule_engine_artifact_hash)
from app.services.parser_versions import (BASELINE_ID, ParserVersionError, apply_parser_snapshot,
    copy_parser_snapshot, create_version, parser_metadata, pin_parser, read_parser_snapshot, version_summary)
from test_parser_management import analysis, login
from test_result_contract_v3 import result_fixture


PRIVATE_MARKER = "SYNTHETIC-CAPTURE-ONLY"
REQUEST = "GET /health?q=fixture HTTP/1.1\r\nHost: synthetic.invalid\r\n\r\n"

V2_ENGINE_SHA256 = {
    "parser_rule_schemas.py": "868587153a0d272a8e1e0c84c9df531cb48b4eab04e7abdedba5e11c9aabab63",
    "services/parser_rule_engine.py": "d482166ad3a6ff844d381384dd9a77f782c1050d55d51cfcb2544cadf66e2d12",
    "services/parser_rule_versions.py": "81501686893d99c431bd1ba04d8fab6ea8c5d80dc72e599f402cc60a1ef82e7b",
    "services/http_parser.py": "7599ecc078722962536b029c4f065c00af67108cffb4acb5d449a10c592e0cca",
    "services/parser_engine_v1.py": "3a112a075a3fb716c4a9edea33b29740a869ee3343f41b5d3e2f2d80e13e862c",
}


def test_rule_v2_execution_files_remain_byte_exact_for_queued_and_approved_work():
    root = Path(__file__).resolve().parents[1] / "app"
    assert {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in V2_ENGINE_SHA256} == V2_ENGINE_SHA256


def bundle(*, vendor="Fixture-A", identifier="fixture-rule-a", revision=1):
    return {"schema_version": "vendor-parser-bundle-v1", "selection_policy_version": "vendor-rule-selection-v1",
        "rules": [{"rule_id": identifier, "rule_version": revision, "waf_vendor": vendor,
            "definition": {"schema_version": "parser-rule-proposal-v1", "name": "Synthetic marked request",
                "match": {"all": [{"kind": "starts_with", "value": PRIVATE_MARKER}]},
                "extract": {"kind": "between_markers", "start_marker": PRIVATE_MARKER + "\n",
                            "end_marker": "\nEND-SYNTHETIC", "start_anchor": "payload_start"},
                "capture": {"line_endings": "actual", "request_line_whitespace": "strict", "skip_leading_blank_lines": False},
                "reason_ko": "테스트용 고정 표식에서 요청 구간을 추출합니다.", "limitations_ko": "단일 구간만 지원합니다."}}]}


def create(client, definition=None, parent=None):
    with client.app.state.session_factory() as db:
        row = create_rule_bundle_version(db, client.app.state.crypto, definition or bundle(),
            name="Synthetic vendor rules", parent_id=parent, actor="synthetic-only")
        identifier = row.id
        db.commit()
        return identifier


def holder(client, identifier):
    with client.app.state.session_factory() as db:
        target = SimpleNamespace()
        pin_parser(db, client.app.state.crypto, target, version_id=identifier, selection_origin="candidate")
        return target


def test_rule_definitions_are_encrypted_and_frozen_snapshots_are_json_safe(client):
    identifier = create(client)
    target = holder(client, identifier)
    assert PRIVATE_MARKER not in json.dumps(target.parser_snapshot_json)
    with client.app.state.session_factory() as db:
        version = db.get(ParserVersion, identifier)
        assert PRIVATE_MARKER not in json.dumps(version.options_json)
        assert "definition_ciphertext" not in version_summary(version)["options"]
        assert read_rule_bundle_definition(version, client.app.state.crypto) == bundle()
    runtime = read_parser_snapshot(client.app.state.crypto, target)
    assert PRIVATE_MARKER not in json.dumps(runtime)
    raw = PRIVATE_MARKER + "\n" + REQUEST + "\nEND-SYNTHETIC"
    parsed = apply_parser_snapshot(raw, runtime, waf_vendor=" fixture-A ")
    assert parsed["method"] == "GET" and parsed["headers"]["host"] == ["synthetic.invalid"]
    assert parsed["body"] == ""
    assert parsed["parse_status"] == "partial"  # Log envelope is not wire completeness.
    assert parsed["capture_selection"]["status"] == "matched"
    start, end = parsed["raw_source_spans"]["method"][0]
    assert raw[start:end] == "GET"
    with pytest.raises(ParserVersionError, match="parser_rule_decryption_required"):
        apply_parser_snapshot(raw, dict(runtime), waf_vendor="Fixture-A")


def test_new_rules_keep_previous_vendor_capture_adapter_as_fallback(client):
    with client.app.state.session_factory() as db:
        row = create_version(db, client.app.state.crypto, name="Old serialized format", options={
            "line_endings": "escaped_crlf", "request_line_whitespace": "strict", "skip_leading_blank_lines": False,
            "profiles": [{"name": "Existing B", "waf_vendor": "Fixture-B", "line_endings": "escaped_crlf",
                          "request_line_whitespace": "strict", "skip_leading_blank_lines": False}]})
        parent = row.id
        db.commit()
    runtime = read_parser_snapshot(client.app.state.crypto, holder(client, create(client, parent=parent)))
    parsed = apply_parser_snapshot(REQUEST.replace("\r\n", "\\r\\n"), runtime, waf_vendor="Fixture-B")
    assert parsed["method"] == "GET" and parsed["headers"]["host"] == ["synthetic.invalid"]
    assert parsed["capture_selection"]["fallback_engine"] == "http-capture-v1"
    assert parsed["capture_selection"]["fallback_selection"]["status"] == "matched"


def test_adding_another_vendor_preserves_old_rules_and_requires_revision_for_updates(client):
    first = create(client)
    second = create(client, bundle(vendor="Fixture-B", identifier="fixture-rule-b"), parent=first)
    with client.app.state.session_factory() as db:
        saved = read_rule_bundle_definition(db.get(ParserVersion, second), client.app.state.crypto)
        assert {r["waf_vendor"] for r in saved["rules"]} == {"Fixture-A", "Fixture-B"}
        invalid = bundle()
        invalid["rules"][0]["definition"]["name"] = "Changed without revision"
        with pytest.raises(ParserVersionError, match="parser_rule_revision_conflict"):
            create_rule_bundle_version(db, client.app.state.crypto, invalid, name="Invalid", parent_id=second)
        row = create_rule_bundle_version(db, client.app.state.crypto, bundle(revision=2), name="Updated", parent_id=second)
        assert len(read_rule_bundle_definition(row, client.app.state.crypto)["rules"]) == 2


def test_snapshot_copy_is_exact_and_tampering_fails_closed(client):
    original = holder(client, create(client))
    child = SimpleNamespace()
    copy_parser_snapshot(original, child)
    assert child.parser_snapshot_json == original.parser_snapshot_json
    assert child.parser_version_id == original.parser_version_id
    child.parser_snapshot_json = deepcopy(child.parser_snapshot_json)
    child.parser_snapshot_json["options"]["definition_ciphertext"] = "A" * 100
    with pytest.raises(ParserVersionError):
        read_parser_snapshot(client.app.state.crypto, child)


@pytest.mark.parametrize("name,changes_identity", [("parser_versions.py", False), ("parser_rule_engine.py", True)])
def test_only_versioned_engine_changes_affect_rule_artifact(monkeypatch, name, changes_identity):
    """A future shared dispatcher branch must not invalidate admitted v2 jobs."""
    before = rule_engine_artifact_hash()
    original = Path.read_bytes

    def changed(path):
        content = original(path)
        return content + b"\n# synthetic future branch\n" if path.name == name else content

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert (rule_engine_artifact_hash() != before) is changes_identity


def test_public_v3_parser_keeps_identifier_fields_and_hides_encrypted_options(client):
    target = holder(client, create(client))
    result = result_fixture()
    result["agent"]["parser"] = parser_metadata(read_parser_snapshot(client.app.state.crypto, target))
    public = validate_new_result(result)
    assert set(public["agent"]["parser"]) == {"version_id", "engine_version", "content_hash"}
    assert public["agent"]["parser"]["engine_version"] == "vendor-http-v2"
    assert PRIVATE_MARKER not in json.dumps(public)


def test_existing_comparison_route_can_compare_encrypted_rule_versions(client):
    login(client)
    identifier = create(client)
    raw = PRIVATE_MARKER + "\n" + REQUEST + "\nEND-SYNTHETIC"
    source = analysis(client, raw, vendor="Fixture-A")
    response = client.post("/api/v1/admin/parsers/comparisons", json={"analysis_ids": [source],
        "baseline_version_id": BASELINE_ID, "candidate_version_id": identifier})
    assert response.status_code == 201, response.text
    assert response.json()["result"]["items"][0]["candidate"]["method"] == "GET"
    assert client.post(f"/api/v1/admin/parsers/{identifier}/activate").status_code == 409


def test_stub_runtime_uses_the_pinned_encrypted_rule_without_rewriting_raw(client):
    identifier = create(client)
    raw = PRIVATE_MARKER + "\n" + REQUEST + "\nEND-SYNTHETIC"
    source = analysis(client, raw, vendor="Fixture-A")
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, source)
        pin_parser(db, client.app.state.crypto, row, version_id=identifier, selection_origin="candidate")
        worker.process_stub(db, client.app.state.crypto, row)
        parser_step = db.scalar(select(AgentStep).join(AgentRun).where(AgentRun.analysis_id == source, AgentStep.step_type == "parser"))
        parsed = json.loads(client.app.state.crypto.decrypt_text(parser_step.output_ciphertext))
        assert parsed["method"] == "GET"
        assert client.app.state.crypto.decrypt_text(row.payload_ciphertext) == raw
        assert row.result_json["agent"]["parser"]["version_id"] == identifier
