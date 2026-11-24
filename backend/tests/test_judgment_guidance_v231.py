"""Detector observations and rule descriptions differ; request-source limits stay."""
import hashlib
from pathlib import Path

from app.agent import legacy_prompts_v230 as previous, legacy_prompts_v231 as prompts
from app.agent.contracts_v230 import SecurityAnalysisOutputV230
from app.agent.contracts_v231 import SecurityAnalysisOutputV231
from app.agent.output_schema_v226 import compact_strict_schema


def test_old_instruction_archive_and_role_independence_remain_exact():
    assert hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest() == "fd1d8f930d8dcf307d5ea6ecf3c8a415ab70d14e36b19a48a9ca2cf6e7428bf3"
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.31"
    assert previous.V3_RULES_VERSIONS <= prompts.V3_RULES_VERSIONS
    assert prompts.DEFAULT_POLICY_TEXT == previous.DEFAULT_POLICY_TEXT
    assert prompts.PRIMARY_ROLE == previous.PRIMARY_ROLE and prompts.VERIFIER_ROLE == previous.VERIFIER_ROLE
    primary, verifier = prompts.build_role_instructions("보존할 관리자 지침")
    assert primary.removesuffix(prompts.PRIMARY_ROLE) == verifier.removesuffix(prompts.VERIFIER_ROLE)
    assert "다른 Agent의 답변은 제공되지 않는다" in verifier


def test_only_two_common_lines_change_and_context_is_useful_but_not_direct_request_evidence():
    old, current = previous.FIXED_INSTRUCTIONS.splitlines(), prompts.FIXED_INSTRUCTIONS.splitlines()
    assert len(old) == len(current)
    changes = [(a, b) for a, b in zip(old, current) if a != b]
    assert len(changes) == 2
    assert "signature는 전달된 장비 탐지 기록" in changes[0][1]
    assert "signature_explanation은 규칙의 일반 설명" in changes[0][1]
    assert "context는 판정에 활용하는 보조 정보" in changes[1][1]
    assert "시그니처/등록 설명은 context에만" in prompts.FIXED_INSTRUCTIONS
    assert len(prompts.FIXED_INSTRUCTIONS) <= len(previous.FIXED_INSTRUCTIONS) + 100


def test_initial_generation_schema_remains_identical():
    old = compact_strict_schema(SecurityAnalysisOutputV230.model_json_schema())
    current = compact_strict_schema(SecurityAnalysisOutputV231.model_json_schema())
    old.pop("title")
    current.pop("title")
    assert old == current
