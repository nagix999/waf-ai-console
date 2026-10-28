"""Isolated v2.14/current comparison and frozen v2.15 ten-case diagnosis.

Default is a no-network plan. --execute-model-calls explicitly enables paid
calls. The source SQLite connection is OS read-only and query-only. Credentials,
source policy and encrypted traces live only in an in-memory evaluation DB.
No source concurrency reservations are written: idle checks reduce interference
but cannot atomically exclude a new live request. This is not a load benchmark
or an official approved-Ground-Truth evaluation / Production promotion.
"""
import argparse
import hashlib
import json
import logging
import os
import sqlite3
import statistics
import sys
import time
from collections import Counter
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import worker
from app.agent import legacy_prompts_v214, prompts, result_editor
from app.agent.contracts import (SecurityAnalysisOutput, SecurityAnalysisOutputV216,
                                 SemanticAssessmentOutput, EvidenceSelectionCorrectionOutput)
from app.agent.executor import SAFE_VALIDATION_TYPES, _output_schema_field_names
from app.candidate_schemas import CandidateConfiguration
from app.config import Settings, get_settings
from app.database import Base, build_engine
from app.models import (Analysis, AgentRun, AgentStep, InternalEgressTarget, LLMCallSlot,
                        PromptPolicyState, PromptPolicyVersion, TestRunItem, VLLMProfile, VLLMTestRun, utcnow)
from app.services import evidence_editor, prompt_snapshots
from app.services.agent_configuration import validate_role_profile
from app.services.concurrency import server_key
from app.services.crypto import CryptoService
from app.services.input_schemas import get_active_schema, default_definition, content_hash as input_schema_hash
from app.services.prompt_policies import read_policy_text
from app.services.test_comparisons import _step_usage
from app.services.test_runs import enqueue_named_run
from app.services.vllm_profiles import profile_fingerprint
from app.services.provider_options import strict_json_schema

SOURCES = (
    ("medium", "waf-dummy-v1/medium_50.json", "e5b0f1dc37014e2ea7c04d40d05d7820d728ec574c26048e2f380ecc506b0c22"),
    ("hard", "waf-dummy-v1/hard_50.json", "0bd259c02732fdf559588c1116e1a597657ac7b6d9aa73d09b5690c804c27aa2"),
    ("parser_stress", "waf-parser-stress-v1/parser_stress_50.json", "147ab601d5ec69ed8ba371ba3e92b2280c9a8b447d3c8a718ea1595d80ac7f8f"),
)
VERDICTS = ("true_positive", "false_positive", "inconclusive")
DIAGNOSTIC_IDS = (
    "waf-syn-32a32a21289a", "waf-syn-635a3987a50e", "waf-syn-6102cbe604f8",
    "waf-syn-c00af87fa643", "waf-syn-e6b5d2529ec0", "waf-parser-v1-014",
    "waf-parser-v1-018", "waf-syn-4198661f108f", "waf-syn-5c5f7fd45629", "waf-parser-v1-007",
)
DIAGNOSTIC_ORIGIN_MANIFEST_HASH = "b1a1a71a66d4ea7062f5d4e4269145a50e3f947a8fb54004df4f9ed3be976bb8"
FROZEN_V215_RULES_HASH = "18e17593e9e05e97d30758273e2799f7a17e01e91b29b3db15eba29fcfde63b2"
FROZEN_V215_STRICT_SCHEMA_HASH = "df7de8c4515f345f5a07c38acca768eb5ac92dee1e51b90ab4358ecda8cbebc7"


def synthetic_cases(root):
    """Fixed 4 attack / 4 normal / 2 hold references per source, before calls."""
    selected = []
    for group, filename, expected_hash in SOURCES:
        data = (root / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError("evaluation_synthetic_source_hash_mismatch")
        rows = json.loads(data)
        if len(rows) != 50:
            raise ValueError("evaluation_synthetic_source_count_invalid")
        batch = []
        for verdict, count in zip(VERDICTS, (4, 4, 2), strict=True):
            choices = sorted((row for row in rows if row["expected_verdict"] == verdict),
                key=lambda row: hashlib.sha256(("security-v3-30:" + row["event_id"]).encode()).hexdigest())
            if len(choices) < count:
                raise ValueError("evaluation_synthetic_reference_count_invalid")
            batch.extend(choices[:count])
        for row in sorted(batch, key=lambda row: row["event_id"]):
            selected.append({"group": group, "event": row})
    return selected


def diagnostic_cases(root):
    # Outcome-informed diagnostic subset, not a new independent quality set.
    original = {case["event"]["event_id"]: case for case in synthetic_cases(root)}
    if len(set(DIAGNOSTIC_IDS)) != 10 or not set(DIAGNOSTIC_IDS) <= original.keys():
        raise ValueError("evaluation_diagnostic_selection_invalid")
    return [original[identifier] for identifier in DIAGNOSTIC_IDS]


def frozen_diagnostic_revision():
    from app.agent import legacy_prompts_v215
    schema = strict_json_schema(SecurityAnalysisOutput.model_json_schema())
    schema_hash = hashlib.sha256(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if (legacy_prompts_v215.FIXED_RULES_VERSION != "waf-system-v2.15"
            or hashlib.sha256(legacy_prompts_v215.FIXED_INSTRUCTIONS.encode()).hexdigest() != FROZEN_V215_RULES_HASH
            or schema_hash != FROZEN_V215_STRICT_SCHEMA_HASH):
        raise ValueError("evaluation_diagnostic_revision_changed")
    return legacy_prompts_v215


def wire_model_for_revision(revision):
    models = {"waf-system-v2.14": SemanticAssessmentOutput,
              "waf-system-v2.15": SecurityAnalysisOutput,
              "waf-system-v2.16": SecurityAnalysisOutputV216}
    try:
        return models[revision.FIXED_RULES_VERSION]
    except KeyError:
        raise ValueError("evaluation_wire_revision_unsupported") from None


def readonly_source_engine(database_url):
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("evaluation_requires_file_sqlite_source")
    path = Path(url.database).resolve(strict=True)
    def connection():
        db = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True,
                             check_same_thread=False, timeout=10)
        db.execute("PRAGMA query_only=ON")
        return db
    return create_engine("sqlite+pysqlite://", creator=connection, hide_parameters=True)


class ReadonlyAuthority:
    def __init__(self, engine, identifier, fingerprint, *, idle_timeout=60):
        self.engine, self.identifier, self.fingerprint = engine, identifier, fingerprint
        self.idle_timeout, self.wait_ms = idle_timeout, 0
        self.blocked = False

    def __call__(self):
        started = time.monotonic()
        while True:
            with Session(self.engine) as db:
                profile = db.get(VLLMProfile, self.identifier)
                try:
                    validate_role_profile(db, profile, self.fingerprint)
                except Exception:
                    self.blocked = True
                    raise
                active = db.scalar(select(func.count()).select_from(LLMCallSlot).where(
                    LLMCallSlot.server_key == server_key(profile), LLMCallSlot.expires_at > utcnow()))
            if not active:
                self.wait_ms += round((time.monotonic() - started) * 1000)
                return
            if time.monotonic() - started >= self.idle_timeout:
                self.blocked = True
                raise ValueError("evaluation_source_server_busy")
            time.sleep(.25)


def source_configuration(engine, crypto, profile_name):
    with Session(engine) as db:
        profile = db.scalar(select(VLLMProfile).where(VLLMProfile.name == profile_name))
        validate_role_profile(db, profile)
        values = {column.name: getattr(profile, column.name) for column in VLLMProfile.__table__.columns}
        fingerprint = profile_fingerprint(profile)
        proof = db.scalar(select(VLLMTestRun).where(VLLMTestRun.profile_id == profile.id,
            VLLMTestRun.mode == "full", VLLMTestRun.status == "passed",
            VLLMTestRun.profile_fingerprint == fingerprint).order_by(VLLMTestRun.created_at.desc()).limit(1))
        if proof is None:
            raise ValueError("evaluation_source_profile_not_verified")
        proof_values = {key: getattr(proof, key) for key in (
            "id", "profile_id", "mode", "status", "profile_fingerprint", "created_at", "completed_at")}
        targets = [{column.name: getattr(target, column.name) for column in InternalEgressTarget.__table__.columns}
                   for target in db.scalars(select(InternalEgressTarget))] if profile.provider == "vllm" else []
        state = db.get(PromptPolicyState, 1)
        policy = db.get(PromptPolicyVersion, state.active_version_id) if state else None
        if policy is None:
            raise ValueError("evaluation_source_policy_missing")
        # Verify ciphertext integrity; never print or export the plaintext.
        read_policy_text(policy, crypto)
        policy_values = {column.name: getattr(policy, column.name) for column in PromptPolicyVersion.__table__.columns}
        policy_values["parent_version_id"] = None  # Only this version enters memory.
    return values, proof_values, targets, policy_values


@contextmanager
def revision_capture(revision):
    original = prompt_snapshots._build_snapshot
    def snapshot(version, crypto, *, origin="enqueue", rules=None):
        return original(version, crypto, origin=origin, rules=rules or revision)
    with patch.object(prompt_snapshots, "_build_snapshot", snapshot):
        if revision is legacy_prompts_v214:
            with patch.object(evidence_editor, "VERSION", result_editor.VERSION), patch.object(
                    evidence_editor, "INSTRUCTIONS", result_editor.INSTRUCTIONS):
                yield
        else:
            yield


def write_json(path, value):
    """Only safe synthetic outputs; exclusive private files, no secret export."""
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


@lru_cache(maxsize=1)
def validation_fields():
    return frozenset().union(*(_output_schema_field_names(model) for model in (
        SecurityAnalysisOutput, SecurityAnalysisOutputV216, SemanticAssessmentOutput, EvidenceSelectionCorrectionOutput)))


def retry_accounting(metadata, depth=0):
    """Count every logical call once, including repairs before citation repair.

    Consume only the executor's already-sanitized issue type/path. No provider
    output, invalid value, exception context or request body is exported.
    """
    if not isinstance(metadata, dict) or depth > 4:
        return {"schema_attempts": 0, "schema_retries": 0, "output_recovery_retries": 0,
                "validation_errors": [], "output_retry_kinds": {}, "output_recovery_duration_ms": 0}
    grounding = metadata.get("evidence_grounding_retry")
    nested = grounding.get("attempts") if isinstance(grounding, dict) else None
    if isinstance(nested, list) and nested:
        children = [retry_accounting(attempt, depth + 1) for attempt in nested[:2]]
        errors = Counter()
        kinds = Counter()
        for child in children:
            errors.update({(issue["field"], issue["type"]): issue["count"] for issue in child["validation_errors"]})
            kinds.update(child["output_retry_kinds"])
        return {"schema_attempts": sum(child["schema_attempts"] for child in children),
            "schema_retries": sum(child["schema_retries"] for child in children),
            "output_recovery_retries": sum(child["output_recovery_retries"] for child in children),
            "output_recovery_duration_ms": (sum(child["output_recovery_duration_ms"] for child in children)
                if all(child["output_recovery_duration_ms"] is not None for child in children) else None),
            "output_retry_kinds": dict(sorted(kinds.items())),
            "validation_errors": [{"field": field, "type": kind, "count": count}
                                  for (field, kind), count in sorted(errors.items())]}
    retry = metadata.get("output_validation_retry")
    retry = retry if isinstance(retry, dict) else {}
    attempts = retry.get("attempts")
    attempts = attempts[:4] if isinstance(attempts, list) else []
    count = retry.get("attempt_count", len(attempts))
    count = count if type(count) is int and 0 <= count <= 4 else len(attempts)
    fields = validation_fields()
    errors = Counter()
    kinds = Counter()
    for index in range(max(0, count - 1)):
        attempt = attempts[index] if index < len(attempts) and isinstance(attempts[index], dict) else {}
        kind = attempt.get("retry_kind")
        kinds[kind if isinstance(kind, str) and kind in {"schema_correction", "compact_output", "timeout_recovery"}
              else "unrecorded"] += 1
    recovery_durations = [attempt.get("duration_ms") if isinstance(attempt, dict) else None
                          for attempt in attempts[1:count]]
    recovery_duration = (sum(recovery_durations) if len(recovery_durations) == max(0, count - 1)
        and all(type(value) is int and value >= 0 for value in recovery_durations) else None)
    for attempt in attempts:
        issues = attempt.get("validation_issues") if isinstance(attempt, dict) else None
        for issue in issues[:12] if isinstance(issues, list) else []:
            if not isinstance(issue, dict):
                continue
            kind = issue.get("type")
            kind = kind if isinstance(kind, str) and kind in SAFE_VALIDATION_TYPES else "validation_error"
            path = issue.get("field")
            parts = path.split(".") if isinstance(path, str) and len(path) <= 256 else ["<unknown>"]
            path = ".".join(part if part in fields or part in {"[]", "$", "<unknown>"} else "<unknown>" for part in parts)
            errors[path, kind] += 1
    return {"schema_attempts": count, "schema_retries": max(0, count - 1),
        "output_recovery_retries": max(0, count - 1),
        "output_recovery_duration_ms": recovery_duration,
        "output_retry_kinds": dict(sorted(kinds.items())),
        "validation_errors": [{"field": field, "type": kind, "count": total}
                              for (field, kind), total in sorted(errors.items())]}


def pipeline_hashes():
    root = Path(__file__).resolve().parents[2]
    paths = [Path(__file__).resolve(), root / "backend/app/worker.py"]
    paths.extend((root / "backend/app/agent").glob("*.py"))
    paths.extend(root / "backend/app/services" / (name + ".py") for name in (
        "http_parser", "payload_decoding", "request_integrity", "json_duplicate_values", "provider_options", "prompt_snapshots",
        "input_schemas", "evidence_editor", "candidate_configurations", "test_runs", "test_comparisons",
        "analysis_retries", "analysis", "uploads", "upload_expected_labels", "crypto", "concurrency"))
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(paths))}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest, hashes


def write_summary(directory, summary):
    """Preserve initial summary and name each current immutable revision."""
    digest = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    filename = "summary-" + digest[:20] + ".json"
    path = directory / filename
    if path.exists():
        if json.loads(path.read_text()) != summary:
            raise ValueError("evaluation_summary_hash_conflict")
    else:
        write_json(path, summary)
    if not (directory / "summary.json").exists():
        write_json(directory / "summary.json", summary)
    return filename


def collect_record(db, analysis, case, revision, duration_ms, idle_wait_ms):
    steps = list(db.scalars(select(AgentStep).join(AgentRun, AgentStep.run_id == AgentRun.id)
        .where(AgentRun.analysis_id == analysis.id).order_by(AgentStep.sequence)))
    phases = []
    for step in steps:
        metadata = step.metadata_json or {}
        error = metadata.get("error_summary")
        code = error.get("code") if isinstance(error, dict) else None
        category = metadata.get("failure_category")
        phases.append({"phase": step.step_type, "status": step.status, "duration_ms": metadata.get("duration_ms"),
            "role": {"llm_primary": "primary", "llm_verifier": "verifier", "llm_evidence_editor": "evidence_editor"}.get(step.step_type),
            "failure_category": category if category in {"output_validation_failed", "output_incomplete", "model_refusal", "invocation_failed"} else None,
            "safe_error_code": code if code in {"output_validation_failed", "model_output_incomplete", "openai_output_incomplete",
                "vllm_output_incomplete", "openai_refusal", "vllm_refusal", "openai_http_401", "openai_http_403", "openai_http_429"} else None,
            "usage": _step_usage(metadata),
            **retry_accounting(metadata),
            "citation_attempts": metadata.get("evidence_grounding_retry", {}).get("attempt_count", 0),
            "editor_called": metadata.get("llm_called") if step.step_type == "llm_evidence_editor" else None,
            "provider_access_failure": code if code in {"openai_http_401", "openai_http_403", "openai_http_429",
                "vllm_http_401", "vllm_http_403", "vllm_http_429"} else None})
    result = analysis.result_json or {}
    reasons = result.get("diagnostics", {}).get("inconclusive_reasons", [])
    technical_causes = [reason for reason in reasons if reason in {
        "verifier_failed", "primary_evidence_rejected", "verifier_evidence_rejected"}]
    mandatory_failures = [{"phase": phase["phase"], "role": phase["role"], "failure_category": phase["failure_category"],
                          "safe_error_code": phase["safe_error_code"]} for phase in phases
        if phase["status"] == "failed" and phase["phase"] != "llm_evidence_editor"]
    return {"revision": revision.FIXED_RULES_VERSION, "event_id": case["event"]["event_id"],
        "group": case["group"], "expected_verdict": case["event"]["expected_verdict"],
        "status": analysis.status, "verdict": analysis.verdict, "severity": analysis.severity,
        "match": analysis.status == "completed" and analysis.verdict == case["event"]["expected_verdict"],
        "duration_ms": duration_ms, "live_idle_wait_ms": idle_wait_ms, "phases": phases,
        "mandatory_stage_failures": mandatory_failures,
        "technical_hold": analysis.verdict == "inconclusive" and bool(technical_causes),
        "technical_hold_causes": technical_causes,
        "input_limited_hold": analysis.verdict == "inconclusive" and "input_integrity_limited" in reasons,
        "verifier_executed": result.get("verifier", {}).get("executed", False),
        "verifier_reasons": result.get("verifier", {}).get("reasons", []),
        "inconclusive_reasons": result.get("diagnostics", {}).get("inconclusive_reasons", []),
        "error_code": analysis.error_code}


def aggregate(records, revisions=None):
    report = {}
    for revision in revisions or (legacy_prompts_v214, prompts):
        rows = [row for row in records if row["revision"] == revision.FIXED_RULES_VERSION]
        completed = [row for row in rows if row["status"] == "completed"]
        matrix = {truth: {predicted: 0 for predicted in VERDICTS} for truth in VERDICTS}
        for row in completed:
            matrix[row["expected_verdict"]][row["verdict"]] += 1
        durations = sorted(row["duration_ms"] for row in completed)
        errors = Counter()
        kinds = Counter()
        measured_recovery = [phase["output_recovery_duration_ms"] for row in rows for phase in row["phases"]
                             if type(phase.get("output_recovery_duration_ms")) is int]
        missing_recovery = sum(phase.get("output_recovery_retries", 0) > 0
            and phase.get("output_recovery_duration_ms") is None for row in rows for phase in row["phases"])
        recovery_retries = sum(phase.get("output_recovery_retries", phase.get("schema_retries",
            max(0, phase["schema_attempts"] - 1))) for row in rows for phase in row["phases"])
        for row in rows:
            for phase in row["phases"]:
                errors.update({(issue["field"], issue["type"]): issue["count"] for issue in phase.get("validation_errors", [])})
                kinds.update(phase.get("output_retry_kinds", {}))
        report[revision.FIXED_RULES_VERSION] = {"cases": len(rows), "completed": len(completed),
            "failed": len(rows) - len(completed), "matches": sum(row["match"] for row in completed),
            "accuracy_completed": sum(row["match"] for row in completed) / len(completed) if completed else None,
            "confusion_matrix": matrix, "median_ms": statistics.median(durations) if durations else None,
            "p95_ms": durations[min(len(durations) - 1, int(.95 * len(durations)))] if durations else None,
            "verifier_calls": sum(row["verifier_executed"] for row in rows),
            "editor_calls": sum(phase["editor_called"] is True for row in rows for phase in row["phases"]),
            "input_tokens_reported": sum((phase["usage"] or {}).get("input_tokens") or 0 for row in rows for phase in row["phases"]),
            "output_tokens_reported": sum((phase["usage"] or {}).get("output_tokens") or 0 for row in rows for phase in row["phases"]),
            "schema_attempts": sum(phase["schema_attempts"] for row in rows for phase in row["phases"]),
            "output_recovery_retries": recovery_retries,
            "output_recovery_duration_ms_reported": sum(measured_recovery),
            "output_recovery_duration_missing_phases": missing_recovery,
            "mandatory_stage_failures": sum(len(row.get("mandatory_stage_failures", [])) for row in rows),
            "technical_holds": sum(row.get("technical_hold", False) for row in rows),
            "technical_hold_causes": dict(Counter(reason for row in rows for reason in row.get("technical_hold_causes", []))),
            "schema_retries": recovery_retries,  # Historical alias, not schema-only.
            "output_retry_kinds": dict(sorted(kinds.items())),
            "validation_errors": [{"field": field, "type": kind, "count": count}
                                  for (field, kind), count in sorted(errors.items())],
            "citation_repairs": sum(max(0, phase["citation_attempts"] - 1) for row in rows for phase in row["phases"])}
    return report


def runtime_settings(args):
    if not args.runtime_config_stdin:
        return get_settings()
    # Pipe directly from the configured runtime; never echo this document.
    value = json.loads(sys.stdin.read(16385))
    allowed = {"database_url", "data_encryption_key", "encryption_key_version", "verifier_confidence_threshold"}
    if not isinstance(value, dict) or set(value) != allowed:
        raise ValueError("evaluation_runtime_document_invalid")
    return Settings(_env_file=None, agent_mode="moduagent", **value)


def run(args):
    diagnostic = args.diagnostic_ten
    revisions = (frozen_diagnostic_revision(),) if diagnostic else (legacy_prompts_v214, prompts)
    cases = diagnostic_cases(args.samples_root) if diagnostic else synthetic_cases(args.samples_root)
    mode = "frozen-v215-diagnostic-ten" if diagnostic else "paired-comparison-thirty"
    if not args.execute_model_calls:
        print(json.dumps({"dry_run": True, "mode": mode, "cases_per_revision": len(cases), "analyses": len(cases) * len(revisions),
            "revisions": [revision.FIXED_RULES_VERSION for revision in revisions],
            "groups": {name: sum(case["group"] == name for case in cases) for name, *_ in SOURCES},
            "model_calls_made": 0, "source_database_read": False, "editor_enabled": True}))
        return 0
    if not args.profile_name or args.output_dir is None:
        raise ValueError("evaluation_profile_and_output_required")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = runtime_settings(args)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    source = readonly_source_engine(settings.database_url)
    scratch = build_engine("sqlite+pysqlite:///:memory:")
    scratch.hide_parameters = True
    records = []
    try:
        values, proof, targets, policy = source_configuration(source, crypto, args.profile_name)
        authority = ReadonlyAuthority(source, values["id"], profile_fingerprint(VLLMProfile(**values)))
        code_hash, source_hashes = pipeline_hashes()
        manifest = {"version": "security-analysis-comparison-v2", "synthetic_only": True,
            "mode": mode, "selection_version": "posthoc-diagnostic-ten-v1" if diagnostic else "stratified-thirty-v1",
            "selection_hash": hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "diagnostic_origin_manifest_hash": DIAGNOSTIC_ORIGIN_MANIFEST_HASH if diagnostic else None,
            "revision_versions": [revision.FIXED_RULES_VERSION for revision in revisions],
            "source_database_readonly": True, "editor_enabled": True, "independent_official_evaluation": False,
            "shared_live_admission": False, "model": values["model_name"], "provider": values["provider"],
            "profile_fingerprint": proof["profile_fingerprint"], "editable_policy_hash": policy["content_hash"],
            "verifier_confidence_threshold": settings.verifier_confidence_threshold,
            "default_input_schema_hash": input_schema_hash(default_definition()),
            "pipeline_code_hash": code_hash, "pipeline_source_hashes": source_hashes,
            "cases": [{"event_id": case["event"]["event_id"], "group": case["group"],
                       "expected_verdict": case["event"]["expected_verdict"]} for case in cases],
            "rules_hashes": {revision.FIXED_RULES_VERSION: hashlib.sha256(revision.FIXED_INSTRUCTIONS.encode()).hexdigest()
                             for revision in revisions},
            "wire_schema_hashes": {revision.FIXED_RULES_VERSION: hashlib.sha256(json.dumps(
                strict_json_schema(model.model_json_schema()) if values["provider"] == "openai" else model.model_json_schema(),
                ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                for revision, model in ((revision, wire_model_for_revision(revision))
                                         for revision in revisions)},
            "editor_instruction_hashes": {"result-editor-v3": hashlib.sha256(result_editor.INSTRUCTIONS.encode()).hexdigest(),
                evidence_editor.VERSION: hashlib.sha256(evidence_editor.INSTRUCTIONS.encode()).hexdigest()}}
        directory = args.output_dir.resolve()
        if directory.exists():
            if not (directory / "manifest.json").is_file() or json.loads((directory / "manifest.json").read_text()) != manifest:
                raise ValueError("evaluation_resume_manifest_mismatch")
        else:
            directory.mkdir(mode=0o700, parents=True)
            write_json(directory / "manifest.json", manifest)
        Base.metadata.create_all(scratch)
        local_settings = settings.model_copy(update={"agent_mode": "moduagent"})
        with Session(scratch) as db:
            db.add(VLLMProfile(**values))
            db.flush()
            db.add(VLLMTestRun(**proof))
            db.add(PromptPolicyVersion(**policy))
            db.flush()
            db.add(PromptPolicyState(id=1, active_version_id=policy["id"], revision=1))
            db.add_all(InternalEgressTarget(**target) for target in targets)
            schema = get_active_schema(db, crypto)
            candidate = CandidateConfiguration(primary_profile_id=values["id"], verifier_profile_id=values["id"],
                evidence_editor_enabled=True, evidence_editor_profile_id=values["id"],
                prompt_policy_version_id=policy["id"], input_schema_version_id=schema.id)
            db.commit()
            analyses = {}
            for revision in revisions:
                with revision_capture(revision):
                    named, _ = enqueue_named_run(db, crypto, local_settings,
                        name="Synthetic security analysis " + revision.FIXED_RULES_VERSION,
                        idempotency_key=revision.FIXED_RULES_VERSION + "-" + mode,
                        rows=[case["event"] for case in cases], kind="upload", actor="isolated-synthetic-comparison",
                        filename="reviewed-synthetic-" + str(len(cases)) + ".json", candidate_configuration=candidate)
                rows = db.scalars(select(Analysis).join(TestRunItem, TestRunItem.analysis_id == Analysis.id)
                    .where(TestRunItem.test_run_id == named.id, TestRunItem.ingest_status == "accepted")).all()
                if len(rows) != len(cases):
                    raise ValueError("evaluation_synthetic_ingest_incomplete")
                analyses[revision.FIXED_RULES_VERSION] = {row.event_id: row for row in rows}
            abort_access = False
            for index, case in enumerate(cases):
                ordered = revisions if index % 2 == 0 else tuple(reversed(revisions))
                for revision in ordered:
                    filename = revision.FIXED_RULES_VERSION + "--" + case["event"]["event_id"] + ".json"
                    path = directory / filename
                    if path.exists():
                        records.append(json.loads(path.read_text())["record"])
                        continue
                    row = analyses[revision.FIXED_RULES_VERSION][case["event"]["event_id"]]
                    started, previous_wait = time.monotonic(), authority.wait_ms
                    try:
                        authority()
                        worker.process_moduagent(db, crypto, row, "", settings.verifier_confidence_threshold, request_check=authority)
                    except Exception as exc:
                        db.rollback()
                        worker.mark_failed(db, row, exc)
                    record = collect_record(db, row, case, revision, round((time.monotonic() - started) * 1000),
                                            authority.wait_ms - previous_wait)
                    record["authority_denied"] = authority.blocked
                    records.append(record)
                    # Only results of hash-whitelisted synthetic inputs. Never
                    # dump settings, ORM profiles, step ciphertext or prompts.
                    write_json(path, {"record": record, "synthetic_result": row.result_json})
                    print(json.dumps(record, ensure_ascii=False), flush=True)
                    if authority.blocked or any(phase["provider_access_failure"] for phase in record["phases"]):
                        abort_access = True
                        break  # Do not consume the remaining budget on auth/quota failures.
                if abort_access:
                    break
        summary = {"manifest": manifest, "comparison": aggregate(records, revisions),
            "metric_definitions": {"output_recovery_retries": "All structured-output recovery retries; see output_retry_kinds for causes.",
                "schema_retries": "Historical alias of output_recovery_retries, not schema-only failures."},
            "limitations": ["Synthetic references are not approved expert Ground Truth.",
                "Source idle checks do not atomically reserve live server capacity.",
                "Serving load and nondeterminism affect latency; no general speed guarantee.",
                "Reported usage may be incomplete; token totals are not billing totals.",
                "Diagnostic-ten is a post-hoc cause investigation, not an unbiased quality estimate."]}
        summary_file = write_summary(directory, summary)
        print(json.dumps({"finished": True, "summary_file": summary_file,
                          "comparison": summary["comparison"]}, ensure_ascii=False), flush=True)
        return 0 if len(records) == len(cases) * len(revisions) and all(row["status"] == "completed" for row in records) else 1
    finally:
        scratch.dispose()
        source.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-root", type=Path, default=Path(__file__).resolve().parents[2] / "samples")
    parser.add_argument("--profile-name")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--execute-model-calls", action="store_true")
    parser.add_argument("--diagnostic-ten", action="store_true", help="Exactly ten targeted cases, frozen v2.15 only; no baseline calls")
    parser.add_argument("--runtime-config-stdin", "--context-stdin", action="store_true",
                        help="Read only DB URL, encryption key/version and threshold from a direct private runtime pipe")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception:
        # Do not echo source paths, credentials, prompts or upstream errors.
        print(json.dumps({"error": "isolated_comparison_failed", "raw_details_omitted": True}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
