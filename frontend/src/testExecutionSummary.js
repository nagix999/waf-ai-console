const VERSION = "test-execution-summary-v1";

const reasons = Object.freeze({
  primary_inconclusive: ["1차 판정 보류", "Initial verdict inconclusive"],
  low_confidence: ["1차 판정의 확신 부족", "Low confidence in the initial verdict"],
  signature_not_exact: ["탐지 내용과 요청이 완전히 일치하지 않음", "Detection and request do not fully match"],
  true_positive_but_allowed: ["1차 정탐·WAF 허용", "Initial true positive · WAF allowed"],
  false_positive_but_denied: ["1차 오탐·WAF 차단", "Initial false positive · WAF denied"],
  parser_incomplete: ["요청 구조를 모두 인식하지 못함", "Request structure not fully recognized"],
  input_truncated: ["모델 입력 일부 생략", "Part of the model input omitted"],
  tuning_recommended: ["정책 조정 제안 있음·이전 지침", "Policy adjustment proposed · older instructions"],
  interpretation_conflict: ["근거 해석이 서로 맞지 않음", "Evidence interpretations conflict"],
  conflicting_evidence: ["상충 근거 있음·이전 지침", "Conflicting evidence · older instructions"],
  evidence_grounding_failed: ["일부 근거의 원문 연결 확인 실패", "Some evidence could not be linked to the source"],
  input_integrity_requires_review: ["수집 내용·요청 경계 추가 확인 필요", "Capture or request boundaries need review"],
});

const warnings = Object.freeze({
  request_protocol_not_recognized: ["HTTP 버전 미인식", "HTTP version not recognized"],
  request_target_not_recognized: ["요청 대상 형식 미인식", "Request target not recognized"],
  headers_terminator_missing: ["헤더 종료 구분자 없음", "Header terminator missing"],
  request_line_not_recognized: ["요청 시작 줄 미인식", "Request line not recognized"],
  escaped_line_endings_not_decoded: ["문자로 저장된 줄바꿈은 변환하지 않음", "Literal newline escapes left unchanged"],
  header_line_not_recognized: ["일부 헤더 형식 미인식", "Some header lines not recognized"],
  capture_inspection_limit: ["파싱 검사 한도 도달", "Parsing inspection limit reached"],
  capture_line_endings_ambiguous: ["줄바꿈 형식이 섞여 있음", "Mixed line ending formats"],
  capture_serialization_unverified: ["문자 줄바꿈 해석·전송 원문 확인 필요", "Serialized line endings interpreted; wire input unverified"],
});

const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
export const executionCount = value => Number.isSafeInteger(value) && value >= 0 ? value : null;

function labelFor(mapping, code, words, fallback) {
  return typeof code === "string" && Object.hasOwn(mapping, code) ? words(...mapping[code]) : words(...fallback);
}

export function verifierReasonLabel(code, words = ko => ko) {
  return labelFor(reasons, code, words, ["기타 검증 사유", "Other verification reason"]);
}

export function parserWarningLabel(code, words = ko => ko) {
  return labelFor(warnings, code, words, ["기타 파싱 경고", "Other parsing warning"]);
}

function rows(value, mapping, fields) {
  if (!Array.isArray(value)) return null;
  return value.map((row, index) => ({
    rowKey: String(index),
    code: object(row) && typeof row.code === "string" && Object.hasOwn(mapping, row.code) ? row.code : null,
    ...Object.fromEntries(fields.map(field => [field, executionCount(object(row) ? row[field] : null)])),
  }));
}

export function executionSummary(value) {
  if (!object(value) || value.version !== VERSION) return null;
  const parser = object(value.parser) ? value.parser : {};
  const verifier = object(value.verifier) ? value.verifier : {};
  return {
    cases: executionCount(value.cases),
    parser: {
      ...Object.fromEntries(["success", "partial", "failed", "unrecorded", "warnings_recorded", "warnings_unrecorded"].map(key => [key, executionCount(parser[key])])),
      warnings: rows(parser.warnings, warnings, ["cases"]),
    },
    verifier: {
      ...Object.fromEntries(["policy_recorded", "executed", "not_executed", "unrecorded", "not_applicable", "awaiting_execution", "reasons_unrecorded", "parser_triggered", "parser_only", "parser_with_other"].map(key => [key, executionCount(verifier[key])])),
      reasons: rows(verifier.reasons, reasons, ["cases", "only_cases", "with_other_cases"]),
    },
  };
}
