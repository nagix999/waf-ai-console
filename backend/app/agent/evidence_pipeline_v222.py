"""Additive v2.22 recovery provenance; all earlier manifests stay byte-exact."""
import hashlib
import hmac
import json
from pathlib import Path

from . import evidence_pipeline_v221 as legacy

RULES_VERSION = "waf-system-v2.22"
RULES_VERSIONS = legacy.RULES_VERSIONS | {RULES_VERSION}
VERSION = "evidence-pipeline-v3"
_FILES = (
    "evidence_pipeline_v222.py", "input_builder_v222.py", "executor_v222.py",
    "relation_recovery.py", "execution_policy_v222.py", "../worker_v222.py",
    "../services/worker_runtime_v222.py", "../services/worker_concurrency_v222.py",
    "../services/model_validation_worker_v222.py", "../worker_entrypoint_v222.py",
)


def pipeline_artifact():
    root = Path(__file__).parent
    files = dict(legacy.pipeline_artifact()["files"])
    try:
        files.update({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in _FILES})
    except OSError:
        raise ValueError("evidence_pipeline_unavailable") from None
    identity = {"version": VERSION, "input_version": "agent-input-v2",
                "source_catalog_version": "evidence-selection-v4",
                "relations_version": "evidence-relations-v1", "files": files}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return {**identity, "sha256": hashlib.sha256(encoded.encode()).hexdigest()}


def pipeline_for_rules(fixed_rules_version):
    return pipeline_artifact() if fixed_rules_version == RULES_VERSION else legacy.pipeline_for_rules(fixed_rules_version)


def validate_pipeline_snapshot(value, fixed_rules_version):
    if fixed_rules_version != RULES_VERSION:
        return legacy.validate_pipeline_snapshot(value, fixed_rules_version)
    expected = pipeline_artifact()
    try:
        actual = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
        encoded = json.dumps(expected, sort_keys=True, allow_nan=False, separators=(",", ":"))
        if not hmac.compare_digest(actual, encoded):
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("evidence_pipeline_snapshot_invalid") from None
    return expected
