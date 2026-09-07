"""Independent named-run boundary review using synthetic, local-only inputs."""
import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import Analysis, AnalysisLabel, TestRun as RunModel, TestRunItem as ItemModel
from test_evaluation_labels import confirm, preview
from test_model_profiles import login_admin


def direct(client, event_payload, key="synthetic-review-direct", **metadata):
    return client.post("/api/v1/test-runs", json={
        "name": "합성 경계 검토", "idempotency_key": key, "event": event_payload, **metadata,
    })


def upload(client, rows, key="synthetic-review-upload"):
    return client.post("/api/v1/test-runs/uploads", data={
        "name": "합성 파일 경계 검토", "idempotency_key": key,
    }, files={"file": ("synthetic.json", json.dumps(rows), "application/json")})


def test_admin_only_and_service_source_cannot_read_named_test_analysis(client, event_payload, service_headers):
    assert client.get("/api/v1/test-runs").status_code == 401
    assert direct(client, event_payload).status_code == 401
    assert client.get("/api/v1/test-runs", headers=service_headers).status_code == 403
    assert client.post("/api/v1/test-runs", headers=service_headers, json={
        "name": "합성 권한 검토", "idempotency_key": "synthetic-denied", "event": event_payload,
    }).status_code == 403
    login_admin(client)
    response = direct(client, event_payload)
    assert response.status_code == 202, response.text
    run = response.json()
    analysis_id = run["items"][0]["analysis_id"]
    assert run["source_system"].startswith("waf-internal-test-run-")
    denied_key = client.post("/api/v1/admin/service-api-keys", json={
        "name": "금지된 테스트 source", "source_system": run["source_system"], "scopes": ["ingest"],
    })
    assert denied_key.status_code == 422
    client.post("/api/v1/auth/logout")
    assert client.get(f"/api/v1/test-runs/{run['id']}", headers=service_headers).status_code == 403
    assert client.get(f"/api/v1/analyses/{analysis_id}", headers=service_headers).status_code == 404
    listed = client.get("/api/v1/analyses", params={"test_run_id": run["id"]}, headers=service_headers)
    assert listed.status_code == 200 and listed.json()["total"] == 0


def test_reference_and_test_metadata_are_not_event_or_prompt_input(client, event_payload):
    login_admin(client)
    metadata = {"difficulty": "SYNTHETIC_DIFFICULTY_ONLY", "test_category": "SYNTHETIC_CATEGORY_ONLY",
                "case_name": "SYNTHETIC_CASE_ONLY", "expected_verdict": "true_positive"}
    response = direct(client, event_payload, **metadata)
    assert response.status_code == 202, response.text
    item = response.json()["items"][0]
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, item["analysis_id"])
        assert analysis.extra_fields == {"future_vendor_field": "preserved"}
        assert client.app.state.crypto.decrypt_text(analysis.payload_ciphertext) == event_payload["payload"]
        prompt = client.app.state.crypto.decrypt_text(analysis.prompt_snapshot_ciphertext)
        for marker in (metadata["difficulty"], metadata["test_category"], metadata["case_name"]):
            assert marker not in prompt
        label = db.scalar(select(AnalysisLabel).where(AnalysisLabel.analysis_id == analysis.id))
        assert label.verdict == "true_positive" and label.ai_visible is None
        for key in ("difficulty", "test_category", "case_name"):
            assert item[key] == metadata[key]


def test_idempotent_retry_and_new_run_do_not_duplicate_or_reuse_old_execution(client, event_payload):
    login_admin(client)
    first = direct(client, event_payload)
    assert first.status_code == 202, first.text
    first = first.json()
    duplicate = direct(client, event_payload)
    assert duplicate.status_code == 202 and duplicate.json()["id"] == first["id"]
    changed = direct(client, {**event_payload, "payload": event_payload["payload"] + "synthetic changed"})
    assert changed.status_code == 409
    second = direct(client, event_payload, key="synthetic-review-second")
    assert second.status_code == 202, second.text
    assert second.json()["id"] != first["id"]
    assert second.json()["items"][0]["analysis_id"] != first["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(RunModel)) == 2
        assert db.scalar(select(func.count()).select_from(Analysis)) == 2
        assert db.scalar(select(func.count()).select_from(ItemModel)) == 2


def test_duplicate_rows_preserve_line_history_without_inflating_quality_sample(client, event_payload):
    login_admin(client)
    row = {**event_payload, "difficulty": "easy", "expected_verdict": "true_positive"}
    response = upload(client, [row, row])
    assert response.status_code == 202, response.text
    run = response.json()
    assert (run["total"], run["accepted"], run["duplicates"], run["rejected"]) == (2, 1, 1, 0)
    assert run["evaluation_summary"]["total"] == 1
    assert len(run["items"]) == 2 and run["items"][0]["analysis_id"] == run["items"][1]["analysis_id"]
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        analysis.status, analysis.verdict = "completed", "true_positive"
        analysis.completed_at = datetime.now(UTC)
        analysis.model_profile, analysis.prompt_version = "synthetic-model", "synthetic-prompt"
        analysis.result_json = {"verdict": "true_positive", "agent": {"framework": "moduagent"}}
        db.commit()
    summary = client.get(f"/api/v1/test-runs/{run['id']}").json()["evaluation_summary"]
    assert summary["confusion_matrix"]["tp"] == summary["binary_evaluable"] == 1


def test_invalid_port_is_a_rejected_row_not_a_rollback_of_valid_rows(client, event_payload):
    login_admin(client)
    response = upload(client, [event_payload, {**event_payload, "event_id": "bad-port", "src_port": "not-a-port"}])
    assert response.status_code == 202, response.text
    result = response.json()
    assert (result["accepted"], result["rejected"]) == (1, 1)
    assert result["items"][1]["error_code"] == "invalid_test_event"
    assert "not-a-port" not in response.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1


def test_nonfinite_json_is_rejected_safely_without_an_incomplete_run(client, event_payload):
    login_admin(client)
    response = upload(client, [{**event_payload, "future_vendor_field": float("nan")}])
    assert response.status_code == 422, response.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(RunModel)) == 0
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0


def test_later_label_does_not_score_a_run_whose_initial_reference_was_missing(client, event_payload):
    login_admin(client)
    response = direct(client, event_payload)
    assert response.status_code == 202, response.text
    run = response.json()
    analysis_id = run["items"][0]["analysis_id"]
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, analysis_id)
        analysis.status, analysis.verdict = "completed", "true_positive"
        analysis.completed_at = datetime.now(UTC)
        analysis.model_profile, analysis.prompt_version = "synthetic-model", "synthetic-prompt"
        analysis.result_json = {"verdict": "true_positive", "agent": {"framework": "moduagent"}}
        db.commit()
    attached = confirm(client, preview(client, [{"event_id": event_payload["event_id"], "expected_verdict": "true_positive"}],
                                      source_system=run["source_system"]))
    assert attached.status_code == 200, attached.text
    frozen = client.get(f"/api/v1/test-runs/{run['id']}").json()["evaluation_summary"]
    assert frozen["labeled"] == frozen["binary_evaluable"] == 0
    assert frozen["outcomes"]["unlabeled"] == 1
    latest = client.get(f"/api/v1/analyses/{analysis_id}").json()["evaluation"]
    assert latest["outcome"] == "match"
