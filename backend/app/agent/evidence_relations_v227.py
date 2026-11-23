"""Map server-expanded indexes without rewriting the encrypted model output."""
from dataclasses import replace

from .evidence_relations import EvidenceRelationsError, build_evidence_relations as build_previous


def _expanded_call(call):
    if not call or not call.succeeded or not call.grounding_history:
        return call
    initial, *remaining = call.grounding_history
    expanded = initial.get("expanded_output")
    if not isinstance(expanded, dict):
        raise EvidenceRelationsError()
    return replace(call, grounding_history=[{**initial, "output": expanded}, *remaining])


def build_evidence_relations(primary, verifier=None, *, evidence):
    return build_previous(_expanded_call(primary), _expanded_call(verifier), evidence=evidence)
