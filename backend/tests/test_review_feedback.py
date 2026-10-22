"""Feedback is optional, append-only and never a verdict/reference label."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.api_key_schemas import ServiceApiKeyCreate
from app.models import Analysis, AnalysisLabel, Review, utcnow
from app.services.service_api_keys import issue_key


@pytest.mark.parametrize("fields", [{}, {"rating": None, "comment": None}, {"rating": "like"},
    {"rating": "dislike"}, {"comment": "근거 설명이 도움이 됐습니다."},
    {"rating": "dislike", "comment": "설명을 더 구체적으로 작성해 주세요."}, {"comment": ""}])
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
    assert "decision" not in response.json()
    assert response.json()["rating"] == fields.get("rating")
    assert response.json()["comment"] == fields.get("comment")
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
        assert db.scalar(select(Review)).decision is None
        assert db.scalar(select(func.count()).select_from(AnalysisLabel)) == 1


@pytest.mark.parametrize("fields", [{"decision": "false_positive"}, {"rating": "true_positive"},
    {"rating": True}, {"rating": False}, {"rating": 1}, {"rating": ""}, {"comment": []}, {"unexpected": "x"}])
def test_old_decision_and_invalid_feedback_are_rejected(client, event_payload, service_headers, fields):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    response = client.post(f"/api/v1/analyses/{created['id']}/reviews", headers=service_headers,
        json={"external_review_id": "invalid-feedback", "event_id": event_payload["event_id"], **fields})
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 0


def test_null_and_omitted_feedback_are_equivalent_but_changes_are_conflicts(client, event_payload, service_headers):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    url = f"/api/v1/analyses/{created['id']}/reviews"
    body = {"external_review_id": "empty-feedback", "event_id": event_payload["event_id"]}
    assert client.post(url, headers=service_headers, json=body).status_code == 201
    assert client.post(url, headers=service_headers, json={**body, "rating": None, "comment": None}).status_code == 200
    for change in ({"rating": "like"}, {"rating": "dislike"}, {"comment": "new opinion"}, {"ai_visible": True}, {"analyst_id": "other"}):
        response = client.post(url, headers=service_headers, json={**body, **change})
        assert response.status_code == 409 and response.json()["detail"] == "external_review_id_conflict"


def test_historical_decision_is_not_reinterpreted_as_optional_feedback(client, event_payload, service_headers):
    created = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    with client.app.state.session_factory() as db:
        db.add(Review(analysis_id=created["id"], source_system="test-parser", event_id=event_payload["event_id"],
            external_review_id="historical-review", decision="deferred", created_at=utcnow() - timedelta(days=1)))
        db.commit()
    path = f"/api/v1/analyses/{created['id']}"
    assert client.get(path, headers=service_headers).json()["review_state"] == "deferred"
    body = {"external_review_id": "historical-review", "event_id": event_payload["event_id"]}
    assert client.post(path + "/reviews", headers=service_headers, json=body).status_code == 409
    assert client.post(path + "/reviews", headers=service_headers,
        json={**body, "external_review_id": "new-feedback", "rating": "like"}).status_code == 201
    assert client.get(path, headers=service_headers).json()["review_state"] == "confirmed"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(Review).where(Review.external_review_id == "historical-review")).decision == "deferred"


def test_test_key_can_leave_feedback_only_in_its_own_test_scope(client, event_payload, service_headers):
    production = client.post("/api/v1/analyses", headers=service_headers, json=event_payload).json()
    with client.app.state.session_factory() as db:
        _, key = issue_key(db, ServiceApiKeyCreate(name="Test feedback", source_system="test-parser",
            scopes=["ingest", "review"], purpose="test"), "synthetic-fixture")
        db.commit()
    headers = {"X-API-Key": key}
    test = client.post("/api/v1/analyses", headers=headers, json=event_payload)
    assert test.status_code == 202, test.text
    body = {"external_review_id": "test-feedback", "event_id": event_payload["event_id"], "rating": "dislike"}
    assert client.post(f"/api/v1/analyses/{test.json()['id']}/reviews", headers=headers, json=body).status_code == 201
    assert client.post(f"/api/v1/analyses/{production['id']}/reviews", headers=headers, json=body).status_code == 404
    assert client.post(f"/api/v1/analyses/{test.json()['id']}/reviews", headers=service_headers, json=body).status_code == 404


def test_openapi_exposes_optional_rating_and_comment_without_decision(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    request, response = schemas["ReviewCreate"], schemas["ReviewResponse"]
    assert set(request["required"]) == {"external_review_id", "event_id"}
    assert "rating" in request["properties"] and "comment" in request["properties"]
    assert "decision" not in request["properties"] and "decision" not in response["properties"]
    assert request["additionalProperties"] is False


def test_live_api_document_describes_the_same_feedback_contract(client, service_headers):
    response = client.get("/api/v1/production-api", headers=service_headers)
    assert response.status_code == 200
    markdown = response.json()["markdown"]
    assert '"rating": "like"' in markdown and '"decision": "false_positive"' not in markdown
    assert "좋아요·싫어요와 의견을 모두 생략해도 접수한다" in markdown
