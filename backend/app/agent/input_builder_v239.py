"""Pin the v2.38 input contract for the recovery-only v2.39 candidate."""
from . import input_builder_v238 as legacy
from .input_builder import PROMPT_SCHEMA_RESERVED_TOKENS

INPUT_POLICY_VERSION = legacy.INPUT_POLICY_VERSION
FIXED_RULES_VERSION = "waf-system-v2.39"


def build_agent_input(event, raw_payload, parsed, context_window, max_output_tokens, *,
                      decoding=None, prompt_reserved_tokens=PROMPT_SCHEMA_RESERVED_TOKENS,
                      evidence_selection=False, request_integrity=None, fixed_rules_version=None):
    if fixed_rules_version != FIXED_RULES_VERSION:
        raise ValueError("unsupported_candidate_input_contract")
    return legacy.build_agent_input(event, raw_payload, parsed, context_window, max_output_tokens,
        decoding=decoding, prompt_reserved_tokens=prompt_reserved_tokens,
        evidence_selection=evidence_selection, request_integrity=request_integrity,
        fixed_rules_version=legacy.FIXED_RULES_VERSION)
