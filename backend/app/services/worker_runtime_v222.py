"""Additive dispatch for v2.22 without changing earlier runtime globals."""
import re

from . import worker_runtime as legacy
from .prompt_snapshots import PromptSnapshotError, load_analysis_prompt


RULES_VERSION = "waf-system-v2.22"


def execution_rules(analysis, crypto):
    """Use encrypted provenance, never an editable label, to choose execution."""
    if analysis.prompt_snapshot_ciphertext:
        version = load_analysis_prompt(analysis, crypto).fixed_rules_version
        marker = re.match(r"^waf-system-v2\.(\d+)$", version)
        if marker and int(marker.group(1)) >= 22 and version != RULES_VERSION:
            raise PromptSnapshotError()
        return version
    version = str(analysis.prompt_version or "")
    marker = re.match(r"^waf-judgment-v2\.(\d+)", version)
    if marker and int(marker.group(1)) >= 21:
        # A missing new snapshot cannot acquire the legacy reconstruction path.
        raise PromptSnapshotError()
    return None


def process_moduagent(db, crypto, analysis, allowed_targets, verifier_confidence_threshold, *, request_check=None):
    if execution_rules(analysis, crypto) == RULES_VERSION:
        from .. import worker_v222
        return worker_v222.process_moduagent(db, crypto, analysis, allowed_targets,
            verifier_confidence_threshold, request_check=request_check)
    return legacy.process_moduagent(db, crypto, analysis, allowed_targets,
        verifier_confidence_threshold, request_check=request_check)
