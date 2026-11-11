"""Candidate v2.21 execution, isolated from the byte-pinned v2.20 pipeline.

Only the validated runtime dispatcher selects this module. Shared legacy files
remain unchanged so already-approved v2.20 jobs still execute their exact code.
"""

import hashlib
import asyncio
import json
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent.contracts import AgentVerdict, EvidenceCorrectionOutput, WAFAnalysisOutput
from .agent.analyst_guidance import build_analyst_guidance
from .agent.evidence import EvidenceSourceResolver
from .agent.executor import AgentCallResult
from .agent.prompts import SEMANTIC_RULES_VERSIONS, V3_RULES_VERSIONS
from .agent.policy import (
    VerifierPolicyContext,
    finalize_with_verifier,
    finalize_without_verifier,
)
from .models import (
    AgentRun,
    Analysis,
    ModelProfileStatus,
    VLLMProfile,
    VLLMTestRun,
)
from .services.crypto import CryptoService
from .services.internal_egress import allowed_targets_from_db
from .services.parser_versions import apply_parser_snapshot, parser_metadata, read_parser_snapshot
from .services.test_execution_statistics import fixed_codes, PARSER_WARNING_CODES, VERIFIER_REASON_CODES
from .services.request_integrity import assess_request_integrity
from .services.payload_decoding import DECODER_VERSION
from .services.prompt_snapshots import pin_analysis_prompt
from .services.input_schemas import pin_schema, schema_metadata
from .services.vllm_profiles import (
    TargetNotAllowedError,
    normalize_and_validate_profile_url,
    profile_fingerprint,
    validate_profile_provider_settings,
)
from .worker import (
    WorkerExecutionError, measured_run, measured_step, encrypted_json, step_output,
    _event_document, _decode_payload_step, _public_parser_metadata,
    _ground_agent_call, _failed_agent_call, _agent_step_output,
    _invalid_relation_representatives, _ensure_benchmark_owner,
)
from .agent.executor_v221 import execute_structured_agent
from .agent.input_builder_v221 import build_agent_input
from .agent.execution_policy_v221 import (
    verifier_reasons, affected_normal_findings, apply_integrity_guard,
    apply_final_followup_policy, INTEGRITY_RULES_VERSIONS, integrity_version_for_rules,
    verifier_policy_metadata,
)

def _execute_grounded_agent(*, analysis, raw_payload, parsed, input_truncated,
                            submitted_payload_spans=None, assessment_enabled=False, semantic_enabled=False,
                            security_analysis_enabled=False, fixed_rules_version="waf-system-v2.21", **kwargs):
    from .agent.contracts import (EvidenceSelectionOutput, EvidenceSelectionCorrectionOutput, EvidenceAssessmentOutput,
                                 SemanticAssessmentOutput, SecurityAnalysisOutput, SecurityAnalysisOutputV216,
                                 SecurityAnalysisOutputV220)
    from .agent.evidence_candidates import (
        REPAIR_INSTRUCTIONS as SELECTION_REPAIR_INSTRUCTIONS,
        resolve_selection, apply_selection_corrections, selection_version_for_rules,
        IDENTIFIER_FILTER_RULES_VERSIONS,
    )
    from .agent.grounding_repair import (
        REPAIR_INSTRUCTIONS, REPAIR_VERSION, apply_corrections, correction_input,
        evidence_issues, failure_category, has_content_evidence, resolve_evidence,
    )
    document = json.loads(kwargs["user_input"])
    catalog = document.get("evidence_candidates")
    if fixed_rules_version != "waf-system-v2.21":
        raise WorkerExecutionError("unsupported_candidate_execution")
    selection_version = "evidence-selection-v4"
    selecting = isinstance(catalog, dict) and catalog.get("version") == selection_version
    if not security_analysis_enabled:
        raise WorkerExecutionError("unsupported_security_output_contract")
    if not selecting:
        # Never bypass the pinned exclusion policy via legacy field/excerpt
        # output when a new execution's catalog is missing or mismatched.
        raise WorkerExecutionError("evidence_selection_catalog_invalid")
    linked = True
    security_model = (SecurityAnalysisOutputV220 if linked else SecurityAnalysisOutput
                      if fixed_rules_version == "waf-system-v2.15" else SecurityAnalysisOutputV216)
    selection_model = (security_model if security_analysis_enabled else SemanticAssessmentOutput if semantic_enabled
                       else EvidenceAssessmentOutput if assessment_enabled else EvidenceSelectionOutput)
    repair_instructions = SELECTION_REPAIR_INSTRUCTIONS if selecting else REPAIR_INSTRUCTIONS
    if selecting:
        kwargs["output_model"] = selection_model
    kwargs["structured_output_recovery"] = True
    repair_instructions += ("\nsignature_support_not_allowed는 시그니처를 공격/정상 근거로 인용한 오류다. "
        "같은 해석과 분류를 실제 요청 원문으로 뒷받침할 수 있을 때만 출처를 교정한다. "
        "시그니처의 탐지 사실만으로 작성한 해석이면 requires_reanalysis=true로 표시하고 해당 index는 생략한다.")
    if submitted_payload_spans is None:
        submitted_payload_spans = ((0, len(raw_payload)),) if document["event"].get("payload") == raw_payload else ()
    sources = EvidenceSourceResolver(
        raw_payload, _event_document(analysis), parsed,
        submitted_payload_spans=submitted_payload_spans, submitted_event=document["event"],
        decoding_hints=document.get("decoded_payload_hints"),
    )
    history, attempts = [], []
    original_kwargs = dict(kwargs)
    original_output, requested_indexes = None, set()
    original_selection = None
    requires_review, resolution_count = False, 0
    skipped_reason, context_only = None, False
    interpretation_conflict = False
    original_relations = []
    for attempt in range(2):
        try:
            call = asyncio.run(execute_structured_agent(**kwargs))
        except Exception as exc:
            call = _failed_agent_call(exc)
        expected_model = ((EvidenceSelectionCorrectionOutput if attempt else selection_model) if selecting
                          else (EvidenceCorrectionOutput if attempt else WAFAnalysisOutput))
        if call.succeeded and type(call.output) is not expected_model:
            call = replace(call, output=None, finish_reason="error", error="unexpected_agent_output_contract",
                           telemetry={**call.telemetry, "error_summary": {"code": "output_validation_failed"}})
        raw_output = call.output
        issues, resolutions = [], []
        if call.succeeded:
            if selecting:
                if not attempt:
                    original_selection = raw_output.legacy_bridge() if security_analysis_enabled else raw_output
                    if linked:
                        original_relations = [item.model_dump(mode="json") for item in raw_output.evidence_relations]
                    interpretation_conflict = bool(security_analysis_enabled and raw_output.interpretation_conflict)
                    selection = original_selection
                else:
                    selection, patch_issues = apply_selection_corrections(original_selection, raw_output, requested_indexes)
                    issues.extend(patch_issues)
                    requires_review = raw_output.requires_reanalysis or bool(patch_issues)
                merged, selection_issues, resolutions = resolve_selection(selection, catalog, sources)
                issues.extend(selection_issues)
                from .agent.evidence_source_policy import signature_support_issues
                policy_issues = signature_support_issues(selection, catalog,
                    fixed_rules_version=fixed_rules_version)
                issues.extend(policy_issues)
                if policy_issues:
                    rejected = {item["index"] for item in policy_issues}
                    kept = [(source, item) for source, item in zip(resolutions, merged.evidence, strict=True)
                            if source["index"] not in rejected]
                    resolutions = [source for source, _ in kept]
                    merged = merged.model_copy(update={"evidence": [item for _, item in kept]})
                merged = merged.model_copy(update={"input_truncated": input_truncated})
            elif not attempt:
                original_output, resolutions = resolve_evidence(raw_output, sources)
                merged = original_output.model_copy(update={"input_truncated": input_truncated})
            else:
                merged, patch_issues, resolutions = apply_corrections(original_output, raw_output, requested_indexes, sources)
                issues.extend(patch_issues)
                requires_review = raw_output.requires_reanalysis or bool(patch_issues)
                merged = merged.model_copy(update={"input_truncated": input_truncated})
            issues.extend(evidence_issues(merged, sources))
            if attempt and issues:
                requires_review = True
            if (attempt or selecting) and merged.verdict != AgentVerdict.inconclusive and not has_content_evidence(merged, sources):
                requires_review, context_only = True, True
            call = replace(call, output=merged)
        resolution_count += len(resolutions)
        # Arbitrary field paths/excerpts occur only inside the encrypted history.
        history.append({"attempt": attempt + 1,
                        "output": raw_output.model_dump(mode="json") if raw_output else None,
                        "resolved_output": call.output.model_dump(mode="json") if call.output else None,
                        "source_resolutions": resolutions,
                        "rejected_evidence": issues,
                        **({"assessment": {**selection.model_dump(mode="json", include={"evidence", "decision_issue"}),
                            **({"interpretation_conflict": interpretation_conflict} if security_analysis_enabled else {}),
                            **({"evidence_relations": original_relations} if linked else {})}}
                           if selecting and assessment_enabled and call.succeeded else {}),
                        **({"correction_user_input": kwargs["user_input"],
                            "correction_instructions": repair_instructions} if attempt else {})})
        attempts.append({"attempt": attempt + 1, "framework_run_id": call.framework_run_id,
                         "finish_reason": call.finish_reason, "issues": issues,
                         "usage": call.telemetry.get("usage"),
                         "output_validation_retry": call.telemetry.get("output_validation_retry")})
        if attempt or not call.succeeded or not issues:
            break
        corrected_input = correction_input(original_kwargs["user_input"], original_selection if selecting else original_output, issues)
        requested_indexes = {item["index"] for item in json.loads(corrected_input)["evidence_correction"]["items"]}
        if not requested_indexes:
            requires_review, skipped_reason = True, "feedback_budget_exceeded"
            break
        kwargs = {**original_kwargs,
                  # A citation-only task has its own versioned instructions;
                  # the pinned judgment policy/output contract is not rewritten.
                  "instructions": repair_instructions,
                  "user_input": corrected_input,
                  "agent_name": original_kwargs["agent_name"] + "-evidence-repair",
                  "session_id": original_kwargs["session_id"] + ":evidence-repair",
                  # No nested schema-repair loop on this one extra model call.
                  "output_validation_max_attempts": 1,
                  "output_model": EvidenceSelectionCorrectionOutput if selecting else EvidenceCorrectionOutput}
    relation_rejected = 0
    if call.succeeded and linked and original_relations:
        from .agent.evidence_relations import assert_relation_sources, EvidenceRelationsError
        invalid_representatives = _invalid_relation_representatives(
            original_relations, selection, resolutions, call.output)
        if requires_review:
            # Citation-only repair cannot certify an explanation that the
            # correcting model explicitly says needs fresh interpretation.
            invalid_representatives.update(item["representative_index"] for item in original_relations)
        try:
            assert_relation_sources(selection.model_dump(mode="json"), resolutions, relations=original_relations)
        except EvidenceRelationsError:
            invalid_representatives.update(item["representative_index"] for item in original_relations)
            requires_review = True
        if invalid_representatives:
            # Keep the exact model/history output for audit, but never expose
            # an integrated claim whose essential source did not survive.
            history[-1]["excluded_relation_indexes"] = sorted(invalid_representatives)
            accepted = [item for source, item in zip(resolutions, call.output.evidence, strict=True)
                        if source["index"] not in invalid_representatives]
            relation_rejected = len(call.output.evidence) - len(accepted)
            call = replace(call, output=call.output.model_copy(update={"evidence": accepted}))
            requires_review = True
    if call.succeeded:
        call = _ground_agent_call(call, analysis, raw_payload, parsed, sources=sources, force_inconclusive=requires_review)
        if security_analysis_enabled and (requires_review or call.telemetry["evidence_grounding"].get("downgraded_to_inconclusive")):
            call = replace(call, output=call.output.model_copy(update={"summary_ko":
                "수집된 요청과 인용 자료만으로는 요청의 동작을 충분히 확인하기 어렵습니다."}))
        if selecting:
            grounding = {**call.telemetry["evidence_grounding"], "mode": "selected_raw_source",
                         "checked_count": len(original_selection.evidence), "rejected_count": len(issues)}
            call = replace(call, telemetry={**call.telemetry, "evidence_grounding": grounding})
    retry = {"version": selection_version if selecting else REPAIR_VERSION,
             "mode": "source_selection" if selecting else "citations_only",
             "attempted": len(attempts) > 1, "attempt_count": len(attempts), "max_attempts": 2,
             "recovered": len(attempts) > 1 and call.succeeded and not attempts[-1]["issues"] and not requires_review,
             "requires_review": requires_review, "resolved_source_count": resolution_count,
             "context_only_evidence": context_only,
             "skipped_reason": skipped_reason,
             "attempts": attempts}
    return replace(call, grounding_history=history,
                   telemetry={**call.telemetry, "evidence_grounding_retry": retry,
                              **({"evidence_relation_rejected_count": relation_rejected} if linked else {}),
                              "failure_category": failure_category(call)})




def process_moduagent(
    db: Session,
    crypto: CryptoService,
    analysis: Analysis,
    allowed_targets: str,
    verifier_confidence_threshold: float,
    *, request_check=None,
) -> None:
    # Legacy positional argument is retained for callers, never used as policy.
    with measured_run(db, analysis) as run:
        _process_moduagent_steps(db, crypto, analysis, run, allowed_targets, verifier_confidence_threshold, request_check=request_check)


def _process_moduagent_steps(
    db: Session,
    crypto: CryptoService,
    analysis: Analysis,
    run: AgentRun,
    allowed_targets: str,
    verifier_confidence_threshold: float,
    *, request_check=None,
) -> None:
    with measured_step(db, crypto, run, 1, "input", "이벤트 입력 검증") as step:
        from .services.analysis_retries import (check_profile, execution_request_check, load_execution_snapshot,
                                               make_execution_snapshot)
        from .services.agent_configuration import (profile_metadata as role_metadata, select_verifier,
                                                  role_request_check, validate_role_profile)
        from .agent.grounding_repair import REPAIR_RESERVED_TOKENS, decision_diagnostics
        # Check retries BEFORE either legacy pin function could reconstruct a
        # missing snapshot using a current/default policy or field definition.
        pinned_execution = load_execution_snapshot(analysis, crypto) if (analysis.retry_of_analysis_id or
            (analysis.execution_snapshot_ciphertext and not analysis.model_test_run_id)) else None
        # History only: definitions never enter system/user messages. An old
        # unversioned queued event uses the original default, not today's rules.
        schema_snapshot = pin_schema(db, crypto, analysis, selection_origin="legacy_default")
        parser_snapshot = read_parser_snapshot(crypto, analysis)
        step_output(crypto, step, {"input_schema": schema_snapshot}, {
            "input_schema": schema_metadata(schema_snapshot),
        })
        from .services.test_runs import analysis_test_run, named_test_request_check, selected_test_request_check
        named_run = analysis_test_run(db, analysis)
        if named_run is not None:
            if named_run.execution_mode != "moduagent" or analysis.analysis_purpose != "test":
                raise WorkerExecutionError("test_run_execution_mode_mismatch")
            if named_run.configuration_hash or named_run.configuration_snapshot_json is not None:
                from .services.candidate_configurations import verify_configuration
                verify_configuration(named_run, crypto, analysis)
            check_named = named_test_request_check(db.get_bind(), named_run.id)
            check_named()
            previous_check = request_check
            def combined_check():
                if previous_check:
                    previous_check()
                check_named()
            request_check = combined_check
            verifier_confidence_threshold = named_run.profile_metadata["verifier_confidence_threshold"]
        prompt = pin_analysis_prompt(db, crypto, analysis, origin="legacy_execution")
        if prompt.fixed_rules_version != "waf-system-v2.21":
            raise WorkerExecutionError("unsupported_candidate_execution")
        # Retry and lease recovery reuse pinned roles. Candidate qualification
        # retains its dedicated claim checks and always evaluates its own model.
        if pinned_execution is not None:
            profile = check_profile(db, pinned_execution)
            verifier_confidence_threshold = pinned_execution.verifier_confidence_threshold
            snapshot_check = execution_request_check(db.get_bind(), pinned_execution)
            before_snapshot_check = request_check
            def check_snapshot():
                if before_snapshot_check:
                    before_snapshot_check()
                snapshot_check()
            request_check = check_snapshot
        elif analysis.model_test_run_id:
            test_run = db.get(VLLMTestRun, analysis.model_test_run_id)
            claim = db.info.get("model_validation_claim")
            if analysis.analysis_purpose != "test" or test_run is None or not test_run.include_dataset or not claim or claim[0] != analysis.model_test_run_id:
                raise WorkerExecutionError("model_validation_claim_required")
            profile = db.get(VLLMProfile, test_run.profile_id)
            if profile is None or profile.status == "disabled" or profile_fingerprint(profile) != test_run.profile_fingerprint:
                raise WorkerExecutionError("model_validation_profile_changed")
        elif named_run is not None:
            profile = db.get(VLLMProfile, named_run.profile_id)
        elif analysis.analysis_purpose == "test":
            from .services.vllm_profiles import assignment_block_reason
            profile = db.scalar(select(VLLMProfile).where(VLLMProfile.is_test.is_(True)))
            if profile is not None and assignment_block_reason(db, profile):
                raise WorkerExecutionError("test_model_profile_not_verified")
            if profile is not None:
                check_selected = selected_test_request_check(db.get_bind(), profile.id, profile_fingerprint(profile))
                prior_test_check = request_check
                def check_legacy_test():
                    if prior_test_check:
                        prior_test_check()
                    check_selected()
                request_check = check_legacy_test
        else:
            profile = db.scalar(
                select(VLLMProfile).where(VLLMProfile.status == ModelProfileStatus.production.value)
            )
        if profile is None:
            raise WorkerExecutionError("test_model_profile_required" if analysis.analysis_purpose == "test" else "production_model_profile_required")
        if pinned_execution is not None:
            verifier_profile = check_profile(db, pinned_execution, role="verifier")
        elif analysis.model_test_run_id:
            verifier_profile = profile
        elif named_run is not None:
            saved = named_run.profile_metadata.get("verifier_profile")
            # Older named runs were explicitly single-profile; never apply a
            # new Verifier assignment retroactively to their queued items.
            verifier_profile = validate_role_profile(db, db.get(VLLMProfile, saved["model_profile_id"]),
                saved["profile_fingerprint"]) if saved else profile
        else:
            verifier_profile = select_verifier(db, analysis.analysis_purpose, profile)
        # Recheck the egress boundary in the worker, including profiles that
        # were changed outside the administrator API. No event is sent yet.
        try:
            normalized_url = normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
            api_key = crypto.decrypt_text(profile.api_key_ciphertext) if profile.api_key_ciphertext else None
            validate_profile_provider_settings(profile, has_api_key=bool(api_key and api_key.strip()))
        except TargetNotAllowedError as exc:
            raise WorkerExecutionError(str(exc)) from None
        if normalized_url != profile.base_url:
            raise WorkerExecutionError("production_model_profile_url_not_normalized")
        provider = profile.provider
        egress_check = role_request_check(db.get_bind(), profile, request_check, require_verified=False,
                                          internal_only=analysis.internal_only)
        verifier_check = role_request_check(db.get_bind(), verifier_profile, request_check,
            require_verified=verifier_profile.id != profile.id and not analysis.model_test_run_id,
            internal_only=analysis.internal_only)
        verifier_key = crypto.decrypt_text(verifier_profile.api_key_ciphertext) if verifier_profile.api_key_ciphertext else None
        verifier_metadata = role_metadata(verifier_profile)
        profile_metadata = {
            "llm_provider": provider,
            "model_profile": profile.name,
            "model_profile_id": profile.id,
            "model_name": profile.model_name,
            "profile_fingerprint": profile_fingerprint(profile),
            "external_data_approved": profile.external_data_approved,
        }
        # Capture the selected profile even when a later model call fails.
        analysis.model_profile = profile.name
        from .services.evidence_editor import EditorSnapshot, capture_editor
        if pinned_execution is not None:
            editor_snapshot = pinned_execution.evidence_editor
        elif analysis.model_test_run_id:
            editor_snapshot = None  # Candidate qualification uses no other model.
        elif named_run is not None:
            editor_snapshot = EditorSnapshot.model_validate_json(crypto.decrypt_text(
                named_run.evidence_editor_snapshot_ciphertext)) if named_run.evidence_editor_snapshot_ciphertext else None
        else:
            editor_snapshot = capture_editor(db, analysis.analysis_purpose, profile)
        if pinned_execution is None:
            execution = make_execution_snapshot(analysis, profile, prompt, verifier_confidence_threshold, verifier_profile, editor_snapshot, crypto=crypto)
            analysis.execution_snapshot_ciphertext = crypto.encrypt_text(execution.model_dump_json())
            # Persist before parsing/context/Primary failures can lose provenance.
            # The measured-step commit also persists this; the early commit is
            # deliberate so failures inside this same input step remain pinned.
            db.commit()
        raw_payload = crypto.decrypt_text(analysis.payload_ciphertext)
        input_fingerprint = hashlib.sha256(
            f"{prompt.instructions_hash}:{profile_fingerprint(profile)}:{profile_fingerprint(verifier_profile)}:{analysis.source_system}:{analysis.event_id}:{raw_payload}".encode(
                "utf-8"
            )
        ).hexdigest()
        run.fingerprint = input_fingerprint
        step.input_ciphertext = encrypted_json(crypto, {**_event_document(analysis), "payload": raw_payload})
        step_output(crypto, step, {"valid": True, "payload_chars": len(raw_payload), "input_schema": schema_snapshot}, {
            "input_fingerprint": input_fingerprint,
            "prompt": prompt.metadata(),
            "verifier_confidence_threshold": verifier_confidence_threshold,
            **profile_metadata,
            "verifier_profile": verifier_metadata,
            "parser": parser_metadata(parser_snapshot),
        })

    integrity = None
    with measured_step(
        db, crypto, run, 2, "parser", "범용 HTTP 파싱", {"payload": raw_payload}
    ) as step:
        parsed = apply_parser_snapshot(raw_payload, parser_snapshot, waf_vendor=analysis.waf_vendor)
        if prompt.fixed_rules_version in INTEGRITY_RULES_VERSIONS:
            integrity = assess_request_integrity(raw_payload, parsed,
                version=integrity_version_for_rules(prompt.fixed_rules_version))
        step_output(crypto, step, {**parsed, **({"request_integrity": integrity} if integrity is not None else {})}, {
            "parser_version": parser_snapshot["engine_version"],
            "parser": parser_metadata(parser_snapshot),
            "parse_status": parsed["parse_status"],
            "parser_warning_codes": fixed_codes(parsed.get("warnings"), PARSER_WARNING_CODES),
            "fallback_llm_used": False,
            **({"request_integrity": {"version": integrity["version"], "status": integrity["status"],
                "issue_codes": [item["code"] for item in integrity["issues"]],
                "limitation_codes": integrity["limitations"]}} if integrity is not None else {}),
        })

    decoding = _decode_payload_step(db, crypto, run, raw_payload)
    with measured_step(db, crypto, run, 4, "agent_input", "분석 입력 구성") as step:
        from .agent.evidence_candidates import SELECTION_RULES_VERSIONS
        try:
            agent_input = build_agent_input(
                _event_document(analysis), raw_payload, parsed,
                min(profile.context_window - profile.max_output_tokens,
                    verifier_profile.context_window - verifier_profile.max_output_tokens), 0,
                decoding=decoding,
                prompt_reserved_tokens=prompt.reserved_tokens + REPAIR_RESERVED_TOKENS,
                evidence_selection=prompt.fixed_rules_version == "waf-system-v2.21",
                request_integrity=integrity,
                fixed_rules_version=prompt.fixed_rules_version,
            )
        except ValueError as exc:
            if exc.args == ("agent_context_budget_too_small",):
                raise WorkerExecutionError("agent_context_budget_too_small") from None
            raise
        analysis.input_truncated = agent_input.input_truncated
        pipeline_history = ({"evidence_pipeline": prompt.evidence_pipeline}
                            if prompt.evidence_pipeline is not None else {})
        pipeline_metadata = ({"evidence_pipeline_version": prompt.evidence_pipeline["version"],
                              "evidence_pipeline_hash": prompt.evidence_pipeline["sha256"]}
                             if prompt.evidence_pipeline is not None else {})
        step_output(crypto, step, {"user_input": agent_input.text,
                                   "retained_payload_spans": agent_input.retained_payload_spans,
                                   **pipeline_history}, {
            "input_schema": schema_metadata(schema_snapshot),
            "decoder_version": DECODER_VERSION,
            "input_truncated": agent_input.input_truncated,
            "submitted_payload_chars": agent_input.submitted_payload_chars,
            "prompt_reserved_tokens": prompt.reserved_tokens,
            "evidence_repair_reserved_tokens": REPAIR_RESERVED_TOKENS,
            "shared_role_input": True,
            "evidence_source_scope": "submitted_raw_spans",
            **pipeline_metadata,
        })

    with measured_step(
        db, crypto, run, 5, "llm_primary", "Primary LLM 판정",
        {"system_instructions": prompt.primary_instructions, "user_input": agent_input.text},
    ) as step:
        from .agent.analyst_assessment import RULES_VERSIONS as ASSESSMENT_RULES_VERSIONS, build_analyst_assessment
        assessment_enabled = prompt.fixed_rules_version == "waf-system-v2.21"
        semantic_enabled = prompt.fixed_rules_version in SEMANTIC_RULES_VERSIONS
        security_analysis_enabled = prompt.fixed_rules_version in V3_RULES_VERSIONS
        primary = _execute_grounded_agent(
                analysis=analysis, raw_payload=raw_payload, parsed=parsed, input_truncated=agent_input.input_truncated,
                submitted_payload_spans=agent_input.retained_payload_spans,
                assessment_enabled=assessment_enabled,
                semantic_enabled=semantic_enabled,
                security_analysis_enabled=security_analysis_enabled,
                fixed_rules_version=prompt.fixed_rules_version,
                profile=profile, api_key=api_key, instructions=prompt.primary_instructions,
                user_input=agent_input.text, session_id=f"{analysis.id}:primary", agent_name="waf-primary",
                egress_check=egress_check,
                concurrency_engine=db.get_bind(),
            )
        run.framework_run_id = primary.framework_run_id
        run.fingerprint = primary.agent_fingerprint or input_fingerprint
        run.failure_id = primary.failure_id
        step_output(crypto, step, _agent_step_output(primary), {
            **primary.telemetry,
            **profile_metadata,
            "input_truncated": agent_input.input_truncated,
            "original_payload_chars": agent_input.original_payload_chars,
            "submitted_payload_chars": agent_input.submitted_payload_chars,
            "estimated_input_token_budget": agent_input.estimated_input_token_budget,
        })
        step.status = "completed" if primary.succeeded else "failed"
    if not primary.succeeded:
        raise WorkerExecutionError("primary_agent_failed")

    with measured_step(db, crypto, run, 6, "policy", "독립 검증 필요성 판정") as step:
        policy_context = VerifierPolicyContext(
            waf_action=analysis.waf_action,
            parser_status=str(parsed.get("parse_status") or "failed"),
            input_truncated=agent_input.input_truncated,
            confidence_threshold=verifier_confidence_threshold,
            evidence_grounding_failed=bool(primary.telemetry.get("evidence_grounding", {}).get("rejected_count")),
            interpretation_conflict=(any(record.get("assessment", {}).get("interpretation_conflict") is True
                for record in primary.grounding_history) if security_analysis_enabled else None),
            fixed_rules_version=prompt.fixed_rules_version,
        )
        reasons = verifier_reasons(primary.output, policy_context)
        if affected_normal_findings(primary.output, agent_input.request_integrity, raw_payload, parsed):
            reasons.append("input_integrity_requires_review")
        step.input_ciphertext = encrypted_json(crypto, {
            "primary_verdict": primary.output.verdict.value,
            "primary_confidence": primary.output.confidence_score,
            "waf_action": analysis.waf_action,
            "parser_status": policy_context.parser_status,
            "input_truncated": policy_context.input_truncated,
        })
        step_output(crypto, step, {"execute_verifier": bool(reasons), "reasons": reasons}, {
            "confidence_threshold": verifier_confidence_threshold,
            "execute_verifier": bool(reasons),
            "verifier_reason_codes": fixed_codes(reasons, VERIFIER_REASON_CODES),
        })

    verifier: AgentCallResult | None = None
    if reasons:
        with measured_step(
            db, crypto, run, 7, "llm_verifier", "독립 Verifier LLM 판정",
            {"system_instructions": prompt.verifier_instructions, "user_input": agent_input.text},
        ) as step:
            verifier = _execute_grounded_agent(
                    analysis=analysis, raw_payload=raw_payload, parsed=parsed, input_truncated=agent_input.input_truncated,
                    submitted_payload_spans=agent_input.retained_payload_spans,
                    assessment_enabled=assessment_enabled,
                    semantic_enabled=semantic_enabled,
                    security_analysis_enabled=security_analysis_enabled,
                    fixed_rules_version=prompt.fixed_rules_version,
                    profile=verifier_profile, api_key=verifier_key, instructions=prompt.verifier_instructions,
                    user_input=agent_input.text, session_id=f"{analysis.id}:verifier", agent_name="waf-verifier",
                    egress_check=verifier_check,
                    concurrency_engine=db.get_bind(),
                )
            step_output(crypto, step, _agent_step_output(verifier), {
                **verifier.telemetry, **verifier_metadata, "independent_of_primary": True,
            })
            step.status = "completed" if verifier.succeeded else "failed"
            if verifier.failure_id:
                run.failure_id = verifier.failure_id

    final_sequence = 8 if reasons else 7
    with measured_step(
        db, crypto, run, final_sequence, "finalize", "최종 판정 결합 및 저장",
        {
            "primary": primary.output.model_dump(mode="json"),
            "verifier": verifier.output.model_dump(mode="json") if verifier and verifier.output else None,
            "policy_reasons": reasons,
        },
    ) as step:
        if not reasons:
            finalization = finalize_without_verifier(primary.output)
        elif verifier is not None and verifier.succeeded:
            finalization = finalize_with_verifier(primary.output, verifier.output, reasons)
        else:
            finalization = finalize_with_verifier(
                primary.output,
                None,
                reasons,
                verifier_failure=(verifier.failure_id or verifier.error) if verifier else "verifier_not_executed",
            )
        integrity_metadata = None
        if agent_input.request_integrity is not None:
            output, integrity_metadata = apply_integrity_guard(
                finalization.output, agent_input.request_integrity, raw_payload, parsed)
            finalization = replace(finalization, output=output)
        final_output, followup_metadata = apply_final_followup_policy(
            finalization.output, analysis.waf_action, prompt.fixed_rules_version)
        finalization = replace(finalization, output=final_output)
        diagnostics = decision_diagnostics(primary, verifier, finalization, parsed, agent_input.input_truncated)
        if followup_metadata is not None:
            diagnostics["followup_policy"] = followup_metadata
        if integrity_metadata is not None:
            diagnostics["request_integrity"] = integrity_metadata
            if integrity_metadata["issue_codes"]:
                diagnostics["input_signals"].append("input_integrity_observed")
            if integrity_metadata["downgraded_to_inconclusive"]:
                diagnostics["inconclusive_reasons"].append("input_integrity_limited")
        result = final_output.model_dump(mode="json")
        if assessment_enabled and not security_analysis_enabled:
            result["analyst_assessment"] = build_analyst_assessment(primary, verifier)
        result.update({
            "diagnostics": diagnostics,
            "schema_version": "waf-analysis-v2",
            "analyst_guidance": build_analyst_guidance(
                final_output, incomplete_execution=bool(reasons and (verifier is None or not verifier.succeeded)),
                evidence_rejected=any(bool(call and call.telemetry.get("evidence_grounding", {}).get("rejected_count")) for call in (primary, verifier)),
            ),
            "primary": primary.output.model_dump(mode="json"),
            "verifier": {
                "executed": finalization.verifier_executed,
                "agreement": finalization.agreement,
                "reasons": list(finalization.verifier_reasons),
                "failure_id": verifier.failure_id if verifier else None,
                "error": verifier.error if verifier else finalization.verifier_failure,
                "output": verifier.output.model_dump(mode="json") if verifier and verifier.output else None,
                "framework_run_id": verifier.framework_run_id if verifier else None,
            },
            "policy": {
                **prompt.metadata(),
                "verifier_confidence_threshold": verifier_confidence_threshold,
                "disagreement_or_failure_becomes_inconclusive": True,
            },
            "agent": {
                "role_profiles": {"primary": profile_metadata, "verifier": verifier_metadata},
                "framework": "moduagent",
                "input_schema": schema_metadata(schema_snapshot),
                "parser": _public_parser_metadata(parser_snapshot),
                "framework_version": primary.telemetry.get("framework_version"),
                "execution": "standard",
                "tools": [],
                "preprocessors": [DECODER_VERSION] + ([integrity["version"]] if integrity is not None else []),
                "llm_provider": provider,
                "model_name": profile.model_name,
                "model_profile_id": profile.id,
                "profile_fingerprint": profile_metadata["profile_fingerprint"],
                "external_data_approved": profile.external_data_approved,
                "thinking_enabled": False if provider == "vllm" else None,
            },
        })
        if security_analysis_enabled:
            from .agent.security_analysis import (canonical_evidence, conflict_observed, decision_evidence_ids,
                guidance, observational_summary, role_result)
            from .result_contracts import validate_new_result
            evidence, issues, omitted = canonical_evidence(primary, verifier)
            summary = observational_summary(primary, verifier, finalization, integrity_metadata)
            for retired in ("recommended_checks", "analyst_checks", "tuning_recommendation", "conflicting_evidence"):
                result.pop(retired, None)
            result.update(schema_version="waf-analysis-v3", evidence=evidence, decision_issues=issues,
                omitted_issue_count=omitted, interpretation_conflict=conflict_observed(primary) or conflict_observed(verifier),
                summary_ko=summary, analyst_guidance=guidance(summary, final_output, primary, verifier, finalization),
                primary=role_result(primary, "primary", evidence, issues))
            result["verifier"]["output"] = role_result(verifier, "verifier", evidence, issues)
            result["policy"].update(decision_evidence_ids=decision_evidence_ids(final_output, evidence),
                **verifier_policy_metadata(prompt.fixed_rules_version))
            result = validate_new_result(result)
        encrypted_result = result
        relation_metadata = {}
        if prompt.fixed_rules_version == "waf-system-v2.21":
            from .agent.evidence_relations import build_evidence_relations, EvidenceRelationsError
            try:
                links = build_evidence_relations(primary, verifier, evidence=result["evidence"])
            except EvidenceRelationsError:
                raise WorkerExecutionError("evidence_relations_invalid") from None
            # Internal, encrypted administrator view only. Never extend the
            # closed public result or turn an evidence link into a verdict.
            encrypted_result = {**result, "evidence_relations": links}
            relation_metadata = {"evidence_relations_version": links["version"],
                                 "evidence_relation_group_count": len(links["groups"])}
        step_output(crypto, step, encrypted_result, {
            "agreement": finalization.agreement, "verifier_executed": finalization.verifier_executed,
            **relation_metadata,
        })
        analysis.verdict = final_output.verdict.value
        analysis.severity = final_output.threat_analysis.severity.value
        analysis.threat_category = final_output.threat_analysis.category
        analysis.confidence_score = final_output.confidence_score
        analysis.summary_ko = result["summary_ko"]
        analysis.result_json = result
        analysis.prompt_version = prompt.prompt_version
        analysis.model_profile = profile.name

    if editor_snapshot is not None:
        _organize_evidence(db, crypto, analysis, run, final_sequence + 1, editor_snapshot, request_check)


def _organize_evidence(db, crypto, analysis, run, sequence, snapshot, request_check):
    from .agent.evidence_editor import (VERSION, CANONICAL_VERSION, EvidenceEditorOutput, editor_input,
                                       input_fits, serialized_input, validate_groups)
    from .agent import result_editor
    from .services.evidence_editor import editor_profile
    from .services.agent_configuration import profile_metadata, role_request_check
    result = dict(analysis.result_json)
    canonical = result.get("schema_version") == "waf-analysis-v3"
    assessment = {"version": "analyst-assessment-v1", "evidence": result["evidence"]} if canonical else result.get("analyst_assessment")
    extended = not canonical and snapshot.version in result_editor.SUPPORTED_VERSIONS
    source_checks = result.get("analyst_guidance", {}).get("checks", [])
    preparation_failed = False
    try:
        document = result_editor.editor_input(assessment, source_checks) if extended else editor_input(assessment)
    except (ValueError, TypeError, KeyError):
        document, preparation_failed = None, True
    metadata = {"editor_version": snapshot.version, "instructions_hash": snapshot.instructions_hash,
                "model_profile_id": snapshot.profile_id, "profile_fingerprint": snapshot.profile_fingerprint,
                "verdict_changed": False, "llm_called": False}
    presentation = {"version": VERSION, "status": "skipped", "reason": "no_duplicate_candidates"}
    check_presentation = {"version": result_editor.CHECK_VERSION if snapshot.version == result_editor.VERSION else "follow-up-editor-v1"}
    with measured_step(db, crypto, run, sequence, "llm_evidence_editor", "근거·확인사항 정리" if extended else "근거 정리",
                       {"instructions": snapshot.instructions, "input": document}, metadata) as step:
        call = None
        if (preparation_failed or snapshot.version not in {VERSION, CANONICAL_VERSION, *result_editor.SUPPORTED_VERSIONS}
                or (canonical and snapshot.version in result_editor.SUPPORTED_VERSIONS)):
            presentation.update(status="fallback", reason="editor_unavailable_or_invalid")
        elif document is not None:
            try:
                profile = editor_profile(db, snapshot)
                metadata.update(profile_metadata(profile))
                text = serialized_input(document)
                if not input_fits(profile, snapshot.instructions, text):
                    presentation.update(status="fallback", reason="input_budget_exceeded")
                else:
                    key = crypto.decrypt_text(profile.api_key_ciphertext) if profile.api_key_ciphertext else None
                    check = role_request_check(db.get_bind(), profile, request_check, internal_only=analysis.internal_only)
                    metadata["llm_called"] = True
                    # Includes at most 90 s of queue + execution. The executor
                    # separately limits model response time to at most 30 s.
                    call = asyncio.run(asyncio.wait_for(execute_structured_agent(
                        profile=profile, api_key=key, instructions=snapshot.instructions, user_input=text,
                        session_id=f"{run.id}:evidence-editor", agent_name="waf-evidence-editor",
                        egress_check=check, concurrency_engine=db.get_bind(),
                        output_model=result_editor.ResultEditorOutput if extended else EvidenceEditorOutput,
                        output_validation_max_attempts=1, structured_output_recovery=True), timeout=90))
                    _ensure_benchmark_owner(db)
                    if call.succeeded:
                        if extended:
                            groups, check_groups = result_editor.validate_result(assessment, source_checks, call.output)
                            check_presentation.update(items=result_editor.check_items(source_checks), groups=check_groups)
                        else:
                            groups = validate_groups(assessment, call.output)
                        presentation.update(status="completed", reason=None, groups=groups)
                    else:
                        presentation.update(status="fallback", reason="editor_call_failed")
            except Exception as exc:
                # Never hide lost ownership or write a stale analysis result.
                if getattr(exc, "code", None) in {"analysis_lease_lost", "model_validation_lease_lost"}:
                    raise
                _ensure_benchmark_owner(db)
                presentation.update(status="fallback", reason="editor_unavailable_or_invalid")
        metadata.update(presentation_status=presentation["status"], reason=presentation["reason"])
        if call:
            metadata.update(call.telemetry)
            metadata.update(framework_run_id=call.framework_run_id, agent_fingerprint=call.agent_fingerprint,
                            failure_id=call.failure_id)
        if extended:
            check_presentation.update(status=presentation["status"], reason=presentation["reason"])
            check_count = len(source_checks) if isinstance(source_checks, list) else 0
            metadata.update(check_count=check_count, displayed_check_count=len(check_presentation["groups"]) if "groups" in check_presentation else check_count)
        step_output(crypto, step, {"presentation": presentation,
                    **({"follow_up_presentation": check_presentation} if extended else {}),
                    "call": _agent_step_output(call) if call else None}, metadata)
        if presentation["status"] == "fallback":
            step.status = "failed"  # Optional stage failure, not analysis failure.
        result["evidence_presentation"] = presentation
        if extended:
            result["follow_up_presentation"] = check_presentation
        result["agent"] = {**result["agent"], "evidence_editor": {
            key: metadata[key] for key in ("editor_version", "instructions_hash", "model_profile_id", "profile_fingerprint", "presentation_status")}}
        if canonical:
            from .result_contracts import validate_new_result
            result = validate_new_result(result)
        analysis.result_json = result



