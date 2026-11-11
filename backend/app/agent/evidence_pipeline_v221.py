"""Additive v2.21 provenance; never rehash or substitute a v2.20 approval."""
import hashlib
import hmac
import json
from pathlib import Path

from . import evidence_pipeline as legacy

RULES_VERSION = "waf-system-v2.21"
RULES_VERSIONS = frozenset({legacy.RULES_VERSION, RULES_VERSION})
VERSION = "evidence-pipeline-v2"
_FILES = (
    "evidence_pipeline_v221.py", "input_builder_v221.py", "executor_v221.py",
    "structured_json.py", "evidence_source_policy.py", "execution_policy_v221.py",
    "../worker_v221.py", "../services/worker_runtime.py",
    "../services/worker_concurrency.py", "../services/model_validation_worker.py",
    "../services/provider_options.py",
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
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return {**identity, "sha256": hashlib.sha256(serialized.encode()).hexdigest()}


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
