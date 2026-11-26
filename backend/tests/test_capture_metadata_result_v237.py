"""Synthetic result compatibility for the explicitly selected capture-v2 engine."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.result_contracts import AnalysisResultV3, ParserMetadata, legacy_result_projection, validate_new_result
from app.services.analysis_exports import build_report
from app.services.analyst_presentation import assessment_view
from app.services.parser_engine import DEFAULT_OPTIONS
from app.services.parser_versions import apply_parser_snapshot, definition_hash, parser_metadata
from app.services.test_run_exports import evidence_columns
from test_result_contract_v3 import result_fixture


def capture_snapshot(engine="http-capture-v2"):
    profile = {"name": "Synthetic field boundaries", "waf_vendor": "Synthetic Vendor", **DEFAULT_OPTIONS,
        "field_separator": "slash_at_slash"}
    options = {**DEFAULT_OPTIONS, "profiles": [profile]} if engine == "http-capture-v2" else dict(DEFAULT_OPTIONS)
    return {"schema_version": 1, "version_id": "synthetic-parser-version", "engine_version": engine,
        "options": options, "content_hash": definition_hash(engine, options), "selection_origin": "candidate"}


@pytest.mark.parametrize("engine", ["generic-http-v3", "http-capture-v1", "http-capture-v2"])
def test_pinned_capture_metadata_survives_strict_v3_and_legacy_serialization(engine):
    snapshot = capture_snapshot(engine)
    raw = "GET /fixture HTTP/1.1/@/Host: example.invalid/@//@/"
    parsed = apply_parser_snapshot(raw, snapshot, waf_vendor="Synthetic Vendor")
    if engine == "http-capture-v2":
        assert parsed["method"] == "GET"
        assert parsed["capture_boundaries"]["status"] == "confirmed"
    metadata = parser_metadata(snapshot)
    document = result_fixture()
    document["agent"]["parser"] = metadata
    original = deepcopy(document)
    result = validate_new_result(document)
    public = {key: snapshot[key] for key in ("version_id", "engine_version", "content_hash")}
    assert result["agent"]["parser"] == public
    assert ParserMetadata.model_validate(metadata).model_dump(mode="json") == public
    assert AnalysisResultV3.model_validate(result).model_dump(mode="json", exclude_unset=True) == result
    assert legacy_result_projection(result)["agent"]["parser"] == public
    assert document == original
    assert "field_separator" not in str(result)


def test_capture_v2_result_keeps_the_existing_public_field_shape_and_downstream_views():
    document = result_fixture()
    document["agent"]["parser"] = parser_metadata(capture_snapshot())
    result = validate_new_result(document)
    assert set(result) == set(result_fixture())
    assert set(result["agent"]["parser"]) == {"version_id", "engine_version", "content_hash"}
    assert assessment_view(result) == assessment_view(result_fixture())
    assert evidence_columns(result) == evidence_columns(result_fixture())
    report = build_report({"id": "synthetic", "status": "completed", "result": result})
    assert report.sections
    invalid = deepcopy(result)
    invalid["agent"]["parser"]["unexpected"] = "must not pass"
    with pytest.raises(ValidationError):
        validate_new_result(invalid)
    with pytest.raises(ValidationError):
        ParserMetadata.model_validate({**result["agent"]["parser"], "engine_version": "custom-executable"})


@pytest.mark.parametrize("version", ["waf-analysis-v1", "waf-analysis-v2"])
def test_historical_result_metadata_is_not_rewritten_to_the_new_engine(client, event_payload, service_headers, version):
    from app.models import Analysis
    response = client.post("/api/v1/analyses?wait_seconds=0", json=event_payload, headers=service_headers)
    assert response.status_code == 202
    identifier = response.json()["id"]
    historical = {"schema_version": version, "agent": {"parser": {"engine_version": "http-capture-v1", "historical": True}}}
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, identifier)
        row.status, row.result_json = "completed", historical
        db.commit()
    response = client.get(f"/api/v1/analyses/{identifier}?result_version=v3", headers=service_headers)
    assert response.status_code == 200
    assert response.json()["result"] == historical
    with client.app.state.session_factory() as db:
        assert db.get(Analysis, identifier).result_json == historical
