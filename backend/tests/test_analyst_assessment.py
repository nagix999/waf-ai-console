import copy
import json

import pytest
from pydantic import ValidationError

from app.agent.analyst_assessment import build_analyst_assessment
from app.agent.contracts import EvidenceAssessmentOutput, EvidenceSelectionOutput, WAFAnalysisOutput
from app.agent.policy import finalize_with_verifier
from app.services.provider_options import strict_json_schema
from test_evidence_selection import RAW, build, patch, selected
from test_grounding_repair import execute


def assessed(*supports, verdict="true_positive", ids=None, issue=None):
    value = selected(*(ids or ["c1"] * len(supports))).model_dump(mode="json")
    value["verdict"] = verdict
    value["threat_analysis"]["severity"] = {"true_positive": "HIGH", "false_positive": "NONE", "inconclusive": "UNKNOWN"}[verdict]
    for index, (item, support) in enumerate(zip(value["evidence"], supports, strict=True)):
        item.update(supports=support, interpretation_ko=f"code_verifier 요청에 대한 {support} 해석 {index}")
    value["decision_issue"] = issue
    return EvidenceAssessmentOutput.model_validate(value)


def run(monkeypatch, *outputs):
    return execute(monkeypatch, outputs, raw=RAW, built=build(), assessment_enabled=True)


def issue(indexes=None):
    return {"point_ko": "입력값이 조회 조건인지 일반 검색어인지 구분해야 합니다.",
            "evidence_indexes": indexes or [0], "missing_condition_ko": "검색어를 데이터로만 처리하는지 확인되지 않았습니다."}


def test_new_schema_is_strict_but_old_contract_stays_unchanged():
    schema = strict_json_schema(EvidenceAssessmentOutput.model_json_schema())
    assert schema["additionalProperties"] is False
    assert "supports" in schema["$defs"]["AssessedEvidenceSelection"]["required"]
    assert "decision_issue" in schema["required"]
    assert "supports" not in EvidenceSelectionOutput.model_json_schema()["$defs"]["EvidenceSelection"]["properties"]
    assert "decision_issue" not in WAFAnalysisOutput.model_fields
    value = assessed("context").model_dump(mode="json")
    del value["evidence"][0]["supports"]
    with pytest.raises(ValidationError):
        EvidenceAssessmentOutput.model_validate(value)


def test_both_roles_keep_all_ten_evidence_without_changing_final_policy(monkeypatch):
    primary, _ = run(monkeypatch, assessed(*(["true_positive"] * 5)))
    verifier, _ = run(monkeypatch, assessed(*(["false_positive"] * 5), verdict="false_positive"))
    before = copy.deepcopy((primary, verifier))
    final = finalize_with_verifier(primary.output, verifier.output, ["low_confidence"])
    view = build_analyst_assessment(primary, verifier)
    assert len(final.output.evidence) == 5  # Existing policy contract is untouched.
    assert final.output.verdict.value == "inconclusive"
    assert len(view["evidence"]) == 10
    assert [item["supports"] for item in view["evidence"]] == ["true_positive"] * 5 + ["false_positive"] * 5
    assert all(item["excerpt"] == RAW for item in view["evidence"])
    assert (primary, verifier) == before
    assert RAW not in json.dumps(primary.telemetry)
    assert "code_verifier" not in json.dumps(primary.telemetry)
    assert primary.grounding_history[0]["assessment"]["evidence"][0]["supports"] == "true_positive"


def test_exact_duplicates_merge_origins_but_opposing_and_context_evidence_survive(monkeypatch):
    output = assessed("true_positive", "false_positive", "context", verdict="inconclusive", issue=issue([0, 1]))
    primary, _ = run(monkeypatch, output)
    verifier, _ = run(monkeypatch, output)
    view = build_analyst_assessment(primary, verifier)
    assert len(view["evidence"]) == 3
    assert all(len(item["origins"]) == 2 for item in view["evidence"])
    assert view["decision_issues"] == [{"point_ko": issue()["point_ko"], "evidence_ids": ["e1", "e2"],
        "missing_condition_ko": issue()["missing_condition_ko"], "origins": ["primary", "verifier"]}]


def test_citation_repair_preserves_direction_and_reattaches_issue_by_index(monkeypatch):
    first = assessed("false_positive", ids=["c999"], verdict="inconclusive", issue=issue())
    before = first.model_dump(mode="json")
    call, calls = run(monkeypatch, first, patch(0, "c1"))
    view = build_analyst_assessment(call)
    assert len(calls) == 2
    assert calls[0]["output_model"] is EvidenceAssessmentOutput
    assert set(calls[1]["output_model"].model_json_schema()["$defs"]["EvidenceSelectionCorrection"]["properties"]) == {"index", "source_id"}
    assert view["evidence"][0]["supports"] == "false_positive"
    assert view["evidence"][0]["origins"][0]["source_id"] == "c1"
    assert view["decision_issues"][0]["evidence_ids"] == ["e1"]
    assert first.model_dump(mode="json") == before


def test_rejected_citations_and_their_issue_are_not_published(monkeypatch):
    call, _ = run(monkeypatch, assessed("true_positive", "false_positive", ids=["c1", "c999"], issue=issue([1])), patch(1, "c999"))
    view = build_analyst_assessment(call)
    assert call.output.verdict.value == "inconclusive"
    assert len(view["evidence"]) == 1
    assert view["evidence"][0]["supports"] == "true_positive"
    assert not view["decision_issues"]
    assert view["omitted_issue_count"] == 1


def test_bad_issue_reference_does_not_change_a_grounded_verdict(monkeypatch):
    call, _ = run(monkeypatch, assessed("context", issue=issue([4])))
    view = build_analyst_assessment(call)
    assert call.output.verdict.value == "true_positive"
    assert view["evidence"][0]["supports"] == "context"  # Not inferred from that verdict.
    assert view["decision_issues"] == [] and view["omitted_issue_count"] == 1


def test_decisive_role_cannot_create_missing_condition_for_a_later_hold(monkeypatch):
    call, _ = run(monkeypatch, assessed("true_positive", issue=issue()))
    view = build_analyst_assessment(call)
    assert view["decision_issues"][0]["missing_condition_ko"] is None


def test_failed_repair_does_not_resurrect_first_attempt_evidence(monkeypatch):
    failed, _ = run(monkeypatch, assessed("true_positive", ids=["c999"]), None)
    assert not failed.succeeded
    assert build_analyst_assessment(failed)["evidence"] == []
    primary, _ = run(monkeypatch, assessed("context"))
    assert build_analyst_assessment(primary, failed) == build_analyst_assessment(primary)


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("repair", [False, True])
def test_assessment_contract_on_moduagent_transport_with_fake_http(monkeypatch, provider, repair):
    import asyncio
    import httpx
    from app.agent.executor import execute_structured_agent
    from test_agent_openai import completion, install_http, openai_profile, assert_strict_schema
    from test_agent_executor import profile
    reply = assessed("true_positive", "false_positive", verdict="inconclusive", issue=issue([0, 1]))
    bad = reply.model_dump(mode="json")
    bad["evidence"][0]["supports"] = "synthetic-invalid-support"
    requests, _ = install_http(monkeypatch, lambda request, count: httpx.Response(200,
        json=completion(json.dumps(bad) if repair and count == 1 else reply.model_dump_json())))
    result = asyncio.run(execute_structured_agent(profile=openai_profile() if provider == "openai" else profile(),
        api_key="synthetic-key", instructions="synthetic-assessment-policy", user_input=build().text,
        session_id="synthetic", agent_name="waf-primary", output_model=EvidenceAssessmentOutput, egress_check=lambda: None))
    assert result.succeeded and result.output == reply
    assert len(requests) == (2 if repair else 1)
    schema = json.loads(requests[0].content)["response_format"]["json_schema"]["schema"]
    assert "supports" in schema["$defs"]["AssessedEvidenceSelection"]["properties"]
    if provider == "openai":
        assert_strict_schema(schema)
    if repair:
        assert result.telemetry["output_validation_retry"]["recovered"]
        assert "synthetic-invalid-support" not in json.dumps(result.telemetry)
