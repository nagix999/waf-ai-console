export function retryError(code) {
  const messages = {
    retry_requires_failed_analysis: "실패한 분석만 재실행할 수 있습니다.",
    retry_requires_moduagent_mode: "실제 분석 모드에서 재실행할 수 있습니다.",
    retry_model_validation_requires_new_validation: "모델 검증의 실패 항목입니다. LLM 설정에서 새 검증을 진행하세요.",
    retry_execution_snapshot_unavailable: "당시 실행 설정이 남아 있지 않아 재실행할 수 없습니다.",
    retry_execution_snapshot_invalid: "당시 실행 설정을 확인할 수 없습니다. 관리자에게 문의하세요.",
    retry_input_or_prompt_snapshot_unavailable: "당시 입력 또는 지침을 복원할 수 없습니다.",
    retry_original_profile_missing: "당시 모델 프로필을 찾을 수 없습니다.",
    retry_original_profile_disabled: "당시 모델이 비활성화되어 있습니다.",
    retry_original_profile_changed: "당시 모델 설정이 변경되어 같은 조건으로 재실행할 수 없습니다.",
    retry_original_profile_not_verified: "당시 모델 설정에 대한 검증이 유효하지 않습니다.",
    retry_original_profile_target_not_allowed: "당시 모델의 연결 주소 또는 외부 전송 승인을 확인하세요.",
    retry_event_unavailable: "당시 입력을 복원할 수 없습니다.",
    retry_event_reference_contamination: "입력에 참고 답안 정보가 섞여 있어 재실행할 수 없습니다. 관리자에게 입력 이력 확인을 요청하세요.",
    retry_event_fingerprint_mismatch: "저장된 입력이 최초 접수 내용과 일치하지 않아 재실행을 중단했습니다.",
    retry_storage_unavailable: "재실행 정보를 저장하거나 조회하지 못했습니다. 잠시 후 조건을 다시 확인하세요.",
    retry_already_created: "이미 재실행한 결과가 있습니다.",
    retry_idempotency_conflict: "재실행 요청이 충돌했습니다. 결과를 새로고침하세요.",
    retry_concurrent_request_conflict: "다른 재실행 요청이 접수되었습니다. 다시 확인하세요.",
    analysis_not_found: "분석을 찾을 수 없습니다.",
  };
  return messages[code] || "재실행을 접수하지 못했습니다. 다시 확인하세요.";
}

export function retryPayload(idempotencyKey, acknowledged) {
  if (!acknowledged || typeof idempotencyKey !== "string" || idempotencyKey.length < 8) throw new Error("retry_confirmation_required");
  return { idempotency_key: idempotencyKey, cost_acknowledged: true };
}
