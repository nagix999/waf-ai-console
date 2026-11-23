"""Concise first-pass instructions retain shared security criteria and history."""
import hashlib
from pathlib import Path

from app.agent import legacy_prompts_v227 as previous, prompts
from app.agent.contracts_v227 import SecurityAnalysisOutputV227
from app.agent.contracts_v228 import SecurityAnalysisOutputV228
from app.agent.output_schema_v226 import compact_strict_schema


def test_archived_instructions_and_admin_text_remain_exact():
    assert hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest() == "7efcf5977c26b06c31d0b4b1faa5a8feae575e9d189b9a7997c1a68108993174"
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.28"
    assert previous.V3_RULES_VERSIONS <= prompts.V3_RULES_VERSIONS
    assert prompts.DEFAULT_POLICY_TEXT == previous.DEFAULT_POLICY_TEXT
    assert prompts.policy_reserved_tokens is previous.policy_reserved_tokens
    policy = "보존할 관리자 지침\n{{ literal_input }} 그대로 유지"
    primary, verifier = prompts.build_role_instructions(policy)
    assert primary.removesuffix(prompts.PRIMARY_ROLE) == verifier.removesuffix(prompts.VERIFIER_ROLE)
    assert "\n" + policy + "\n\n" in primary
    assert "다른 Agent의 답변은 제공되지 않는다" in verifier


def test_brevity_applies_from_first_call_without_dropping_combined_or_opposing_evidence():
    common = prompts.FIXED_INSTRUCTIONS
    for text in ("첫 응답부터", "관련 근거까지 최대 5개인 단일 목록", "related_evidence_ids",
                 "필수 원문과 중요한 반대 근거를 보존", "같은 설명을 여러 필드에 반복하지 않는다",
                 "사실 나열로 끝내지 않는다", "표준/읽기 전용 기능", "시그니처/등록 설명/비교 내용은 context 전용",
                 "같은 방향의 요청 내용 해석", "보류라고 타당한 공격 근거를 context로 바꾸지 않는다"):
        assert text in common
    for text in ("contribution_ko", "related_sources", "decision_issue.source_ids", "/wwwroot", "Range:", "CVE-", "React2Shell", "2048", "Gemma", "Qwen"):
        assert text not in common


def test_generation_bound_matches_expanded_total_instead_of_nested_twenty_five():
    old = compact_strict_schema(SecurityAnalysisOutputV227.model_json_schema())
    new = compact_strict_schema(SecurityAnalysisOutputV228.model_json_schema())
    groups = old["properties"]["evidence"]["maxItems"]
    related = old["$defs"]["EvidenceInterpretation"]["properties"]["related_sources"]["maxItems"]
    assert groups * (1 + related) == 25
    assert new["properties"]["evidence"]["maxItems"] == 5
    assert "related_sources" not in str(new)
