"""New candidate: preserve source-bound dependencies across evidence directions.

Historical instructions and engines remain pinned to their archived versions.
"""
from .legacy_prompts_v238 import (
    DEFAULT_POLICY_NAME, DEFAULT_POLICY_TEXT, policy_reserved_tokens,
    PROMPT_VERSION, FIXED_RULES_VERSION, FIXED_INSTRUCTIONS,
    PRIMARY_ROLE, VERIFIER_ROLE, build_role_instructions,
    BASE_INSTRUCTIONS, PRIMARY_INSTRUCTIONS, VERIFIER_INSTRUCTIONS,
    SEMANTIC_RULES_VERSIONS, V3_RULES_VERSIONS,
)
