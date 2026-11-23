"""Instruction identity and composition; these checks do not measure LLM quality."""
import hashlib
from pathlib import Path

from app.agent import legacy_prompts_v225 as previous, legacy_prompts_v226 as prompts


def test_new_instructions_preserve_the_exact_previous_revision_and_admin_policy():
    assert hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest() == (
        "251518da4b037532a18dfbe6e4fcf52dcfc9695ca7fce977a5b6f2059441fd75"
    )
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.26"
    assert previous.V3_RULES_VERSIONS <= prompts.V3_RULES_VERSIONS
    assert previous.SEMANTIC_RULES_VERSIONS <= prompts.SEMANTIC_RULES_VERSIONS
    assert prompts.DEFAULT_POLICY_TEXT == previous.DEFAULT_POLICY_TEXT
    assert prompts.policy_reserved_tokens is previous.policy_reserved_tokens
    assert len(prompts.FIXED_INSTRUCTIONS.encode()) < len(previous.FIXED_INSTRUCTIONS.encode())


def test_roles_share_one_criterion_without_rewriting_admin_text_or_showing_each_other_answers():
    policy = "보존할 관리자 지침\nJSON 표기와 줄바꿈은 유지한다."
    primary, verifier = prompts.build_role_instructions(policy)
    common = primary.removesuffix(prompts.PRIMARY_ROLE)
    assert common == verifier.removesuffix(prompts.VERIFIER_ROLE)
    assert common.startswith(prompts.FIXED_INSTRUCTIONS + "\n\n")
    assert "\n" + policy + "\n\n" in common
    assert "충돌하면 위 필수 규칙을 따른다" in common
    assert "다른 Agent의 답변은 제공되지 않는다" in prompts.VERIFIER_ROLE
    assert "자신이 도출한 근거" in prompts.VERIFIER_ROLE


def test_task_guidance_keeps_detection_as_context_and_requires_meaningful_evidence():
    common = prompts.FIXED_INSTRUCTIONS
    assert common.startswith("당신은 WAF 탐지 이벤트를 검토하는 보안 관제 분석가다")
    assert "관측 → 요청에서의 역할 → 공격/정상 해석의 이유" in common
    assert "탐지/차단 자체로 공격을 확정" in common
    assert "시그니처와 설명 및 비교 결과는 context로만 선택" in common
    assert "공격 구문 부재, 시그니처 불일치" in common
    assert "양쪽 해석을 억지로 채우지 않는다" in common
    assert "시그니처/패딩/추정으로 보이지 않는 공격 코드나 우회 성공을 만들지 않는다" in common
    assert "이미 뒷받침된 공격에 전체 코드/성공 결과를 요구하지 않는다" in common
    for value in ("1024", "2048", "CVE-", "React2Shell", "/wp-config", "Qwen", "Gemma"):
        assert value not in common
