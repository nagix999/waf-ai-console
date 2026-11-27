"""Read v2.39 through the identical v2.38 relation contract, preserving archives."""
from . import evidence_relation_dispatch as legacy

RULES_VERSION = "waf-system-v2.39"


def _read_contract(result):
    policy = result.get("policy") if isinstance(result, dict) else None
    if isinstance(policy, dict) and policy.get("fixed_rules_version") == RULES_VERSION:
        return {**result, "policy": {**policy, "fixed_rules_version": "waf-system-v2.38"}}
    return result


def empty_relations(result):
    return legacy.empty_relations(_read_contract(result))


def validate_saved_relations(value, result):
    return legacy.validate_saved_relations(value, _read_contract(result))
