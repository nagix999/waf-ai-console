"""Explicit read routing for frozen v1 and candidate-only v2 references."""
from ..agent import evidence_relations as legacy
from ..agent import evidence_relations_v238 as current


def empty_relations(result):
    policy = result.get("policy") if isinstance(result, dict) else None
    version = (current.VERSION if isinstance(policy, dict)
               and policy.get("fixed_rules_version") == "waf-system-v2.38"
               else legacy.VERSION)
    return {"version": version, "groups": []}


def validate_saved_relations(value, result):
    if isinstance(value, dict) and value.get("version") == current.VERSION:
        if result.get("policy", {}).get("fixed_rules_version") != "waf-system-v2.38":
            raise legacy.EvidenceRelationsError()
        return current.validate_evidence_relations(value, result["evidence"])
    if result.get("policy", {}).get("fixed_rules_version") == "waf-system-v2.38":
        raise legacy.EvidenceRelationsError()
    return legacy.validate_evidence_relations(value, result["evidence"])
