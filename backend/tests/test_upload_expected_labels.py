"""Synthetic, offline test-file answers stay outside all event/model documents."""
import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.input_builder import build_agent_input
from app.agent.executor import AgentCallResult
from app.models import AccessAudit, Analysis, AnalysisLabel, AgentRun, AgentStep, VLLMProfile
from app.schemas import AnalysisInput
from app.services.analysis import AnalysisIngestError, enqueue_analysis, event_fingerprint
from app.services.http_parser import parse_http_payload
from app.services.label_fields import LABEL_FIELDS
from app.services.upload_expected_labels import enqueue_test_upload_row
from app.services.uploads import UploadFormatError, extract_test_upload_row, normalize_upload_row, parse_upload
from app.worker import _event_document, process_moduagent, process_stub
from test_model_profiles import login_admin
from test_moduagent_worker import primary_output


def upload(client, rows, *, filename="events.json", path="/api/v1/test-uploads", headers=None, key=None):
    if filename.endswith(".csv"):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        content = output.getvalue()
    else:
        content = json.dumps(rows)
    params = None
    if path == "/api/v1/test-uploads":
        params = {"name": "합성 파일 평가", "idempotency_key": key or hashlib.sha256(content.encode()).hexdigest()}
    return client.post(path, params=params, headers=headers, files={"file": (filename, content, "application/octet-stream")})


@pytest.mark.parametrize("verdict", ["true_positive", "false_positive", "inconclusive"])
@pytest.mark.parametrize("filename", ["events.json", "events.csv"])
def test_answer_attaches_separately_before_any_model_execution(client, event_payload, verdict, filename):
    login_admin(client)
    result = upload(client, [{**event_payload, "expected_verdict": verdict}], filename=filename)
    assert result.status_code == 202
    response = result.json()
    assert (response["accepted"], response["rejected"], response["label_attached"], response["label_unchanged"]) == (1, 0, 1, 0)
    analysis_id = response["analysis_ids"][0]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        label = db.scalar(select(AnalysisLabel))
        assert (label.verdict, label.source_kind, label.source_ref, label.ai_visible, label.created_by) == (
            verdict, "synthetic_expected", "test-upload:expected_verdict", None, "admin",
        )
        assert label.revision == 1
        assert row.analysis_purpose == "test" and row.ingest_channel == "file_upload"
        assert row.event_fingerprint == event_fingerprint(AnalysisInput.model_validate(event_payload).model_dump(mode="json"))
        assert row.extra_fields == {"future_vendor_field": "preserved"}
        raw = client.app.state.crypto.decrypt_text(row.payload_ciphertext)
        assert raw == event_payload["payload"]
        model_document = build_agent_input(_event_document(row), raw, parse_http_payload(raw), 32768, 3072).text
        assert "expected_verdict" not in model_document
        assert "test-upload:expected_verdict" not in model_document
        assert not any(key in _event_document(row)["extra_fields"] for key in LABEL_FIELDS)
        assert db.query(AgentRun).count() == 0
    detail = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert detail["evaluation"]["outcome"] == "pending"
    assert detail["evaluation"]["reference_label"]["verdict"] == verdict
    raw_response = client.get(f"/api/v1/analyses/{analysis_id}/event").json()
    assert "expected_verdict" not in json.dumps(raw_response)


@pytest.mark.parametrize("expected", [None, ""])
@pytest.mark.parametrize("filename", ["events.json", "events.csv"])
def test_empty_answer_is_unlabeled(client, event_payload, expected, filename):
    login_admin(client)
    response = upload(client, [{**event_payload, "expected_verdict": expected}], filename=filename).json()
    assert (response["accepted"], response["label_attached"], response["label_unchanged"]) == (1, 0, 0)
    assert client.get(f"/api/v1/analyses/{response['analysis_ids'][0]}").json()["evaluation"]["outcome"] == "unlabeled"


@pytest.mark.parametrize("expected", ["TRUE_POSITIVE", " true_positive", "true_positive ", "unknown", " ", True, 1, [], {}])
def test_invalid_answer_rejects_row_without_partial_event_or_echo(client, event_payload, expected):
    login_admin(client)
    response = upload(client, [{**event_payload, "expected_verdict": expected}]).json()
    assert (response["accepted"], response["rejected"], response["analysis_ids"]) == (0, 1, [])
    assert response["errors"] == [{"row": 1, "message": "invalid_expected_verdict"}]
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 0


def test_retransmission_is_idempotent_and_new_run_preserves_previous_answer(client, event_payload):
    login_admin(client)
    original = [{**event_payload, "expected_verdict": "true_positive"}]
    first = upload(client, original, key="synthetic-original-upload").json()
    analysis_id = first["analysis_ids"][0]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        before = {column.name: getattr(row, column.name) for column in Analysis.__table__.columns}
    repeated = upload(client, original, key="synthetic-original-upload").json()
    assert repeated["analysis_ids"] == [analysis_id]
    conflict = upload(client, [{**event_payload, "expected_verdict": "false_positive"}], key="synthetic-original-upload")
    assert conflict.status_code == 409
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        assert {column.name: getattr(row, column.name) for column in Analysis.__table__.columns} == before
        assert db.query(AnalysisLabel).count() == 1
        assert db.query(AccessAudit).filter_by(action="attach_test_upload_expected_label").count() == 1
    # Existing explicit confirmation remains the only way to change the answer.
    preview = client.post("/api/v1/evaluation-labels/preview", data={
        "source_system": before["source_system"], "source_kind": "synthetic_expected", "source_ref": "corrected-synthetic", "ai_visible": "unknown",
    }, files={"file": ("answers.json", json.dumps([{"event_id": event_payload["event_id"], "expected_verdict": "false_positive"}]), "application/json")})
    assert preview.status_code == 200 and preview.json()["can_confirm"]
    confirmed = client.post("/api/v1/evaluation-labels/confirm", json={"preview_token": preview.json()["preview_token"]})
    assert confirmed.status_code == 200
    with client.app.state.session_factory() as db:
        assert [label.verdict for label in db.scalars(select(AnalysisLabel).order_by(AnalysisLabel.revision))] == ["true_positive", "false_positive"]
    # A new execution uses a new source and cannot change the original history.
    new = upload(client, original, key="synthetic-new-execution").json()
    assert new["analysis_ids"][0] != analysis_id
    with client.app.state.session_factory() as db:
        labels = list(db.scalars(select(AnalysisLabel).where(AnalysisLabel.analysis_id == analysis_id).order_by(AnalysisLabel.revision)))
        assert [label.verdict for label in labels] == ["true_positive", "false_positive"]


def test_same_verdict_preserves_existing_reference_provenance(client, event_payload):
    login_admin(client)
    upload(client, [{**event_payload, "expected_verdict": "true_positive"}])
    with client.app.state.session_factory() as db:
        label = db.scalar(select(AnalysisLabel))
        label.source_kind, label.source_ref, label.ai_visible = "reference", "synthetic-fixture-reference", False
        db.commit()
    response = upload(client, [{**event_payload, "expected_verdict": "true_positive"}]).json()
    assert len(response["analysis_ids"]) == 1
    with client.app.state.session_factory() as db:
        assert db.query(AnalysisLabel).count() == 1
        label = db.scalar(select(AnalysisLabel))
        assert (label.source_kind, label.source_ref, label.ai_visible) == ("reference", "synthetic-fixture-reference", False)


@pytest.mark.parametrize("field", sorted(LABEL_FIELDS - {"expected_verdict", "difficulty", "test_category", "case_name"}))
def test_other_label_metadata_remains_rejected(client, event_payload, field):
    login_admin(client)
    response = upload(client, [{**event_payload, "expected_verdict": "true_positive", field: "SYNTHETIC_PRIVATE_REFERENCE"}]).json()
    assert response["accepted"] == 0 and response["rejected"] == 1
    assert "SYNTHETIC_PRIVATE_REFERENCE" not in json.dumps(response)
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 0


def test_production_upload_and_direct_inputs_still_reject_answers(client, event_payload, service_headers):
    login_admin(client)
    payload = {**event_payload, "expected_verdict": "true_positive"}
    production = upload(client, [payload], path="/api/v1/uploads", headers=service_headers).json()
    assert production["accepted"] == 0 and production["rejected"] == 1
    for path in ("/api/v1/analyses", "/api/v1/test-analyses"):
        params = {"name": "합성 답안 격리", "idempotency_key": "synthetic-direct-answer"} if path.endswith("/test-analyses") else None
        assert client.post(path, params=params, json=payload, headers=service_headers if path.endswith("/analyses") else None).status_code == 422
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 0


def test_mixed_rows_keep_row_errors_and_continue_after_conflict(client, event_payload):
    login_admin(client)
    response = upload(client, [
        {**event_payload, "expected_verdict": "true_positive"},
        {**event_payload, "expected_verdict": "false_positive"},
        {**event_payload, "event_id": "synthetic-next", "expected_verdict": "inconclusive"},
    ]).json()
    assert (response["accepted"], response["rejected"], response["label_attached"]) == (2, 1, 2)
    assert response["errors"] == [{"row": 2, "message": "expected_verdict_conflict"}]


def test_label_write_failure_rolls_back_event_then_next_row_can_succeed(client, event_payload):
    login_admin(client)
    injected = False

    def fail_once(db, context, instances):
        nonlocal injected
        if not injected and any(isinstance(row, AnalysisLabel) for row in db.new):
            injected = True
            raise IntegrityError(None, None, Exception("synthetic_label_write_failure"))

    event.listen(Session, "before_flush", fail_once)
    try:
        response = upload(client, [
            {**event_payload, "expected_verdict": "true_positive"},
            {**event_payload, "event_id": "synthetic-after-failure", "expected_verdict": "false_positive"},
        ]).json()
    finally:
        event.remove(Session, "before_flush", fail_once)
    assert (response["accepted"], response["rejected"], response["label_attached"]) == (1, 1, 1)
    assert response["errors"] == [{"row": 1, "message": "test_upload_changed_concurrently"}]
    with client.app.state.session_factory() as db:
        assert [row.event_id for row in db.scalars(select(Analysis))] == ["synthetic-after-failure"]
        assert db.query(AnalysisLabel).count() == 1


def test_caller_can_atomically_rollback_multiple_events_and_labels(client, event_payload):
    with client.app.state.session_factory() as db:
        for index in range(2):
            row, duplicate, state = enqueue_test_upload_row(
                db, client.app.state.crypto, "synthetic-benchmark",
                AnalysisInput.model_validate({**event_payload, "event_id": f"batch-{index}"}),
                expected_verdict="true_positive", actor="admin", commit=False,
                label_source_ref="waf-dummy-v1", attachment_id="synthetic-batch-id", ai_visible=False,
            )
            assert state == "attached" and not duplicate
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 2
        assert {label.attachment_id for label in db.scalars(select(AnalysisLabel))} == {"synthetic-batch-id"}
        db.rollback()
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == 0


def test_enqueue_without_commit_does_not_rollback_callers_work_on_error(client, event_payload):
    with client.app.state.session_factory() as db:
        row, _ = enqueue_analysis(db, client.app.state.crypto, "synthetic-batch", AnalysisInput.model_validate(event_payload), commit=False)
        assert row.id
        with pytest.raises(AnalysisIngestError, match="payload_too_large"):
            enqueue_analysis(db, client.app.state.crypto, "synthetic-batch", AnalysisInput.model_validate({**event_payload, "event_id": "synthetic-invalid"}), payload_max_bytes=1, commit=False)
        assert db.query(Analysis).count() == 1
        db.rollback()


@pytest.mark.parametrize("real_verdict,expected,outcome", [
    ("true_positive", "true_positive", "match"),
    ("false_positive", "true_positive", "false_negative"),
    ("inconclusive", "true_positive", "abstained"),
    ("inconclusive", "inconclusive", "expected_abstention_match"),
])
def test_existing_evaluation_semantics_apply_to_automatic_reference(client, event_payload, real_verdict, expected, outcome):
    login_admin(client)
    analysis_id = upload(client, [{**event_payload, "expected_verdict": expected}]).json()["analysis_ids"][0]
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        row.status, row.verdict, row.completed_at = "completed", real_verdict, datetime.now(UTC)
        row.result_json = {"verdict": real_verdict, "agent": {"framework": "moduagent", "execution": "standard"}}
        row.model_profile, row.prompt_version = "synthetic-model", "synthetic-prompt"
        db.commit()
    assert client.get(f"/api/v1/analyses/{analysis_id}").json()["evaluation"]["outcome"] == outcome


def test_stub_is_excluded_and_encrypted_agent_input_has_no_answer(client, event_payload):
    login_admin(client)
    analysis_id = upload(client, [{**event_payload, "expected_verdict": "true_positive"}]).json()["analysis_ids"][0]
    with client.app.state.session_factory() as db:
        process_stub(db, client.app.state.crypto, db.get(Analysis, analysis_id))
        inputs = [client.app.state.crypto.decrypt_text(step.input_ciphertext) for step in db.scalars(select(AgentStep)) if step.input_ciphertext]
        assert inputs and all("expected_verdict" not in value for value in inputs)
    assert client.get(f"/api/v1/analyses/{analysis_id}").json()["evaluation"]["outcome"] == "stub"


def test_primary_and_verifier_receive_no_upload_answer_or_provenance(
    client, event_payload, registered_vllm_target, monkeypatch,
):
    login_admin(client)
    client.app.state.settings.agent_mode = "moduagent"
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(
            name="synthetic-label-isolation", base_url="http://10.0.0.10:8000/v1",
            model_name="synthetic-model", context_window=32768, max_output_tokens=3072,
            status="verified",
        )
        db.add(profile)
        from test_test_runs import record_synthetic_full_pass
        record_synthetic_full_pass(db, profile)
        db.commit()
    analysis_id = upload(client, [{**event_payload, "expected_verdict": "false_positive"}]).json()["analysis_ids"][0]
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        return AgentCallResult(
            output=primary_output("test").model_copy(update={"confidence_score": 0.5}),
            framework_run_id="synthetic-no-network", agent_fingerprint="synthetic-offline",
            finish_reason="completed", failure_id=None, error=None,
            telemetry={"framework": "moduagent", "framework_version": "0.6.2", "tool_trace": []},
        )

    monkeypatch.setattr("app.worker.execute_structured_agent", fake_execute)
    with client.app.state.session_factory() as db:
        process_moduagent(db, client.app.state.crypto, db.get(Analysis, analysis_id), "10.0.0.10:8000", 0.75)
        inputs = [client.app.state.crypto.decrypt_text(step.input_ciphertext) for step in db.scalars(select(AgentStep)) if step.input_ciphertext]
        assert all("expected_verdict" not in value for value in inputs)
    assert [call["agent_name"] for call in calls] == ["waf-primary", "waf-verifier"]
    for call in calls:
        assert "expected_verdict" not in call["user_input"]
        assert "test-upload:expected_verdict" not in call["user_input"]
    detail = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert detail["verdict"] == "true_positive"
    assert detail["evaluation"]["outcome"] == "false_positive"


@pytest.mark.parametrize("filename,content,code", [
    ("events.json", b'[{"expected_verdict":"true_positive","expected_verdict":"false_positive"}]', "duplicate_json_field"),
    ("events.csv", b"event_id,expected_verdict,expected_verdict\nfixture,true_positive,false_positive\n", "duplicate_csv_header"),
])
def test_ambiguous_duplicate_answer_fields_are_rejected(filename, content, code):
    with pytest.raises(UploadFormatError, match=code):
        parse_upload(filename, content)


def test_extraction_does_not_mutate_callers_document(event_payload):
    original = {**event_payload, "expected_verdict": "true_positive"}
    clean, answer = extract_test_upload_row(original)
    assert original["expected_verdict"] == answer == "true_positive"
    assert clean == event_payload


SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "samples" / "waf-dummy-v1"


@pytest.mark.skipif(not SAMPLE_ROOT.is_dir(), reason="repository sample directory not mounted")
@pytest.mark.parametrize("stem,count", [("all_150", 150), ("easy_50", 50), ("medium_50", 50), ("hard_50", 50)])
def test_bundled_samples_exactly_match_answers_and_reupload_without_label_leak(client, stem, count):
    reference = [answer for tier in ("easy", "medium", "hard") for answer in json.loads((SAMPLE_ROOT / "reference" / f"{tier}_answers.json").read_text())]
    answers = {answer["event_id"]: answer["expected_verdict"] for answer in reference}
    all_events = {row["event_id"]: row for row in json.loads((SAMPLE_ROOT / "all_150.json").read_text())}
    rows = parse_upload(f"{stem}.json", (SAMPLE_ROOT / f"{stem}.json").read_bytes())
    assert len(answers) == 150 and len(rows) == count and len({row["event_id"] for row in rows}) == count
    for row in rows:
        assert row["expected_verdict"] == answers[row["event_id"]]
        assert row == all_events[row["event_id"]]
        clean, _ = extract_test_upload_row(row)
        for key in ("difficulty", "test_category", "case_name"):
            clean.pop(key)
        assert set(clean) == set(AnalysisInput.model_fields)
        AnalysisInput.model_validate(normalize_upload_row(clean))
    login_admin(client)
    first, second = upload(client, rows).json(), upload(client, rows).json()
    assert (first["accepted"], first["rejected"], first["label_attached"]) == (count, 0, count)
    assert second["analysis_ids"] == first["analysis_ids"]
    with client.app.state.session_factory() as db:
        assert db.query(Analysis).count() == db.query(AnalysisLabel).count() == count
        assert all(row.extra_fields == {} for row in db.scalars(select(Analysis)))
