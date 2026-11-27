"""Synthetic, offline source-repair exhaustion tests for the v2.39 candidate.

These checks exercise actual ModuAgent HTTP serialization. They establish
failure isolation and provenance invariants, not real-model judgment quality.
An incomplete correction is never evidence, even when its JSON is parseable.
"""
from copy import deepcopy
from dataclasses import replace
import asyncio
import json

import httpx
import pytest

from app import worker_v238 as archived_worker
from app import worker_v239 as worker
from app.agent.contracts import AgentVerdict
from app.agent.evidence_relations_v238 import build_evidence_relations
from app.agent.input_builder_v238 import build_agent_input
from app.agent.security_analysis_v234 import canonical_evidence
from test_agent_openai import completion, install_http
from test_source_support_worker_v230 import profile, scenario


BAD_CLAIM = "SYNTHETIC_EXCLUDED_SIGNATURE_CLAIM"
DEPENDENT_CLAIM = "SYNTHETIC_EXCLUDED_DEPENDENT_CLAIM"
BAD_SUMMARY = "SYNTHETIC_EXCLUDED_PRIOR_SUMMARY"
BAD_TECHNIQUE = "SYNTHETIC_EXCLUDED_PRIOR_TECHNIQUE"
BAD_IMPACT = "SYNTHETIC_EXCLUDED_PRIOR_IMPACT"
BAD_PARTIAL = "SYNTHETIC_INCOMPLETE_CORRECTION_MUST_NOT_SURVIVE"
NO_BUDGET = object()


def case(*, normal=False, opposite=False, reference_field="signature"):
    data = scenario(direction="normal_request" if normal else "attack_request",
                    reference_field=reference_field)
    # Use current source-block/catalog construction, not a hand-made resolver.
    data["built"] = build_agent_input(data["event"], data["raw"], data["parsed"],
        32768, 3072, evidence_selection=True, fixed_rules_version="waf-system-v2.38")
    data["document"] = json.loads(data["built"].text)
    if reference_field == "extra_fields.request_note":
        entry = next(item for item in data["document"]["evidence_candidates"]["items"]
                     if item["field"] == reference_field)
        entry["allowed_supports"] = ["context"]
    value = data["value"]
    good = deepcopy(value["evidence"][0])
    good.update(evidence_id="e1", source_id=data["actual"]["source_id"], related_evidence_ids=[])
    invalid = deepcopy(good)
    invalid.update(evidence_id="e2", source_id=data["reference"]["source_id"],
                   supports="attack_request", interpretation_ko=BAD_CLAIM)
    dependent = deepcopy(value["evidence"][1])
    dependent.update(evidence_id="e3", interpretation_ko=DEPENDENT_CLAIM,
                     related_evidence_ids=["e2"])
    value.update(evidence=[good, invalid, dependent], verdict="true_positive",
                 summary_ko=BAD_SUMMARY)
    value["threat_analysis"].update(severity="HIGH", technique_ko=BAD_TECHNIQUE,
                                    potential_impact_ko=BAD_IMPACT)
    if opposite:
        counter = deepcopy(good)
        counter.update(evidence_id="e4", supports="normal_request",
            interpretation_ko="같은 입력의 별도 업무상 쓰임을 설명하는 독립적인 반대 해석입니다.")
        value["evidence"].append(counter)
    return data


def review(*, verdict="true_positive", retained=None):
    severity = {"true_positive": "MEDIUM", "false_positive": "NONE", "inconclusive": "UNKNOWN"}[verdict]
    value = {"retained_evidence_ids": ["e1"] if retained is None else retained,
        "verdict": verdict, "confidence_score": .7,
        "summary_ko": "남은 요청 입력의 동작과 용도를 바탕으로 보안 의미를 재검토했습니다.",
        "threat_analysis": {"severity": severity, "category": "synthetic", "target": "payload",
            "technique_ko": "검증을 통과한 요청 입력의 쓰임을 연결하여 해석했습니다.",
            "obfuscations": [], "potential_impact_ko": "수신된 요청 밖의 실행 결과는 확인되지 않았습니다."},
        "decision_issue": None, "interpretation_conflict": False}
    if verdict == "inconclusive":
        value["decision_issue"] = {"point_ko": "남은 입력의 실제 처리 용도를 구분해야 합니다.",
            "evidence_ids": ["e1"], "missing_condition_ko": "입력값의 실제 서비스상 쓰임이 필요합니다."}
    return value


def user_document(request):
    return json.loads(next(message["content"] for message in json.loads(request.content)["messages"]
                           if message["role"] == "user"))


def stage(request):
    properties = json.loads(request.content)["response_format"]["json_schema"]["schema"]["properties"]
    if "corrections" in properties:
        return "source_repair"
    if "retained_evidence_ids" in properties:
        return "decision_review"
    return "analysis"


def install(monkeypatch, data, reviewed=None, *, correction_content=None, correction_finish="length",
            initial_defects=0, review_defect=None, correction_refusal=None):
    counts = {"analysis": 0, "source_repair": 0, "decision_review": 0}
    reviewed = review() if reviewed is None else reviewed
    correction_content = (json.dumps({"corrections": [{"index": 1,
        "source_id": data["actual"]["source_id"]}], "requires_reanalysis": False})
        if correction_content is None else correction_content)

    def respond(request, _count):
        current = stage(request)
        counts[current] += 1
        if current == "analysis":
            return httpx.Response(200, json=completion("{" if counts[current] <= initial_defects
                else json.dumps(data["value"])))
        if current == "source_repair":
            if correction_finish == "http_error":
                return httpx.Response(503, json={"error": {"message": "Synthetic offline provider failure"}})
            return httpx.Response(200, json=completion(correction_content,
                finish_reason="stop" if correction_finish == "refusal" else correction_finish,
                refusal="synthetic refusal" if correction_finish == "refusal" else correction_refusal))
        if review_defect == "http_error":
            return httpx.Response(503, json={"error": {"message": "Synthetic offline review failure"}})
        return httpx.Response(200, json=completion("{" if review_defect == "malformed" else json.dumps(reviewed),
            finish_reason="length" if review_defect == "length" else "stop"))

    requests, _ = install_http(monkeypatch, respond)
    return requests, counts


def run(data, provider="vllm", *, role="primary", char_budget=65536, archived=False, egress_check=None):
    selected = archived_worker if archived else worker
    kwargs = dict(analysis=None, inference_event=data["event"], raw_payload=data["raw"],
        parsed=data["parsed"], input_truncated=False,
        submitted_payload_spans=data["built"].retained_payload_spans,
        assessment_enabled=True, semantic_enabled=True, security_analysis_enabled=True,
        fixed_rules_version="waf-system-v2.38" if archived else "waf-system-v2.39",
        user_input=json.dumps(data["document"]), profile=profile(provider), api_key="synthetic-key",
        instructions=f"Synthetic independent {role} analysis.", agent_name=f"waf-{role}",
        session_id=f"synthetic:{role}", egress_check=egress_check or (lambda: None))
    if char_budget is not NO_BUDGET:
        kwargs["decision_review_char_budget"] = char_budget
    return selected._execute_grounded_agent(**kwargs)


def assert_failed(call):
    assert not call.succeeded and call.output is None
    assert call.telemetry["error_summary"]["code"] == "model_output_incomplete"
    retry = call.telemetry["output_validation_retry"]
    assert retry["attempt_count"] == 1
    assert retry["attempts"][-1]["provider_finish_reason"] in {"length", "max_tokens"}
    assert call.grounding_history[1]["output"] is None
    assert call.grounding_history[1]["resolved_output"] is None


def assert_recovery(call, data, reviewed, *, retained=None):
    assert call.succeeded and call.output.verdict.value == reviewed["verdict"]
    assert call.output.summary_ko == reviewed["summary_ko"]
    assert call.output.threat_analysis.model_dump(mode="json") == reviewed["threat_analysis"]
    assert call.grounding_history[0]["output"] == data["value"]
    assert "excluded_relation_indexes" not in call.grounding_history[0]
    assert call.grounding_history[1]["output"] is None
    assert call.grounding_history[1]["resolved_output"] is None
    assert call.telemetry["evidence_grounding_retry"]["attempts"][1]["output_validation_retry"]["attempts"][-1][
        "provider_finish_reason"] in {"length", "max_tokens"}
    record = call.grounding_history[-1]
    assert record["kind"] == "decision_review" and record["decision_review_validated"] is True
    expected_ids = set(retained or ["e1"])
    assert set(record["output"]["retained_evidence_ids"]) == expected_ids
    evidence, _, _ = canonical_evidence(call, None)
    exposed = json.dumps([item.model_dump(mode="json") for item in call.output.evidence])
    assert all(marker not in exposed for marker in (BAD_CLAIM, DEPENDENT_CLAIM, BAD_PARTIAL))
    assert all(item["field"] not in {"signature", "signature_explanation"} for item in evidence)
    assert all(marker not in call.output.summary_ko for marker in (BAD_SUMMARY, BAD_CLAIM))
    assert call.output.threat_analysis.technique_ko != BAD_TECHNIQUE
    assert call.output.threat_analysis.potential_impact_ko != BAD_IMPACT
    assert data["raw"] not in json.dumps(call.telemetry)
    recovery = call.telemetry["evidence_source_recovery"]
    assert recovery["eligible"] and recovery["attempted"] and recovery["recovered"]
    assert recovery["skipped_reason"] is None
    return evidence


@pytest.mark.parametrize("provider", ["vllm", "openai"])
@pytest.mark.parametrize("role", ["primary", "verifier"])
def test_only_completed_same_role_review_recovers_length_failure(monkeypatch, provider, role):
    data = case()
    reviewed = review()
    requests, counts = install(monkeypatch, data, reviewed)
    call = run(data, provider, role=role)
    evidence = assert_recovery(call, data, reviewed)
    assert counts == {"analysis": 1, "source_repair": 1, "decision_review": 1}
    assert len(requests) == 3 and len(evidence) == 1
    assert evidence[0]["supports"] == "attack_request"
    review_doc = user_document(requests[-1])["evidence_decision_review"]
    assert review_doc["eligible_evidence_ids"] == ["e1"]
    base = review_doc["original_output"]
    assert base["verdict"] == "inconclusive" and base["threat_analysis"]["severity"] == "UNKNOWN"
    assert base["decision_issue"] is None
    assert base["evidence"] == [data["value"]["evidence"][0]]
    assert all(marker not in json.dumps(review_doc) for marker in
               (BAD_SUMMARY, BAD_CLAIM, DEPENDENT_CLAIM, BAD_TECHNIQUE, BAD_IMPACT))
    bodies = [json.loads(request.content) for request in requests]
    assert all(body["model"] == profile(provider).model_name for body in bodies)
    assert all(body.get("max_tokens", body.get("max_completion_tokens")) == 3072 for body in bodies)
    assert all(str(request.url) == str(requests[0].url) for request in requests)
    assert f"Synthetic independent {role} analysis." in json.dumps(bodies[-1])
    assert all(("verifier" if role == "primary" else "primary") not in json.dumps(body["messages"])
               for body in bodies)
    role_evidence, _, _ = canonical_evidence(call if role == "primary" else None,
                                            call if role == "verifier" else None)
    assert role_evidence and all(origin["role"] == role for item in role_evidence for origin in item["origins"])


@pytest.mark.parametrize("verdict", ["false_positive", "inconclusive"])
def test_recovery_reconsiders_instead_of_restoring_old_verdict_summary_or_severity(monkeypatch, verdict):
    data = case(normal=verdict == "false_positive")
    reviewed = review(verdict=verdict)
    requests, _ = install(monkeypatch, data, reviewed)
    call = run(data)
    evidence = assert_recovery(call, data, reviewed)
    assert len(requests) == 3
    assert evidence[0]["supports"] == ("normal_request" if verdict == "false_positive" else "attack_request")
    assert call.output.threat_analysis.severity.value != "HIGH"


@pytest.mark.parametrize("finish", ["length", "max_tokens"])
@pytest.mark.parametrize("partial", [False, True])
def test_no_part_of_incomplete_correction_is_applied_or_stored(monkeypatch, finish, partial):
    data = case()
    patch = json.dumps({"corrections": [{"index": 1, "source_id": data["actual"]["source_id"]}],
        "requires_reanalysis": False})
    if partial:
        patch = '{"corrections":[{"index":1,"source_id":"' + BAD_PARTIAL
    requests, _ = install(monkeypatch, data, correction_content=patch, correction_finish=finish)
    call = run(data)
    assert_recovery(call, data, review())
    assert len(requests) == 3
    record = call.grounding_history[-1]
    assert len(record["assessment"]["evidence"]) == 1
    assert record["assessment"]["evidence"][0]["source_id"] == data["actual"]["source_id"]
    assert BAD_PARTIAL not in json.dumps(call.grounding_history)
    assert BAD_PARTIAL not in json.dumps(call.telemetry)


def test_initial_invalid_aliases_and_transitive_dependents_are_not_resurrected(monkeypatch):
    data = case(normal=True)
    good, invalid, dependent = deepcopy(data["value"]["evidence"])
    alias = deepcopy(invalid)
    alias["evidence_id"] = "e4"
    transitive = deepcopy(dependent)
    transitive.update(evidence_id="e5", related_evidence_ids=["e3"])
    dependent["related_evidence_ids"] = ["e4"]
    data["value"]["evidence"] = [invalid, alias, dependent, transitive, good]
    requests, _ = install(monkeypatch, data, review(verdict="false_positive"))
    call = run(data)
    evidence = assert_recovery(call, data, review(verdict="false_positive"))
    assert len(requests) == 3 and len(evidence) == 1
    envelope = user_document(requests[-1])["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e1"]
    assert envelope["original_output"]["evidence"] == [good]
    assert call.grounding_history[-1]["source_resolutions"][0]["index"] == 0
    assert evidence[0]["origins"][0]["index"] == 0
    assert build_evidence_relations(call, evidence=evidence)["groups"] == []


@pytest.mark.parametrize("reference_field", ["signature_explanation", "extra_fields.request_note"])
def test_all_restricted_source_roles_are_excluded_not_automatically_relabelled(monkeypatch, reference_field):
    data = case(reference_field=reference_field)
    requests, _ = install(monkeypatch, data)
    call = run(data)
    assert_recovery(call, data, review())
    expected_reason = ("signature_support_not_allowed" if reference_field == "signature_explanation"
                       else "source_support_not_allowed")
    assert call.grounding_history[0]["rejected_evidence"] == [{"index": 1, "reason": expected_reason}]
    assert len(requests) == 3


def test_every_forbidden_source_is_excluded_even_if_model_repairs_only_one(monkeypatch):
    data = case()
    explanation = next(item for item in data["document"]["evidence_candidates"]["items"]
                       if item["field"] == "signature_explanation")
    extra = deepcopy(data["value"]["evidence"][1])
    extra.update(evidence_id="e4", source_id=explanation["source_id"], interpretation_ko=BAD_CLAIM + "_SECOND")
    data["value"]["evidence"].append(extra)
    requests, _ = install(monkeypatch, data)
    call = run(data)
    assert_recovery(call, data, review())
    assert {item["index"] for item in call.grounding_history[0]["rejected_evidence"]} == {1, 3}
    assert user_document(requests[-1])["evidence_decision_review"]["eligible_evidence_ids"] == ["e1"]


def test_opposing_independent_evidence_cannot_be_hidden_by_recovery(monkeypatch):
    data = case(opposite=True)
    reviewed = review(retained=["e4", "e1"])
    requests, _ = install(monkeypatch, data, reviewed)
    call = run(data)
    evidence = assert_recovery(call, data, reviewed, retained=["e1", "e4"])
    assert {item["supports"] for item in evidence} == {"attack_request", "normal_request"}
    envelope = user_document(requests[-1])["evidence_decision_review"]
    assert envelope["eligible_evidence_ids"] == ["e1", "e4"]
    assert [item["evidence_id"] for item in envelope["original_output"]["evidence"]] == ["e1", "e4"]


@pytest.mark.parametrize("defect", ["malformed", "unknown_id", "missing_id", "hide_opposite",
                                   "new_evidence", "forbidden_direction", "length", "http_error"])
def test_review_failure_does_not_turn_incomplete_primary_into_success(monkeypatch, defect):
    data = case(opposite=defect == "hide_opposite")
    reviewed = review()
    if defect == "unknown_id":
        reviewed["retained_evidence_ids"] = ["e5"]
    elif defect == "missing_id":
        reviewed["retained_evidence_ids"] = []
    elif defect == "new_evidence":
        reviewed["evidence"] = [data["value"]["evidence"][0]]
    elif defect == "forbidden_direction":
        reviewed = review(verdict="false_positive")
    requests, counts = install(monkeypatch, data, reviewed, review_defect=defect)
    call = run(data)
    assert_failed(call)
    assert counts == {"analysis": 1, "source_repair": 1, "decision_review": 1}
    assert len(requests) == 3
    assert call.grounding_history[-1]["decision_review_validated"] is False
    assert call.grounding_history[0]["output"] == data["value"]


@pytest.mark.parametrize("char_budget", [NO_BUDGET, None, 10])
def test_missing_or_insufficient_review_input_budget_preserves_failure(monkeypatch, char_budget):
    data = case()
    requests, counts = install(monkeypatch, data)
    call = run(data, char_budget=char_budget)
    assert_failed(call)
    assert counts["decision_review"] == 0 and len(requests) == 2


def test_four_initial_attempts_plus_one_correction_never_grants_a_sixth_call(monkeypatch):
    data = case()
    requests, counts = install(monkeypatch, data, initial_defects=3)
    call = run(data)
    assert_failed(call)
    assert len(requests) == 5
    assert counts == {"analysis": 4, "source_repair": 1, "decision_review": 0}
    assert call.grounding_history[0]["output"] == data["value"]


def test_unknown_attempt_count_never_grants_an_extra_review(monkeypatch):
    data = case()
    requests, counts = install(monkeypatch, data)
    execute = worker.execute_structured_agent

    async def without_initial_counter(**kwargs):
        call = await execute(**kwargs)
        if kwargs["agent_name"].endswith("-primary"):
            return replace(call, telemetry={key: value for key, value in call.telemetry.items()
                                             if key != "output_validation_retry"})
        return call

    monkeypatch.setattr(worker, "execute_structured_agent", without_initial_counter)
    call = run(data)
    assert_failed(call)
    assert len(requests) == 2 and counts["decision_review"] == 0


@pytest.mark.parametrize("remaining", ["none", "context_only"])
def test_no_independent_directional_request_anchor_preserves_failure(monkeypatch, remaining):
    data = case()
    data["value"]["evidence"] = data["value"]["evidence"][1:]
    if remaining == "context_only":
        context = deepcopy(data["value"]["evidence"][1])
        context.update(evidence_id="e4", related_evidence_ids=[])
        data["value"]["evidence"].append(context)
    requests, counts = install(monkeypatch, data)
    call = run(data)
    assert_failed(call)
    assert len(requests) == 2 and counts["decision_review"] == 0


@pytest.mark.parametrize("failure", ["timeout", "stop", "refusal", "http_error", "content_filter", "tool_calls"])
def test_only_length_or_max_tokens_correction_failures_are_recoverable(monkeypatch, failure):
    data = case()
    requests, counts = install(monkeypatch, data, correction_content="{", correction_finish=failure)
    call = run(data)
    assert not call.succeeded and call.output is None
    assert counts["decision_review"] == 0
    assert len(requests) <= 3  # Existing transport retry policy may retry an HTTP failure.


@pytest.mark.parametrize("provider", ["vllm", "openai"])
def test_refusal_with_length_finish_reason_never_qualifies_for_recovery(monkeypatch, provider):
    data = case()
    requests, counts = install(monkeypatch, data, correction_refusal="Synthetic provider refusal")
    call = run(data, provider)
    assert not call.succeeded and call.output is None
    assert counts["decision_review"] == 0
    assert not call.telemetry["evidence_source_recovery"]["eligible"]
    assert len(requests) == 2


def test_allowlist_revocation_before_source_correction_is_not_recovered(monkeypatch):
    from app.services.vllm_profiles import TargetNotAllowedError

    data = case()
    requests, counts = install(monkeypatch, data)

    def require_still_allowed():
        if requests:
            raise TargetNotAllowedError("vllm_target_not_allowed")

    call = run(data, egress_check=require_still_allowed)
    assert not call.succeeded and call.output is None
    assert len(requests) == 1
    assert counts["source_repair"] == counts["decision_review"] == 0
    assert not call.telemetry["evidence_source_recovery"]["eligible"]


def test_source_correction_cancellation_propagates_without_review(monkeypatch):
    data = case()

    def respond(request, _count):
        if stage(request) == "analysis":
            return httpx.Response(200, json=completion(json.dumps(data["value"])))
        raise asyncio.CancelledError()

    requests, _ = install_http(monkeypatch, respond)
    with pytest.raises(asyncio.CancelledError):
        run(data)
    assert len(requests) == 2
    assert [stage(request) for request in requests] == ["analysis", "source_repair"]


def test_initial_output_failure_is_not_mistaken_for_failed_source_correction(monkeypatch):
    data = case()
    requests, _ = install_http(monkeypatch, lambda *_: httpx.Response(200,
        json=completion('{"evidence": [', finish_reason="length")))
    call = run(data)
    assert not call.succeeded and call.output is None and len(requests) == 4
    assert len(call.grounding_history) == 1
    assert all(stage(request) == "analysis" for request in requests)


def test_unknown_initial_source_never_qualifies_for_source_only_recovery(monkeypatch):
    data = case()
    data["value"]["evidence"][0]["source_id"] = "c999"
    requests, counts = install(monkeypatch, data)
    call = run(data)
    assert not call.succeeded and call.output is None
    assert len(requests) == 4 and counts["source_repair"] == counts["decision_review"] == 0


def test_initial_unresolved_raw_source_is_not_silently_treated_as_role_error(monkeypatch):
    data = case()
    selected = next(item for item in data["document"]["evidence_candidates"]["items"]
                    if item["source_id"] == data["actual"]["source_id"])
    selected["excerpt"] = "SYNTHETIC_SOURCE_NOT_PRESENT_IN_SUBMITTED_RAW"
    requests, counts = install(monkeypatch, data)
    call = run(data)
    assert_failed(call)
    assert len(requests) == 2 and counts["decision_review"] == 0


def test_invalid_review_context_preserves_original_correction_failure(monkeypatch):
    from app.agent import evidence_decision_review_v238 as review_helpers

    data = case()
    requests, counts = install(monkeypatch, data)

    def reject_review_context(*_args, **_kwargs):
        raise ValueError("synthetic invalid frozen review basis")

    monkeypatch.setattr(review_helpers, "review_base", reject_review_context)
    call = run(data)
    assert_failed(call)
    assert len(requests) == 2 and counts["decision_review"] == 0
    assert call.telemetry["evidence_source_recovery"]["skipped_reason"] == "review_context_invalid"


def test_ambiguous_relation_mapping_never_authorizes_review(monkeypatch):
    from app.agent import evidence_relations_v238 as relation_helpers

    data = case()
    requests, counts = install(monkeypatch, data)

    def reject_relation_mapping(*_args, **_kwargs):
        raise relation_helpers.EvidenceRelationsError()

    monkeypatch.setattr(relation_helpers, "assert_relation_sources", reject_relation_mapping)
    call = run(data)
    assert_failed(call)
    assert len(requests) == 2 and counts["decision_review"] == 0
    assert call.telemetry["evidence_source_recovery"]["skipped_reason"] == "ambiguous_relations"


def test_completed_review_still_requires_final_raw_source_revalidation(monkeypatch):
    from app.agent import evidence_candidates

    data = case()
    requests, counts = install(monkeypatch, data)
    resolve = evidence_candidates.resolve_selection

    def reject_review_source(selection, *args, **kwargs):
        merged, issues, resolutions = resolve(selection, *args, **kwargs)
        if len(selection.evidence) == 1:
            issues.append({"index": 0, "reason": "synthetic_source_recheck_failed"})
        return merged, issues, resolutions

    monkeypatch.setattr(evidence_candidates, "resolve_selection", reject_review_source)
    call = run(data)
    assert_failed(call)
    assert len(requests) == 3 and counts["decision_review"] == 1
    assert call.grounding_history[-1]["decision_review_validated"] is False
    assert call.telemetry["evidence_source_recovery"]["skipped_reason"] == "review_grounding_invalid"


def test_pinned_v238_retains_original_length_failure_instead_of_using_new_recovery(monkeypatch):
    data = case()
    requests, counts = install(monkeypatch, data)
    call = run(data, archived=True)
    assert_failed(call)
    assert len(requests) == 2 and counts["decision_review"] == 0


@pytest.mark.parametrize("failure", ["failed_verifier", "disagreement"])
def test_recovered_primary_never_bypasses_required_verifier_policy(monkeypatch, failure):
    data = case()
    install(monkeypatch, data)
    primary = run(data)
    assert primary.succeeded
    verifier = (None if failure == "failed_verifier" else primary.output.model_copy(update={
        "verdict": AgentVerdict.false_positive,
        "threat_analysis": primary.output.threat_analysis.model_copy(update={"severity": "NONE"})}))
    finalized = worker.finalize_with_verifier(primary.output, verifier,
        reasons=["true_positive_but_allowed"], verifier_failure="synthetic_failure" if verifier is None else None)
    assert finalized.output.verdict == AgentVerdict.inconclusive
    assert finalized.verifier_executed and finalized.agreement is False
