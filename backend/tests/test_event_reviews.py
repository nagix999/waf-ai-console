"""Synthetic event-keyed feedback: isolated, append-only, content-idempotent."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api_key_schemas import ServiceApiKeyCreate
from app.event_api_schemas import EventReviewCreate
from app.models import Analysis, AnalysisLabel, Review
from app.security import Principal, get_principal
from app.services.analysis_access import ServiceAnalysisScope
from app.services.event_reviews import create_event_review, review_content_key
from app.services.service_api_keys import issue_key


CATEGORIES = ["insufficient_evidence", "evidence_mismatch", "explanation_error",
    "exaggerated_impact", "inappropriate_severity", "other"]
PUBLIC_FIELDS = {"event_id", "feedback_categories", "analyst_id", "comment", "created_at"}


def submit(client, event_payload, headers, **query):
    response = client.post("/api/v1/analyses", params={"wait_seconds": 0, **query},
        headers=headers, json=event_payload)
    assert response.status_code == 202, response.text
    return response.json()


def service_key(client, *, source="test-parser", purpose="production", scopes=None):
    with client.app.state.session_factory() as db:
        _, raw = issue_key(db, ServiceApiKeyCreate(name=f"Synthetic event review {uuid.uuid4()}",
            source_system=source, purpose=purpose, scopes=scopes or ["ingest", "review"]),
            "synthetic-fixture")
        db.commit()
    return {"X-API-Key": raw}


@pytest.mark.parametrize("fields", [{}, {"feedback_categories": [], "comment": None},
    *({"feedback_categories": [category]} for category in CATEGORIES),
    {"feedback_categories": CATEGORIES, "analyst_id": "synthetic-analyst", "comment": "원문 근거를 확인해 주세요."},
    {"comment": ""}, {"feedback_categories": ["other"], "comment": ""}])
def test_optional_event_feedback_returns_only_public_fields_and_is_idempotent(
        client, event_payload, service_headers, fields):
    created = submit(client, {**event_payload, "expected_verdict": "true_positive"}, service_headers)
    body = {"event_id": event_payload["event_id"], **fields}
    first = client.post("/api/v1/reviews", headers=service_headers, json=body)
    assert first.status_code == 201, first.text
    assert first.headers["Cache-Control"] == "no-store"
    assert set(first.json()) == PUBLIC_FIELDS
    assert first.json()["created_at"].endswith("Z")
    assert first.json()["feedback_categories"] == fields.get("feedback_categories", [])
    duplicate = client.post("/api/v1/reviews", headers=service_headers, json=body)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.headers["Cache-Control"] == "no-store"
    assert duplicate.json() == first.json()
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 1
        review = db.scalar(select(Review))
        assert review.analysis_id == created["id"]
        assert review.external_review_id.startswith("event-review:v1:")
        assert review.decision is None and review.rating is None
        analysis = db.get(Analysis, created["id"])
        assert analysis.status == "pending" and analysis.verdict is None
        assert db.scalar(select(func.count()).select_from(AnalysisLabel)) == 1


def test_category_order_is_ignored_but_opinion_and_author_changes_append(client, event_payload, service_headers):
    submit(client, event_payload, service_headers)
    base = {"event_id": event_payload["event_id"], "feedback_categories": list(reversed(CATEGORIES)),
        "analyst_id": "analyst-a", "comment": "정상 입력 가능성을 설명해 주세요."}
    response = client.post("/api/v1/reviews", headers=service_headers, json=base)
    assert response.status_code == 201
    assert response.json()["feedback_categories"] == CATEGORIES
    duplicate = client.post("/api/v1/reviews", headers=service_headers,
        json={**base, "feedback_categories": CATEGORIES})
    assert duplicate.status_code == 200 and duplicate.json() == response.json()
    for change in ({"comment": "다른 의견"}, {"analyst_id": "analyst-b"}, {"feedback_categories": ["other"]}):
        assert client.post("/api/v1/reviews", headers=service_headers, json={**base, **change}).status_code == 201
    assert client.post("/api/v1/reviews", headers=service_headers, json=base).status_code == 200
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 4


@pytest.mark.parametrize("fields", [
    {"analysis_id": "internal"}, {"review_id": "internal"}, {"external_review_id": "external"},
    {"ai_visible": False}, {"decision": "true_positive"}, {"rating": "like"}, {"extra": "value"},
    {"comment": []}, {"analyst_id": "x" * 256},
    *({"feedback_categories": value} for value in [None, "other", {}, True, [None],
        [True], [1], [[]], [{}], [""], ["unknown"], ["other", "other"], CATEGORIES + ["other"]]),
])
def test_internal_fields_and_invalid_feedback_are_rejected(client, event_payload, service_headers, fields):
    response = client.post("/api/v1/reviews", headers=service_headers,
        json={"event_id": event_payload["event_id"], **fields})
    assert response.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 0


@pytest.mark.parametrize("body", [{}, {"event_id": ""}, {"event_id": None}, {"event_id": "x" * 256}])
def test_event_id_is_required(client, service_headers, body):
    assert client.post("/api/v1/reviews", headers=service_headers, json=body).status_code == 422


@pytest.mark.parametrize("query", [{"source_system": "other"}, {"analysis_id": "internal"},
    {"ai_visible": "false"}, {"external_review_id": "caller-managed"}])
def test_unknown_query_fields_are_rejected(client, event_payload, service_headers, query):
    response = client.post("/api/v1/reviews", headers=service_headers, params=query,
        json={"event_id": event_payload["event_id"]})
    assert response.status_code == 422 and response.json()["detail"] == "unsupported_review_query"


@pytest.mark.parametrize("field", ["event_id", "analyst_id", "comment"])
def test_invalid_unicode_is_rejected_without_echoing_content(client, event_payload, service_headers, field):
    import json
    body = {"event_id": event_payload["event_id"], field: "private\ud800value"}
    response = client.post("/api/v1/reviews", headers={**service_headers, "Content-Type": "application/json"},
        content=json.dumps(body).encode("ascii"))
    assert response.status_code == 422
    assert "private" not in response.text


def test_service_scope_and_review_permission_are_enforced(client, event_payload, service_headers):
    submit(client, event_payload, service_headers)
    body = {"event_id": event_payload["event_id"]}
    foreign = service_key(client, source="another-source")
    assert client.post("/api/v1/reviews", headers=foreign, json=body).status_code == 404
    missing_scope = service_key(client, scopes=["ingest"])
    assert client.post("/api/v1/reviews", headers=missing_scope, json=body).status_code == 403
    assert client.post("/api/v1/reviews", json=body).status_code == 401
    assert client.post("/api/v1/reviews", headers=service_headers,
        json={"event_id": "missing-event"}).status_code == 404
    invalid_query = client.post("/api/v1/reviews", headers=service_headers,
        params={"test_run_id": "not-production"}, json=body)
    assert invalid_query.status_code == 422
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 0


def test_missing_source_fails_closed(client, event_payload):
    client.app.dependency_overrides[get_principal] = lambda: Principal(kind="service_api_key",
        scopes=frozenset({"review"}))
    try:
        response = client.post("/api/v1/reviews", json={"event_id": event_payload["event_id"]})
        assert response.status_code == 403 and response.json()["detail"] == "source_system_required"
    finally:
        client.app.dependency_overrides.pop(get_principal)


def test_test_reviews_require_explicit_run_if_event_is_ambiguous(client, event_payload, service_headers):
    production = submit(client, event_payload, service_headers)
    test_headers = service_key(client, purpose="test")
    runs = []
    for number in range(2):
        response = client.post("/api/v1/test-sessions", headers=test_headers,
            json={"name": f"Synthetic review test {number}", "idempotency_key": f"review-run-{number}"})
        assert response.status_code == 201, response.text
        runs.append(response.json()["id"])
        submit(client, event_payload, test_headers, test_run_id=runs[-1])
    body = {"event_id": event_payload["event_id"], "comment": "테스트 피드백"}
    ambiguous = client.post("/api/v1/reviews", headers=test_headers, json=body)
    assert ambiguous.status_code == 409 and ambiguous.json()["detail"] == "test_event_ambiguous"
    for run_id in runs:
        response = client.post("/api/v1/reviews", headers=test_headers,
            params={"test_run_id": run_id}, json=body)
        assert response.status_code == 201, response.text
    other = service_key(client, source="other-test-source", purpose="test")
    assert client.post("/api/v1/reviews", headers=other,
        params={"test_run_id": runs[0]}, json=body).status_code == 404
    with client.app.state.session_factory() as db:
        rows = list(db.scalars(select(Review)))
        assert len(rows) == 2 and len({row.analysis_id for row in rows}) == 2
        assert all(row.analysis_id != production["id"] for row in rows)


def test_same_feedback_on_different_events_has_independent_internal_identity(client, event_payload, service_headers):
    for event_id in ("event-one", "event-two"):
        submit(client, {**event_payload, "event_id": event_id}, service_headers)
        assert client.post("/api/v1/reviews", headers=service_headers,
            json={"event_id": event_id, "comment": "동일한 의견"}).status_code == 201
    with client.app.state.session_factory() as db:
        assert len(set(db.scalars(select(Review.external_review_id)))) == 2


def test_compatible_feedback_is_reused_without_rewriting_history(client, event_payload, service_headers):
    created = submit(client, event_payload, service_headers)
    legacy_body = {"external_review_id": "legacy-feedback", "event_id": event_payload["event_id"],
        "feedback_categories": ["other"], "comment": "기존 의견", "ai_visible": True}
    assert client.post(f"/api/v1/analyses/{created['id']}/reviews",
        headers=service_headers, json=legacy_body).status_code == 201
    response = client.post("/api/v1/reviews", headers=service_headers,
        json={key: value for key, value in legacy_body.items() if key not in {"external_review_id", "ai_visible"}})
    assert response.status_code == 200 and set(response.json()) == PUBLIC_FIELDS
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 1
        assert db.scalar(select(Review)).ai_visible is True


def test_historical_decisions_and_ratings_are_never_reused(client, event_payload, service_headers):
    created = submit(client, event_payload, service_headers)
    with client.app.state.session_factory() as db:
        for index, fields in enumerate(({"decision": "deferred"}, {"rating": "like"})):
            db.add(Review(analysis_id=created["id"], source_system="test-parser",
                external_review_id=f"historical-{index}", event_id=event_payload["event_id"], **fields))
        db.commit()
    response = client.post("/api/v1/reviews", headers=service_headers, json={"event_id": event_payload["event_id"]})
    assert response.status_code == 201
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Review)) == 3
        assert db.scalar(select(Review).where(Review.external_review_id == "historical-0")).decision == "deferred"
        assert db.scalar(select(Review).where(Review.external_review_id == "historical-1")).rating == "like"


def test_unique_collision_never_acknowledges_different_content(client, event_payload, service_headers):
    created = submit(client, event_payload, service_headers)
    payload = EventReviewCreate(event_id=event_payload["event_id"], comment="새 의견")
    with client.app.state.session_factory() as db:
        db.add(Review(analysis_id=created["id"], source_system="test-parser", event_id=payload.event_id,
            external_review_id=review_content_key(created["id"], payload), comment="다른 의견"))
        db.commit()
    response = client.post("/api/v1/reviews", headers=service_headers, json=payload.model_dump())
    assert response.status_code == 409 and response.json()["detail"] == "review_content_conflict"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(Review)).comment == "다른 의견"


def test_concurrent_unique_race_returns_winner_after_rollback(client, event_payload, service_headers, monkeypatch):
    submit(client, event_payload, service_headers)
    payload = EventReviewCreate(event_id=event_payload["event_id"], comment="동시 제출")
    with client.app.state.session_factory() as db:
        def commit_with_concurrent_winner():
            pending = next(row for row in db.new if isinstance(row, Review))
            values = {field: getattr(pending, field) for field in ("analysis_id", "source_system",
                "external_review_id", "event_id", "feedback_categories", "analyst_id", "comment")}
            with client.app.state.session_factory() as competitor:
                competitor.add(Review(**values))
                competitor.commit()
            raise IntegrityError("synthetic unique race", {}, Exception("synthetic"))
        monkeypatch.setattr(db, "commit", commit_with_concurrent_winner)
        row, duplicate = create_event_review(db, payload,
            source=ServiceAnalysisScope("test-parser", "production"))
        assert duplicate is True and row.comment == payload.comment
        assert db.scalar(select(func.count()).select_from(Review)) == 1


def test_storage_errors_are_sanitized(client, event_payload, service_headers, monkeypatch):
    from app.api import event_reviews

    def unavailable(*_args, **_kwargs):
        raise OperationalError("PRIVATE RAW CONTENT", {}, Exception("PRIVATE RAW CONTENT"))
    monkeypatch.setattr(event_reviews, "create_event_review", unavailable)
    response = client.post("/api/v1/reviews", headers=service_headers,
        json={"event_id": event_payload["event_id"]})
    assert response.status_code == 503
    assert response.json() == {"detail": "review_storage_unavailable"}


def test_openapi_only_exposes_event_review_contract(client):
    document = client.get("/openapi.json").json()
    request = document["components"]["schemas"]["EventReviewCreate"]
    response = document["components"]["schemas"]["EventReviewResponse"]
    assert request["required"] == ["event_id"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == PUBLIC_FIELDS - {"created_at"}
    assert set(response["properties"]) == PUBLIC_FIELDS
    categories = request["properties"]["feedback_categories"]
    assert categories["items"]["enum"] == CATEGORIES
    assert categories["uniqueItems"] is True and categories["maxItems"] == 6
    responses = document["paths"]["/api/v1/reviews"]["post"]["responses"]
    assert {"200", "201", "403", "404", "409", "422", "503"} <= set(responses)
