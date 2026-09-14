// Only complete, content-free legacy messages. Substring filtering used to
// erase valid explanations about code_verifier, primary_email, etc.
const legacyDiagnostics = new Set([
  "Primary와 Verifier의 분석 결과가 서로 다릅니다.", "Primary와 Verifier 판정이 서로 다릅니다.",
  "Primary/Verifier 판정 불일치로 최종 판정을 보류합니다.", "독립 검증 실패", "output_validation_failed", "framework_run_id",
  "Primary와 Verifier 판정이 다릅니다.", "독립 검증 실패로 보류합니다.",
  "분석 결과가 서로 다릅니다.", "판정 불일치 발생", "판정이 일치하지 않아 보류",
  "1차 판정의 신뢰도가 낮습니다", "실패 ID 확인", "failure_id",
  "Primary 오류", "Primary/Verifier 오류", "Primary / Verifier 결과를 확인하세요.",
  "Verifier 결과", "독립 검증 결과 확인", "Primary와 Verifier의 판정 불일치",
  "판정 불일치 상태", "Primary/Verifier verdict 불일치",
]);
const oldInputLimitation = "원문 전체가 분석 입력에 포함되지 않았습니다. 생략된 구간을 원문에서 확인하세요.";

export function isTechnicalText(value) {
  return typeof value === "string" && legacyDiagnostics.has(value.trim());
}

export function analystText(value, fallback = "미기록") {
  return typeof value === "string" && value.trim() && !isTechnicalText(value)
    ? value === oldInputLimitation ? "분석 입력의 일부가 생략되었습니다. 전체 원문을 함께 확인해 주세요." : value : fallback;
}
