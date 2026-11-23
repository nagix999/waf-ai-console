"""Resolve original links before normalizing exact canonical evidence aliases.

Canonical evidence has already merged only identical field, excerpt, meaning
and support records, retaining their origins and source spans. Such aliases
may map an original valid edge to a self edge or repeat an existing edge. No
source, interpretation, support or model-history value is changed here.
"""
from dataclasses import replace

from .evidence_relations import (
    VERSION, EvidenceRelationsError, _canonical_index, _relations,
    assert_relation_sources, validate_evidence_relations,
)


def _expanded_call(call):
    if not call or not call.succeeded or not call.grounding_history:
        return call
    initial, *remaining = call.grounding_history
    expanded = initial.get("expanded_output")
    if not isinstance(expanded, dict):
        raise EvidenceRelationsError()
    return replace(call, grounding_history=[{**initial, "output": expanded}, *remaining])


def build_evidence_relations(primary, verifier=None, *, evidence):
    _, origins = _canonical_index(evidence)
    groups = {}
    for role, original_call in (("primary", primary), ("verifier", verifier)):
        call = _expanded_call(original_call)
        if not call or not call.succeeded or not call.grounding_history:
            continue
        grounding = call.telemetry.get("evidence_grounding", {})
        retry = call.telemetry.get("evidence_grounding_retry", {})
        if (grounding.get("downgraded_to_inconclusive") or grounding.get("correction_requires_review")
                or retry.get("requires_review")):
            continue
        initial = call.grounding_history[0].get("output") or {}
        relations = initial.get("evidence_relations", [])
        if not relations:
            continue
        history = call.grounding_history[-1]
        assessment = history.get("assessment") or {}
        # Validate every original dependency before touching canonical aliases.
        # Missing sources, conflicting support and malformed original links
        # remain errors, including links that would later become self edges.
        resolutions = assert_relation_sources(
            assessment, history.get("source_resolutions", []), relations=relations)
        for relation in _relations(relations):
            indexes = [relation.representative_index, *relation.related_indexes]
            ids = [origins.get((role, index, resolutions[index])) for index in indexes]
            if any(identifier is None for identifier in ids):
                raise EvidenceRelationsError("evidence_relation_source_missing")
            representative, *related = ids
            for identifier in related:
                if identifier == representative:
                    continue  # Same exact canonical observation, no association.
                dependencies = groups.setdefault((role, representative), [])
                if identifier not in dependencies:
                    dependencies.append(identifier)
    document = {"version": VERSION, "groups": [
        {"role": role, "representative_evidence_id": representative,
         "related_evidence_ids": related}
        for (role, representative), related in groups.items()
    ]}
    return validate_evidence_relations(document, evidence)
