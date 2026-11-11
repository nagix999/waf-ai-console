"""Internal, source-resolved evidence links, separate from the public v3 result.

The representative's existing interpretation is the only explanation. Links do
not create a new verdict, change supports, or prove semantic correctness.
"""
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from .contracts import EvidenceRelation, StrictContract

VERSION = "evidence-relations-v1"


class EvidenceRelationsError(ValueError):
    """Fixed diagnostics only: never expose input, model text or raw payload."""

    def __init__(self, code="evidence_relations_invalid"):
        self.code = code
        super().__init__(code)


class EvidenceRelationGroup(StrictContract):
    role: Literal["primary", "verifier"]
    representative_evidence_id: str = Field(pattern=r"^e[1-9][0-9]?$")
    related_evidence_ids: list[Annotated[str, Field(pattern=r"^e[1-9][0-9]?$")]] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def distinct_evidence(self):
        if (self.representative_evidence_id in self.related_evidence_ids
                or len(set(self.related_evidence_ids)) != len(self.related_evidence_ids)):
            raise ValueError("evidence_relations_invalid")
        return self


class EvidenceRelations(StrictContract):
    version: Literal["evidence-relations-v1"]
    groups: list[EvidenceRelationGroup] = Field(max_length=10)


def _relations(values):
    if not isinstance(values, list) or len(values) > 5:
        raise EvidenceRelationsError()
    try:
        result = [EvidenceRelation.model_validate(value) for value in values]
    except (ValidationError, TypeError, ValueError):
        raise EvidenceRelationsError() from None
    if len({item.representative_index for item in result}) != len(result):
        raise EvidenceRelationsError()
    return result


def assert_relation_sources(assessment, source_resolutions, *, relations=None):
    """Check every linked selection survived citation resolution or correction.

    Supply the original wire relationships with the final assessment after a
    citation-only repair. Corrected source IDs come from the final resolutions,
    never from the original invalid selection or an inferred text match.
    """
    if not isinstance(assessment, Mapping):
        raise EvidenceRelationsError()
    parsed = _relations(assessment.get("evidence_relations", []) if relations is None else relations)
    if not parsed:
        return {}
    selections = assessment.get("evidence")
    if (not isinstance(selections, list) or len(selections) > 5
            or not isinstance(source_resolutions, list) or len(source_resolutions) > 5):
        raise EvidenceRelationsError()
    resolved = {}
    for source in source_resolutions:
        if not isinstance(source, Mapping):
            raise EvidenceRelationsError()
        index, source_id = source.get("index"), source.get("source_id")
        if type(index) is not int or not 0 <= index < len(selections) or index in resolved:
            raise EvidenceRelationsError()
        if not isinstance(source_id, str) or not _valid_source_id(source_id):
            raise EvidenceRelationsError()
        selection = selections[index]
        if not isinstance(selection, Mapping) or selection.get("source_id") != source_id:
            raise EvidenceRelationsError("evidence_relation_source_mismatch")
        resolved[index] = source_id
    for relation in parsed:
        indexes = [relation.representative_index, *relation.related_indexes]
        if any(index not in resolved for index in indexes):
            raise EvidenceRelationsError("evidence_relation_source_missing")
        directions = {_support(selections[index].get("supports")) for index in indexes}
        if None in directions or {"attack_request", "normal_request"} <= directions:
            raise EvidenceRelationsError("evidence_relation_conflicting_supports")
    return resolved


def _valid_source_id(value):
    # Match the existing c1..c999 source contract without widening old schemas.
    return (len(value) in (2, 3, 4) and value[0] == "c" and value[1] in "123456789"
            and all(char in "0123456789" for char in value[2:]))


def _support(value):
    # Grounding assessments use the established legacy bridge; public canonical
    # evidence uses request semantics. Their meanings are not inferred by role.
    if not isinstance(value, str):
        return None
    return {"true_positive": "attack_request", "false_positive": "normal_request",
            "attack_request": "attack_request", "normal_request": "normal_request", "context": "context"}.get(value)


def _canonical_index(evidence):
    if not isinstance(evidence, list) or len(evidence) > 10:
        raise EvidenceRelationsError()
    items, origins, role_indexes = {}, {}, set()
    for item in evidence:
        if not isinstance(item, Mapping):
            raise EvidenceRelationsError()
        identifier, references = item.get("evidence_id"), item.get("origins")
        if (not isinstance(identifier, str) or not _valid_evidence_id(identifier) or identifier in items or _support(item.get("supports")) is None
                or not isinstance(references, list) or not references or len(references) > 10):
            raise EvidenceRelationsError()
        items[identifier] = item
        for origin in references:
            if not isinstance(origin, Mapping):
                raise EvidenceRelationsError()
            role, index, source_id = origin.get("role"), origin.get("index"), origin.get("source_id")
            if (not isinstance(role, str) or role not in {"primary", "verifier"}
                    or type(index) is not int or not 0 <= index <= 4
                    or not isinstance(source_id, str) or not _valid_source_id(source_id)):
                raise EvidenceRelationsError()
            key = (role, index, source_id)
            if key in origins or (role, index) in role_indexes:
                raise EvidenceRelationsError("evidence_relation_origin_ambiguous")
            origins[key] = identifier
            role_indexes.add((role, index))
    return items, origins


def _valid_evidence_id(value):
    return (len(value) in (2, 3) and value[0] == "e" and value[1] in "123456789"
            and all(char in "0123456789" for char in value[2:]))


def validate_evidence_relations(value, canonical_evidence):
    """Validate an encrypted relation record against the canonical v3 evidence.

    Invalid records raise one fixed error instead of returning partial groups
    that could present an integrated explanation without an essential source.
    """
    try:
        document = EvidenceRelations.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise EvidenceRelationsError() from None
    items, _ = _canonical_index(canonical_evidence)
    representatives = set()
    role_counts = {"primary": 0, "verifier": 0}
    for group in document.groups:
        key = (group.role, group.representative_evidence_id)
        role_counts[group.role] += 1
        if key in representatives or role_counts[group.role] > 5:
            raise EvidenceRelationsError()
        representatives.add(key)
        ids = [group.representative_evidence_id, *group.related_evidence_ids]
        if any(identifier not in items for identifier in ids):
            raise EvidenceRelationsError("evidence_relation_source_missing")
        if any(not any(origin["role"] == group.role for origin in items[identifier]["origins"]) for identifier in ids):
            raise EvidenceRelationsError("evidence_relation_role_mismatch")
        supports = {_support(items[identifier]["supports"]) for identifier in ids}
        if {"attack_request", "normal_request"} <= supports:
            raise EvidenceRelationsError("evidence_relation_conflicting_supports")
    return document.model_dump(mode="json")


def build_evidence_relations(primary, verifier=None, *, evidence):
    """Map original model indexes through final, source-validated role origins."""
    _, origins = _canonical_index(evidence)
    groups = []
    for role, call in (("primary", primary), ("verifier", verifier)):
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
        resolutions = assert_relation_sources(assessment, history.get("source_resolutions", []), relations=relations)
        for relation in _relations(relations):
            indexes = [relation.representative_index, *relation.related_indexes]
            ids = [origins.get((role, index, resolutions[index])) for index in indexes]
            if any(identifier is None for identifier in ids):
                raise EvidenceRelationsError("evidence_relation_source_missing")
            groups.append({"role": role, "representative_evidence_id": ids[0], "related_evidence_ids": ids[1:]})
    return validate_evidence_relations({"version": VERSION, "groups": groups}, evidence)
