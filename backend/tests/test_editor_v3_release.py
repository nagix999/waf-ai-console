"""Offline release and admission gates for an editor-only candidate."""
import ast
from copy import deepcopy
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import worker_entrypoint_v232, worker_entrypoint_v233
from app.agent import evidence_pipeline_v232, legacy_prompts_v232 as prompts
from app.models import AgentConfiguration, Analysis, EvidenceDisplayJob, ProductionPromotion
from app.models import TestRun as NamedRun, VLLMProfile
from app.services import evidence_display_jobs as jobs1, evidence_display_jobs_v2 as jobs2
from app.services import evidence_display_jobs_v3 as jobs3
from app.services import evidence_display_worker_v232 as display2, evidence_display_worker_v233 as display3
from app.services import worker_coordinator_v232 as coordinator2, worker_coordinator_v233 as coordinator3
from app.services.candidate_configurations import configuration_digest, verify_configuration
from app.services.evidence_editor import capture_editor, capture_editor_profile
from app.services.production_configurations import live_snapshot
from app.services.prompt_snapshots import approved_production_record, load_analysis_prompt
from test_candidate_configurations import candidate, direct


pytestmark = pytest.mark.usefixtures("registered_vllm_target")

@pytest.fixture(autouse=True)
def frozen_editor_release_admissions(monkeypatch):
    """Replay this editor-only release under its original judgment rules."""
    monkeypatch.setattr("app.agent.prompts", prompts)


FROZEN_V232 = "11c5bd23b63ae871ed743295accfeb7b7f63a4aaa8c4eef9c7cec0a0b22e6c3f"
FROZEN_EDITOR_V2 = "874ff813b97d62a7a0a27f87031fe897158e0705284e7b0992f0dd32b52878b1"


def test_judgment_and_old_editor_artifacts_remain_exactly_frozen():
    artifact = evidence_pipeline_v232.pipeline_artifact()
    assert artifact["version"] == "evidence-pipeline-v13"
    assert len(artifact["files"]) == 177 and artifact["sha256"] == FROZEN_V232
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.32"
    assert hashlib.sha256(Path(prompts.__file__).read_bytes()).hexdigest() == (
        "521e280c06b2ffbca6fd62175054858f4095bb872d76030af2bff58b7673ddaf")
    assert jobs2.implementation_artifact()["sha256"] == FROZEN_EDITOR_V2
    current = jobs3.implementation_artifact()["files"]
    assert all(current[name] == digest for name, digest in jobs2.implementation_artifact()["files"].items())
    assert {"../worker_entrypoint_v233.py", "worker_coordinator_v233.py",
            "evidence_display_worker_v233.py"}.issubset(current)


def test_new_launcher_only_changes_display_lane_and_never_patches_old_modules():
    assert coordinator3.serve is coordinator2.serve
    assert worker_entrypoint_v233.serve_analyses is coordinator2.serve
    assert worker_entrypoint_v233.serve_display is display3.serve
    assert worker_entrypoint_v232.serve_display is display2.serve
    assert display3.legacy is jobs3 and display2.legacy is jobs2
    assert display3.MAX_DISPLAY_JOBS == display2.MAX_DISPLAY_JOBS == 4


@pytest.mark.parametrize("name", ["_positive_limit", "_capacity_from_configuration", "configured_capacity", "serve"])
def test_display_admission_code_is_identical_except_for_dispatch_module_and_documentation(name):
    def executable_tree(module):
        tree = ast.parse(inspect.getsource(getattr(module, name)))
        body = tree.body[0].body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body.pop(0)
        return ast.dump(tree, include_attributes=False)
    assert executable_tree(display3) == executable_tree(display2)


@pytest.mark.parametrize(("production", "test", "limits", "expected"), [
    (1, 1, {}, 1), (4, 4, {}, 1), (1, 1, {"synthetic": 4}, 1),
    (3, 2, {"synthetic": 4}, 3), (8, 8, {"synthetic": 9}, 4),
    (None, -1, {"synthetic": False}, 1),
])
def test_display_scheduler_keeps_existing_capacity_limits(production, test, limits, expected):
    config = SimpleNamespace(production=production, test=test, server_limits=limits)
    assert display3._capacity_from_configuration(config) == expected
    assert display3._capacity_from_configuration(config) == display2._capacity_from_configuration(config)


@pytest.mark.parametrize("role", ["analysis", "model_test", "both"])
def test_new_entrypoint_preserves_roles_shared_shutdown_and_signal_restoration(monkeypatch, role):
    entry, calls, signals = worker_entrypoint_v233, [], []
    settings = SimpleNamespace(worker_role=role, database_url="synthetic", data_encryption_key="synthetic",
                               encryption_key_version="synthetic")

    class Thread:
        def __init__(self, *, target, args, name):
            assert target is display3.serve and name == "waf-evidence-display"
            self.stop = args[-1]
            calls.append(("thread", self.stop))
        def start(self): calls.append(("start", self.stop))
        def join(self):
            assert self.stop.is_set()
            calls.append(("join", self.stop))

    def signal(signum, handler):
        signals.append((signum, handler))
        return f"old-{signum}"

    def serve(_sessions, _crypto, _settings, _worker_id, *, stop, install_signals):
        assert not install_signals and not stop.is_set()
        calls.append(("serve", stop))

    monkeypatch.setattr(entry, "get_settings", lambda: settings)
    monkeypatch.setattr(entry, "build_engine", lambda _url: object())
    monkeypatch.setattr(entry, "build_session_factory", lambda _engine: object())
    monkeypatch.setattr(entry, "CryptoService", lambda *_a: object())
    monkeypatch.setattr(entry, "Thread", Thread)
    monkeypatch.setattr(entry.signal, "signal", signal)
    monkeypatch.setattr(entry, "serve_analyses", serve)
    entry.main()
    assert [name for name, _ in calls] == (["thread", "start", "serve", "join"]
                                           if role in {"analysis", "both"} else ["serve"])
    assert len({id(stop) for _, stop in calls}) == 1 and calls[0][1].is_set()
    assert len(signals) == 4
    assert all(handler == f"old-{signum}" for signum, handler in signals[-2:])


def test_new_candidate_selects_editor3_without_new_judgment_prompt_or_inline_editor(client, event_payload, candidate):
    accepted = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert accepted.status_code == 202, accepted.text
    document, crypto = accepted.json(), client.app.state.crypto
    with client.app.state.session_factory() as db:
        run = db.get(NamedRun, document["id"])
        row = db.get(Analysis, document["items"][0]["analysis_id"])
        selected = jobs3.load_snapshot(crypto, run.display_editor_snapshot_ciphertext)
        assert selected.version == "evidence-display-editor-v3"
        assert run.evidence_editor_snapshot_ciphertext is None
        prompt = load_analysis_prompt(row, crypto)
        assert prompt.fixed_rules_version == "waf-system-v2.32"
        assert prompt.evidence_pipeline["sha256"] == FROZEN_V232
        assert verify_configuration(run, crypto, row) == document["configuration_snapshot"]
        display = db.scalar(select(EvidenceDisplayJob).where(EvidenceDisplayJob.analysis_id == row.id))
        assert display.status == "pending" and display.attempt_count == 0
        assert jobs3.load_snapshot(crypto, display.snapshot_ciphertext) == selected
        db.info["worker_run_id"] = "synthetic-run"
        from app.models import AgentRun
        agent_run = AgentRun(id="synthetic-run", analysis_id=row.id, status="running")
        db.add(agent_run)
        db.flush()
        assert capture_editor(db, "test", db.get(VLLMProfile, candidate["primary_profile_id"])) is None


@pytest.mark.parametrize("version", [jobs1.VERSION, jobs2.VERSION])
@pytest.mark.parametrize("source_run", [False, True])
def test_approved_old_editor_remains_exact_and_is_not_recaptured_as3(client, event_payload, candidate, version, source_run):
    accepted = direct(client, {**event_payload, "vendor_score": 2}, candidate)
    assert accepted.status_code == 202, accepted.text
    document, crypto = accepted.json(), client.app.state.crypto
    with client.app.state.session_factory() as db:
        profile = db.get(VLLMProfile, candidate["evidence_editor_profile_id"])
        selected = jobs3.capture_display_profile(profile, version=version)
        run = db.get(NamedRun, document["id"])
        run.display_editor_snapshot_ciphertext = crypto.encrypt_text(selected.model_dump_json())
        original_ciphertext = run.display_editor_snapshot_ciphertext
        approved = live_snapshot(db, crypto, client.app.state.settings)
        approved["evidence_editor"] = jobs3.editor_metadata(selected)
        record = ProductionPromotion(kind="promotion", snapshot_json=approved,
            configuration_hash=configuration_digest(approved), actor_id="synthetic-editor3-release",
            source_test_run_id=run.id if source_run else None)
        db.add(record)
        config = db.get(AgentConfiguration, 1)
        if config is None:
            config = AgentConfiguration(id=1, revision=0)
            db.add(config)
        config.production_evidence_editor_enabled = True
        config.production_evidence_editor_profile_id = profile.id
        db.flush()
        before = deepcopy(approved)
        captured = jobs3.capture_for_purpose(db, crypto, "production")
        bridge = capture_editor_profile(profile, db=db)
        assert captured.model_dump() == selected.model_dump() == bridge.model_dump()
        assert captured.version == version
        assert approved_production_record(db).snapshot_json == before
        assert record.configuration_hash == configuration_digest(before)
        assert run.display_editor_snapshot_ciphertext == original_ciphertext
        assert jobs3.capture_display_profile(profile).version == jobs3.VERSION

