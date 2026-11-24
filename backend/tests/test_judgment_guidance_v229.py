"""Small reference-policy correction without new analysis criteria or wire fields."""
import hashlib
from pathlib import Path

from app.agent import legacy_prompts_v228 as previous, legacy_prompts_v229 as prompts
from app.agent.contracts_v228 import SecurityAnalysisOutputV228
from app.agent.contracts_v229 import SecurityAnalysisOutputV229
from app.agent.output_schema_v226 import compact_strict_schema


def test_archived_instructions_and_admin_text_remain_exact():
    assert hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest() == "b383f145f8052620c2d810e2866e3bd5a19e8b43c4c5bd89957b475f81c515b9"
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.29"
    assert previous.V3_RULES_VERSIONS <= prompts.V3_RULES_VERSIONS
    assert prompts.DEFAULT_POLICY_TEXT == previous.DEFAULT_POLICY_TEXT
    assert prompts.policy_reserved_tokens is previous.policy_reserved_tokens
    policy = "보존할 관리자 지침\n{{ literal_input }} 그대로 유지"
    primary, verifier = prompts.build_role_instructions(policy)
    assert primary.removesuffix(prompts.PRIMARY_ROLE) == verifier.removesuffix(prompts.VERIFIER_ROLE)
    assert "\n" + policy + "\n\n" in primary
    assert "다른 Agent의 답변은 제공되지 않는다" in verifier
    assert prompts.PRIMARY_ROLE == previous.PRIMARY_ROLE
    assert prompts.VERIFIER_ROLE == previous.VERIFIER_ROLE


def test_only_link_guidance_changes_without_other_analysis_criteria():
    expected = previous.FIXED_INSTRUCTIONS.replace(
        "없는 번호, 자기 연결이나 순환을 만들지 않는다.",
        "서로 필요한 근거는 상호 연결할 수 있으며 연결 자체가 논리적 입증은 아니다. 없는 번호나 자기 연결은 만들지 않는다.")
    assert prompts.FIXED_INSTRUCTIONS == expected
    assert "순환을 만들지 않는다" not in prompts.FIXED_INSTRUCTIONS
    assert "상호 연결할 수" in prompts.FIXED_INSTRUCTIONS


def test_generation_schema_stays_identical_apart_from_the_model_title():
    old = compact_strict_schema(SecurityAnalysisOutputV228.model_json_schema())
    current = compact_strict_schema(SecurityAnalysisOutputV229.model_json_schema())
    old.pop("title")
    current.pop("title")
    assert old == current
    assert current["properties"]["evidence"]["maxItems"] == 5
