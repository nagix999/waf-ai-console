"""Synthetic rule snapshots, actual parser execution and promotion boundaries.

No provider transport is used: the worker test stops after its committed parser
step. Release-gate fixtures create their ordinary local official evaluation.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import timedelta
from importlib import import_module
import hashlib
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import worker_v238
from app.agent import evidence_pipeline_v222
from app.models import AgentRun, AgentStep, Analysis, ParserJob, ParserWorkbench, ParserWorkbenchJob, VLLMProfile, utcnow
from app.services import parser_job_dispatch
from app.services.analysis_retries import load_execution_snapshot
from app.services.parser_management import parse_version_payload
from app.services.parser_rule_versions import create_rule_bundle_version, read_rule_bundle_definition
from app.services.parser_versions import BASELINE_ID, activate_parser_version, ensure_parser_state, pin_parser
from app.services.parser_workbench import _parse_candidate
from app.services.prompt_snapshots import load_analysis_prompt
from app.services.worker_runtime_v238 import process_moduagent
from test_candidate_configurations import candidate
from test_compact_prompt_preservation import ENGINE_SHA256 as OLD_ENGINE_HASHES
from test_parser_lifecycle import review_record
from test_parser_management import profile
from test_parser_rule_versions import PRIVATE_MARKER, REQUEST, bundle, create
from test_production_promotion import BASE, qualified
from test_prompt_evidence_semantics_preservation import ENGINE_SHA256
from test_runtime_versions_v222 import DISPLAY_SHA256


pytestmark = pytest.mark.usefixtures("registered_vllm_target")


class StopAfterParser(RuntimeError):
    pass


def enqueue(client, event_payload, service_headers, *, raw):
    """Admit the actual current contract, not a frozen worker's fixture."""
    event = {**event_payload, "payload": raw}
    response = client.post("/api/v1/analyses?wait_seconds=0", headers=service_headers, json=event)
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as db:
        db.add(VLLMProfile(name="synthetic-parser-current-worker", base_url="http://10.0.0.10:8000/v1",
                          model_name="synthetic-only-model", status="production"))
        row = db.get(Analysis, response.json()["id"])
        prompt = load_analysis_prompt(row, client.app.state.crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.38"
        assert prompt.evidence_pipeline["source_catalog_version"] == "evidence-selection-v5"
        db.commit()
    return response.json()["id"], event


def _parse_stage(client, row, monkeypatch):
    """Run real prompt/routing/input/parser/snapshot code, no LLM stages."""
    def stop(*_args, **_kwargs):
        raise StopAfterParser("synthetic-stop-after-parser")

    async def forbidden(**_kwargs):
        pytest.fail("Provider execution is outside this synthetic parser integration test")

    monkeypatch.setattr(worker_v238, "_decode_payload_step", stop)
    monkeypatch.setattr(worker_v238, "execute_structured_agent", forbidden)
    with pytest.raises(StopAfterParser, match="^synthetic-stop-after-parser$"):
        process_moduagent(row[0], client.app.state.crypto, row[1], "", .75)


def test_actual_moduagent_worker_reads_pinned_encrypted_bundle_not_new_active_parser(
    client, event_payload, service_headers, monkeypatch,
):
    identifier = create(client)
    raw = PRIVATE_MARKER + "\n" + REQUEST + "\nEND-SYNTHETIC"
    analysis_id, _ = enqueue(client, {**event_payload, "waf_vendor": "Fixture-A"}, service_headers, raw=raw)
    crypto, observed = client.app.state.crypto, []
    actual_apply = worker_v238.apply_parser_snapshot

    def observe(source, snapshot, **kwargs):
        assert hasattr(snapshot, "document")
        assert PRIVATE_MARKER not in json.dumps(snapshot)
        observed.append((source, snapshot["version_id"], kwargs["waf_vendor"]))
        return actual_apply(source, snapshot, **kwargs)

    monkeypatch.setattr(worker_v238, "apply_parser_snapshot", observe)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        row.parser_version_id = row.parser_snapshot_json = None
        pin_parser(db, crypto, row, version_id=identifier, selection_origin="candidate")
        pinned = deepcopy(row.parser_snapshot_json)
        # Change only the isolated synthetic configuration, after admission.
        activate_parser_version(db, BASELINE_ID)
        db.commit()
        _parse_stage(client, (db, row), monkeypatch)
        assert row.parser_snapshot_json == pinned
        assert crypto.decrypt_text(row.payload_ciphertext) == raw
        execution = load_execution_snapshot(row, crypto)
        assert execution.parser["version_id"] == identifier
        assert PRIVATE_MARKER not in execution.model_dump_json()
        step = db.scalar(select(AgentStep).join(AgentRun).where(
            AgentRun.analysis_id == analysis_id, AgentStep.step_type == "parser"))
        assert step.status == "completed"
        parsed = json.loads(crypto.decrypt_text(step.output_ciphertext))
        assert parsed["method"] == "GET" and parsed["uri"] == "/health"
        assert parsed["capture_selection"]["status"] == "matched"
        assert parsed["capture_extraction"]["transformed"] is True
        assert parsed["request_integrity"]["status"] == "not_checked"
        assert PRIVATE_MARKER not in json.dumps(step.metadata_json)
        assert step.metadata_json["parser"]["version_id"] == identifier
    assert observed == [(raw, identifier, "Fixture-A")]


@pytest.mark.parametrize("tamper", ["ciphertext", "definition_hash", "artifact_hash"])
def test_corrupt_encrypted_bundle_is_blocked_before_actual_parser_or_model(
    client, event_payload, service_headers, monkeypatch, tamper,
):
    from app.services.parser_versions import ParserVersionError
    identifier = create(client)
    raw = PRIVATE_MARKER + "\n" + REQUEST + "\nEND-SYNTHETIC"
    analysis_id, _ = enqueue(client, {**event_payload, "waf_vendor": "Fixture-A"}, service_headers, raw=raw)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Corrupt parser provenance reached the parser/model")

    monkeypatch.setattr(worker_v238, "apply_parser_snapshot", forbidden)
    monkeypatch.setattr(worker_v238, "execute_structured_agent", forbidden)
    with client.app.state.session_factory() as db:
        row = db.get(Analysis, analysis_id)
        row.parser_version_id = row.parser_snapshot_json = None
        pin_parser(db, client.app.state.crypto, row, version_id=identifier, selection_origin="candidate")
        value = deepcopy(row.parser_snapshot_json)
        if tamper == "ciphertext":
            value["options"]["definition_ciphertext"] = "A" * 100
        elif tamper == "definition_hash":
            value["options"]["definition_hash"] = "0" * 64
        else:
            value["options"]["engine_artifact_hash"] = "0" * 64
        row.parser_snapshot_json = value
        db.commit()
        with pytest.raises(ParserVersionError):
            process_moduagent(db, client.app.state.crypto, row, "", .75)


def _json_bundle(identifier, pointer):
    result = bundle(identifier=identifier)
    definition = result["rules"][0]["definition"]
    definition["match"] = {"all": [{"kind": "json_string_exists", "json_pointer": pointer}]}
    definition["extract"] = {"kind": "json_string", "json_pointer": pointer}
    return result


def test_workbench_preview_and_actual_snapshot_have_identical_conflict_fallback(client):
    """A previous vendor rule must not conceal a new merged-bundle conflict."""
    crypto = client.app.state.crypto
    with client.app.state.session_factory() as db:
        previous = create_rule_bundle_version(db, crypto, _json_bundle("old-first", "/first"), name="Previous JSON rule")
        current = create_rule_bundle_version(db, crypto, _json_bundle("new-second", "/second"), name="Merged rules", parent_id=previous.id)
        definition = read_rule_bundle_definition(current, crypto)
        raw = json.dumps({"first": "GET /old HTTP/1.1\r\n\r\n", "second": "GET /new HTTP/1.1\r\n\r\n"})
        before = parse_version_payload(raw, previous, crypto, waf_vendor="Fixture-A")
        assert before["method"] == "GET" and before["uri"] == "/old"
        actual = parse_version_payload(raw, current, crypto, waf_vendor="Fixture-A")
        preview = _parse_candidate(raw, definition, previous, crypto, "Fixture-A")
        assert actual["capture_selection"]["reason_code"] == "parser_rule_ambiguous"
        assert actual["parse_status"] == "failed"  # Original generic baseline, not the old vendor rule.
        assert preview == actual


@pytest.mark.usefixtures("legacy_evidence_editor_admissions")
def test_legacy_global_review_cannot_promote_generated_rule_bundle(
    client, event_payload, candidate,
):
    identifier = create(client)
    run = qualified(client, event_payload, {**candidate, "parser_version_id": identifier})
    before = client.get(BASE).json()
    review_record(client, identifier, reviewed=True, valid=True)
    response = client.get(f"{BASE}/preflight/{run['id']}")
    assert response.status_code == 200, response.text
    checks = response.json()["checks"]
    assert next(check for check in checks if check["code"] == "parser_structure_reviewed")["passed"] is False
    assert all(check["passed"] for check in checks if check["code"] != "parser_structure_reviewed"), checks
    promoted = client.post(BASE + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert promoted.status_code == 409 and promoted.json()["detail"] == "candidate_not_eligible_for_promotion"
    assert client.get(BASE).json()["configuration_hash"] == before["configuration_hash"]


def _admin_jobs(client, *, old_time=None, new_time=None, old_status="pending", new_status="pending", lease=None):
    profile_id = profile(client)
    now = utcnow()
    with client.app.state.session_factory() as db:
        ensure_parser_state(db)
        session = ParserWorkbench(name="Synthetic workbench", status="draft", source_kind="test_run",
            baseline_version_id=BASELINE_ID, input_ciphertext=client.app.state.crypto.encrypt_text("{}"),
            state_ciphertext=client.app.state.crypto.encrypt_text("{}"),
            encryption_key_version=client.app.state.crypto.key_version, created_by="synthetic-only")
        db.add(session)
        db.flush()
        old = ParserJob(kind="suggestion", status=old_status, baseline_version_id=BASELINE_ID,
            request_ciphertext=client.app.state.crypto.encrypt_text("{}"),
            encryption_key_version=client.app.state.crypto.key_version, created_by="synthetic-only",
            created_at=old_time or now, lease_expires_at=lease)
        new = ParserWorkbenchJob(workbench_id=session.id, kind="format_discovery", status=new_status,
            session_revision=1, profile_id=profile_id, profile_fingerprint="0" * 64,
            request_ciphertext=client.app.state.crypto.encrypt_text("{}"),
            encryption_key_version=client.app.state.crypto.key_version, created_by="synthetic-only",
            created_at=new_time or now, lease_expires_at=lease)
        db.add_all([old, new])
        db.commit()
        return old.id, new.id


@pytest.mark.parametrize("first", ["legacy", "workbench"])
def test_administration_lane_orders_both_parser_generations_and_does_not_starve_remaining(client, first):
    now = utcnow()
    old_id, new_id = _admin_jobs(client, old_time=now - timedelta(seconds=2 if first == "legacy" else 1),
        new_time=now - timedelta(seconds=2 if first == "workbench" else 1))
    sessions = client.app.state.session_factory
    assert parser_job_dispatch.next_parser_job_kind(sessions) == first
    with sessions() as db:
        db.get(ParserJob if first == "legacy" else ParserWorkbenchJob, old_id if first == "legacy" else new_id).status = "completed"
        db.commit()
    assert parser_job_dispatch.next_parser_job_kind(sessions) == ("workbench" if first == "legacy" else "legacy")


@pytest.mark.parametrize("expired", [True, False])
def test_administration_lane_only_reclaims_expired_running_workbench_job(client, expired):
    now = utcnow()
    _admin_jobs(client, old_status="completed", new_status="running",
        lease=now + timedelta(seconds=-1 if expired else 60))
    assert parser_job_dispatch.next_parser_job_kind(client.app.state.session_factory) == ("workbench" if expired else None)


@pytest.mark.parametrize("first", ["legacy", "workbench"])
def test_administration_dispatch_calls_only_selected_generation(client, monkeypatch, first):
    now, called = utcnow(), []
    _admin_jobs(client, old_time=now - timedelta(seconds=2 if first == "legacy" else 1),
        new_time=now - timedelta(seconds=2 if first == "workbench" else 1))

    async def old(*args, **kwargs):
        called.append(("legacy", kwargs["worker_id"]))
        return True

    async def new(*args, **kwargs):
        called.append(("workbench", kwargs["worker_id"]))
        return True

    monkeypatch.setattr("app.services.parser_jobs.process_pending_parser_job", old)
    monkeypatch.setattr("app.services.parser_workbench_jobs.process_pending_workbench_job", new)
    assert parser_job_dispatch.run_parser_job(client.app.state.session_factory, client.app.state.crypto,
        client.app.state.settings, "synthetic-worker") is True
    assert called == [(first, "synthetic-worker")]


def test_additive_parser_administration_keeps_all_frozen_verdict_artifacts_exact():
    artifact = evidence_pipeline_v222.pipeline_artifact()
    assert len(artifact["files"]) == 40
    assert artifact["files"] == ENGINE_SHA256
    assert {name: artifact["files"][name] for name in OLD_ENGINE_HASHES} == OLD_ENGINE_HASHES
    root = Path(evidence_pipeline_v222.__file__).parent.parent
    assert {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in DISPLAY_SHA256} == DISPLAY_SHA256


def test_historical_parser_coordinator_reuses_frozen_runners_and_unchanged_independent_display_lane():
    from app import worker_entrypoint_parsers
    from app.services import evidence_display_jobs, worker_concurrency_parsers, worker_concurrency_v222
    assert worker_concurrency_parsers.run_analysis is worker_concurrency_v222.run_analysis
    assert worker_concurrency_parsers.run_model_test is worker_concurrency_v222.run_model_test
    assert worker_entrypoint_parsers.serve_analyses is worker_concurrency_parsers.serve
    assert worker_entrypoint_parsers.serve_display is evidence_display_jobs.serve


@pytest.mark.usefixtures("legacy_evidence_editor_admissions")
def test_genuinely_saved_workbench_candidate_passes_official_preflight_and_promotes(
    client, settings, monkeypatch, candidate, event_payload,
):
    from app.services import parser_workbench
    from app.services.parser_versions import create_version
    from test_parser_workbench import (PREFIX, expectations, generated, profile as workbench_profile,
        raw, validate)

    session, _calls = generated(client, settings, monkeypatch, workbench_profile(client))
    # Holdout becomes visible only after generation has finished and rules lock.
    session = validate(client, session)
    assert session["rules_locked"] and session["holdout_revealed"]
    session = validate(client, expectations(client, session))
    assert session["validation_summary"]["ready"] is True
    response = client.post(PREFIX + f"/sessions/{session['id']}/save-candidate", json={
        "expected_revision": session["revision"], "name": "Reviewed synthetic release candidate"})
    assert response.status_code == 201, response.text
    saved = response.json()
    identifier = saved["candidate_version_id"]
    before = client.get(BASE).json()
    assert before["snapshot"]["parser"]["version_id"] == BASELINE_ID

    run = qualified(client, {**event_payload, "waf_vendor": "SyntheticVendor", "payload": raw("/alpha")},
        {**candidate, "parser_version_id": identifier})
    endpoint = f"{BASE}/preflight/{run['id']}"
    ready = client.get(endpoint)
    assert ready.status_code == 200 and ready.json()["eligible"] is True, ready.text
    assert next(check for check in ready.json()["checks"] if check["code"] == "parser_structure_reviewed")["passed"]

    with client.app.state.session_factory() as db:
        # Same generic definition/hash but another ID is not the reviewed baseline.
        other = create_version(db, client.app.state.crypto, name="Different baseline identity", engine_version=BASELINE_ID)
        from app.models import ParserVersion
        assert other.content_hash == db.get(ParserVersion, BASELINE_ID).content_hash
        assert not parser_workbench.workbench_parser_review_ready(db, identifier, other.id)
        review = db.get(ParserWorkbench, saved["id"])
        original_metadata, original_revision = deepcopy(review.metadata_json), review.revision
        db.commit()

    # A genuinely completed official evaluation cannot mask stale review hashes
    # or later edits. Restore the exact reviewed metadata before valid promotion.
    for changed in ("candidate_content_hash", "baseline_content_hash", "review_revision"):
        with client.app.state.session_factory() as db:
            review = db.get(ParserWorkbench, saved["id"])
            review.metadata_json = {**original_metadata,
                changed: original_revision + 1 if changed == "review_revision" else "0" * 64}
            db.commit()
        stale = client.get(endpoint)
        assert stale.status_code == 200 and stale.json()["eligible"] is False
        assert next(check for check in stale.json()["checks"] if check["code"] == "parser_structure_reviewed")["passed"] is False
    with client.app.state.session_factory() as db:
        db.get(ParserWorkbench, saved["id"]).metadata_json = original_metadata
        db.commit()
    assert client.get(endpoint).json()["eligible"] is True
    promoted = client.post(BASE + "/promote", json={"candidate_test_run_id": run["id"],
        "expected_production_configuration_hash": before["configuration_hash"], "acknowledge_schema_change": True})
    assert promoted.status_code == 200, promoted.text
    current = client.get(BASE).json()
    assert current["snapshot"]["parser"]["version_id"] == identifier
    assert current["configuration_hash"] == run["configuration_hash"]
    assert not current["drifted"]


@pytest.mark.parametrize("revision", ["232", "238"], ids=["archived-v232", "current-v238"])
def test_actual_new_coordinator_preserves_purpose_claims_and_shared_stop(monkeypatch, revision):
    from app import worker
    coordinator = import_module("app.services.worker_coordinator_v" + revision)
    calls, stop = [], Event()

    @contextmanager
    def sessions():
        yield object()

    class Future:
        def done(self): return True
        def result(self): return None

    class Pool:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def submit(self, function, *_args):
            assert function is coordinator.run_analysis
            if len(calls) == 2:
                stop.set()
            return Future()

    def claim(_db, _owner, seconds, **kwargs):
        calls.append((seconds, kwargs))
        return SimpleNamespace(id="synthetic-case", lease_owner="synthetic-owner", attempt_count=1)

    monkeypatch.setattr(coordinator, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(worker, "claim_next", claim)
    monkeypatch.setattr("app.services.runtime_status.heartbeat", lambda *_args: None)
    monkeypatch.setattr("app.services.official_evaluations.recover_pending_evaluations", lambda *_args, **_kwargs: None)
    settings = SimpleNamespace(worker_role="analysis", job_lease_seconds=60, worker_poll_seconds=.01)
    coordinator.serve(sessions, None, settings, "synthetic-worker", stop=stop, install_signals=False)
    assert calls == [(60, {"purpose": "production", "enforce_limits": True}),
        (60, {"purpose": "test", "enforce_limits": True})]


@pytest.mark.parametrize("revision", ["232", "238"], ids=["archived-v232", "current-v238"])
def test_actual_new_coordinator_alternates_model_validation_and_parser_admin(monkeypatch, revision):
    from app import worker
    coordinator = import_module("app.services.worker_coordinator_v" + revision)
    dispatched, stop = [], Event()

    @contextmanager
    def sessions():
        yield object()

    class Future:
        def done(self): return True
        def result(self): return None

    class Pool:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def submit(self, function, *_args):
            dispatched.append(function)
            if len(dispatched) == 3:
                stop.set()
            return Future()

    monkeypatch.setattr(coordinator, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(coordinator, "pending_parser_job", lambda *_args: True)
    monkeypatch.setattr(worker, "claim_next_vllm_test", lambda *_args: SimpleNamespace(id="synthetic-model-test", lease_owner="synthetic-owner"))
    monkeypatch.setattr(worker, "claim_next", lambda *_args, **_kwargs: pytest.fail("Model-only role claimed an analysis"))
    monkeypatch.setattr("app.services.runtime_status.heartbeat", lambda *_args: None)
    monkeypatch.setattr("app.services.official_evaluations.recover_pending_evaluations",
        lambda *_args, **_kwargs: pytest.fail("Model-only role finalized ordinary analyses"))
    settings = SimpleNamespace(worker_role="model_test", vllm_test_lease_seconds=120, worker_poll_seconds=.01)
    coordinator.serve(sessions, None, settings, "synthetic-worker", stop=stop, install_signals=False)
    assert dispatched == [coordinator.run_model_test, coordinator.run_parser_job, coordinator.run_model_test]


@pytest.mark.parametrize("role", ["analysis", "model_test", "both"])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("revision", ["232", "238"], ids=["archived-v232", "current-v238"])
def test_actual_new_entrypoint_shares_stop_drains_display_and_restores_signals(monkeypatch, role, failure, revision):
    entry = import_module("app.worker_entrypoint_v" + revision)
    calls, signals = [], []
    settings = SimpleNamespace(worker_role=role, database_url="synthetic", data_encryption_key="synthetic", encryption_key_version="synthetic")
    old_display = entry.serve_display

    class Thread:
        def __init__(self, *, target, args, name):
            assert target is old_display and name == "waf-evidence-display"
            self.stop = args[-1]
            calls.append(("thread", self.stop))
        def start(self):
            assert not self.stop.is_set()
            calls.append(("start", self.stop))
        def join(self):
            assert self.stop.is_set()
            calls.append(("join", self.stop))

    def signal(signum, handler):
        signals.append((signum, handler))
        return f"synthetic-old-{signum}"

    def serve(_sessions, _crypto, _settings, _worker_id, *, stop, install_signals):
        assert not install_signals and not stop.is_set()
        calls.append(("serve", stop))
        if failure:
            raise RuntimeError("synthetic-coordinator-failure")

    monkeypatch.setattr(entry, "get_settings", lambda: settings)
    monkeypatch.setattr(entry, "build_engine", lambda _url: object())
    monkeypatch.setattr(entry, "build_session_factory", lambda _engine: object())
    monkeypatch.setattr(entry, "CryptoService", lambda *_args: object())
    monkeypatch.setattr(entry, "Thread", Thread)
    monkeypatch.setattr(entry.signal, "signal", signal)
    monkeypatch.setattr(entry, "serve_analyses", serve)
    if failure:
        with pytest.raises(RuntimeError, match="^synthetic-coordinator-failure$"):
            entry.main()
    else:
        entry.main()
    assert [name for name, _ in calls] == (["thread", "start", "serve", "join"] if role in {"analysis", "both"} else ["serve"])
    assert len({id(stop) for _, stop in calls}) == 1 and calls[0][1].is_set()
    assert len(signals) == 4
    assert signals[-2][1] == f"synthetic-old-{signals[-2][0]}" and signals[-1][1] == f"synthetic-old-{signals[-1][0]}"
