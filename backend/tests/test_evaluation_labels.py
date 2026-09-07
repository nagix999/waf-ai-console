"""Reference attachments are synthetic, offline, and never execute a worker."""
import json
import random
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import AccessAudit, Analysis, AnalysisLabel, Review
from app.services.evaluation_labels import MAX_LABEL_FILE_BYTES, confirm_labels, parse_answer_file, LabelAttachmentError
from test_model_profiles import login_admin


def seed(client, event_payload, service_headers, event_id="label-event", verdict="true_positive", **changes):
    response = client.post("/api/v1/analyses", headers=service_headers, json={**event_payload, "event_id": event_id})
    assert response.status_code == 202
    analysis_id = response.json()["id"]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        row.status = "completed"
        row.completed_at = datetime.now(UTC)
        row.verdict = verdict
        row.result_json = {"verdict": verdict, "agent": {"framework": "moduagent", "execution": "standard"}}
        row.model_profile = "synthetic-model"
        row.prompt_version = "synthetic-prompt"
        for key, value in changes.items():
            setattr(row, key, value)
        db.commit()
    return analysis_id


def preview(client, answers=None, **changes):
    fields = {"source_system": "test-parser", "source_kind": "synthetic_expected", "source_ref": "synthetic-v1", "ai_visible": "unknown"}
    fields.update(changes)
    return client.post("/api/v1/evaluation-labels/preview", data=fields, files={
        "file": ("answers.json", json.dumps(answers or [{"event_id": "label-event", "expected_verdict": "true_positive"}]), "application/json"),
    })


def confirm(client, preview_response):
    return client.post("/api/v1/evaluation-labels/confirm", json={"preview_token": preview_response.json()["preview_token"]})


def counts(client):
    with client.app.state.session_factory() as db:
        return db.query(AnalysisLabel).count(), db.query(AccessAudit).count()


def test_preview_is_read_only_confirm_is_append_only_and_exactly_idempotent(client, event_payload, service_headers):
    analysis_id = seed(client, event_payload, service_headers)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        before = {column.name: getattr(row, column.name) for column in Analysis.__table__.columns}
    login_admin(client)
    initial_counts = counts(client)
    first = preview(client, [{
        "event_id": "label-event", "expected_verdict": "true_positive",
        "rationale_ko": "SYNTHETIC_REFERENCE_TEXT_NOT_STORED", "important_evidence": [{"excerpt": "SYNTHETIC_SECRET"}],
    }])
    assert first.status_code == 200
    assert first.json()["can_confirm"] is True
    assert first.json()["change_count"] == 1
    assert first.json()["ai_visible"] is None
    assert "SYNTHETIC_REFERENCE_TEXT_NOT_STORED" not in first.text
    assert counts(client) == initial_counts
    applied = confirm(client, first)
    assert applied.status_code == 200
    assert applied.json()["applied_count"] == 1
    assert applied.json()["duplicate"] is False
    repeated = confirm(client, first)
    assert repeated.status_code == 200 and repeated.json()["duplicate"] is True
    assert repeated.json()["attachment_id"] == applied.json()["attachment_id"]
    assert counts(client) == (1, initial_counts[1] + 1)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        assert {column.name: getattr(row, column.name) for column in Analysis.__table__.columns} == before
        label = db.scalar(select(AnalysisLabel))
        assert label.ai_visible is None
        assert db.query(Review).count() == 0
        assert "SYNTHETIC_REFERENCE_TEXT_NOT_STORED" not in str(label.__dict__)
    detail = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert detail["evaluation"]["outcome"] == "match"
    assert detail["review_state"] == "unreviewed"
    assert detail["evaluation"]["reference_label"]["created_at"].endswith("Z")
    history = client.get(f"/api/v1/analyses/{analysis_id}/evaluation-labels").json()["items"]
    assert history[0]["revision"] == 1 and history[0]["created_by"] == "admin"
    assert history[0]["created_at"].endswith("Z")


def test_noop_is_not_another_label_or_audit(client, event_payload, service_headers):
    seed(client, event_payload, service_headers)
    login_admin(client)
    assert confirm(client, preview(client)).status_code == 200
    before = counts(client)
    unchanged = preview(client)
    assert unchanged.json()["unchanged_count"] == 1 and unchanged.json()["change_count"] == 0
    for _ in range(2):
        response = confirm(client, unchanged)
        assert response.status_code == 200
        assert response.json()["applied_count"] == 0 and response.json()["unchanged_count"] == 1
    assert counts(client) == before


def test_revision_staleness_is_atomic_and_old_retry_does_not_restore_old_label(client, event_payload, service_headers):
    first_id = seed(client, event_payload, service_headers)
    seed(client, event_payload, service_headers, event_id="second-event")
    login_admin(client)
    batch = preview(client, [{"event_id": name, "expected_verdict": "true_positive"} for name in ("label-event", "second-event")])
    competing = preview(client, [{"event_id": "label-event", "expected_verdict": "false_positive"}])
    assert confirm(client, competing).status_code == 200
    stale = confirm(client, batch)
    assert stale.status_code == 409 and stale.json()["detail"] == "label_preview_stale"
    assert counts(client)[0] == 1
    corrected = preview(client)
    assert corrected.json()["rows"][0]["current_revision"] == 1
    assert confirm(client, corrected).status_code == 200
    assert confirm(client, competing).json()["duplicate"] is True
    history = client.get(f"/api/v1/analyses/{first_id}/evaluation-labels").json()["items"]
    assert [(row["revision"], row["verdict"]) for row in history] == [(2, "true_positive"), (1, "false_positive")]


def test_completion_between_preview_and_confirm_is_not_stale(client, event_payload, service_headers):
    analysis_id = seed(client, event_payload, service_headers, status="processing", completed_at=None)
    login_admin(client)
    pending = preview(client)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        row.status = "completed"
        row.completed_at = datetime.now(UTC)
        db.commit()
    assert confirm(client, pending).status_code == 200
    assert client.get(f"/api/v1/analyses/{analysis_id}").json()["evaluation"]["outcome"] == "match"


@pytest.mark.parametrize("changes,code", [
    ({"source_ref": "bad/path"}, "invalid_label_source_ref"),
    ({"source_ref": "bad\nSYNTHETIC_SECRET"}, "invalid_label_source_ref"),
    ({"source_system": " test-parser"}, "invalid_label_source_system"),
])
def test_source_validation_is_static(client, changes, code):
    login_admin(client)
    response = preview(client, **changes)
    assert response.status_code == 422 and response.json() == {"detail": code}
    assert "SYNTHETIC_SECRET" not in response.text


@pytest.mark.parametrize("answers,code", [
    ([{"event_id": "absent", "expected_verdict": "true_positive"}], "analysis_not_found_in_source"),
    ([{"event_id": "label-event", "expected_verdict": "deferred"}], "invalid_reference_verdict"),
    ([{"event_id": "label-event", "expected_verdict": "SYNTHETIC_SECRET"}], "invalid_reference_verdict"),
    ([{"event_id": "label-event", "expected_verdict": "true_positive"}] * 2, "duplicate_answer_event_id"),
    ([{"event_id": ["SYNTHETIC_SECRET"], "expected_verdict": "true_positive"}], "invalid_answer_event_id"),
    (["SYNTHETIC_SECRET"], "answer_row_must_be_object"),
])
def test_preview_errors_disable_confirmation_without_echoing_rows(client, event_payload, service_headers, answers, code):
    seed(client, event_payload, service_headers)
    login_admin(client)
    response = preview(client, answers)
    assert response.status_code == 200
    body = response.json()
    assert body["can_confirm"] is False and body["preview_token"] is None
    assert code in [error["code"] for error in body["errors"]]
    assert "SYNTHETIC_SECRET" not in response.text
    assert counts(client)[0] == 0


def test_reference_kind_cannot_treat_abstention_as_binary_ground_truth(client, event_payload, service_headers):
    seed(client, event_payload, service_headers)
    login_admin(client)
    response = preview(client, [{"event_id": "label-event", "expected_verdict": "inconclusive"}], source_kind="reference")
    assert response.json()["errors"][0]["code"] == "inconclusive_requires_synthetic_expected"
    assert response.json()["preview_token"] is None


def test_scope_must_match_exact_source_and_service_key_cannot_attach(client, event_payload, service_headers):
    analysis_id = seed(client, event_payload, service_headers)
    denied = client.post("/api/v1/evaluation-labels/confirm", headers=service_headers, json={"preview_token": "synthetic"})
    assert denied.status_code == 403
    login_admin(client)
    response = preview(client, source_system="test-parse")
    assert response.json()["matched_count"] == 0
    assert response.json()["errors"][0]["code"] == "analysis_not_found_in_source"
    assert confirm(client, preview(client)).status_code == 200
    client.post("/api/v1/auth/logout")
    assert client.get(f"/api/v1/analyses/{analysis_id}", headers=service_headers).json()["evaluation"]["outcome"] == "match"
    assert client.get(f"/api/v1/analyses/{analysis_id}/evaluation-labels", headers=service_headers).status_code == 403


def test_tampered_expired_and_wrong_actor_tokens_cannot_write(client, event_payload, service_headers, monkeypatch):
    seed(client, event_payload, service_headers)
    login_admin(client)
    response = preview(client)
    token = response.json()["preview_token"]
    assert client.post("/api/v1/evaluation-labels/confirm", json={"preview_token": token + "x"}).status_code == 422
    with client.app.state.session_factory() as db:
        with pytest.raises(LabelAttachmentError, match="^invalid_label_preview$"):
            confirm_labels(db, token=token, actor="different-synthetic-admin", secret=client.app.state.settings.session_secret)
    now = datetime.now(UTC).timestamp()
    monkeypatch.setattr("app.services.evaluation_labels.time.time", lambda: now + 901)
    expired = confirm(client, response)
    assert expired.status_code == 409 and expired.json()["detail"] == "label_preview_expired"
    assert counts(client)[0] == 0


def test_attached_label_and_correction_never_change_primary_or_verifier_inputs(client, event_payload, service_headers):
    from app.agent.input_builder import build_agent_input
    from app.agent.prompts import PRIMARY_INSTRUCTIONS, VERIFIER_INSTRUCTIONS
    from app.services.analysis import fetch_analysis
    from app.services.http_parser import parse_http_payload
    from app.worker import _event_document

    analysis_id = seed(client, event_payload, service_headers)

    def inputs():
        with client.app.state.session_factory() as db:
            row = fetch_analysis(db, analysis_id)
            raw = client.app.state.crypto.decrypt_text(row.payload_ciphertext)
            text = build_agent_input(_event_document(row), raw, parse_http_payload(raw), 32768, 3072).text
            return [
                {"system_instructions": instructions, "user_input": text}
                for instructions in (PRIMARY_INSTRUCTIONS, VERIFIER_INSTRUCTIONS)
            ]

    before = inputs()
    login_admin(client)
    for verdict in ("true_positive", "false_positive"):
        answers = [{"event_id": "label-event", "expected_verdict": verdict, "rationale_ko": "SYNTHETIC_ANSWER_SECRET"}]
        attachment = preview(client, answers, source_ref="SYNTHETIC_REFERENCE_MARKER", ai_visible="true")
        assert confirm(client, attachment).status_code == 200
        after = inputs()
        assert after == before
        assert "SYNTHETIC_REFERENCE_MARKER" not in json.dumps(after)
        assert "SYNTHETIC_ANSWER_SECRET" not in json.dumps(after)


@pytest.mark.parametrize("content,code", [
    (b'{"rows": []}', "label_file_requires_nonempty_array"),
    (b'[]', "label_file_requires_nonempty_array"),
    (b'[{"event_id":"one","event_id":"two"}]', "duplicate_answer_json_key"),
    (b'[{"expected_verdict": NaN}]', "invalid_label_json"),
    (b'\xff', "invalid_label_json"),
    (b'[' * 34 + b']' * 34, "label_json_too_deep"),
    (b' ' * (MAX_LABEL_FILE_BYTES + 1), "label_file_too_large"),
])
def test_bounded_json_parser_safe_failures(content, code):
    with pytest.raises(LabelAttachmentError, match=f"^{code}$"):
        parse_answer_file(content, "answers.json")


@pytest.mark.parametrize("field", ["label", "expected_verdict", "expected_severity", "ground_truth", "evaluation", "reference_label", "difficulty", "case_name"])
def test_new_inline_label_metadata_is_rejected_not_silently_stripped(client, event_payload, service_headers, field):
    response = client.post("/api/v1/analyses", headers=service_headers, json={**event_payload, field: "SYNTHETIC_SECRET"})
    assert response.status_code == 422
    assert "evaluation_labels_require_separate_attachment" in response.text
    assert "SYNTHETIC_SECRET" not in response.text
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == 0


def test_500_max_length_unicode_event_ids_produce_a_confirmable_bounded_token(client):
    rng = random.Random(87213)
    event_ids = ["".join(chr(rng.randrange(0x4E00, 0x9FFF)) for _ in range(255)) for _ in range(500)]
    assert len(set(event_ids)) == 500
    with client.app.state.session_factory() as db:
        db.execute(Analysis.__table__.insert(), [{
            "source_system": "test-parser", "event_id": event_id, "company_name": "Synthetic",
            "src_ip": "192.0.2.1", "dest_ip": "198.51.100.1", "waf_vendor": "generic", "waf_action": "D",
            "payload_ciphertext": "synthetic-encrypted-placeholder", "encryption_key_version": "synthetic",
        } for event_id in event_ids])
        db.commit()
    answers = [{"event_id": event_id, "expected_verdict": "true_positive"} for event_id in event_ids]
    assert len(json.dumps(answers).encode()) < MAX_LABEL_FILE_BYTES
    login_admin(client)
    response = preview(client, answers)
    assert response.status_code == 200 and response.json()["can_confirm"] is True
    assert 262144 < len(response.json()["preview_token"]) <= 2 * 1024 * 1024
    confirmed = confirm(client, response)
    assert confirmed.status_code == 200 and confirmed.json()["applied_count"] == 500
    assert confirm(client, response).json()["duplicate"] is True
