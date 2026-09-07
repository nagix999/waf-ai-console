from datetime import UTC, datetime, timedelta

import pytest

from app.api import dashboard
from app.models import Analysis, Review, VLLMProfile


FIXED_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def synthetic_analysis(event_id, *, created_at=None, **values):
    return Analysis(
        id=event_id, source_system="synthetic-parser", event_id=event_id, company_name="Synthetic",
        src_ip="192.0.2.1", dest_ip="198.51.100.1", waf_vendor="synthetic",
        payload_ciphertext="synthetic-payload-must-not-be-returned", encryption_key_version="test-only",
        created_at=created_at or FIXED_NOW - timedelta(days=1),
        **{"analysis_purpose": "production", "ingest_channel": "service_api", "waf_action": "A", **values},
    )


def test_dashboard_requires_admin(client, service_headers):
    assert client.get("/api/v1/dashboard/summary").status_code == 401
    assert client.get("/api/v1/dashboard/summary", headers=service_headers).status_code == 403
    login(client)
    assert client.get("/api/v1/dashboard/summary").status_code == 200


def test_empty_dashboard_zero_counts_and_explicit_configuration_only(client, monkeypatch):
    monkeypatch.setattr(dashboard, "utcnow", lambda: FIXED_NOW)
    login(client)
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["window"] == {
        "days": 7, "created_from": "2026-08-29T12:00:00Z", "created_to": "2026-09-05T12:00:00Z",
    }
    assert all(count == 0 for count in body["counts"].values())
    assert body["severity_counts"] == dict.fromkeys(("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN", "unrated"), 0)
    assert body["runtime"] == {
        "agent_mode": "stub", "production_profile": None,
        "source": "api_configuration", "worker_health_verified": False,
    }


def test_aggregates_all_production_rows_in_window_without_page_limit(client, monkeypatch):
    monkeypatch.setattr(dashboard, "utcnow", lambda: FIXED_NOW)
    start = FIXED_NOW - timedelta(days=7)
    rows = []
    for prefix, count, values in (
        ("high", 120, {"status": "completed", "verdict": "true_positive", "severity": "HIGH"}),
        ("critical", 2, {"status": "completed", "verdict": "true_positive", "severity": "CRITICAL", "waf_action": "D"}),
        ("false", 3, {"status": "completed", "verdict": "false_positive", "severity": "NONE", "waf_action": "D"}),
        ("hold", 4, {"status": "completed", "verdict": "inconclusive", "severity": "UNKNOWN"}),
        ("unrated", 1, {"status": "completed"}),
        # In-flight or failed results must not contribute to completed verdict/severity counts.
        ("pending", 5, {"status": "pending", "verdict": "true_positive", "severity": "HIGH"}),
        ("processing", 6, {"status": "processing"}),
        ("failed", 7, {"status": "failed", "verdict": "false_positive", "severity": "NONE", "waf_action": "D"}),
    ):
        rows.extend(synthetic_analysis(f"{prefix}-{index}", **values) for index in range(count))
    next(row for row in rows if row.id == "pending-0").created_at = start
    excluded = [
        synthetic_analysis("too-old", created_at=start - timedelta(microseconds=1)),
        synthetic_analysis("at-end", created_at=FIXED_NOW),
        synthetic_analysis("future", created_at=FIXED_NOW + timedelta(microseconds=1)),
        synthetic_analysis("test", analysis_purpose="test", status="completed", severity="CRITICAL"),
        synthetic_analysis("legacy", analysis_purpose="legacy_unknown", status="completed", severity="HIGH"),
    ]
    with client.app.state.session_factory() as db:
        db.add_all(rows + excluded)
        db.flush()
        for review_id, analysis_id in (("review-1", "high-0"), ("review-2", "high-0"), ("review-3", "hold-0")):
            db.add(Review(
                analysis_id=analysis_id, event_id=analysis_id, source_system="synthetic-reviews",
                external_review_id=review_id, decision="deferred", comment="synthetic-review-not-returned",
            ))
        db.commit()
    login(client)
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {
        "total": 148, "pending": 5, "processing": 6, "completed": 130, "failed": 7,
        "true_positive": 122, "false_positive": 3, "inconclusive": 4,
        "unreviewed": 146, "critical_high_allowed": 120, "false_positive_denied": 3,
    }
    assert body["severity_counts"] == {
        "CRITICAL": 2, "HIGH": 120, "MEDIUM": 0, "LOW": 0, "NONE": 3, "UNKNOWN": 4, "unrated": 1,
    }
    assert sum(body["severity_counts"].values()) == body["counts"]["completed"]
    assert "synthetic-payload" not in response.text and "synthetic-review" not in response.text
    month = client.get("/api/v1/dashboard/summary?days=30").json()
    assert month["counts"]["total"] == 149
    assert month["window"]["days"] == 30


def test_runtime_exposes_only_profile_identity_not_connection_or_health(client):
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(
            id="synthetic-production-id", name="synthetic-profile", model_name="synthetic-model",
            base_url="http://synthetic-private-endpoint:8000/v1", api_key_ciphertext="synthetic-api-secret",
            status="production",
        ))
        db.commit()
    client.app.state.settings.agent_mode = "moduagent"
    login(client)
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    assert response.json()["runtime"] == {
        "agent_mode": "moduagent", "production_profile": {
            "id": "synthetic-production-id", "name": "synthetic-profile", "model_name": "synthetic-model",
        }, "source": "api_configuration", "worker_health_verified": False,
    }
    assert "synthetic-private-endpoint" not in response.text
    assert "synthetic-api-secret" not in response.text


def test_window_boundaries_preserve_browser_millisecond_precision(client, monkeypatch):
    now = FIXED_NOW.replace(microsecond=123456)
    end = now.replace(microsecond=123000)
    start = end - timedelta(days=7)
    monkeypatch.setattr(dashboard, "utcnow", lambda: now)
    with client.app.state.session_factory() as db:
        db.add_all([
            synthetic_analysis("at-start-ms", created_at=start),
            synthetic_analysis("before-start-ms", created_at=start - timedelta(microseconds=1)),
            synthetic_analysis("before-end-ms", created_at=end - timedelta(microseconds=1)),
            synthetic_analysis("at-end-ms", created_at=end),
            synthetic_analysis("within-discarded-fraction", created_at=end + timedelta(microseconds=100)),
        ])
        db.commit()
    login(client)
    body = client.get("/api/v1/dashboard/summary").json()
    assert datetime.fromisoformat(body["window"]["created_from"]).microsecond == 123000
    assert datetime.fromisoformat(body["window"]["created_to"]).microsecond == 123000
    assert body["counts"]["total"] == 2
    listing = client.get("/api/v1/analyses", params={
        "analysis_purpose": "production",
        "created_from": body["window"]["created_from"],
        "created_to": body["window"]["created_to"],
    }).json()
    assert listing["total"] == body["counts"]["total"]
    assert {row["id"] for row in listing["items"]} == {"at-start-ms", "before-end-ms"}


@pytest.mark.parametrize("days", [0, 91, -1, "1.5", "invalid"])
def test_invalid_window_is_rejected(client, days):
    login(client)
    assert client.get("/api/v1/dashboard/summary", params={"days": days}).status_code == 422


@pytest.mark.parametrize("days", [1, 90])
def test_window_limits_are_inclusive(client, days):
    login(client)
    response = client.get("/api/v1/dashboard/summary", params={"days": days})
    assert response.status_code == 200 and response.json()["window"]["days"] == days
