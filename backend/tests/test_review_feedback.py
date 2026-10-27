"""Feedback is optional, append-only and never a verdict/reference label."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.api_key_schemas import ServiceApiKeyCreate
from app.models import Analysis, AnalysisLabel, Review, utcnow
from app.services.service_api_keys import issue_key


CATEGORIES = ["insufficient_evidence", "evidence_mismatch", "explanation_error",
    "exaggerated_impact", "inappropriate_severity", "other"]


@pytest.mark.parametrize("fields", [{}, {"feedback_categories": [], "comment": None},
    *({"feedback_categories": [category]} for category in CATEGORIES),
    {"comment": "근거 설명이 도움이 됐습니다."},
    {"feedback_categories": ["insufficient_evidence", "exaggerated_impact"], "comment": "설명을 더 구체적으로 작성해 주세요."},
    {"feedback_categories": ["other"], "comment": ""}, {"comment": ""},
    {"feedback_categories": CATEGORIES, "analyst_id": "synthetic-analyst", "ai_visible": True}])
def test_optional_feedback_is_idempotent_without_affecting_evaluation(client, event_payload, service_headers, fields):
    created = client.post("/api/v1/analyses", headers=service_headers,
        json={**event_payload, "expected_verdict": "true_positive"}).json()
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, created["id"])
        analysis.status, analysis.verdict = "completed", "true_positive"
        analysis.result_json = {"verdict": "true_positive", "agent": {"framework": "moduagent"}}
        db.commit()
    url = f"/api/v1/analyses/{created['id']}"
    before = client.get(url, headers=service_headers).json()
    body = {"external_review_id": "synthetic-feedback", "event_id": event_payload["event_id"], **fields}
    response = client.post(url + "/reviews", headers=service_headers, json=body)
    assert response.status_code == 201, response.text
    assert "decision" not in response.json() and "rating" not in response.json()
    assert response.json()["feedback_categories"] == fields.get("feedback_categories", [])
    assert response.json()["comment"] == fields.get("comment")
    assert response.json()["analyst_id"] == fields.get("analyst_id")
    assert response.json()["ai_visible"] == fields.get("ai_visible", False)
    duplicate = client.post(url + "/reviews", headers=service_headers, json=body)
    assert duplicate.status_code == 200 and duplicate.json() == response.json()
    after = client.get(url, headers=service_headers).json()
    assert after["review_state"] == "confirmed"
    assert after["verdict"] == before["verdict"] and after["result"] == before["result"]
    assert after["evaluation"] == before["evaluation"]
    for state, count in (("confirmed", 1), ("unreviewed", 0), ("deferred", 0)):
        page = client.get("/api/v1/analyses", headers=service_headers, params={"review_state": state}).json()
        assert page["total"] == count
        if count:
            assert page["items"][0]["review_state"] == after["review_state"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 1
        review = db.scalar(select(Review))
        assert review.decision is None and review.rating is None
        assert review.feedback_categories == fields.get("feedback_categories", [])
        assert db.scalar(select(func.count()).select_from(AnalysisLabel)) == 1


@pytest.mark.parametrize("fields", [{"decision": "false_positive"}, {"rating": "like"}, {"rating": "dislike"},
    {"rating": None}, {"comment": []}, {"unexpected": "x"},
    *({"feedback_categories": value} for value in [None, "other", {}, True, 1, [None], [True], [1],
        [[]], [{}], [""], ["unknown"], ["OTHER"], ["other", "other"], CATEGORIES + ["other"]])])
def test_old_decision_and_invalid_feedback_are_rejected(client, event_payload, service_headers, fields):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    response = client.post(f"/api/v1/analyses/{created['id']}/reviews", headers=service_headers,
        json={"external_review_id": "invalid-feedback", "event_id": event_payload["event_id"], **fields})
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 0


@pytest.mark.parametrize("missing", ["external_review_id", "event_id"])
def test_feedback_still_requires_external_id_and_event_id(client, event_payload, service_headers, missing):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    body = {"external_review_id": "required-fields", "event_id": event_payload["event_id"]}
    body.pop(missing)
    response = client.post(f"/api/v1/analyses/{created['id']}/reviews", headers=service_headers, json=body)
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 0


def test_feedback_event_id_must_match_analysis(client, event_payload, service_headers):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    body = {"external_review_id": "mismatched-event", "event_id": "different-synthetic-event",
        "feedback_categories": ["evidence_mismatch"]}
    response = client.post(f"/api/v1/analyses/{created['id']}/reviews", headers=service_headers, json=body)
    assert response.status_code == 409 and response.json()["detail"] == "event_id_mismatch"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 0


def test_empty_and_omitted_categories_are_equivalent_but_changes_are_conflicts(client, event_payload, service_headers):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    url = f"/api/v1/analyses/{created['id']}/reviews"
    body = {"external_review_id": "empty-feedback", "event_id": event_payload["event_id"]}
    assert client.post(url, headers=service_headers, json=body).status_code == 201
    assert client.post(url, headers=service_headers, json={**body, "feedback_categories": [], "comment": None}).status_code == 200
    for change in ({"feedback_categories": ["insufficient_evidence"]}, {"feedback_categories": ["other"]},
            {"comment": "new opinion"}, {"ai_visible": True}, {"analyst_id": "other"}):
        response = client.post(url, headers=service_headers, json={**body, **change})
        assert response.status_code == 409 and response.json()["detail"] == "external_review_id_conflict"


def test_category_order_is_canonical_and_same_set_is_idempotent(client, event_payload, service_headers):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    url = f"/api/v1/analyses/{created['id']}/reviews"
    body = {"external_review_id": "ordered-feedback", "event_id": event_payload["event_id"],
        "feedback_categories": list(reversed(CATEGORIES)), "comment": "Synthetic category order"}
    response = client.post(url, headers=service_headers, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["feedback_categories"] == CATEGORIES
    duplicate = client.post(url, headers=service_headers, json={**body, "feedback_categories": CATEGORIES})
    assert duplicate.status_code == 200 and duplicate.json() == response.json()
    different = client.post(url, headers=service_headers, json={**body, "feedback_categories": CATEGORIES[:-1]})
    assert different.status_code == 409 and different.json()["detail"] == "external_review_id_conflict"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 1
        assert db.scalar(select(Review)).feedback_categories == CATEGORIES


@pytest.mark.parametrize("legacy", [{"decision": "deferred"}, {"rating": "like"}, {"rating": "dislike"}])
def test_historical_feedback_is_preserved_and_not_reinterpreted_as_categories(client, event_payload, service_headers, legacy):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    with client.app.state.session_factory() as db:
        db.add(Review(analysis_id=created["id"], source_system="test-parser", event_id=event_payload["event_id"],
            external_review_id="historical-review", created_at=utcnow() - timedelta(days=1), **legacy))
        db.commit()
    path = f"/api/v1/analyses/{created['id']}"
    expected_state = "deferred" if legacy.get("decision") == "deferred" else "confirmed"
    assert client.get(path, headers=service_headers).json()["review_state"] == expected_state
    body = {"external_review_id": "historical-review", "event_id": event_payload["event_id"]}
    assert client.post(path + "/reviews", headers=service_headers, json=body).status_code == 409
    assert client.post(path + "/reviews", headers=service_headers,
        json={**body, "external_review_id": "new-feedback", "feedback_categories": ["other"]}).status_code == 201
    assert client.get(path, headers=service_headers).json()["review_state"] == "confirmed"
    with client.app.state.session_factory() as db:
        historical = db.scalar(select(Review).where(Review.external_review_id == "historical-review"))
        assert historical.decision == legacy.get("decision") and historical.rating == legacy.get("rating")
        assert historical.feedback_categories == []


def test_test_key_can_leave_feedback_only_in_its_own_test_scope(client, event_payload, service_headers):
    production = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    with client.app.state.session_factory() as db:
        _, key = issue_key(db, ServiceApiKeyCreate(name="Test feedback", source_system="test-parser",
            scopes=["ingest", "review"], purpose="test"), "synthetic-fixture")
        db.commit()
    headers = {"X-API-Key": key}
    test = client.post("/api/v1/analyses", headers=headers, json=event_payload)
    assert test.status_code == 202, test.text
    body = {"external_review_id": "test-feedback", "event_id": event_payload["event_id"], "feedback_categories": ["explanation_error"]}
    assert client.post(f"/api/v1/analyses/{test.json()['id']}/reviews", headers=headers, json=body).status_code == 201
    assert client.post(f"/api/v1/analyses/{production['id']}/reviews", headers=headers, json=body).status_code == 404
    assert client.post(f"/api/v1/analyses/{test.json()['id']}/reviews", headers=service_headers, json=body).status_code == 404


def test_openapi_exposes_optional_categories_and_comment_without_legacy_fields(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    request, response = schemas["ReviewCreate"], schemas["ReviewResponse"]
    assert set(request["required"]) == {"external_review_id", "event_id"}
    assert "feedback_categories" in request["properties"] and "comment" in request["properties"]
    for legacy in ("decision", "rating"):
        assert legacy not in request["properties"] and legacy not in response["properties"]
    for model in (request, response):
        categories = model["properties"]["feedback_categories"]
        assert categories["type"] == "array" and categories["items"]["enum"] == CATEGORIES
    assert request["properties"]["feedback_categories"]["maxItems"] == 6
    assert request["properties"]["feedback_categories"]["uniqueItems"] is True
    assert request["properties"]["feedback_categories"]["default"] == []
    assert request["additionalProperties"] is False


def test_live_api_document_describes_the_same_feedback_contract(client, service_headers):
    response = client.get("/api/v1/production-api", headers=service_headers)
    assert response.status_code == 200
    markdown = response.json()["markdown"]
    assert '"feedback_categories":' in markdown
    assert all(category in markdown for category in CATEGORIES)
    assert '"rating": "like"' not in markdown and '"decision": "false_positive"' not in markdown
