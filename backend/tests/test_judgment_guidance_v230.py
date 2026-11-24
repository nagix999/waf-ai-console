"""Only source selection guidance changes; public output and analysis stay pinned."""
import hashlib
from pathlib import Path

from app.agent import legacy_prompts_v229 as previous, legacy_prompts_v230 as prompts


def test_old_instructions_remain_exact_and_roles_share_the_same_new_criteria():
    assert hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest() == "a0c3276ff44b8305636110569a86a79be5d51b719b4e9ce0db7295f88d4f825d"
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.30"
    assert previous.V3_RULES_VERSIONS <= prompts.V3_RULES_VERSIONS
    assert prompts.DEFAULT_POLICY_TEXT == previous.DEFAULT_POLICY_TEXT
    assert prompts.PRIMARY_ROLE == previous.PRIMARY_ROLE
    assert prompts.VERIFIER_ROLE == previous.VERIFIER_ROLE
    primary, verifier = prompts.build_role_instructions("보존할 관리자 지침")
    assert primary.removesuffix(prompts.PRIMARY_ROLE) == verifier.removesuffix(prompts.VERIFIER_ROLE)
    assert "다른 Agent의 답변은 제공되지 않는다" in verifier


def test_only_two_source_guidance_lines_change_and_prompt_growth_is_bounded():
    old = previous.FIXED_INSTRUCTIONS.splitlines()
    current = prompts.FIXED_INSTRUCTIONS.splitlines()
    assert len(old) == len(current)
    changed = [(before, after) for before, after in zip(old, current) if before != after]
    assert len(changed) == 2
    assert changed[0][0].startswith("- evidence는")
    assert changed[1][0].startswith("- 핵심 해석")
    assert "source_id의 field와 원문을 대조" in changed[0][1]
    assert "시그니처/등록 설명은 context에만" in changed[0][1]
    assert "allowed_supports를 지킨다" in changed[0][1]
    assert len(prompts.FIXED_INSTRUCTIONS) <= len(previous.FIXED_INSTRUCTIONS) + 80
