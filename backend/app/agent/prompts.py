"""Observe failed evidence attempts without changing analysis instructions."""
from .legacy_prompts_v234 import (
    BASE_INSTRUCTIONS, DEFAULT_POLICY_NAME, DEFAULT_POLICY_TEXT,
    FIXED_INSTRUCTIONS, PRIMARY_INSTRUCTIONS, PRIMARY_ROLE,
    VERIFIER_INSTRUCTIONS, VERIFIER_ROLE, build_role_instructions,
    policy_reserved_tokens,
)
from .legacy_prompts_v234 import SEMANTIC_RULES_VERSIONS as PREVIOUS_SEMANTIC_RULES
from .legacy_prompts_v234 import V3_RULES_VERSIONS as PREVIOUS_V3_RULES

PROMPT_VERSION = "waf-judgment-v2.35"
FIXED_RULES_VERSION = "waf-system-v2.35"
V3_RULES_VERSIONS = PREVIOUS_V3_RULES | {FIXED_RULES_VERSION}
SEMANTIC_RULES_VERSIONS = PREVIOUS_SEMANTIC_RULES | {FIXED_RULES_VERSION}
