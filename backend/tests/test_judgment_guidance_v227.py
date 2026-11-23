"""Composition, archived identity and no paid/model quality claims."""
import hashlib
from pathlib import Path

from app.agent import legacy_prompts_v226 as previous, legacy_prompts_v227 as prompts
from app.agent.contracts_v227 import SecurityAnalysisOutputV227
from app.agent.output_schema_v226 import compact_strict_schema


def test_previous_instructions_are_exact_and_admin_text_is_not_rewritten():
    assert hashlib.sha256(Path(previous.__file__).read_bytes()).hexdigest() == "bf1bf44c4df2cf8ae356ac6027fedde0f4aca458448fcb58be3e5c3a324a721b"
    assert prompts.FIXED_RULES_VERSION == "waf-system-v2.27"
    assert previous.V3_RULES_VERSIONS <= prompts.V3_RULES_VERSIONS
    assert prompts.DEFAULT_POLICY_TEXT == previous.DEFAULT_POLICY_TEXT
    assert prompts.policy_reserved_tokens is previous.policy_reserved_tokens
    policy = "보존할 관리자 지침\n{{ literal_input }} 그대로 유지"
    primary, verifier = prompts.build_role_instructions(policy)
    assert primary.removesuffix(prompts.PRIMARY_ROLE) == verifier.removesuffix(prompts.VERIFIER_ROLE)
    assert "\n" + policy + "\n\n" in primary
    assert "다른 Agent의 답변은 제공되지 않는다" in verifier
    assert "해석과 supports 및 판정을 함께 재검토" in verifier


def test_general_criteria_and_internal_wire_agree_without_case_specific_rules():
    common = prompts.FIXED_INSTRUCTIONS
    assert "같은 방향의 요청 내용 해석" in common
    assert "사실 나열로 끝내지 않는다" in common
    assert "표준/읽기 전용 기능" in common
    assert "시그니처/등록 설명/비교 내용은 context 전용" in common
    assert "보류라고 타당한 공격 근거를 context로 바꾸지 않는다" in common
    for text in ("/wwwroot", "Range:", "CVE-", "React2Shell", "2048", "Gemma", "Qwen"):
        assert text not in common
    schema = compact_strict_schema(SecurityAnalysisOutputV227.model_json_schema())
    assert set(schema["$defs"]["EvidenceInterpretation"]["properties"]) == {"source_id", "related_sources", "interpretation_ko", "supports"}
    assert "contribution_ko" in common and "source_ids" in common


def test_current_comparison_can_plan_v226_to_latest_pair_without_db_or_model(monkeypatch, capsys):
    import json
    from scripts import compare_security_analysis_v3 as comparison
    monkeypatch.setattr(comparison, "get_settings", lambda: (_ for _ in ()).throw(AssertionError("No runtime read")))
    assert comparison.main(["--baseline-version", "v2.26"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["revisions"] == ["waf-system-v2.26", comparison.prompts.FIXED_RULES_VERSION]
    assert plan["analyses"] == 60 and plan["model_calls_made"] == 0
    assert plan["source_database_read"] is False
