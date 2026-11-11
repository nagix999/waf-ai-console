"""Explicit execution dispatch by a validated, encrypted prompt snapshot.

The old worker and its globals are never patched. The existing claim, lease,
heartbeat and slot coordinators remain the only work owners.
"""
from .. import worker as legacy
from .prompt_snapshots import load_analysis_prompt, PromptSnapshotError


def process_moduagent(db, crypto, analysis, allowed_targets, verifier_confidence_threshold, *, request_check=None):
    if analysis.prompt_snapshot_ciphertext:
        prompt = load_analysis_prompt(analysis, crypto)
        if prompt.fixed_rules_version == "waf-system-v2.21":
            from .. import worker_v221
            return worker_v221.process_moduagent(db, crypto, analysis, allowed_targets,
                verifier_confidence_threshold, request_check=request_check)
    elif str(analysis.prompt_version or "").startswith("waf-judgment-v2.21"):
        # Lost new provenance is not a legacy row and must never be rebuilt.
        raise PromptSnapshotError()
    return legacy.process_moduagent(db, crypto, analysis, allowed_targets,
        verifier_confidence_threshold, request_check=request_check)
