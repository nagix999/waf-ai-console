"""Dispatch the v2.35 interpretation engine without upgrading old jobs."""
import re

from . import worker_runtime_v234 as legacy
from .prompt_snapshots import PromptSnapshotError, load_analysis_prompt


RULES_VERSION = "waf-system-v2.35"


def execution_rules(analysis, crypto):
    if analysis.prompt_snapshot_ciphertext:
        version = load_analysis_prompt(analysis, crypto).fixed_rules_version
        marker = re.match(r"^waf-system-v2\.(\d+)$", version)
        if marker and int(marker.group(1)) >= 35 and version != RULES_VERSION:
            raise PromptSnapshotError()
        return version
    marker = re.match(r"^waf-judgment-v2\.(\d+)", str(analysis.prompt_version or ""))
    if marker and int(marker.group(1)) >= 21:
        raise PromptSnapshotError()
    return None


def process_moduagent(db, crypto, analysis, allowed_targets, verifier_confidence_threshold, *, request_check=None):
    if execution_rules(analysis, crypto) == RULES_VERSION:
        from .. import worker_v235
        return worker_v235.process_moduagent(db, crypto, analysis, allowed_targets,
            verifier_confidence_threshold, request_check=request_check)
    return legacy.process_moduagent(db, crypto, analysis, allowed_targets,
        verifier_confidence_threshold, request_check=request_check)
