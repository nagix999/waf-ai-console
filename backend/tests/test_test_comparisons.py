"""Synthetic, network-free paired evaluation and metadata-only read contract."""
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from app.models import Analysis, AnalysisLabel, AgentRun, AgentStep, TestRun as NamedRun, VLLMProfile, utcnow
from app.services.test_comparisons import _step_usage


@pytest.fixture
def comparison_client(client):
    # Keep the new router independently testable before application integration.
    from app.api.test_comparisons import router
    if not any(route.path == "/api/v1/test-runs/{candidate_id}/comparison" for route in client.app.routes):
        client.app.include_router(router, prefix="/api/v1")
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200
    return client


def submit(client, event_payload, references, *, event_changes=None, metadata_changes=None):
    rows = [{**event_payload, "event_id": f"synthetic-{i}", "expected_verdict": reference,
             "difficulty": "easy", "test_category": "synthetic", "case_name": f"합성 {i}",
             **(event_changes or {}).get(i, {}), **(metadata_changes or {}).get(i, {})}
            for i, reference in enumerate(references)]
    response = client.post("/api/v1/test-runs/uploads", data={"name": "합성 후보 비교",
        "idempotency_key": str(uuid.uuid4())}, files={"file": ("synthetic.json", json.dumps(rows).encode())})
    assert response.status_code == 202, response.text
    return response.json()


def finish(client, run, predictions, *, states=None):
    with client.app.state.session_factory() as db:
        profile = db.scalar(select(VLLMProfile).where(VLLMProfile.name == "synthetic-comparison"))
        if profile is None:
            profile = VLLMProfile(name="synthetic-comparison", base_url="http://10.0.0.10:8000/v1",
                                  model_name="synthetic-model", status="verified")
            db.add(profile)
            db.flush()
        saved = db.get(NamedRun, run["id"])
        saved.execution_mode = "moduagent"
        saved.profile_id = profile.id
        saved.profile_fingerprint = "a" * 64
        saved.profile_metadata = {"model_profile_id": profile.id, "model_name": "synthetic-model",
                                  "profile_fingerprint": "a" * 64, "verifier_confidence_threshold": .75}
        for i, prediction in enumerate(predictions):
            row = db.get(Analysis, run["items"][i]["analysis_id"])
            row.status = (states or {}).get(i, "completed")
            row.started_at = utcnow() - timedelta(seconds=i + 1)
            row.completed_at = utcnow() if row.status in {"completed", "failed"} else None
            row.verdict = prediction
            row.model_profile = "synthetic-model"
            row.result_json = {"verdict": prediction, "agent": {"framework": "moduagent",
                "model_profile_id": profile.id, "profile_fingerprint": "a" * 64},
                "policy": {"fixed_rules_hash": "f" * 64, "verifier_confidence_threshold": .75}}
        db.commit()


def compare(client, baseline, candidate, **params):
    response = client.get(f"/api/v1/test-runs/{candidate['id']}/comparison",
                          params={"baseline_id": baseline["id"], **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_paired_metrics_expected_hold_and_pagination(comparison_client, event_payload):
    refs = ["true_positive", "false_positive", "inconclusive", "true_positive"]
    baseline = submit(comparison_client, event_payload, refs)
    candidate = submit(comparison_client, event_payload, refs)
    finish(comparison_client, baseline, ["false_positive", "false_positive", "true_positive", "true_positive"])
    finish(comparison_client, candidate, ["true_positive", "true_positive", "inconclusive", "inconclusive"])
    result = compare(comparison_client, baseline, candidate, limit=1, offset=1)
    assert result["counts"] == {"accepted_pairs": 4, "comparable_pairs": 4, "changed": 4,
                                 "improved": 2, "regressed": 2, "exclusions": {}}
    assert len(result["items"]) == 1 and result["total_items"] == 4
    assert result["baseline_evaluation"]["binary_evaluable"] == 3
    assert result["candidate_evaluation"]["confusion_matrix"] == {
        "tp": 1, "fn": 0, "fp": 1, "tn": 0, "abstained_positive": 1, "abstained_negative": 0}
    assert result["candidate_evaluation"]["outcomes"]["expected_abstention_match"] == 1
    assert result["performance"]["scope"] == "comparable_pairs_all_recorded_attempts"


def test_fixed_labels_not_latest_and_duplicate_uploads_do_not_add_support(comparison_client, event_payload):
    baseline = submit(comparison_client, event_payload, ["true_positive", "true_positive"],
                      event_changes={1: {"event_id": "synthetic-0", "case_name": "합성 0"}})
    candidate = submit(comparison_client, event_payload, ["true_positive"])
    finish(comparison_client, baseline, ["true_positive"])
    finish(comparison_client, candidate, ["true_positive"])
    with comparison_client.app.state.session_factory() as db:
        db.add(AnalysisLabel(analysis_id=baseline["items"][0]["analysis_id"], revision=2,
            verdict="false_positive", source_kind="reference", source_ref="synthetic-correction",
            ai_visible=False, created_by="fixture", attachment_id=str(uuid.uuid4()), token_digest="d" * 64))
        db.commit()
    result = compare(comparison_client, baseline, candidate)
    assert result["counts"]["comparable_pairs"] == 1
    assert result["baseline_evaluation"]["matches"] == 1
    assert result["baseline"]["duplicates"] == 1


def test_equivalence_uses_event_fingerprint_not_file_hash_or_order(comparison_client, event_payload):
    baseline = submit(comparison_client, event_payload, ["true_positive", "false_positive"])
    candidate = submit(comparison_client, event_payload, ["false_positive", "true_positive"],
        event_changes={0: {"event_id": "synthetic-1"}, 1: {"event_id": "synthetic-0"}})
    finish(comparison_client, baseline, ["true_positive", "false_positive"])
    finish(comparison_client, candidate, ["false_positive", "true_positive"])
    result = compare(comparison_client, baseline, candidate)
    assert result["counts"]["comparable_pairs"] == 2
    assert result["counts"]["changed"] == 0
    assert "case_metadata_different" in result["warnings"]
    filtered = compare(comparison_client, baseline, candidate, changes_only=True)
    assert filtered["total_items"] == 0
    assert filtered["baseline_evaluation"]["total"] == 2


def test_mismatched_inputs_references_missing_and_failed_are_excluded(comparison_client, event_payload):
    refs = ["true_positive"] * 6
    baseline = submit(comparison_client, event_payload, refs)
    candidate = submit(comparison_client, event_payload, ["true_positive", "false_positive", *refs[2:]],
        event_changes={0: {"payload": "GET /different HTTP/1.1\r\n\r\n"}, 4: {"event_id": "candidate-only"}})
    finish(comparison_client, baseline, refs)
    finish(comparison_client, candidate, refs, states={2: "failed"})
    with comparison_client.app.state.session_factory() as db:
        db.get(Analysis, candidate["items"][3]["analysis_id"]).event_fingerprint = None
        db.commit()
    result = compare(comparison_client, baseline, candidate)
    assert result["counts"]["exclusions"] == {"input_mismatch": 1, "reference_mismatch": 1,
        "candidate_not_evaluable": 1, "fingerprint_missing": 1, "candidate_missing": 1, "baseline_missing": 1}
    assert result["counts"]["comparable_pairs"] == 1
    assert result["baseline_evaluation"]["total"] == result["candidate_evaluation"]["total"] == 1
    assert result["candidate"]["failed"] == 1


@pytest.mark.parametrize("field,value", [("source_kind", "reference"), ("ai_visible", True)])
def test_reference_provenance_must_match(comparison_client, event_payload, field, value):
    baseline = submit(comparison_client, event_payload, ["true_positive"])
    candidate = submit(comparison_client, event_payload, ["true_positive"])
    finish(comparison_client, baseline, ["true_positive"])
    finish(comparison_client, candidate, ["true_positive"])
    with comparison_client.app.state.session_factory() as db:
        label = db.scalar(select(AnalysisLabel).where(AnalysisLabel.analysis_id == candidate["items"][0]["analysis_id"]))
        setattr(label, field, value)
        db.commit()
    result = compare(comparison_client, baseline, candidate)
    assert result["counts"]["exclusions"] == {"reference_mismatch": 1}


def test_retry_usage_has_no_double_count_and_includes_recorded_prior_runs(comparison_client, event_payload):
    baseline = submit(comparison_client, event_payload, ["true_positive"])
    candidate = submit(comparison_client, event_payload, ["true_positive"])
    finish(comparison_client, baseline, ["true_positive"])
    finish(comparison_client, candidate, ["true_positive"])
    identifier = candidate["items"][0]["analysis_id"]
    with comparison_client.app.state.session_factory() as db:
        for index, metadata in enumerate([
            {"usage": {"input_tokens": 99999, "output_tokens": 99999, "total_tokens": 99999},
             "output_validation_retry": {"attempted": True, "attempt_count": 2, "attempts": [
                 {"usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}},
                 {"usage": {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220}}]}},
            {"usage": {"input_tokens": 3, "output_tokens": None, "total_tokens": None}},
        ]):
            run = AgentRun(analysis_id=identifier, status="completed" if index == 0 else "failed")
            db.add(run)
            db.flush()
            db.add(AgentStep(run_id=run.id, sequence=1, step_type="llm_primary", name="합성",
                status="completed" if index == 0 else "failed", metadata_json={**metadata,
                    "timing_measured": True, "duration_ms": 1000}, completed_at=utcnow()))
        db.commit()
    result = compare(comparison_client, baseline, candidate)
    performance = result["performance"]["candidate"]
    assert performance["tokens"]["input_tokens"] == {"known_sum": 303, "measured_steps": 2, "missing_steps": 0}
    assert performance["tokens"]["total_tokens"] == {"known_sum": 330, "measured_steps": 1, "missing_steps": 1}
    assert performance["output_repair_steps"] == 1
    assert performance["llm_step_ms"]["sum_ms"] == 2000
    assert result["performance"]["baseline"]["missing_agent_histories"] == 1
    assert "token_usage_incomplete" in result["warnings"]


@pytest.mark.parametrize("metadata", [
    {}, {"usage": "LegacyDataclassRepresentation"},
    {"usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
    {"usage": {"input_tokens": True, "output_tokens": False, "total_tokens": True}},
    {"usage": {"input_tokens": False, "output_tokens": False, "total_tokens": False}},
    {"usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
     "output_validation_retry": {"attempted": True, "attempt_count": 2}},
])
def test_missing_or_incomplete_retry_usage_is_not_zero(metadata):
    assert _step_usage(metadata) == {"input_tokens": None, "output_tokens": None, "total_tokens": None}


def test_partial_retry_step_does_not_claim_last_attempt_as_whole_usage():
    metadata = {"usage": {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22},
        "output_validation_retry": {"attempted": True, "attempt_count": 2, "attempts": [
            {"usage": {"input_tokens": None, "output_tokens": 1, "total_tokens": None}},
            {"usage": {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22}},
        ]}}
    assert _step_usage(metadata) == {"input_tokens": None, "output_tokens": 3, "total_tokens": None}
    metadata["output_validation_retry"]["attempt_count"] = 3
    assert _step_usage(metadata) == {"input_tokens": None, "output_tokens": None, "total_tokens": None}


def test_executed_verifier_without_step_history_is_missing_not_free(comparison_client, event_payload):
    baseline = submit(comparison_client, event_payload, ["true_positive"])
    candidate = submit(comparison_client, event_payload, ["true_positive"])
    finish(comparison_client, baseline, ["true_positive"])
    finish(comparison_client, candidate, ["true_positive"])
    with comparison_client.app.state.session_factory() as db:
        row = db.get(Analysis, candidate["items"][0]["analysis_id"])
        row.result_json = {**row.result_json, "verifier": {"executed": True}}
        run = AgentRun(analysis_id=row.id, status="completed")
        db.add(run)
        db.flush()
        db.add(AgentStep(run_id=run.id, sequence=1, step_type="llm_primary", name="합성", status="completed",
            metadata_json={"timing_measured": True, "duration_ms": 100,
                           "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}))
        db.commit()
    result = compare(comparison_client, baseline, candidate)
    assert result["performance"]["candidate"]["missing_agent_histories"] == 1
    assert "token_usage_incomplete" in result["warnings"]


def test_no_pairs_and_failed_stub_or_unlabeled_are_not_counted_as_wrong(comparison_client, event_payload):
    baseline = submit(comparison_client, event_payload, ["true_positive", None, "true_positive"])
    candidate = submit(comparison_client, event_payload, ["true_positive", None, "true_positive"])
    finish(comparison_client, baseline, ["true_positive"] * 3, states={0: "failed"})
    finish(comparison_client, candidate, ["true_positive"] * 3, states={0: "processing"})
    with comparison_client.app.state.session_factory() as db:
        row = db.get(Analysis, baseline["items"][2]["analysis_id"])
        row.result_json = {"verdict": row.verdict, "agent": {"framework": "stub", "llm_called": False}}
        db.commit()
    result = compare(comparison_client, baseline, candidate)
    assert result["counts"]["exclusions"] == {
        "both_not_evaluable": 1, "reference_missing": 1, "baseline_not_evaluable": 1}
    assert result["counts"]["comparable_pairs"] == 0
    assert result["baseline_evaluation"]["metrics"]["accuracy"] is None
    assert result["candidate_evaluation"]["outcomes"]["false_negative"] == 0
    assert result["performance"]["baseline"]["processing_ms"]["count"] == 0
    assert result["baseline"]["failed"] == 1
    assert result["candidate"]["processing"] == 1


def test_source_ref_change_warns_but_reference_verdict_is_fixed(comparison_client, event_payload):
    baseline = submit(comparison_client, event_payload, ["true_positive"])
    candidate = submit(comparison_client, event_payload, ["true_positive"])
    finish(comparison_client, baseline, ["true_positive"])
    finish(comparison_client, candidate, ["inconclusive"])
    with comparison_client.app.state.session_factory() as db:
        label = db.scalar(select(AnalysisLabel).where(AnalysisLabel.analysis_id == candidate["items"][0]["analysis_id"]))
        label.source_ref = "synthetic-other-reference-filename"
        db.commit()
    result = compare(comparison_client, baseline, candidate)
    assert "reference_source_ref_different" in result["warnings"]
    assert result["counts"]["comparable_pairs"] == 1
    assert result["counts"]["regressed"] == 1
    assert result["candidate_evaluation"]["confusion_matrix"]["abstained_positive"] == 1


def test_warnings_read_only_no_decryption_and_no_secret_metadata(comparison_client, event_payload, monkeypatch):
    baseline = submit(comparison_client, event_payload, ["true_positive"])
    candidate = submit(comparison_client, event_payload, ["true_positive"])
    finish(comparison_client, baseline, ["true_positive"])
    finish(comparison_client, candidate, ["true_positive"])
    with comparison_client.app.state.session_factory() as db:
        saved = db.get(NamedRun, candidate["id"])
        saved.profile_fingerprint = "b" * 64
        saved.profile_metadata = {**saved.profile_metadata, "verifier_confidence_threshold": .9,
                                  "secret_extension": "SYNTHETIC_METADATA_MUST_NOT_EXPORT"}
        row = db.get(Analysis, candidate["items"][0]["analysis_id"])
        row.result_json = {**row.result_json, "policy": {"fixed_rules_hash": "c" * 64},
                           "summary_ko": "SYNTHETIC_MODEL_NARRATIVE_MUST_NOT_EXPORT"}
        row.input_truncated = True
        db.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError("comparison must not decrypt data")
    monkeypatch.setattr(comparison_client.app.state.crypto, "decrypt_text", forbidden)
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(comparison_client.app.state.engine, "before_cursor_execute", capture)
    try:
        result = compare(comparison_client, baseline, candidate)
    finally:
        event.remove(comparison_client.app.state.engine, "before_cursor_execute", capture)
    assert not {"INSERT", "UPDATE", "DELETE", "REPLACE"}.intersection(statements)
    text = json.dumps(result)
    assert "SYNTHETIC_METADATA_MUST_NOT_EXPORT" not in text
    assert "SYNTHETIC_MODEL_NARRATIVE_MUST_NOT_EXPORT" not in text
    for code in ("model_configuration_different", "verifier_threshold_different", "fixed_rules_different",
                 "input_truncation_different", "execution_identity_mismatch", "comparison_is_not_causal_proof"):
        assert code in result["warnings"]


def test_admin_permissions_parameter_validation_and_missing_runs(comparison_client, service_headers, event_payload):
    baseline = submit(comparison_client, event_payload, ["true_positive"])
    path = f"/api/v1/test-runs/{baseline['id']}/comparison"
    assert comparison_client.get(path, params={"baseline_id": baseline["id"]}).status_code == 422
    assert comparison_client.get(path, params={"baseline_id": str(uuid.uuid4())}).status_code == 404
    for params in ({"baseline_id": "bad-id"}, {"baseline_id": str(uuid.uuid4()), "limit": 0},
                   {"baseline_id": str(uuid.uuid4()), "offset": -1}):
        assert comparison_client.get(path, params=params).status_code == 422
    comparison_client.post("/api/v1/auth/logout")
    assert comparison_client.get(path, params={"baseline_id": str(uuid.uuid4())}).status_code == 401
    assert comparison_client.get(path, params={"baseline_id": str(uuid.uuid4())}, headers=service_headers).status_code == 403
