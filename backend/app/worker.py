import hashlib
import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from .agent.executor import AgentCallResult, execute_structured_agent
from .agent.input_builder import build_agent_input
from .agent.policy import (
    VerifierPolicyContext,
    finalize_with_verifier,
    finalize_without_verifier,
    verifier_reasons,
)
from .agent.prompts import PRIMARY_INSTRUCTIONS, PROMPT_VERSION, VERIFIER_INSTRUCTIONS
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
from .services.http_parser import parse_http_payload
from .services.vllm_profiles import normalize_and_validate_base_url, profile_fingerprint
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
                started_at=now,
                attempt_count=Analysis.attempt_count + 1,
                updated_at=now,
            )
        )
        if claim.rowcount == 1:
            db.commit()
            return db.get(Analysis, candidate.id)
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
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=now,
            )
        )
        if claim.rowcount == 1:
            db.commit()
            return db.get(VLLMTestRun, candidate.id)
        db.rollback()
    return None


def encrypted_json(crypto: CryptoService, value: object) -> str:
    return crypto.encrypt_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def add_step(
    db: Session,
    crypto: CryptoService,
    run: AgentRun,
    sequence: int,
    step_type: str,
    name: str,
    input_value: object | None,
    output_value: object | None,
    metadata: dict | None = None,
    status: str = "completed",
) -> None:
    now = utcnow()
    db.add(
        AgentStep(
            run_id=run.id,
            sequence=sequence,
            step_type=step_type,
            name=name,
            status=status,
            input_ciphertext=encrypted_json(crypto, input_value) if input_value is not None else None,
            output_ciphertext=encrypted_json(crypto, output_value) if output_value is not None else None,
            encryption_key_version=crypto.key_version,
            metadata_json=metadata or {},
            tool_calls_json=[],
            started_at=now,
            completed_at=now,
        )
    )


def process_stub(db: Session, crypto: CryptoService, analysis: Analysis) -> None:
    raw_payload = crypto.decrypt_text(analysis.payload_ciphertext)
    fingerprint = hashlib.sha256(
        f"{analysis.source_system}:{analysis.event_id}:{raw_payload}".encode("utf-8")
    ).hexdigest()
    run = AgentRun(
        analysis_id=analysis.id,
        fingerprint=fingerprint,
        status=RunStatus.running.value,
    )
    db.add(run)
    db.flush()

    add_step(
        db,
        crypto,
        run,
        1,
        "input",
        "이벤트 입력 검증",
        {
            "event_id": analysis.event_id,
            "signature": analysis.signature,
            "waf_action": analysis.waf_action,
            "payload": raw_payload,
        },
        {"valid": True, "payload_chars": len(raw_payload)},
    )
    parsed = parse_http_payload(raw_payload)
    add_step(
        db,
        crypto,
        run,
        2,
        "parser",
        "범용 HTTP 파싱",
        {"payload": raw_payload},
        parsed,
        {"fallback_llm_used": False},
    )
    result = {
        "verdict": Verdict.inconclusive.value,
        "confidence_score": 0.0,
        "summary_ko": "실행 골격 확인용 결과입니다. ModuAgent와 vLLM 연결 후 실제 판정을 수행합니다.",
        "threat_analysis": None,
        "signature_assessment": None,
        "evidence": [],
        "uncertainties": ["WAF_AGENT_MODE가 stub으로 설정되어 실제 LLM 판정을 수행하지 않았습니다."],
        "recommended_checks": ["vLLM 프로필과 판정 정책을 검증한 뒤 ModuAgent 실행기를 활성화하세요."],
        "tuning_recommendation": None,
        "verifier": {"executed": False, "reason": "stub_mode"},
    }
    add_step(
        db,
        crypto,
        run,
        3,
        "agent_stub",
        "AI 판정 대기",
        {"parser_output": parsed},
        result,
        {"agent_mode": "stub", "llm_called": False},
    )

    now = utcnow()
    run.status = RunStatus.completed.value
    run.completed_at = now
    analysis.status = AnalysisStatus.completed.value
    analysis.verdict = Verdict.inconclusive.value
    analysis.confidence_score = 0.0
    analysis.summary_ko = result["summary_ko"]
    analysis.result_json = result
    analysis.prompt_version = "stub-v0"
    analysis.model_profile = "stub-no-llm"
    analysis.completed_at = now
    analysis.lease_owner = None
    analysis.lease_expires_at = None
    db.commit()


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


def process_moduagent(
    db: Session,
    crypto: CryptoService,
    analysis: Analysis,
    allowed_targets: str,
    verifier_confidence_threshold: float,
) -> None:
    profile = db.scalar(
        select(VLLMProfile).where(VLLMProfile.status == ModelProfileStatus.production.value)
    )
    if profile is None:
        raise WorkerExecutionError("production_model_profile_required")
    normalized_url = normalize_and_validate_base_url(profile.base_url, allowed_targets)
    if normalized_url != profile.base_url:
        raise WorkerExecutionError("production_model_profile_url_not_normalized")

    raw_payload = crypto.decrypt_text(analysis.payload_ciphertext)
    input_fingerprint = hashlib.sha256(
        f"{PROMPT_VERSION}:{profile_fingerprint(profile)}:{analysis.source_system}:{analysis.event_id}:{raw_payload}".encode(
            "utf-8"
        )
    ).hexdigest()
    run = AgentRun(
        analysis_id=analysis.id,
        fingerprint=input_fingerprint,
        status=RunStatus.running.value,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    add_step(
        db,
        crypto,
        run,
        1,
        "input",
        "이벤트 입력 검증",
        {**_event_document(analysis), "payload": raw_payload},
        {"valid": True, "payload_chars": len(raw_payload)},
        {"input_fingerprint": input_fingerprint},
    )
    db.commit()

    parsed = parse_http_payload(raw_payload)
    add_step(
        db,
        crypto,
        run,
        2,
        "parser",
        "범용 HTTP 파싱",
        {"payload": raw_payload},
        parsed,
        {"fallback_llm_used": parsed.get("parse_status") != "success"},
    )
    db.commit()

    agent_input = build_agent_input(
        _event_document(analysis),
        raw_payload,
        parsed,
        profile.context_window,
        profile.max_output_tokens,
    )
    analysis.input_truncated = agent_input.input_truncated
    db.commit()
    api_key = crypto.decrypt_text(profile.api_key_ciphertext) if profile.api_key_ciphertext else None

    try:
        primary = asyncio.run(
            execute_structured_agent(
                profile=profile,
                api_key=api_key,
                instructions=PRIMARY_INSTRUCTIONS,
                user_input=agent_input.text,
                session_id=f"{analysis.id}:primary",
                agent_name="waf-primary",
            )
        )
    except Exception as exc:
        primary = _failed_agent_call(exc)
    if primary.output is not None:
        primary = replace(
            primary,
            output=primary.output.model_copy(update={"input_truncated": agent_input.input_truncated}),
        )
    run.framework_run_id = primary.framework_run_id
    run.fingerprint = primary.agent_fingerprint or input_fingerprint
    run.failure_id = primary.failure_id
    add_step(
        db,
        crypto,
        run,
        3,
        "llm_primary",
        "Primary LLM 판정",
        {"system_instructions": PRIMARY_INSTRUCTIONS, "user_input": agent_input.text},
        _agent_step_output(primary),
        {
            **primary.telemetry,
            "model_profile": profile.name,
            "input_truncated": agent_input.input_truncated,
            "original_payload_chars": agent_input.original_payload_chars,
            "submitted_payload_chars": agent_input.submitted_payload_chars,
            "estimated_input_token_budget": agent_input.estimated_input_token_budget,
        },
        status="completed" if primary.succeeded else "failed",
    )
    if not primary.succeeded:
        run.status = RunStatus.failed.value
        run.completed_at = utcnow()
        db.commit()
        raise WorkerExecutionError("primary_agent_failed")
    db.commit()

    policy_context = VerifierPolicyContext(
        waf_action=analysis.waf_action,
        parser_status=str(parsed.get("parse_status") or "failed"),
        input_truncated=agent_input.input_truncated,
        confidence_threshold=verifier_confidence_threshold,
    )
    reasons = verifier_reasons(primary.output, policy_context)
    add_step(
        db,
        crypto,
        run,
        4,
        "policy",
        "독립 검증 필요성 판정",
        {
            "primary_verdict": primary.output.verdict.value,
            "primary_confidence": primary.output.confidence_score,
            "waf_action": analysis.waf_action,
            "parser_status": policy_context.parser_status,
            "input_truncated": policy_context.input_truncated,
        },
        {"execute_verifier": bool(reasons), "reasons": reasons},
        {"confidence_threshold": verifier_confidence_threshold},
    )
    db.commit()

    verifier: AgentCallResult | None = None
    if reasons:
        try:
            verifier = asyncio.run(
                execute_structured_agent(
                    profile=profile,
                    api_key=api_key,
                    instructions=VERIFIER_INSTRUCTIONS,
                    user_input=agent_input.text,
                    session_id=f"{analysis.id}:verifier",
                    agent_name="waf-verifier",
                )
            )
        except Exception as exc:
            verifier = _failed_agent_call(exc)
        if verifier.output is not None:
            verifier = replace(
                verifier,
                output=verifier.output.model_copy(update={"input_truncated": agent_input.input_truncated}),
            )
        add_step(
            db,
            crypto,
            run,
            5,
            "llm_verifier",
            "독립 Verifier LLM 판정",
            {"system_instructions": VERIFIER_INSTRUCTIONS, "user_input": agent_input.text},
            _agent_step_output(verifier),
            {**verifier.telemetry, "model_profile": profile.name, "independent_of_primary": True},
            status="completed" if verifier.succeeded else "failed",
        )
        if verifier.failure_id:
            run.failure_id = verifier.failure_id
        db.commit()

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
    result.update(
        {
            "schema_version": "waf-analysis-v1",
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
                "prompt_version": PROMPT_VERSION,
                "verifier_confidence_threshold": verifier_confidence_threshold,
                "disagreement_or_failure_becomes_inconclusive": True,
            },
            "agent": {
                "framework": "moduagent",
                "framework_version": primary.telemetry.get("framework_version"),
                "execution": "standard",
                "tools": [],
                "thinking_enabled": False,
            },
        }
    )
    final_sequence = 6 if reasons else 5
    add_step(
        db,
        crypto,
        run,
        final_sequence,
        "finalize",
        "최종 판정 결합 및 저장",
        {
            "primary": primary.output.model_dump(mode="json"),
            "verifier": verifier.output.model_dump(mode="json") if verifier and verifier.output else None,
            "policy_reasons": reasons,
        },
        result,
        {"agreement": finalization.agreement, "verifier_executed": finalization.verifier_executed},
    )

    now = utcnow()
    run.status = RunStatus.completed.value
    run.completed_at = now
    analysis.status = AnalysisStatus.completed.value
    analysis.verdict = final_output.verdict.value
    analysis.confidence_score = final_output.confidence_score
    analysis.summary_ko = final_output.summary_ko
    analysis.result_json = result
    analysis.prompt_version = PROMPT_VERSION
    analysis.model_profile = profile.name
    analysis.completed_at = now
    analysis.lease_owner = None
    analysis.lease_expires_at = None
    db.commit()


def mark_failed(db: Session, analysis: Analysis, exc: Exception) -> None:
    now = utcnow()
    analysis.status = AnalysisStatus.failed.value
    analysis.error_code = getattr(exc, "code", type(exc).__name__)
    analysis.error_message = "Worker execution failed. Raw event data was omitted from this error."
    analysis.completed_at = now
    analysis.lease_owner = None
    analysis.lease_expires_at = None
    db.commit()


def process_vllm_test(db: Session, crypto: CryptoService, test_run: VLLMTestRun, allowed_targets: str) -> None:
    profile = db.get(VLLMProfile, test_run.profile_id)
    if profile is None or profile.status == ModelProfileStatus.disabled.value:
        test_run.status = ModelTestStatus.failed.value
        test_run.error_code = "profile_unavailable"
        test_run.error_message = "The vLLM profile is missing or disabled"
    elif profile_fingerprint(profile) != test_run.profile_fingerprint:
        test_run.status = ModelTestStatus.failed.value
        test_run.error_code = "profile_changed"
        test_run.error_message = "The vLLM profile changed after this test was queued"
    else:
        try:
            normalize_and_validate_base_url(profile.base_url, allowed_targets)
            result = asyncio.run(run_vllm_test(profile, crypto, test_run.mode))
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
            test_run.error_code = type(exc).__name__
            test_run.error_message = "vLLM test execution failed without storing upstream response content"
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
                            settings.vllm_allowed_targets,
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
                logger.info("vllm test started id=%s mode=%s", test_run.id, test_run.mode)
                process_vllm_test(db, crypto, test_run, settings.vllm_allowed_targets)
                logger.info("vllm test completed id=%s status=%s", test_run.id, test_run.status)
                continue
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
