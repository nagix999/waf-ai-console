"""Use the exact v2.36 instructions for new work after withdrawing v2.37.

Keep the supported-version sets at v2.37 so stored executions still select
their original schema and engine. These sets do not activate v2.37 for new work.
"""
from .legacy_prompts_v236 import (
    DEFAULT_POLICY_NAME, DEFAULT_POLICY_TEXT, policy_reserved_tokens,
    PROMPT_VERSION, FIXED_RULES_VERSION, FIXED_INSTRUCTIONS,
    PRIMARY_ROLE, VERIFIER_ROLE, build_role_instructions,
    BASE_INSTRUCTIONS, PRIMARY_INSTRUCTIONS, VERIFIER_INSTRUCTIONS,
)
from .legacy_prompts_v237 import SEMANTIC_RULES_VERSIONS, V3_RULES_VERSIONS
