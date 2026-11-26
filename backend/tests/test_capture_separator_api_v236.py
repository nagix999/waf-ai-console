"""Synthetic admin API checks: a saved capture version never activates itself."""
from app.services.parser_versions import BASELINE_ID
from test_capture_separator_v236 import ENGINE, OPTIONS, RAW
from test_parser_management import PREFIX, analysis, login


def test_explicit_vendor_boundary_profile_can_be_saved_compared_and_reviewed(client):
    login(client)
    before = client.get(PREFIX).json()["active_version_id"]
    response = client.post(PREFIX, json={"name": "Confirmed capture fixture", "engine_version": ENGINE,
        "options": OPTIONS, "parent_id": BASELINE_ID})
    assert response.status_code == 201
    version = response.json()
    assert version["options"]["profiles"][0]["field_separator"] == "slash_at_slash"
    identifier = analysis(client, RAW, vendor="Synthetic Vendor")
    response = client.post(PREFIX + "/comparisons", json={"baseline_version_id": BASELINE_ID,
        "candidate_version_id": version["id"], "analysis_ids": [identifier]})
    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "completed"
    item = result["result"]["items"][0]
    assert item["raw_payload"] == RAW
    assert item["candidate"]["method"] == "POST"
    assert item["candidate"]["capture_boundaries"]["status"] == "confirmed"
    assert client.get(PREFIX).json()["active_version_id"] == before
    rejected = client.post(PREFIX + "/" + version["id"] + "/activate")
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "production_promotion_required"
    assert client.post(PREFIX + "/jobs/" + result["id"] + "/review", json={"confirmed": True}).status_code == 200
    assert client.get(PREFIX).json()["active_version_id"] == before


def test_unscoped_or_legacy_separator_configuration_is_rejected(client):
    login(client)
    assert client.post(PREFIX, json={"name": "invalid", "engine_version": "http-capture-v1",
        "options": OPTIONS}).status_code == 422
    assert client.post(PREFIX, json={"name": "invalid", "engine_version": ENGINE,
        "options": {key: value for key, value in OPTIONS.items() if key != "profiles"}
            | {"field_separator": "slash_at_slash"}}).status_code == 422
