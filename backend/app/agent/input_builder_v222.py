"""Reuse the exact v2.21 source input; only output recovery changes in v2.22."""
from .input_builder_v221 import build_agent_input as _build


def build_agent_input(*args, fixed_rules_version=None, **kwargs):
    if fixed_rules_version != "waf-system-v2.22":
        raise ValueError("unsupported_candidate_input_contract")
    return _build(*args, fixed_rules_version="waf-system-v2.21", **kwargs)
