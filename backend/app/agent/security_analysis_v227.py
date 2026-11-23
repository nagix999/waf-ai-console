"""Preserve request summaries when independent judgments disagree."""
from .security_analysis import (
    canonical_evidence, conflict_observed, decision_evidence_ids, guidance,
    observational_summary as previous_summary, role_result,
)


def observational_summary(primary, verifier, finalization, integrity_metadata=None):
    # Disagreement is a policy outcome. It does not prove that the request
    # itself has two valid security interpretations. Preserve observed content.
    if (finalization.agreement is False and verifier is not None and verifier.succeeded
            and not any(primary.telemetry.get("evidence_grounding", {}).get(key)
                        for key in ("downgraded_to_inconclusive", "correction_requires_review"))
            and not (integrity_metadata or {}).get("downgraded_to_inconclusive")):
        return primary.output.summary_ko
    return previous_summary(primary, verifier, finalization, integrity_metadata)
