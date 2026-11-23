"""Keep the exact v2.28 input and source blocks for the v2.29 candidate."""
from . import input_builder_v228 as legacy

INPUT_POLICY_VERSION = legacy.INPUT_POLICY_VERSION
FIXED_RULES_VERSION = "waf-system-v2.29"


def build_agent_input(event, raw_payload, parsed, context_window, max_output_tokens, *,
                      fixed_rules_version=None, **kwargs):
    if fixed_rules_version != FIXED_RULES_VERSION:
        raise ValueError("unsupported_candidate_input_contract")
    return legacy.build_agent_input(event, raw_payload, parsed, context_window, max_output_tokens,
        fixed_rules_version=legacy.FIXED_RULES_VERSION, **kwargs)
