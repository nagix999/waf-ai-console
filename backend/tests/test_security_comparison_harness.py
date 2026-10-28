"""Offline harness safety: synthetic source configuration, never paid calls."""
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from scripts import compare_security_analysis_v3 as comparison
from app.agent.contracts import SecurityAnalysisOutput, SecurityAnalysisOutputV216, SemanticAssessmentOutput
from app.agent.executor import AgentCallResult
from app.config import Settings
from app.database import Base, build_engine
from app.models import VLLMProfile, VLLMTestRun, utcnow
from app.services.crypto import CryptoService
from app.services.prompt_policies import get_active_policy
from app.services.vllm_profiles import profile_fingerprint
from test_security_analysis_v3 import output


ROOT = Path(__file__).resolve().parents[2] / "samples"


def test_dry_run_uses_only_fixed_synthetic_files_not_runtime_or_model(monkeypatch, capsys):
    monkeypatch.setattr(comparison, "get_settings", lambda: pytest.fail("dry run must not read runtime"))
    assert comparison.main(["--samples-root", str(ROOT)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["analyses"] == 60 and plan["model_calls_made"] == 0
    assert plan["groups"] == {"medium": 10, "hard": 10, "parser_stress": 10}
    assert plan["source_database_read"] is False
    cases = comparison.synthetic_cases(ROOT)
    assert len(cases) == 30 and len({case["event"]["event_id"] for case in cases}) == 30
    for group in plan["groups"]:
        values = [case["event"]["expected_verdict"] for case in cases if case["group"] == group]
        assert [values.count(verdict) for verdict in comparison.VERDICTS] == [4, 4, 2]


def test_diagnostic_ten_is_exact_frozen_single_revision_without_runtime_reads(monkeypatch, capsys):
    monkeypatch.setattr(comparison, "get_settings", lambda: pytest.fail("dry diagnostic must not read runtime"))
    assert comparison.main(["--diagnostic-ten", "--samples-root", str(ROOT)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["analyses"] == 10 and plan["revisions"] == ["waf-system-v2.15"]
    assert plan["cases_per_revision"] == 10 and plan["source_database_read"] is False
    cases = comparison.diagnostic_cases(ROOT)
    assert tuple(case["event"]["event_id"] for case in cases) == comparison.DIAGNOSTIC_IDS
    assert len({case["event"]["event_id"] for case in cases}) == 10
    assert "waf-parser-v1-014" in comparison.DIAGNOSTIC_IDS
    # A later default candidate must not alter this frozen diagnostic.
    monkeypatch.setattr(comparison.prompts, "FIXED_INSTRUCTIONS", "changed-default-candidate")
    assert comparison.frozen_diagnostic_revision().FIXED_RULES_VERSION == "waf-system-v2.15"


def test_changed_frozen_contract_refuses_diagnostic_before_runtime(monkeypatch):
    monkeypatch.setattr(comparison, "get_settings", lambda: pytest.fail("changed contract must not read runtime"))
    monkeypatch.setattr(comparison, "FROZEN_V215_RULES_HASH", "0" * 64)
    assert comparison.main(["--diagnostic-ten"]) == 2


def test_manifest_models_match_pinned_worker_contracts():
    assert comparison.wire_model_for_revision(comparison.frozen_diagnostic_revision()) is SecurityAnalysisOutput
    assert comparison.wire_model_for_revision(comparison.prompts) is SecurityAnalysisOutputV216
    assert comparison.wire_model_for_revision(comparison.legacy_prompts_v214) is SemanticAssessmentOutput
    class Unknown:
        FIXED_RULES_VERSION = "unknown"
    with pytest.raises(ValueError, match="evaluation_wire_revision_unsupported"):
        comparison.wire_model_for_revision(Unknown)


def test_unreviewed_files_are_rejected_before_runtime_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison, "get_settings", lambda: pytest.fail("unapproved data must not read runtime"))
    with pytest.raises((ValueError, FileNotFoundError)):
        comparison.synthetic_cases(tmp_path)


def source_settings(tmp_path):
    path = tmp_path / "source.sqlite"
    settings = Settings(_env_file=None, database_url="sqlite+pysqlite:///" + str(path),
        data_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", encryption_key_version="synthetic")
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    with Session(engine) as db:
        profile = VLLMProfile(name="Synthetic isolated profile", provider="openai", base_url="https://api.openai.com/v1",
            model_name="synthetic-no-real-model", status="verified", external_data_approved=True,
            api_key_ciphertext=crypto.encrypt_text("SYNTHETIC_CREDENTIAL_NOT_FOR_OUTPUT"),
            encryption_key_version=crypto.key_version, last_verified_at=utcnow())
        db.add(profile)
        db.flush()
        db.add(VLLMTestRun(profile_id=profile.id, mode="full", status="passed", completed_at=utcnow(),
                           profile_fingerprint=profile_fingerprint(profile)))
        get_active_policy(db, crypto)
        db.commit()
    engine.dispose()
    return settings, path


def test_source_sqlite_is_os_readonly_and_query_only(tmp_path):
    settings, path = source_settings(tmp_path)
    before = path.read_bytes()
    engine = comparison.readonly_source_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA query_only").scalar() == 1
            with pytest.raises(Exception):
                connection.execute(text("UPDATE vllm_profiles SET name='must not write'"))
    finally:
        engine.dispose()
    assert path.read_bytes() == before


def test_nested_retry_accounting_is_complete_without_top_level_double_count():
    initial = {"output_validation_retry": {"attempt_count": 3, "attempts": [
        {"duration_ms": 9, "retry_kind": "schema_correction", "validation_issues": [{"field": "signature_assessment.matched_points.[]", "type": "string_too_short"}]},
        {"duration_ms": 11, "retry_kind": "compact_output", "validation_issues": [{"field": "summary_ko", "type": "summary_must_describe_request"}]},
        {"duration_ms": 13, "validation_issues": []}]}}
    citation = {"output_validation_retry": {"attempt_count": 1, "attempts": [{"validation_issues": []}]}}
    metadata = {**citation, "evidence_grounding_retry": {"attempt_count": 2, "attempts": [initial, citation]}}
    value = comparison.retry_accounting(metadata)
    assert value["schema_attempts"] == 4 and value["schema_retries"] == 2
    assert value["output_recovery_retries"] == 2
    assert value["output_recovery_duration_ms"] == 24
    assert value["output_retry_kinds"] == {"schema_correction": 1, "compact_output": 1}
    assert value["validation_errors"] == [
        {"field": "signature_assessment.matched_points.[]", "type": "string_too_short", "count": 1},
        {"field": "summary_ko", "type": "summary_must_describe_request", "count": 1}]
    # Two independent successful model calls are not one schema retry.
    metadata["evidence_grounding_retry"]["attempts"] = [citation, citation]
    assert comparison.retry_accounting(metadata)["schema_retries"] == 0


def test_validation_counters_never_export_invalid_values_or_unknown_paths():
    value = comparison.retry_accounting({"output_validation_retry": {"attempt_count": 1, "attempts": [{
        "validation_issues": [{"field": "SYNTHETIC_SECRET.field", "type": "RAW_PROVIDER_TEXT", "input": "SYNTHETIC_SECRET"}]}]}})
    assert value["validation_errors"] == [{"field": "<unknown>.<unknown>", "type": "validation_error", "count": 1}]
    assert "SYNTHETIC_SECRET" not in json.dumps(value) and "RAW_PROVIDER_TEXT" not in json.dumps(value)


def test_summary_resume_preserves_initial_and_writes_current_immutable_revision(tmp_path):
    partial = {"completed": 1, "planned": 60}
    first = comparison.write_summary(tmp_path, partial)
    initial_bytes = (tmp_path / "summary.json").read_bytes()
    complete = {"completed": 60, "planned": 60}
    latest = comparison.write_summary(tmp_path, complete)
    assert latest != first and json.loads((tmp_path / latest).read_text()) == complete
    assert (tmp_path / "summary.json").read_bytes() == initial_bytes
    assert comparison.write_summary(tmp_path, complete) == latest
    assert len(list(tmp_path.glob("summary-*.json"))) == 2


@pytest.mark.parametrize("quota", [False, True])
def test_mocked_comparison_preserves_source_and_aborts_quota(tmp_path, monkeypatch, capsys, quota):
    settings, source_path = source_settings(tmp_path)
    source_before = source_path.read_bytes()
    monkeypatch.setattr(comparison, "get_settings", lambda: settings)
    cases = comparison.synthetic_cases(ROOT)[:2]
    monkeypatch.setattr(comparison, "synthetic_cases", lambda root: cases)
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        calls.append(kwargs)
        if quota:
            return AgentCallResult(None, "synthetic-quota", None, "error", None, "synthetic-safe-error",
                                   {"error_summary": {"code": "openai_http_429"}})
        if kwargs["output_model"] in {SecurityAnalysisOutput, SecurityAnalysisOutputV216}:
            value = kwargs["output_model"].model_validate(output().model_dump(mode="json"))
        elif kwargs["output_model"] is SemanticAssessmentOutput:
            value = SemanticAssessmentOutput.model_validate(output().legacy_bridge().model_dump(mode="json"))
        else:
            pytest.fail("No duplicate synthetic interpretations should invoke editor")
        return AgentCallResult(value, "synthetic-comparison", None, "completed", None, None,
                               {"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}})
    monkeypatch.setattr(comparison.worker, "execute_structured_agent", execute)
    directory = tmp_path / "safe-results"
    result = comparison.main(["--execute-model-calls", "--profile-name", "Synthetic isolated profile",
                              "--output-dir", str(directory)])
    assert result == (1 if quota else 0), capsys.readouterr().out
    assert source_path.read_bytes() == source_before
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["version"] == "security-analysis-comparison-v2"
    assert manifest["verifier_confidence_threshold"] == settings.verifier_confidence_threshold
    assert len(manifest["pipeline_code_hash"]) == 64 and len(manifest["default_input_schema_hash"]) == 64
    assert "backend/app/agent/executor.py" in manifest["pipeline_source_hashes"]
    checkpoints = list(directory.glob("waf-system-*.json"))
    assert len(checkpoints) == (1 if quota else 4)
    text_output = capsys.readouterr().out + "".join(path.read_text() for path in directory.glob("*.json"))
    assert "SYNTHETIC_CREDENTIAL_NOT_FOR_OUTPUT" not in text_output
    assert settings.data_encryption_key not in text_output
    if quota:
        assert len(calls) == 1
    else:
        before_resume = len(calls)
        assert comparison.main(["--execute-model-calls", "--profile-name", "Synthetic isolated profile",
                                "--output-dir", str(directory)]) == 0
        assert len(calls) == before_resume
        settings.verifier_confidence_threshold = .8
        assert comparison.main(["--execute-model-calls", "--profile-name", "Synthetic isolated profile",
                                "--output-dir", str(directory)]) == 2
        assert len(calls) == before_resume


def test_mocked_diagnostic_runs_ten_v215_only_and_captures_technical_hold(tmp_path, monkeypatch, capsys):
    settings, source_path = source_settings(tmp_path)
    source_before = source_path.read_bytes()
    monkeypatch.setattr(comparison, "get_settings", lambda: settings)
    calls = []
    async def execute(**kwargs):
        kwargs["egress_check"]()
        assert kwargs["output_model"] is SecurityAnalysisOutput
        document = json.loads(kwargs["user_input"])
        event = document["event"]
        assert not {"expected_verdict", "case_name", "test_category", "difficulty"} & event.keys()
        calls.append(kwargs)
        if kwargs["agent_name"] == "waf-verifier" and event["event_id"] == "waf-syn-c00af87fa643":
            return AgentCallResult(None, "synthetic-diagnostic", None, "error", None, "synthetic-safe-error", {
                "error_summary": {"code": "output_validation_failed"},
                "output_validation_retry": {"attempt_count": 2, "attempts": [
                    {"duration_ms": 7, "retry_kind": "schema_correction", "validation_issues": [{"field": "summary_ko", "type": "summary_must_describe_request"}]},
                    {"duration_ms": 8, "validation_issues": []}]}})
        value = output()
        value.confidence_score = .6
        return AgentCallResult(value, "synthetic-diagnostic", None, "completed", None, None, {})
    monkeypatch.setattr(comparison.worker, "execute_structured_agent", execute)
    directory = tmp_path / "diagnostic-results"
    assert comparison.main(["--execute-model-calls", "--diagnostic-ten", "--profile-name", "Synthetic isolated profile",
                            "--output-dir", str(directory)]) == 0, capsys.readouterr().out
    assert source_path.read_bytes() == source_before
    files = sorted(directory.glob("waf-system-*.json"))
    assert len(files) == 10 and all("v2.15--" in path.name for path in files)
    assert len(calls) == 20
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["revision_versions"] == ["waf-system-v2.15"]
    assert manifest["diagnostic_origin_manifest_hash"] == comparison.DIAGNOSTIC_ORIGIN_MANIFEST_HASH
    assert len(manifest["selection_hash"]) == 64
    hold = json.loads((directory / "waf-system-v2.15--waf-syn-c00af87fa643.json").read_text())["record"]
    assert hold["status"] == "completed" and hold["technical_hold"] is True
    assert hold["technical_hold_causes"] == ["verifier_failed"]
    assert hold["mandatory_stage_failures"] == [{"phase": "llm_verifier", "role": "verifier",
        "failure_category": "output_validation_failed", "safe_error_code": "output_validation_failed"}]
    phase = next(phase for phase in hold["phases"] if phase["role"] == "verifier")
    assert phase["output_recovery_retries"] == 1 and phase["output_recovery_duration_ms"] == 8
    report = json.loads((directory / "summary.json").read_text())["comparison"]
    assert set(report) == {"waf-system-v2.15"}
    assert report["waf-system-v2.15"]["technical_holds"] == 1
