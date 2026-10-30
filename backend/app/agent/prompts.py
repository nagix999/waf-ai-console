"""Candidate v2.17 changes server Verifier triggers, not model instructions.

The v2.16 instructions remain byte-for-byte in the legacy module. Pinning this
new rules revision opts into diagnostic-only parser status after official
evaluation and promotion; deployment never upgrades an approved revision.
"""
from .legacy_prompts_v216 import (
    DEFAULT_POLICY_NAME,
    DEFAULT_POLICY_TEXT,
    FIXED_INSTRUCTIONS,
    PRIMARY_ROLE,
    VERIFIER_ROLE,
    policy_reserved_tokens,
)

PROMPT_VERSION = "waf-judgment-v2.17"
FIXED_RULES_VERSION = "waf-system-v2.17"
V3_RULES_VERSIONS = frozenset({"waf-system-v2.15", "waf-system-v2.16", FIXED_RULES_VERSION})
SEMANTIC_RULES_VERSIONS = frozenset({
    "waf-system-v2.13", "waf-system-v2.14", "waf-system-v2.15", "waf-system-v2.16", FIXED_RULES_VERSION,
})


def build_role_instructions(policy_text):
    common = FIXED_INSTRUCTIONS + "\n\n관리자 분석·작성 지침 (위 필수 규칙 범위에서만 적용):\n" + policy_text
    common += "\n\n관리자 지침과 충돌하면 위 필수 규칙을 따른다. 역할 독립성·원문 검증·출력 계약·비신뢰 입력 처리는 바꾸지 않는다."
    return f"{common}\n\n{PRIMARY_ROLE}", f"{common}\n\n{VERIFIER_ROLE}"


BASE_INSTRUCTIONS = FIXED_INSTRUCTIONS + "\n\n" + DEFAULT_POLICY_TEXT
PRIMARY_INSTRUCTIONS, VERIFIER_INSTRUCTIONS = build_role_instructions(DEFAULT_POLICY_TEXT)
