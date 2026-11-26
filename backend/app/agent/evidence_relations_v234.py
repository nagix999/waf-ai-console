"""Resolve reviewed links without reintroducing discarded initial claims."""
from dataclasses import replace

from . import evidence_relations_v230 as legacy
from .evidence_relations import EvidenceRelationsError


def _effective_call(call):
    if not call or not call.succeeded or not call.telemetry.get("evidence_decision_review", {}).get("recovered"):
        return call
    if not call.grounding_history:
        raise EvidenceRelationsError()
    last = call.grounding_history[-1]
    if (last.get("kind") != "decision_review" or last.get("decision_review_validated") is not True
            or not isinstance(last.get("expanded_output"), dict)):
        raise EvidenceRelationsError()
    # Only the temporary consumer view changes. The encrypted initial and
    # repair snapshots still retain their original full relationships.
    first, *remaining = call.grounding_history
    return replace(call, grounding_history=[{**first, "expanded_output": last["expanded_output"]}, *remaining])


def build_evidence_relations(primary, verifier=None, *, evidence):
    return legacy.build_evidence_relations(_effective_call(primary), _effective_call(verifier), evidence=evidence)
