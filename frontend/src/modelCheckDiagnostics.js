export const modelCheckNames = {
  models: "모델 연결", basic_chat: "기본 응답", nested_json_schema: "출력 형식",
  waf_analysis_schema: "WAF 판정 형식", system_role: "지침 적용",
  near_configured_context: "긴 입력 처리", near_32k_context: "긴 입력 처리", concurrency: "동시 요청",
};

export function modelValidationError(code) {
  const reason = typeof code === "string" ? code.replace(/^(vllm|openai)_/, "") : "";
  const messages = {
    output_incomplete: "출력이 토큰 한도에 도달해 중단되었습니다. 확인 내용에서 당시 출력 한도·사용량을 확인하고, 서버의 문맥 길이 설정도 확인하세요. 동시 요청 수가 1이어도 발생할 수 있습니다.",
    refusal: "모델이 응답을 거절했습니다. 출력 길이 부족과는 다른 사유입니다. 서버의 거절 처리 설정을 확인하세요.",
    completion_not_finished: "모델이 정상 종료 상태로 응답하지 않았습니다. 확인 내용에서 종료 사유를 확인하세요.",
    invalid_response: "모델 응답이 비어 있거나 응답 형식이 올바르지 않습니다. 서버의 응답 형식 설정을 확인하세요.",
    timeout: "제한 시간 안에 응답을 받지 못했습니다. 서버 부하와 프로필의 제한 시간을 확인하세요.",
    connection_failed: "모델 서버에 연결하지 못했습니다. 서버 상태와 등록된 주소·포트를 확인하세요.",
    concurrency_limit_below_test: "Agent 설정 → 동시 처리의 서버 상한이 검증 요청 수보다 작습니다. 서버 상한 또는 프로필의 검증 동시 요청 수를 조정한 뒤 다시 실행하세요.",
    json_schema_validation_failed: "응답이 지정한 JSON 형식과 다릅니다. 모델 서버의 구조화 출력 지원을 확인하세요.",
    waf_schema_validation_failed: "응답이 WAF 판정 형식이나 필수 조건을 충족하지 못했습니다.",
    system_role_not_applied: "응답에서 검증용 지침이 적용된 것을 확인하지 못했습니다. 서버의 채팅 템플릿을 확인하세요.",
    configured_model_not_served: "서버의 모델 목록에 등록한 모델이 없습니다. 프로필의 모델 이름을 확인하세요.",
    usage_missing: "서버가 입력 토큰 사용량을 반환하지 않아 긴 입력 처리를 확인하지 못했습니다.",
    near_context_below_target: "입력 토큰 사용량이 설정한 문맥 길이의 80%에 못 미쳐 긴 입력 검증을 통과하지 못했습니다.",
    target_not_allowed: "등록된 내부 연결 허용 정책에 따라 요청이 차단되었습니다. 내부 IP·포트 설정을 확인하세요.",
    http_401: "모델 서버 인증에 실패했습니다. 프로필의 API Key를 확인하세요.",
    http_403: "모델 서버가 접근을 거부했습니다. 접근 권한을 확인하세요.",
    http_429: "모델 서버의 호출 한도에 도달했습니다. 서버의 요청 한도와 사용량을 확인하세요.",
  };
  if (Object.hasOwn(messages, reason)) return messages[reason];
  if (/^http_\d{3}$/.test(reason)) return "모델 서버가 오류 상태를 반환했습니다. 확인 내용의 기술 기록에서 HTTP 상태를 확인하세요.";
  return "검증을 완료하지 못했습니다. 확인 내용과 검증 기술정보를 확인하세요.";
}

export function diagnosticCount(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString("ko-KR") : "미기록";
}

export function completionReason(record) {
  const error = typeof record?.error_code === "string" ? record.error_code.replace(/^(vllm|openai)_/, "") : "";
  const requestErrors = { timeout: "시간 초과", connection_failed: "연결 실패", invalid_response: "응답 형식 오류", target_not_allowed: "요청 차단" };
  if (Object.hasOwn(requestErrors, error)) return requestErrors[error];
  if (/^http_\d{3}$/.test(error)) return `HTTP ${error.slice(5)}`;
  if (record?.refused === true) return "응답 거절";
  const labels = { stop: "정상 종료", length: "토큰 한도 도달", content_filter: "콘텐츠 필터", tool_calls: "도구 호출", function_call: "함수 호출", abort: "중단", error: "서버 오류", unknown: "알 수 없음" };
  return typeof record?.finish_reason === "string" && Object.hasOwn(labels, record.finish_reason)
    ? labels[record.finish_reason] : record?.finish_reason == null ? "미기록" : "알 수 없음";
}
