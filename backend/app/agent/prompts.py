"""New candidate: recover a source-valid subset only after same-role decision review.

Historical instructions and engines remain pinned to their archived versions.
"""
from .legacy_prompts_v239 import (
    DEFAULT_POLICY_NAME, DEFAULT_POLICY_TEXT, policy_reserved_tokens,
    PROMPT_VERSION, FIXED_RULES_VERSION, FIXED_INSTRUCTIONS,
    PRIMARY_ROLE, VERIFIER_ROLE, build_role_instructions,
    BASE_INSTRUCTIONS, PRIMARY_INSTRUCTIONS, VERIFIER_INSTRUCTIONS,
    SEMANTIC_RULES_VERSIONS, V3_RULES_VERSIONS,
)
