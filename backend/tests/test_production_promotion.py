"""Release gate uses only synthetic data and local, mocked completions."""
import pytest
from sqlalchemy import func, select, text
from app.models import ChangeEvent, ProductionPromotion, VLLMProfile
from test_candidate_configurations import candidate
from test_official_evaluations import approved_item, execute, finalize
from test_test_retries import finish
from test_validation_data import create_dataset

pytestmark = pytest.mark.usefixtures("registered_vllm_target")
BASE = "/api/v1/admin/production-configurations"


def qualified(client, event_payload, candidate):
    dataset = create_dataset(client)
    approved_item(client, dataset, {**event_payload, "vendor_score": 2}, "true_positive")
    run = execute(client, dataset, candidate)
    finish(client, run["items"][0]["analysis_id"])
    finalize(client, run)
    return run


def test_atomic_promotion_and_legacy_write_boundaries(client, event_payload, candidate):
    run = qualified(client, event_payload, candidate)
    baseline = client.get(BASE).json()
    response = client.get(f"{BASE}/preflight/{run['id']}")
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["eligible"], review["checks"]
    body = {"candidate_test_run_id": run["id"], "expected_production_configuration_hash": baseline["configuration_hash"]}
    assert client.post(f"{BASE}/promote", json=body).json()["detail"] == "schema_change_ack_required"
    before_roles = client.get("/api/v1/admin/agent-settings").json()
    response = client.post(f"{BASE}/promote", json={**body, "acknowledge_schema_change": True})
    assert response.status_code == 200, response.text
    assert response.json()["configuration_hash"] == run["configuration_hash"]
    current = client.get(BASE).json()
    assert current["snapshot"] == run["configuration_snapshot"]
    assert not current["drifted"]
    assert current["configuration_id"] == response.json()["id"]
    after_roles = client.get("/api/v1/admin/agent-settings").json()
    assert after_roles["assignments"]["test"] == before_roles["assignments"]["test"]
    assert client.post(f"{BASE}/promote", json={**body, "acknowledge_schema_change": True}).json()["detail"] == "production_configuration_changed"
    assert client.post(f"/api/v1/model-profiles/{candidate['primary_profile_id']}/promote").json()["detail"] == "production_promotion_required"
    assert client.post(f"/api/v1/model-profiles/{candidate['primary_profile_id']}/disable").status_code == 409
    changed = {**after_roles["assignments"], "production": {**after_roles["assignments"]["production"], "primary_profile_id": None}}
    assert client.put("/api/v1/admin/agent-settings", json={"expected_state_token": after_roles["state_token"], **changed}).json()["detail"] == "production_promotion_required"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ProductionPromotion)) == 2
        assert db.scalar(select(func.count()).select_from(ChangeEvent).where(ChangeEvent.category == "promotion")) == 1


@pytest.mark.parametrize("change", ["fingerprint", "disabled", "missing_evaluation", "snapshot", "incomplete"])
def test_rejects_invalid_qualification_without_partial_mutations(client, event_payload, candidate, change):
    from app.models import TestRun, TestEvaluation, Analysis
    run = qualified(client, event_payload, candidate)
    before = client.get(BASE).json()
    with client.app.state.session_factory() as db:
        saved = db.get(TestRun, run["id"])
        if change == "fingerprint":
            db.get(VLLMProfile, candidate["verifier_profile_id"]).max_output_tokens += 1
        elif change == "disabled":
            db.get(VLLMProfile, candidate["evidence_editor_profile_id"]).status = "disabled"
        elif change == "missing_evaluation":
            for record in db.scalars(select(TestEvaluation).where(TestEvaluation.test_run_id == saved.id)):
                db.delete(record)
        elif change == "snapshot":
            saved.configuration_hash = "a" * 64
        else:
            db.get(Analysis, run["items"][0]["analysis_id"]).status = "processing"
        db.commit()
    response = client.post(f"{BASE}/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 409, response.text
    assert client.get(BASE).json()["snapshot"] == before["snapshot"]


def test_runtime_unknown_and_content_free_change_history(client, candidate):
    response = client.get("/api/v1/admin/runtime/status?window=1h")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["health"]["analysis_worker"]["status"] == "unknown"
    assert body["health"]["assigned_models"]["status"] == "unknown"
    assert body["latency_summary"]["p95"] is None
    assert "CANDIDATE-INSTRUCTIONS-FIXTURE" not in response.text
    assert "profile-secret" not in client.get("/api/v1/admin/activity").text


def test_failed_transaction_rolls_back_all_five_components(client, event_payload, candidate, monkeypatch):
    run = qualified(client, event_payload, candidate)
    before = client.get(BASE).json()
    before_roles = client.get("/api/v1/admin/agent-settings").json()
    def fail_schema(*args, **kwargs):
        raise RuntimeError("synthetic-transaction-failure")
    monkeypatch.setattr("app.services.production_configurations.activate_schema_version", fail_schema)
    with pytest.raises(RuntimeError, match="synthetic-transaction-failure"):
        client.post(f"{BASE}/promote", json={"candidate_test_run_id": run["id"],
            "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert client.get(BASE).json() == before
    assert client.get("/api/v1/admin/agent-settings").json() == before_roles
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ProductionPromotion).where(ProductionPromotion.kind == "promotion")) == 0
        assert db.scalar(select(func.count()).select_from(ChangeEvent).where(ChangeEvent.category == "promotion")) == 0


def test_promotion_updates_live_contract_and_preserves_old_execution_snapshots(client, event_payload, candidate):
    from app.models import Analysis
    run = qualified(client, event_payload, candidate)
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        pinned = (analysis.prompt_snapshot_ciphertext, analysis.input_schema_snapshot_ciphertext)
    before = client.get(BASE).json()
    response = client.post(f"{BASE}/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 200
    assert client.get("/api/v1/admin/input-schemas").json()["active_version_id"] == candidate["input_schema_version_id"]
    assert client.get("/api/v1/admin/prompt-policies").json()["active_version_id"] == candidate["prompt_policy_version_id"]
    assert client.post("/api/v1/analyses", json={**event_payload, "event_id": "requires-new-field"}).status_code == 422
    assert client.post("/api/v1/analyses", json={**event_payload, "event_id": "new-contract-ok", "vendor_score": 4}).status_code == 202
    with client.app.state.session_factory() as db:
        analysis = db.get(Analysis, run["items"][0]["analysis_id"])
        assert (analysis.prompt_snapshot_ciphertext, analysis.input_schema_snapshot_ciphertext) == pinned
    evaluation = client.get(BASE + "/evaluations").json()["items"][0]
    assert evaluation["summary"]["breakdowns"]["test_category"][0]["evaluation_summary"]["evaluable"] == 1


def test_test_default_update_omits_production_and_does_not_mutate_it(client, candidate):
    before = client.get(BASE).json()
    current = client.get("/api/v1/admin/agent-settings").json()
    response = client.put("/api/v1/admin/agent-settings", json={"expected_state_token": current["state_token"],
        "test": {"primary_profile_id": candidate["primary_profile_id"], "verifier_profile_id": None}})
    assert response.status_code == 200, response.text
    assert client.get(BASE).json()["snapshot"] == before["snapshot"]


def test_observed_worker_health_expires_and_does_not_assert_model_readiness(client, candidate):
    from datetime import timedelta
    from app.models import WorkerHeartbeat, utcnow
    from app.services.runtime_status import heartbeat
    heartbeat(client.app.state.session_factory, "fixture-worker", "both")
    result = client.get("/api/v1/admin/runtime/status").json()
    assert result["health"]["analysis_worker"]["status"] == "healthy"
    assert result["health"]["model_worker"]["status"] == "healthy"
    assert result["health"]["assigned_models"]["status"] == "unknown"
    with client.app.state.session_factory() as db:
        db.get(WorkerHeartbeat, "fixture-worker").observed_at = utcnow() - timedelta(seconds=90)
        db.commit()
    assert client.get("/api/v1/admin/runtime/status").json()["health"]["analysis_worker"]["status"] == "stale"


def test_new_admin_reads_reject_service_keys_and_missing_csrf(client, candidate, service_headers):
    client.cookies.clear()
    for path in (BASE, "/api/v1/admin/runtime/status", "/api/v1/admin/activity"):
        assert client.get(path, headers=service_headers).status_code in (401, 403)
    from test_validation_data import login
    login(client)
    client.headers.pop("origin", None)
    response = client.post(BASE + "/promote", json={"candidate_test_run_id": candidate["primary_profile_id"],
        "expected_production_configuration_hash": "a" * 64})
    assert response.status_code == 403


@pytest.mark.parametrize("change", ["credential", "egress", "threshold", "pending", "evaluation_scope"])
def test_preflight_rechecks_changed_runtime_and_evaluation_evidence(client, candidate, event_payload, change):
    from app.models import InternalEgressTarget, TestRun, TestEvaluation
    run = qualified(client, event_payload, candidate)
    before = client.get(BASE).json()
    with client.app.state.session_factory() as db:
        if change == "credential":
            db.get(VLLMProfile, candidate["verifier_profile_id"]).api_key_ciphertext = "corrupt-synthetic-ciphertext"
        elif change == "egress":
            db.delete(db.scalar(select(InternalEgressTarget)))
        elif change == "threshold":
            client.app.state.settings.verifier_confidence_threshold = 0.91
        elif change == "pending":
            db.get(TestRun, run["id"]).official_evaluation_pending = True
        else:
            evaluation = db.scalar(select(TestEvaluation).where(TestEvaluation.test_run_id == run["id"]))
            evaluation.summary_json = {**evaluation.summary_json, "ground_truth": {"membership_hash": "changed"}}
        db.commit()
    preflight = client.get(f"{BASE}/preflight/{run['id']}")
    assert preflight.status_code == 200, preflight.text
    assert not preflight.json()["eligible"]
    current = client.get(BASE).json()
    response = client.post(BASE + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": current["configuration_hash"], "acknowledge_schema_change": True})
    assert response.status_code == 409
    assert response.json()["detail"] == "candidate_not_eligible_for_promotion"
    assert client.get(BASE).json()["configuration_id"] == before["configuration_id"]


def test_runtime_key_filter_and_activity_redaction(client, event_payload):
    from test_service_api_keys import login, logout, issue, headers, BASE as KEYS
    login(client)
    first = issue(client, name="first", source="first")
    second = issue(client, name="second", source="second")
    renamed = client.patch(f"{KEYS}/{first['item']['id']}", json={"name": "renamed"})
    assert renamed.status_code == 200, renamed.text
    logout(client)
    first_analysis = client.post("/api/v1/analyses", json=event_payload, headers=headers(first)).json()
    client.post("/api/v1/analyses", json=event_payload, headers=headers(second))
    login(client)
    runtime = client.get(f"/api/v1/admin/runtime/status?purpose=production&service_api_key_id={first['item']['id']}")
    assert runtime.status_code == 200, runtime.text
    assert runtime.json()["request_count"] == 1
    assert runtime.json()["recent_analyses"][0]["id"] == first_analysis["id"]
    assert client.get(f"/api/v1/admin/runtime/status?purpose=test&service_api_key_id={first['item']['id']}").status_code == 404
    activity = client.get("/api/v1/admin/activity?category=integration")
    change = next(row for row in activity.json()["items"] if row["action"] == "rename_service_api_key")
    assert change["before"]["name"] == "first" and change["after"]["name"] == "renamed"
    assert change["actor"] == "admin"
    for secret in (first["api_key"], second["api_key"], "session=fixture", "GET /search", "future_vendor_field"):
        assert secret not in activity.text and secret not in runtime.text
    assert activity.headers["cache-control"] == "no-store"


def test_concurrent_promotions_have_one_winner_and_duplicate_confirmation_is_idempotent(client, candidate, event_payload, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import sqlite3
    from app.api.production_configurations import PromotionRequest
    from app.database import build_engine, build_session_factory
    from app.services.analysis import AnalysisIngestError
    from app.services.production_configurations import promote, current_configuration
    run = qualified(client, event_payload, candidate)
    before = client.get(BASE).json()
    # Copy only this test's synthetic in-memory DB to a temporary WAL database.
    filename = tmp_path / "concurrent-promotions.db"
    source = client.app.state.engine.raw_connection()
    with sqlite3.connect(filename) as target:
        source.driver_connection.backup(target)
    source.close()
    engine = build_engine(f"sqlite+pysqlite:///{filename}")
    sessions = build_session_factory(engine)
    body = PromotionRequest(candidate_test_run_id=run["id"],
        expected_production_configuration_hash=before["configuration_hash"], acknowledge_schema_change=True)
    barrier = Barrier(2)
    def release():
        with sessions() as db:
            barrier.wait()
            try:
                return promote(db, client.app.state.crypto, client.app.state.settings, body, "synthetic-admin")
            except AnalysisIngestError as exc:
                db.rollback()
                return {"error": exc.code}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: release(), range(2)))
    assert sum("id" in result for result in results) == 1, results
    assert {result.get("error") for result in results} == {None, "production_configuration_changed"}
    with sessions() as db:
        current = current_configuration(db, client.app.state.crypto, client.app.state.settings)
        body.expected_production_configuration_hash = current["configuration_hash"]
        repeated = promote(db, client.app.state.crypto, client.app.state.settings, body, "synthetic-admin")
        assert repeated["id"] == current["configuration_id"]
        assert db.scalar(select(func.count()).select_from(ProductionPromotion).where(ProductionPromotion.kind == "promotion")) == 1
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
