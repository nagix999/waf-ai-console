import hashlib
import asyncio
import json
import logging
import os
import socket
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from .agent.contracts import AgentVerdict, ThreatSeverity, WAFAnalysisOutput
from .agent.analyst_guidance import build_analyst_guidance
from .agent.evidence import EvidenceSourceResolver
from .agent.executor import AgentCallResult, execute_structured_agent
from .agent.input_builder import build_agent_input
from .agent.policy import (
    VerifierPolicyContext,
    finalize_with_verifier,
    finalize_without_verifier,
    verifier_reasons,
)
from .config import get_settings
from .database import build_engine, build_session_factory
from .models import (
    AgentRun,
    AgentStep,
    Analysis,
    AnalysisStatus,
    ModelProfileStatus,
    ModelTestMode,
    ModelTestStatus,
    RunStatus,
    VLLMProfile,
    VLLMTestRun,
    Verdict,
)
from .services.crypto import CryptoService
from .services.internal_egress import allowed_targets_from_db, lock_egress_mutation
from .services.http_parser import HTTP_PARSER_VERSION, parse_http_payload
from .services.payload_decoding import DECODER_VERSION, decode_payload
from .services.prompt_snapshots import pin_analysis_prompt
from .services.input_schemas import pin_schema, schema_metadata
from .services.timing import LEASE_EXPIRED_FAILURE
from .services.vllm_profiles import (
    TargetNotAllowedError,
    normalize_and_validate_profile_url,
    profile_fingerprint,
    validate_profile_provider_settings,
)
from .services.vllm_test_runner import run_vllm_test

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("waf-worker")


class WorkerExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def utcnow() -> datetime:
    return datetime.now(UTC)


def claim_next(db: Session, worker_id: str, lease_seconds: int) -> Analysis | None:
    now = utcnow()
    candidates = db.scalars(
        select(Analysis)
        .where(
            Analysis.model_test_run_id.is_(None),
            or_(
                Analysis.status == AnalysisStatus.pending.value,
                and_(
                    Analysis.status == AnalysisStatus.processing.value,
                    Analysis.lease_expires_at < now,
                ),
            )
        )
        .order_by(Analysis.created_at)
        .limit(5)
    ).all()
    for candidate in candidates:
        claim = db.execute(
            update(Analysis)
            .where(
                Analysis.id == candidate.id,
                or_(
                    Analysis.status == AnalysisStatus.pending.value,
                    and_(
                        Analysis.status == AnalysisStatus.processing.value,
                        Analysis.lease_expires_at < now,
                    ),
                ),
            )
            .values(
                status=AnalysisStatus.processing.value,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=func.coalesce(Analysis.started_at, now),
                attempt_count=Analysis.attempt_count + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount == 1:
            _abandon_running_runs(db, candidate.id)
            db.commit()
            db.refresh(candidate)
            return candidate
        db.rollback()
    return None


def claim_next_vllm_test(db: Session, worker_id: str, lease_seconds: int) -> VLLMTestRun | None:
    now = utcnow()
    candidates = db.scalars(
        select(VLLMTestRun)
        .where(
            or_(
                VLLMTestRun.status == ModelTestStatus.pending.value,
                and_(
                    VLLMTestRun.status == ModelTestStatus.running.value,
                    VLLMTestRun.lease_expires_at < now,
                ),
            )
        )
        .order_by(VLLMTestRun.created_at)
        .limit(5)
    ).all()
    for candidate in candidates:
        claim_owner = f"{worker_id}:{uuid.uuid4()}" if candidate.include_dataset else worker_id
        claim = db.execute(
            update(VLLMTestRun)
            .where(
                VLLMTestRun.id == candidate.id,
                or_(
                    VLLMTestRun.status == ModelTestStatus.pending.value,
                    and_(
                        VLLMTestRun.status == ModelTestStatus.running.value,
                        VLLMTestRun.lease_expires_at < now,
                    ),
                ),
            )
            .values(
                status=ModelTestStatus.running.value,
                lease_owner=claim_owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=func.coalesce(VLLMTestRun.started_at, now),
            )
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount == 1:
            db.commit()
            db.refresh(candidate)
            return candidate
        db.rollback()
    return None


def encrypted_json(crypto: CryptoService, value: object) -> str:
    return crypto.encrypt_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _abandon_running_runs(db: Session, analysis_id: str) -> None:
    for run in db.scalars(
        select(AgentRun).where(AgentRun.analysis_id == analysis_id, AgentRun.status == "running")
    ).all():
        run.status = RunStatus.failed.value
        run.failure_id = LEASE_EXPIRED_FAILURE
        run.completed_at = None
        for step in run.steps:
            if step.status == "running":
                step.status = "failed"
                step.completed_at = None
                metadata = dict(step.metadata_json or {})
                metadata.pop("duration_ms", None)
                step.metadata_json = {
                    **metadata,
                    "timing_incomplete": True,
                    "error_code": LEASE_EXPIRED_FAILURE,
                }


def _ensure_benchmark_owner(db: Session) -> None:
    claim = db.info.get("model_validation_claim")
    if claim:
        from .services.model_validation_worker import require_owned_test
        require_owned_test(db, *claim)


def _ensure_run_active(db: Session, run: AgentRun) -> None:
    _ensure_benchmark_owner(db)
    if db.scalar(select(AgentRun.status).where(AgentRun.id == run.id)) != RunStatus.running.value:
        raise WorkerExecutionError("analysis_lease_lost")


@contextmanager
def measured_run(db: Session, analysis: Analysis) -> Iterator[AgentRun]:
    _ensure_benchmark_owner(db)
    # Validate the original claim before a run exists for recovery to abandon.
    # The conditional write holds the SQLite writer lock until run creation commits.
    if analysis.lease_owner is not None:
        claim_owner, claim_attempt = analysis.lease_owner, analysis.attempt_count
        claim = db.execute(
            update(Analysis)
            .where(
                Analysis.id == analysis.id,
                Analysis.status == AnalysisStatus.processing.value,
                Analysis.lease_owner == claim_owner,
                Analysis.attempt_count == claim_attempt,
            )
            .values(lease_owner=Analysis.lease_owner, updated_at=Analysis.updated_at)
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount != 1:
            db.rollback()
            raise WorkerExecutionError("analysis_lease_lost")
    now = utcnow()
    if analysis.started_at is None:
        analysis.started_at = now
    run = AgentRun(analysis_id=analysis.id, status=RunStatus.running.value, started_at=now)
    db.add(run)
    db.commit()
    db.info["worker_run_id"] = run.id
    try:
        yield run
        _ensure_run_active(db, run)
        completed_at = utcnow()
        run.status = RunStatus.completed.value
        run.completed_at = completed_at
        analysis.status = AnalysisStatus.completed.value
        analysis.completed_at = completed_at
        analysis.lease_owner = None
        analysis.lease_expires_at = None
        db.commit()
    except Exception:
        db.rollback()
        _ensure_benchmark_owner(db)
        db.refresh(run)
        if run.failure_id == LEASE_EXPIRED_FAILURE:
            raise WorkerExecutionError("analysis_lease_lost") from None
        if run.status == RunStatus.running.value:
            run.status = RunStatus.failed.value
            run.completed_at = utcnow()
            _close_unfinished_steps(run, run.completed_at)
            db.commit()
        raise


@contextmanager
def measured_step(
    db: Session,
    crypto: CryptoService,
    run: AgentRun,
    sequence: int,
    step_type: str,
    name: str,
    input_value: object | None = None,
    metadata: dict | None = None,
) -> Iterator[AgentStep]:
    _ensure_run_active(db, run)
    started_at = utcnow()
    started_ns = time.perf_counter_ns()
    step = AgentStep(
        run_id=run.id,
        sequence=sequence,
        step_type=step_type,
        name=name,
        status="running",
        input_ciphertext=encrypted_json(crypto, input_value) if input_value is not None else None,
        encryption_key_version=crypto.key_version,
        metadata_json={**(metadata or {}), "timing_measured": True},
        tool_calls_json=[],
        started_at=started_at,
        completed_at=None,
    )
    db.add(step)
    db.commit()
    try:
        yield step
        _ensure_run_active(db, run)
        if step.status == "running":
            step.status = "completed"
    except Exception as exc:
        db.rollback()
        _ensure_benchmark_owner(db)
        db.refresh(step)
        if step.metadata_json.get("error_code") == LEASE_EXPIRED_FAILURE:
            raise WorkerExecutionError("analysis_lease_lost") from None
        if step.status != "running":
            raise
        step.status = "failed"
        step.metadata_json = {**step.metadata_json, "error_type": type(exc).__name__}
        raise
    finally:
        _ensure_benchmark_owner(db)
        # A recovering worker owns abandoned steps; their true end is unknown.
        if not step.metadata_json.get("timing_incomplete"):
            step.completed_at = utcnow()
            step.metadata_json = {
                **step.metadata_json,
                "duration_ms": max(0, (time.perf_counter_ns() - started_ns) // 1_000_000),
            }
            db.commit()


def step_output(crypto: CryptoService, step: AgentStep, value: object, metadata: dict | None = None) -> None:
    step.output_ciphertext = encrypted_json(crypto, value)
    step.metadata_json = {**step.metadata_json, **(metadata or {})}


def _close_unfinished_steps(run: AgentRun, completed_at: datetime) -> None:
    for step in run.steps:
        if step.status == "running":
            step.status = "failed"
            step.completed_at = completed_at
            step.metadata_json = {**step.metadata_json, "timing_incomplete": True}


def _decode_payload_step(db: Session, crypto: CryptoService, run: AgentRun, raw_payload: str) -> dict:
    with measured_step(db, crypto, run, 3, "decoder", "인코딩 문자열 해석", {"payload": raw_payload}) as step:
        decoding = decode_payload(raw_payload)
        step_output(crypto, step, decoding, {
            "decoder_version": DECODER_VERSION,
            "execution": "deterministic_local_preprocessor",
            "item_count": len(decoding["items"]),
            "scan_truncated": decoding["scan_truncated"],
            "llm_called": False,
        })
    return decoding


def process_stub(db: Session, crypto: CryptoService, analysis: Analysis) -> None:
    with measured_run(db, analysis) as run:
        with measured_step(db, crypto, run, 1, "input", "이벤트 입력 검증") as step:
            schema_snapshot = pin_schema(db, crypto, analysis, selection_origin="legacy_default")
            step_output(crypto, step, {"input_schema": schema_snapshot}, {
                "input_schema": schema_metadata(schema_snapshot), "llm_called": False,
            })
            from .services.test_runs import analysis_test_run
            named_run = analysis_test_run(db, analysis)
            if named_run is not None and named_run.execution_mode != "stub":
                raise WorkerExecutionError("test_run_execution_mode_mismatch")
            raw_payload = crypto.decrypt_text(analysis.payload_ciphertext)
            run.fingerprint = hashlib.sha256(
                f"{analysis.source_system}:{analysis.event_id}:{raw_payload}".encode("utf-8")
            ).hexdigest()
            step.input_ciphertext = encrypted_json(crypto, {
                "event_id": analysis.event_id,
                "signature": analysis.signature,
                "waf_action": analysis.waf_action,
                "payload": raw_payload,
            })
            step_output(crypto, step, {"valid": True, "payload_chars": len(raw_payload), "input_schema": schema_snapshot})
        with measured_step(
            db, crypto, run, 2, "parser", "범용 HTTP 파싱", {"payload": raw_payload}
        ) as step:
            parsed = parse_http_payload(raw_payload)
            step_output(crypto, step, parsed, {
                "parser_version": HTTP_PARSER_VERSION,
                "parse_status": parsed["parse_status"],
                "fallback_llm_used": False,
            })
        _decode_payload_step(db, crypto, run, raw_payload)
        with measured_step(
            db, crypto, run, 4, "agent_stub", "AI 판정 대기", {"parser_output": parsed},
            {"agent_mode": "stub", "llm_called": False},
        ) as step:
            result = _stub_result()
            result["agent"]["input_schema"] = schema_metadata(schema_snapshot)
            step_output(crypto, step, result)
            analysis.verdict = Verdict.inconclusive.value
            analysis.severity = ThreatSeverity.UNKNOWN.value
            analysis.threat_category = "not_analyzed"
            analysis.confidence_score = 0.0
            analysis.summary_ko = result["summary_ko"]
            analysis.result_json = result
            analysis.prompt_version = "stub-v0"
            analysis.model_profile = "stub-no-llm"


def _stub_result() -> dict:
    stub_output = WAFAnalysisOutput.model_validate({
            "verdict": AgentVerdict.inconclusive.value,
            "confidence_score": 0.0,
            "summary_ko": "실행 골격 확인용 결과입니다. ModuAgent와 검증된 LLM 프로필 연결 후 실제 판정을 수행합니다.",
            "threat_analysis": {
                "severity": ThreatSeverity.UNKNOWN.value,
                "category": "not_analyzed",
                "target": "not_analyzed",
                "technique_ko": "stub 모드에서는 위협 기법을 분석하지 않습니다.",
                "obfuscations": [],
                "potential_impact_ko": "실제 LLM 판정이 없어 잠재 영향을 평가하지 않았습니다.",
            },
            "signature_assessment": {
                "relation": "unknown",
                "explanation_ko": "stub 모드에서는 시그니처와 요청의 관계를 평가하지 않습니다.",
            },
            "evidence": [],
            "recommended_checks": ["LLM 프로필과 판정 정책을 검증한 뒤 ModuAgent 실행기를 활성화하세요."],
            "tuning_recommendation": {
                "recommended": False,
                "risk_ko": "실제 판정 전에는 튜닝을 제안하지 않습니다.",
            },
            "conflicting_evidence": [],
            "input_truncated": False,
    })
    result = stub_output.model_dump(mode="json")
    result.update(
        {
            "schema_version": "waf-analysis-v2",
            "primary": None,
            "verifier": {
                "executed": False,
                "agreement": None,
                "reasons": ["stub_mode"],
                "failure_id": None,
                "error": None,
                "output": None,
                "framework_run_id": None,
            },
            "policy": {"prompt_version": "stub-v0"},
            "agent": {"framework": "stub", "thinking_enabled": False},
        }
    )
    return result


def _agent_step_output(call: AgentCallResult) -> dict:
    return {
        "validated_output": call.output.model_dump(mode="json") if call.output else None,
        "finish_reason": call.finish_reason,
        "failure_id": call.failure_id,
        "error": call.error,
    }


def _failed_agent_call(exc: Exception) -> AgentCallResult:
    return AgentCallResult(
        output=None,
        framework_run_id=None,
        agent_fingerprint=None,
        finish_reason="error",
        failure_id=None,
        error=f"Agent invocation failed with {type(exc).__name__}",
        telemetry={
            "framework": "moduagent",
            "exception_type": type(exc).__name__,
            "raw_provider_body_stored": False,
        },
    )


def _event_document(analysis: Analysis) -> dict:
    return {
        "source_system": analysis.source_system,
        "event_id": analysis.event_id,
        "company_name": analysis.company_name,
        "src_ip": analysis.src_ip,
        "dest_ip": analysis.dest_ip,
        "src_port": analysis.src_port,
        "dest_port": analysis.dest_port,
        "signature": analysis.signature,
        "event_name": analysis.event_name,
        "waf_vendor": analysis.waf_vendor,
        "waf_action": analysis.waf_action,
        "extra_fields": analysis.extra_fields,
    }


def _dedupe_checks(values: list[str], *prepend: str) -> list[str]:
    result: list[str] = []
    for value in [*prepend, *values]:
        if value not in result:
            result.append(value)
        if len(result) == 10:
            break
    return result


def _ground_output_evidence(
    output: WAFAnalysisOutput,
    analysis: Analysis,
    raw_payload: str,
) -> tuple[WAFAnalysisOutput, dict[str, object]]:
    sources = EvidenceSourceResolver(raw_payload, _event_document(analysis))
    grounded = [item for item in output.evidence if sources.matches(item.field, item.excerpt)]
    rejected_count = len(output.evidence) - len(grounded)
    downgraded = output.verdict != AgentVerdict.inconclusive and not grounded

    if rejected_count == 0:
        validated = output
    elif downgraded:
        validated = output.model_copy(
            update={
                "verdict": AgentVerdict.inconclusive,
                "confidence_score": min(output.confidence_score, 0.49),
                "summary_ko": "LLM이 제시한 판정 근거를 지정된 입력 필드의 원문에서 확인할 수 없어 최종 판정을 보류합니다.",
                "threat_analysis": output.threat_analysis.model_copy(
                    update={"severity": ThreatSeverity.UNKNOWN}
                ),
                "evidence": [],
                "recommended_checks": _dedupe_checks(
                    output.recommended_checks,
                    "원본 HTTP 요청과 이벤트 필드에서 판정 근거를 다시 확인하세요.",
                ),
                "tuning_recommendation": output.tuning_recommendation.model_copy(
                    update={
                        "recommended": False,
                        "scope": None,
                        "proposal_ko": None,
                        "risk_ko": "원문 근거 검증 실패 상태에서는 튜닝을 제안하지 않습니다.",
                        "validation_ko": None,
                    }
                ),
            }
        )
    else:
        validated = output.model_copy(
            update={
                "evidence": grounded,
                "recommended_checks": _dedupe_checks(
                    output.recommended_checks,
                    "일부 LLM 근거가 지정된 필드의 원문과 일치하지 않아 제외되었습니다. 남은 근거를 직접 확인하세요.",
                ),
            }
        )

    validated = WAFAnalysisOutput.model_validate(validated.model_dump(mode="json"))
    return validated, {
        "mode": "field_exact_substring",
        "checked_count": len(output.evidence),
        "accepted_count": len(grounded),
        "rejected_count": rejected_count,
        "downgraded_to_inconclusive": downgraded,
        "raw_values_stored": False,
    }


def _ground_agent_call(
    call: AgentCallResult,
    analysis: Analysis,
    raw_payload: str,
) -> AgentCallResult:
    if call.output is None:
        return call
    output, grounding = _ground_output_evidence(call.output, analysis, raw_payload)
    return replace(
        call,
        output=output,
        telemetry={**call.telemetry, "evidence_grounding": grounding},
    )


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
        # History only: definitions never enter system/user messages. An old
        # unversioned queued event uses the original default, not today's rules.
        schema_snapshot = pin_schema(db, crypto, analysis, selection_origin="legacy_default")
        step_output(crypto, step, {"input_schema": schema_snapshot}, {
            "input_schema": schema_metadata(schema_snapshot),
        })
        from .services.test_runs import analysis_test_run, named_test_request_check, selected_test_request_check
        named_run = analysis_test_run(db, analysis)
        if named_run is not None:
            if named_run.execution_mode != "moduagent" or analysis.analysis_purpose != "test":
                raise WorkerExecutionError("test_run_execution_mode_mismatch")
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
        if analysis.model_test_run_id:
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
        egress_check = request_check or (vllm_egress_check(db, profile) if provider == "vllm" else None)
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
        raw_payload = crypto.decrypt_text(analysis.payload_ciphertext)
        input_fingerprint = hashlib.sha256(
            f"{prompt.instructions_hash}:{profile_fingerprint(profile)}:{analysis.source_system}:{analysis.event_id}:{raw_payload}".encode(
                "utf-8"
            )
        ).hexdigest()
        run.fingerprint = input_fingerprint
        step.input_ciphertext = encrypted_json(crypto, {**_event_document(analysis), "payload": raw_payload})
        step_output(crypto, step, {"valid": True, "payload_chars": len(raw_payload), "input_schema": schema_snapshot}, {
            "input_fingerprint": input_fingerprint,
            "prompt": prompt.metadata(),
            **profile_metadata,
        })

    with measured_step(
        db, crypto, run, 2, "parser", "범용 HTTP 파싱", {"payload": raw_payload}
    ) as step:
        parsed = parse_http_payload(raw_payload)
        step_output(crypto, step, parsed, {
            "parser_version": HTTP_PARSER_VERSION,
            "parse_status": parsed["parse_status"],
            "fallback_llm_used": False,
        })

    decoding = _decode_payload_step(db, crypto, run, raw_payload)
    with measured_step(db, crypto, run, 4, "agent_input", "분석 입력 구성") as step:
        try:
            agent_input = build_agent_input(
                _event_document(analysis), raw_payload, parsed, profile.context_window, profile.max_output_tokens,
                decoding=decoding,
                prompt_reserved_tokens=prompt.reserved_tokens,
            )
        except ValueError as exc:
            if exc.args == ("agent_context_budget_too_small",):
                raise WorkerExecutionError("agent_context_budget_too_small") from None
            raise
        analysis.input_truncated = agent_input.input_truncated
        step_output(crypto, step, {"user_input": agent_input.text}, {
            "input_schema": schema_metadata(schema_snapshot),
            "decoder_version": DECODER_VERSION,
            "input_truncated": agent_input.input_truncated,
            "submitted_payload_chars": agent_input.submitted_payload_chars,
            "prompt_reserved_tokens": prompt.reserved_tokens,
        })

    with measured_step(
        db, crypto, run, 5, "llm_primary", "Primary LLM 판정",
        {"system_instructions": prompt.primary_instructions, "user_input": agent_input.text},
    ) as step:
        try:
            primary = asyncio.run(execute_structured_agent(
                profile=profile, api_key=api_key, instructions=prompt.primary_instructions,
                user_input=agent_input.text, session_id=f"{analysis.id}:primary", agent_name="waf-primary",
                egress_check=egress_check,
            ))
        except Exception as exc:
            primary = _failed_agent_call(exc)
        if primary.output is not None:
            primary = replace(
                primary, output=primary.output.model_copy(update={"input_truncated": agent_input.input_truncated}),
            )
            primary = _ground_agent_call(primary, analysis, raw_payload)
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
        )
        reasons = verifier_reasons(primary.output, policy_context)
        step.input_ciphertext = encrypted_json(crypto, {
            "primary_verdict": primary.output.verdict.value,
            "primary_confidence": primary.output.confidence_score,
            "waf_action": analysis.waf_action,
            "parser_status": policy_context.parser_status,
            "input_truncated": policy_context.input_truncated,
        })
        step_output(crypto, step, {"execute_verifier": bool(reasons), "reasons": reasons}, {
            "confidence_threshold": verifier_confidence_threshold,
        })

    verifier: AgentCallResult | None = None
    if reasons:
        with measured_step(
            db, crypto, run, 7, "llm_verifier", "독립 Verifier LLM 판정",
            {"system_instructions": prompt.verifier_instructions, "user_input": agent_input.text},
        ) as step:
            try:
                verifier = asyncio.run(execute_structured_agent(
                    profile=profile, api_key=api_key, instructions=prompt.verifier_instructions,
                    user_input=agent_input.text, session_id=f"{analysis.id}:verifier", agent_name="waf-verifier",
                    egress_check=egress_check,
                ))
            except Exception as exc:
                verifier = _failed_agent_call(exc)
            if verifier.output is not None:
                verifier = replace(
                    verifier, output=verifier.output.model_copy(update={"input_truncated": agent_input.input_truncated}),
                )
                verifier = _ground_agent_call(verifier, analysis, raw_payload)
            step_output(crypto, step, _agent_step_output(verifier), {
                **verifier.telemetry, **profile_metadata, "independent_of_primary": True,
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
        final_output = finalization.output
        result = final_output.model_dump(mode="json")
        result.update({
            "schema_version": "waf-analysis-v2",
            "analyst_guidance": build_analyst_guidance(
                final_output, incomplete_execution=bool(reasons and (verifier is None or not verifier.succeeded)),
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
                "framework": "moduagent",
                "input_schema": schema_metadata(schema_snapshot),
                "framework_version": primary.telemetry.get("framework_version"),
                "execution": "standard",
                "tools": [],
                "preprocessors": [DECODER_VERSION],
                "llm_provider": provider,
                "model_name": profile.model_name,
                "model_profile_id": profile.id,
                "profile_fingerprint": profile_metadata["profile_fingerprint"],
                "external_data_approved": profile.external_data_approved,
                "thinking_enabled": False if provider == "vllm" else None,
            },
        })
        step_output(crypto, step, result, {
            "agreement": finalization.agreement, "verifier_executed": finalization.verifier_executed,
        })
        analysis.verdict = final_output.verdict.value
        analysis.severity = final_output.threat_analysis.severity.value
        analysis.threat_category = final_output.threat_analysis.category
        analysis.confidence_score = final_output.confidence_score
        analysis.summary_ko = final_output.summary_ko
        analysis.result_json = result
        analysis.prompt_version = prompt.prompt_version
        analysis.model_profile = profile.name


def mark_failed(db: Session, analysis: Analysis, exc: Exception) -> None:
    if getattr(exc, "code", None) == "analysis_lease_lost":
        return
    now = utcnow()
    run_id = db.info.get("worker_run_id")
    if run_id:
        run = db.get(AgentRun, run_id)
        if run is not None and run.analysis_id == analysis.id:
            db.refresh(run)
            if run.failure_id == LEASE_EXPIRED_FAILURE:
                return
        if run is not None and run.analysis_id == analysis.id and run.status == RunStatus.running.value:
            run.status = RunStatus.failed.value
            run.completed_at = now
            _close_unfinished_steps(run, now)
    analysis.status = AnalysisStatus.failed.value
    analysis.error_code = getattr(exc, "code", type(exc).__name__)
    analysis.error_message = "Worker execution failed. Raw event data was omitted from this error."
    analysis.completed_at = now
    analysis.lease_owner = None
    analysis.lease_expires_at = None
    db.commit()


def vllm_egress_check(db: Session, profile: VLLMProfile):
    """Fresh short read transaction: do not cache authorization across retries.

    A request already handed to the network cannot be recalled. Revocation is
    observed before subsequent dispatches, without a write lock during I/O.
    """
    engine = db.get_bind()
    profile_id, base_url = profile.id, profile.base_url

    def check() -> None:
        with Session(engine) as latest:
            current = latest.get(VLLMProfile, profile_id)
            if current is None or current.provider != "vllm" or current.status == ModelProfileStatus.disabled.value or current.base_url != base_url:
                raise TargetNotAllowedError("vllm_profile_unavailable")
            normalized = normalize_and_validate_profile_url(current, allowed_targets_from_db(latest))
            if normalized != base_url:
                raise TargetNotAllowedError("vllm_target_not_allowed")

    return check


def process_vllm_test(db: Session, crypto: CryptoService, test_run: VLLMTestRun, allowed_targets: str) -> None:
    if test_run.include_dataset:
        from .services.model_validation_worker import process_dataset_test
        process_dataset_test(db, crypto, test_run, verifier_confidence_threshold=db.info.get("verifier_confidence_threshold", 0.75))
        return
    profile = db.get(VLLMProfile, test_run.profile_id)
    if profile is None or profile.status == ModelProfileStatus.disabled.value:
        test_run.status = ModelTestStatus.failed.value
        test_run.error_code = "profile_unavailable"
        test_run.error_message = "The LLM profile is missing or disabled"
    elif profile_fingerprint(profile) != test_run.profile_fingerprint:
        test_run.status = ModelTestStatus.failed.value
        test_run.error_code = "profile_changed"
        test_run.error_message = "The LLM profile changed after this test was queued"
    else:
        try:
            normalized = normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
            if normalized != profile.base_url:
                raise TargetNotAllowedError("model_profile_url_not_normalized")
            validate_profile_provider_settings(profile, has_api_key=bool(profile.api_key_ciphertext))
            kwargs = {"egress_check": vllm_egress_check(db, profile)} if profile.provider == "vllm" else {}
            result = asyncio.run(run_vllm_test(profile, crypto, test_run.mode, **kwargs))
            # A test may have been disabled/edited while network checks ran.
            # Complete under the same short mutation lock as the admin APIs;
            # never restore a stale draft as verified over a disabled profile.
            lock_egress_mutation(db)
            db.refresh(profile)
            if profile.status == ModelProfileStatus.disabled.value:
                raise TargetNotAllowedError("profile_unavailable")
            if profile_fingerprint(profile) != test_run.profile_fingerprint:
                raise TargetNotAllowedError("profile_changed")
            normalize_and_validate_profile_url(profile, allowed_targets_from_db(db) if profile.provider == "vllm" else "")
            test_run.checks_json = result.checks
            test_run.metrics_json = result.metrics
            test_run.error_code = result.error_code
            test_run.error_message = result.error_message
            test_run.status = ModelTestStatus.passed.value if result.passed else ModelTestStatus.failed.value
            if result.passed and test_run.mode == ModelTestMode.full.value:
                profile.last_verified_at = utcnow()
                if profile.status == ModelProfileStatus.draft.value:
                    profile.status = ModelProfileStatus.verified.value
        except Exception as exc:
            test_run.status = ModelTestStatus.failed.value
            test_run.error_code = str(exc) if isinstance(exc, TargetNotAllowedError) else type(exc).__name__
            test_run.error_message = "LLM test execution failed without storing upstream response content"
    now = utcnow()
    test_run.completed_at = now
    test_run.lease_owner = None
    test_run.lease_expires_at = None
    db.commit()


def main() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    logger.info("worker started id=%s role=%s mode=%s", worker_id, settings.worker_role, settings.agent_mode)
    while True:
        with session_factory() as db:
            analysis = claim_next(db, worker_id, settings.job_lease_seconds) if settings.worker_role in {"analysis", "both"} else None
            if analysis is not None:
                try:
                    if settings.agent_mode == "stub":
                        process_stub(db, crypto, analysis)
                    else:
                        process_moduagent(
                            db,
                            crypto,
                            analysis,
                            "",
                            settings.verifier_confidence_threshold,
                        )
                    logger.info("analysis completed id=%s", analysis.id)
                except Exception as exc:
                    logger.error("analysis failed id=%s error_type=%s", analysis.id, type(exc).__name__)
                    db.rollback()
                    current = db.get(Analysis, analysis.id)
                    if current is not None:
                        mark_failed(db, current, exc)
                continue
            test_run = (
                claim_next_vllm_test(db, worker_id, settings.vllm_test_lease_seconds)
                if settings.worker_role in {"model_test", "both"}
                else None
            )
            if test_run is not None:
                db.info["verifier_confidence_threshold"] = settings.verifier_confidence_threshold
                db.info["model_test_lease_seconds"] = settings.vllm_test_lease_seconds
                logger.info("model test started id=%s mode=%s", test_run.id, test_run.mode)
                process_vllm_test(db, crypto, test_run, "")
                logger.info("model test completed id=%s status=%s", test_run.id, test_run.status)
                continue
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
