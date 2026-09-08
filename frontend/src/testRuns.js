import { matrixDrilldown } from "./evaluationMetrics.js";

export const runKinds = { direct: "단건 분석", upload: "배치 파일 분석", model_validation: "150건 모델 검증" };
export const runStatuses = { pending: "대기", processing: "진행 중", completed: "처리 완료", failed: "실행 실패" };

export function newTestRequestKey() {
  return globalThis.crypto?.randomUUID?.() || `test-${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
}

export function autoTestName(name, requestId) {
  return typeof name === "string" && name.trim() ? name.trim() : requestId;
}

export function emptySingleTest() {
  return { event_id: "", company_name: "", src_ip: "", dest_ip: "", src_port: "", dest_port: "", waf_vendor: "", waf_action: "", signature: "", event_name: "", payload: "" };
}

export function singleTestEvent(form, requestId) {
  const event = { ...form, event_id: form.event_id.trim() || `event-${requestId}` };
  for (const key of ["src_port", "dest_port"]) {
    if (form[key] === "") delete event[key];
    else event[key] = Number(form[key]);
  }
  for (const key of ["signature", "event_name"]) if (!form[key].trim()) delete event[key];
  return event;
}

export function validateTestName(name) {
  return typeof name !== "string" || !name.trim() || name.trim().length > 120 ? "테스트명을 1~120자로 입력하세요." : "";
}

export function testRunQuery({ difficulty = "", test_category = "", difficulty_missing = false, test_category_missing = false, status = "", evaluation_outcome = "", cell = "", offset = 0, limit = 25 } = {}) {
  return Object.fromEntries(Object.entries({ limit, offset, ...(difficulty_missing ? { difficulty_missing: true } : { difficulty }), ...(test_category_missing ? { test_category_missing: true } : { test_category }), status, evaluation_outcome, ...matrixDrilldown(cell) }).filter(([, value]) => value !== ""));
}

export function testScopeSelection(name, value) {
  if (!["difficulty", "test_category"].includes(name)) return {};
  return { [name]: value.startsWith("value:") ? value.slice(6) : "", [`${name}_missing`]: value === "missing:" };
}

export function testRunError(error) {
  const messages = {
    test_run_idempotency_conflict: "같은 접수 키로 다른 요청을 보낼 수 없습니다. 실행 이력을 확인한 뒤 새 테스트로 접수하세요.",
    idempotency_key_conflict: "같은 접수 키의 요청 내용이 다릅니다. 실행 이력을 확인한 뒤 새 테스트로 접수하세요.",
    production_model_not_configured: "운영 모델이 설정되지 않았습니다. 설정에서 모델을 검증하고 Production으로 지정하세요.",
    no_production_model: "운영 모델이 설정되지 않았습니다. 설정에서 모델을 검증하고 Production으로 지정하세요.",
    test_model_profile_required: "Test 모델이 지정되지 않았습니다. 설정 → LLM 프로필에서 전체 검증을 통과한 프로필을 Test로 지정하세요. Production으로 자동 대체하지 않습니다.",
    matching_full_test_required: "Test 모델의 현재 설정과 일치하는 전체 검증 통과 이력이 필요합니다. 설정에서 검증 상태를 확인하세요.",
    profile_not_verified: "Test 모델의 검증 상태를 확인하세요. 현재 설정으로 전체 검증을 통과한 프로필을 Test로 지정한 뒤 다시 접수하세요.",
    prompt_policy_version_not_found: "공통 활성 프롬프트 버전을 찾지 못했습니다. 설정의 프롬프트 적용 상태를 확인하세요.",
    prompt_policy_content_unavailable: "공통 활성 프롬프트 내용을 확인하지 못했습니다. 설정과 암호화 키 상태를 확인하세요.",
    prompt_policy_integrity_failed: "공통 활성 프롬프트의 저장 내용을 검증하지 못했습니다. 관리자가 버전 상태를 확인해야 합니다.",
    prompt_policy_context_budget_too_small: "공통 활성 프롬프트가 Test 모델의 입력 예산에 비해 깁니다. 지침 길이와 모델의 컨텍스트·출력 예산을 확인하세요.",
    invalid_test_name: "테스트명을 1~120자로 입력하세요.",
  };
  return Object.hasOwn(messages, error?.message) ? messages[error.message] : error?.status === 422 ? "테스트명·이벤트 필드·파일 형식을 확인하세요. 입력 검증을 통과하지 못했습니다." : error?.status ? "테스트 요청을 처리하지 못했습니다. 실행 이력과 입력을 확인한 뒤 다시 시도하세요." : "접수 응답을 확인하지 못했습니다. 자동 재전송하지 않습니다. 실행 이력을 확인하거나 같은 입력으로 다시 접수하면 동일 요청으로 확인합니다.";
}
