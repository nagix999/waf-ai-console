"""Pin recovery-only v2.39 while preserving every v2.38 execution artifact."""
import hashlib
import hmac
import json
from pathlib import Path

from . import evidence_pipeline_v238 as legacy

RULES_VERSION = "waf-system-v2.39"
RULES_VERSIONS = legacy.RULES_VERSIONS | {RULES_VERSION}
VERSION = "evidence-pipeline-v19"
_FILES = (
    "evidence_pipeline_v239.py", "legacy_prompts_v239.py", "legacy_prompts_v238.py", "input_builder_v239.py",
    "execution_policy_v239.py", "evidence_source_policy_v239.py",
    "source_repair_recovery_v239.py", "../worker_v239.py",
    "../services/worker_runtime_v239.py", "../services/worker_concurrency_v239.py",
    "../services/model_validation_worker_v239.py", "../services/worker_coordinator_v239.py",
    "../worker_entrypoint_v239.py", "../services/evidence_display_worker_v239.py",
    "../services/evidence_relation_dispatch_v239.py",
)


def pipeline_artifact():
    root = Path(__file__).parent
    files = dict(legacy.pipeline_artifact()["files"])
    try:
        files.update({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in _FILES})
    except OSError:
        raise ValueError("evidence_pipeline_unavailable") from None
    identity = {"version": VERSION, "input_version": "agent-input-v4",
                "source_catalog_version": "evidence-selection-v5",
                "relations_version": "evidence-relations-v2", "files": files}
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
