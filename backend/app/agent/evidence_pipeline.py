"""Content-free identity of the candidate's input and evidence implementation.

Only v2.20 uses this manifest. A changed implementation invalidates a queued
candidate instead of silently changing an approved or retried execution.
"""
import hashlib
import hmac
import json
from pathlib import Path

RULES_VERSION = "waf-system-v2.20"
VERSION = "evidence-pipeline-v1"
_FILES = (
    "evidence_pipeline.py", "source_blocks.py", "input_builder.py",
    "evidence_candidates.py", "evidence_relations.py", "contracts.py",
    "evidence.py", "grounding_repair.py", "analyst_assessment.py",
    "security_analysis.py", "executor.py", "policy.py", "input_integrity.py",
    "followup_policy.py", "../worker.py", "../services/payload_decoding.py",
    "../services/request_integrity.py", "../services/json_duplicate_values.py",
    "../services/log4j_normalization.py",
)


def pipeline_artifact():
    root = Path(__file__).parent
    try:
        files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in _FILES}
    except OSError:
        raise ValueError("evidence_pipeline_unavailable") from None
    identity = {
        "version": VERSION,
        "input_version": "agent-input-v2",
        "source_catalog_version": "evidence-selection-v3",
        "relations_version": "evidence-relations-v1",
        "files": files,
    }
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return {**identity, "sha256": hashlib.sha256(serialized.encode()).hexdigest()}


def pipeline_for_rules(fixed_rules_version):
    return pipeline_artifact() if fixed_rules_version == RULES_VERSION else None


def validate_pipeline_snapshot(value, fixed_rules_version):
    expected = pipeline_for_rules(fixed_rules_version)
    if expected is None:
        if value is not None:
            raise ValueError("evidence_pipeline_snapshot_invalid")
        return None
    try:
        actual_text = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
        expected_text = json.dumps(expected, sort_keys=True, allow_nan=False, separators=(",", ":"))
        if not hmac.compare_digest(actual_text, expected_text):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("evidence_pipeline_snapshot_invalid") from None
    return expected
