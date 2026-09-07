from datetime import UTC, datetime, timedelta

import pytest

from app.models import Review
from app.schemas import AnalysisInput
from app.services.analysis import enqueue_analysis


@pytest.fixture
def query_rows(client, event_payload):
    start = datetime(2026, 9, 1, tzinfo=UTC)
    with client.app.state.session_factory() as db:
        ids = []
        for index, (event_id, purpose) in enumerate((("literal_%alpha", "production"), ("beta", "test"), ("gamma", "legacy_unknown"))):
            row, _ = enqueue_analysis(
                db, client.app.state.crypto, "test-parser",
                AnalysisInput.model_validate({**event_payload, "event_id": event_id}),
                analysis_purpose=purpose,
                ingest_channel="test_lab" if purpose == "test" else "service_api" if purpose == "production" else "legacy_unknown",
            )
            row.created_at = start + timedelta(days=index)
            if index < 2:
                row.started_at = row.created_at + timedelta(seconds=2)
                row.completed_at = row.started_at + timedelta(seconds=8)
                row.status = "completed"
                row.confidence_score = 0.9 if index == 0 else 0.6
                row.verdict = "true_positive" if index == 0 else "false_positive"
                row.severity = "HIGH" if index == 0 else "NONE"
                row.threat_category = "sql_injection" if index == 0 else "normal_request"
                row.model_profile = "model-one" if index == 0 else "model-two"
                row.input_truncated = index == 0
            if index != 0:
                row.src_ip = "192.0.2.100"
                row.dest_port = 80
                row.waf_action = "A"
                row.waf_vendor = "other-vendor"
                row.company_name = "Other Company"
                row.signature = "Other Signature"
                row.event_name = "other-event"
                row.src_port = 100
            ids.append(row.id)
        db.add_all([
            Review(analysis_id=ids[0], source_system="test-parser", external_review_id="review-old", event_id="literal_%alpha", decision="deferred", created_at=start),
            Review(analysis_id=ids[0], source_system="test-parser", external_review_id="review-new", event_id="literal_%alpha", decision="true_positive", created_at=start + timedelta(hours=1)),
            Review(analysis_id=ids[1], source_system="test-parser", external_review_id="review-defer", event_id="beta", decision="deferred", created_at=start),
        ])
        db.commit()
    return ids


@pytest.mark.parametrize("params,index", [
    ({"analysis_purpose": "production"}, 0), ({"analysis_purpose": "test"}, 1),
    ({"analysis_purpose": "legacy_unknown"}, 2), ({"ingest_channel": "test_lab"}, 1),
    ({"event_id": "alpha"}, 0), ({"q": "_%"}, 0),
    ({"q": "SQL", "search_field": "threat_category"}, 0),
    ({"q": "192.0.2.10", "search_field": "src_ip"}, 0),
    ({"q": "192.0.2.10"}, 0), ({"company_name": "Example"}, 0),
    ({"signature": "Synthetic"}, 0), ({"event_name": "fixture"}, 0),
    ({"src_ip": "192.0.2.10"}, 0), ({"src_port": 43122}, 0), ({"dest_port": 443}, 0),
    ({"status": "pending"}, 2), ({"verdict": "true_positive"}, 0), ({"severity": "HIGH"}, 0),
    ({"waf_vendor": "generic"}, 0), ({"waf_action": "D"}, 0),
    ({"review_state": "confirmed"}, 0), ({"review_state": "deferred"}, 1), ({"review_state": "unreviewed"}, 2),
    ({"model_profile": "model-one"}, 0), ({"input_truncated": "true"}, 0),
    ({"confidence_min": 0.8}, 0), ({"confidence_max": 0.7}, 1),
    ({"created_from": "2026-09-01T09:00:00+09:00", "created_to": "2026-09-02T00:00:00Z"}, 0),
])
def test_filters_and_count_share_predicates(client, service_headers, query_rows, params, index):
    response = client.get("/api/v1/analyses", headers=service_headers, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [query_rows[index]]
    paged = client.get("/api/v1/analyses", headers=service_headers, params={**params, "offset": 1, "limit": 1}).json()
    assert paged["total"] == 1 and paged["items"] == []


def test_combined_filters_pagination_and_timing(client, service_headers, query_rows):
    all_rows = client.get("/api/v1/analyses?limit=2", headers=service_headers).json()
    assert all_rows["total"] == 3
    assert [row["id"] for row in all_rows["items"]] == [query_rows[2], query_rows[1]]
    combo = client.get("/api/v1/analyses", headers=service_headers, params={
        "analysis_purpose": "production", "severity": "HIGH", "waf_action": "D", "review_state": "confirmed",
    }).json()
    row = combo["items"][0]
    assert combo["total"] == 1
    assert (row["total_elapsed_ms"], row["queue_wait_ms"], row["processing_duration_ms"]) == (10000, 2000, 8000)
    assert row["created_at"].endswith("Z") and row["started_at"].endswith("Z")
    assert row["review_state"] == "confirmed"
    empty = client.get("/api/v1/analyses?analysis_purpose=test&severity=HIGH", headers=service_headers).json()
    assert empty["total"] == 0 and empty["items"] == []
    assert client.get("/api/v1/analyses?search_field=src_ip&q=192.0.2.1", headers=service_headers).json()["total"] == 0


@pytest.mark.parametrize("params", [
    {"analysis_purpose": "made-up"}, {"ingest_channel": "made-up"}, {"severity": "high"},
    {"status": "made-up"}, {"verdict": "made-up"}, {"review_state": "made-up"},
    {"search_field": "payload"}, {"payload": "synthetic"},
    {"confidence_min": -0.1}, {"confidence_max": 1.1}, {"confidence_min": "NaN"},
    {"confidence_min": 0.9, "confidence_max": 0.2},
    {"src_port": 65536}, {"dest_port": -1}, {"limit": 201}, {"offset": -1},
    {"created_from": "2026-09-01"},
    {"created_from": "2026-09-02T00:00:00Z", "created_to": "2026-09-01T00:00:00Z"},
])
def test_invalid_filters_are_rejected(client, service_headers, params):
    response = client.get("/api/v1/analyses", headers=service_headers, params=params)
    assert response.status_code == 422
