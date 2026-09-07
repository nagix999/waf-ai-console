import { isMockAnalysis } from "./labelEvaluation.js";

const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const technicalLanguage = /primary|verifier|1차\s*판정|독립\s*(검증|판정)|검증\s*실패|(?:판정|분석\s*결과).{0,30}(?:불일치|일치하지|서로\s*다|다릅)|(?:실패|failure)[\s_]*id|output_validation_failed|framework_run_id/i;
const legacyGroundingCheck = "일부 LLM 근거가 지정된 필드의 원문과 일치하지 않아 제외되었습니다. 남은 근거를 직접 확인하세요.";
const groundingLimitation = "일부 발췌는 지정한 원문에서 확인되지 않아 판정 근거에서 제외했습니다.";

export function isTechnicalText(value) {
  return typeof value === "string" && technicalLanguage.test(value);
}

// Presentation only. Never replace the stored final verdict with an intermediate
// result, a reference Label or a guess based on WAF action.
export function finalValue(detail, key) {
  return record(detail?.result) && Object.hasOwn(detail.result, key) ? detail.result[key] : detail?.[key];
}

export function analystText(value, fallback = "미기록") {
  return typeof value === "string" && value.trim() && !isTechnicalText(value) ? value : fallback;
}

// Display names only: keep the exact stored path for auditing and quotation
// grounding. An unknown field remains literal; never infer its source.
export function analystFieldLabel(value) {
  if (typeof value !== "string" || !value.trim()) return "위치 미기록";
  const field = value.startsWith("event.") ? value.slice(6) : value;
  const httpNames = {
    request_line: "요청 시작줄", method: "HTTP 메서드", uri: "요청 주소", request_target: "요청 주소",
    path: "요청 경로", query: "요청 파라미터", headers: "HTTP 헤더", body: "요청 본문", protocol: "HTTP 버전",
  };
  const names = {
    payload: "HTTP 원문", raw_payload: "HTTP 원문", signature: "탐지 시그니처",
    src_ip: "출발지 IP", dest_ip: "목적지 IP", src_port: "출발지 포트", dest_port: "목적지 포트",
    company_name: "회사명", event_name: "이벤트명", waf_action: "WAF 조치", waf_vendor: "WAF 벤더",
  };
  const httpField = field.startsWith("payload.") ? field.slice(8) : field;
  if (Object.hasOwn(names, field)) return names[field];
  if (field.startsWith("payload.") && Object.hasOwn(httpNames, httpField)) return httpNames[httpField];
  const part = /^payload\.(headers|query)\.(.+)$/.exec(field);
  if (part) return `${part[1] === "headers" ? "HTTP 헤더" : "요청 파라미터"} · ${part[2]}`;
  return value;
}

export function analystItems(value) {
  return (Array.isArray(value) ? value : []).filter((item) => typeof item === "string" && item.trim() && !isTechnicalText(item));
}

export function hasTuningContent(value) {
  return record(value) && (value.recommended === true
    || [value.scope, value.proposal_ko, value.risk_ko, value.validation_ko].some((item) => analystText(item, "") !== ""));
}

export function analystSummary(detail) {
  if (detail?.status === "pending") return "분석을 기다리고 있습니다. 아직 확정된 판정이 없습니다.";
  if (detail?.status === "processing") return "요청을 분석하고 있습니다. 완료되면 판정과 확인할 내용을 표시합니다.";
  if (detail?.status === "failed") return "자동 분석을 완료하지 못했습니다. 원문을 직접 확인하고 기술정보에서 처리 상태를 확인해 주세요.";
  const guidance = record(detail?.result?.analyst_guidance) ? detail.result.analyst_guidance : {};
  const fallback = finalValue(detail, "verdict") === "inconclusive"
    ? "현재 분석에서는 정탐·오탐 판정을 보류했습니다."
    : "저장된 최종 판정과 원문 근거를 확인해 주세요.";
  return analystText(guidance.summary_ko, analystText(finalValue(detail, "summary_ko"), fallback));
}

export function analystGuidance(detail) {
  const result = record(detail?.result) ? detail.result : {};
  const guidance = record(result.analyst_guidance) ? result.analyst_guidance : {};
  const explicitChecks = Array.isArray(guidance.checks);
  const structured = explicitChecks ? guidance.checks
    : Array.isArray(result.analyst_checks) ? result.analyst_checks : [];
  const recommended = Array.isArray(result.recommended_checks) ? result.recommended_checks : [];
  const savedLimitations = Array.isArray(guidance.limitations) ? guidance.limitations : [];
  const hasGroundingDiagnostic = structured.some((item) => record(item) && item.check_ko === legacyGroundingCheck)
    || recommended.includes(legacyGroundingCheck) || savedLimitations.includes(legacyGroundingCheck);
  let checks = structured.filter(record).filter((item) => typeof item.check_ko === "string"
    && item.check_ko.trim() && item.check_ko !== legacyGroundingCheck && ![item.source_ko, item.check_ko, item.why_ko].some(isTechnicalText))
    .map((item) => ({
      source_ko: analystText(item.source_ko, "확인 위치 미기록"),
      check_ko: item.check_ko,
      why_ko: analystText(item.why_ko, "확인 목적 미기록"),
    }));
  // An explicit final guidance list, including [], is authoritative. Legacy
  // recommendations are a compatibility fallback, not extra required work.
  if (!explicitChecks && !checks.length) checks = recommended
    .filter((item) => typeof item === "string" && item.trim() && item !== legacyGroundingCheck && !isTechnicalText(item))
    .map((check_ko) => ({ source_ko: "확인 위치 미기록", check_ko, why_ko: "확인 목적 미기록" }));
  const limitations = savedLimitations.filter((item) => typeof item === "string" && item.trim() && !isTechnicalText(item))
    .map((item) => item === legacyGroundingCheck ? groundingLimitation : item);
  if (hasGroundingDiagnostic) limitations.push(groundingLimitation);
  return {
    summary_ko: analystSummary(detail), checks,
    limitations: [...new Set(limitations)],
  };
}

export function analystFollowUp(detail) {
  const guidance = analystGuidance(detail);
  const inconclusive = finalValue(detail, "verdict") === "inconclusive";
  return {
    ...guidance,
    visible: detail?.status === "completed" && record(detail?.result) && (inconclusive || guidance.checks.length > 0),
    introduction_ko: inconclusive
      ? "판정 보류를 해소하려면 아래 자료를 확인해 주세요. 확인한 내용과 요청 문맥을 함께 검토해 판단해야 합니다."
      : "위 판정과 별개로, 필요한 경우 아래 자료를 통해 영향 범위나 후속 대응을 확인해 주세요.",
    empty_ko: "구체적인 확인 자료는 기록되지 않았습니다. 저장된 원문과 대상 서비스의 처리 문맥을 함께 검토해 주세요.",
  };
}

// Presentation-only grouping for historical results. Exact field + excerpt
// identify a shared quotation; only exact duplicate interpretations are hidden.
// Do not normalize case/whitespace, guess aliases or discard different meanings.
export function groupedEvidence(value) {
  const groups = [];
  const byQuote = new Map();
  for (const item of Array.isArray(value) ? value : []) {
    if (!record(item)) continue;
    const key = typeof item.field === "string" && typeof item.excerpt === "string"
      ? JSON.stringify([item.field, item.excerpt]) : null;
    let group = key === null ? null : byQuote.get(key);
    if (!group) {
      group = { field: item.field, excerpt: item.excerpt, interpretations: [] };
      groups.push(group);
      if (key !== null) byQuote.set(key, group);
    }
    if (!group.interpretations.includes(item.interpretation_ko)) group.interpretations.push(item.interpretation_ko);
  }
  return groups;
}

export function analysisNotices(detail) {
  const notices = [];
  if (isMockAnalysis(detail)) notices.push("모의 분석 결과입니다. 실제 LLM의 보안 판정이 아닙니다.");
  if (finalValue(detail, "input_truncated") === true) notices.push("분석 입력의 일부가 생략되었습니다. 저장된 전체 원문을 함께 확인해 주세요.");
  if (detail?.status === "completed" && Object.hasOwn(detail, "result") && !record(detail.result)) notices.push("저장된 결과 본문이 없어 상세 근거를 표시할 수 없습니다.");
  return notices;
}

export function visibleDecoding(value) {
  if (!record(value)) return null;
  return {
    decoder_version: typeof value.decoder_version === "string" ? value.decoder_version : "미기록",
    items: (Array.isArray(value.items) ? value.items : []).filter(record)
      .filter((item) => typeof item.original === "string" && typeof item.decoded === "string")
      .map((item, index) => ({
        id: typeof item.id === "string" ? item.id : `candidate-${index + 1}`,
        field: typeof item.field === "string" ? item.field : "위치 미기록",
        start: Number.isInteger(item.start) && item.start >= 0 ? item.start : null,
        end: Number.isInteger(item.end) && item.end >= 0 ? item.end : null,
        original: item.original, decoded: item.decoded,
        steps: (Array.isArray(item.steps) ? item.steps : []).filter(record)
          .filter((step) => [step.encoding, step.input, step.output].every((part) => typeof part === "string"))
          .map(({ encoding, input, output }) => ({ encoding, input, output })),
        warnings: (Array.isArray(item.warnings) ? item.warnings : []).filter((warning) => typeof warning === "string"),
      })),
    warnings: (Array.isArray(value.warnings) ? value.warnings : []).filter((warning) => typeof warning === "string"),
    scan_truncated: value.scan_truncated === true,
    scanned_chars: Number.isInteger(value.scanned_chars) && value.scanned_chars >= 0 ? value.scanned_chars : null,
    total_chars: Number.isInteger(value.total_chars) && value.total_chars >= 0 ? value.total_chars : null,
  };
}

const decodingWarnings = {
  application_decoding_unverified: "대상 애플리케이션이 같은 방식으로 디코딩하는지는 확인되지 않았습니다.",
  base64_candidate: "Base64로 해석한 후보입니다. 실제 인코딩 의도와 문맥을 확인하세요.",
  decoded_control_characters: "변환 결과에 제어 문자가 포함되어 있습니다.",
  max_decode_steps_reached: "변환 단계 상한에 도달했습니다. 추가 인코딩이 남아 있을 수 있습니다.",
  scan_limit_reached: "입력 크기 제한으로 일부 구간만 확인했습니다.",
  incomplete_html_entity: "종료되지 않은 HTML 문자 참조가 있습니다.",
  candidate_crosses_scan_boundary: "확인 범위 경계를 걸치는 후보는 생략했습니다.",
  candidate_too_long: "크기 제한을 넘는 후보는 생략했습니다.",
  candidate_limit_reached: "후보 탐색 개수 상한에 도달했습니다.",
  item_limit_reached: "표시 항목 개수 상한에 도달했습니다.",
  serialized_output_limit_reached: "결과 크기 상한으로 일부 후보를 표시하지 않습니다.",
  decoded_text_too_long: "변환 결과가 크기 제한을 넘어 후속 변환을 중지했습니다.",
  decoded_text_invalid_unicode: "변환 결과의 유니코드가 유효하지 않아 중지했습니다.",
  decoding_cycle_detected: "변환 결과가 반복되어 중지했습니다.",
  invalid_url_percent_escape: "올바르지 않은 URL 퍼센트 인코딩이 있습니다.",
  url_percent_not_utf8: "URL 변환 결과를 UTF-8 텍스트로 읽을 수 없습니다.",
  invalid_html_entity: "인식할 수 없는 HTML 문자 참조가 있습니다.",
  invalid_html_codepoint: "HTML 문자 참조의 코드 포인트가 유효하지 않습니다.",
  invalid_unicode_escape: "유니코드 이스케이프가 올바르지 않습니다.",
  unpaired_unicode_surrogate: "짝이 맞지 않는 유니코드 서로게이트가 있습니다.",
  base64_not_utf8: "Base64 후보를 UTF-8 텍스트로 읽을 수 없습니다.",
  base64_not_printable: "Base64 후보에 표시할 수 없는 문자가 포함되어 있습니다.",
  legacy_percent_u_candidate: "표준 URL 인코딩이 아닌 이전 방식의 %uNNNN 해석 후보입니다. 대상 환경의 처리 방식을 확인하세요.",
  unicode_codepoint_escape_candidate: "중괄호 코드 포인트 이스케이프를 해석한 후보입니다. 대상 환경이 이 문법을 처리하는지 확인하세요.",
  base64_padding_inferred: "누락된 Base64 padding을 가정해 해석했습니다. 실제 인코딩 방식과 문맥을 확인하세요.",
  default_lookup_candidate: "속성이 정의되지 않았을 때 적용될 수 있는 기본값으로 해석했습니다. 실제 환경의 값은 조회하지 않았습니다.",
  unresolved_lookup: "환경이나 속성 등 추가 정보가 필요한 조회 표현식은 해석하지 않고 그대로 남겼습니다.",
  jndi_lookup_not_executed: "JNDI 조회와 외부 연결은 수행하지 않았습니다. 이 표현식만으로 공격 성공을 확인할 수 없습니다.",
  escaped_log4j_lookup: "이스케이프된 조회 표현식을 변환하지 않고 보존했습니다. 실제 처리는 환경과 처리 단계를 확인하세요.",
  malformed_log4j_lookup: "조회 표현식의 구문이 완전하지 않아 해당 후보를 원본대로 보존했습니다.",
  log4j_input_too_long: "조회 표현식의 입력 크기 한도로 정적 해석을 생략했습니다.",
  log4j_output_too_long: "조회 표현식의 결과 크기 한도로 정적 해석을 중지했습니다.",
  log4j_depth_limit_reached: "조회 표현식의 중첩 깊이 한도에 도달했습니다. 미해석 표현식이 남아 있을 수 있습니다.",
  log4j_node_limit_reached: "조회 표현식의 구성 요소 개수 한도에 도달했습니다. 전체 해석 결과가 아닙니다.",
  log4j_operation_limit_reached: "조회 표현식의 처리 횟수 한도에 도달했습니다. 전체 해석 결과가 아닙니다.",
  log4j_unicode_case_unsupported: "비ASCII 문자의 대소문자 변환은 환경 차이 때문에 수행하지 않고 보존했습니다.",
  log4j_case_locale_unverified: "ASCII I/i의 대소문자 처리도 JVM 언어·지역 설정에 따라 달라질 수 있습니다. 대상 환경을 확인하세요.",
};

export function decodingWarningText(code) {
  if (typeof code !== "string" || !code.trim()) return "도구 안내 미기록";
  return Object.hasOwn(decodingWarnings, code) ? decodingWarnings[code] : `추가 도구 안내: ${decodingDisplayText(code)}`;
}

const decodingEncodings = {
  url_percent: "URL 퍼센트 인코딩",
  html_entity: "HTML 문자 참조",
  unicode_escape: "유니코드·문자 이스케이프",
  base64: "표준 Base64 해석 후보",
  url_percent_u: "이전 방식 %uNNNN 해석 후보",
  base64url: "URL-safe Base64 해석 후보",
  log4j_lookup_static: "Log4j 조회 표현식 · 실행 없는 정적 해석",
};

export function decodingEncodingText(code) {
  if (typeof code !== "string" || !code.trim()) return "변환 방식 미기록";
  return Object.hasOwn(decodingEncodings, code) ? decodingEncodings[code] : `기타 변환: ${decodingDisplayText(code)}`;
}

export function decodingItemStatus(item) {
  if (!Array.isArray(item?.steps) || !item.steps.length) {
    return typeof item?.original === "string" && item.original === item.decoded
      ? "변환하지 않음 · 정적 확인 안내" : "변환 단계 미기록";
  }
  return `기록된 변환 ${item.steps.length}단계 · 실제 환경의 처리 결과는 미확인`;
}

// A display-only view, never written back or passed to the model. Show invisible
// controls explicitly so CR/NUL and direction overrides cannot disguise text.
export function decodingDisplayText(value) {
  return typeof value === "string" ? value.replace(/[\u0000-\u0009\u000b-\u001f\u007f-\u009f\u061c\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g,
    (character) => `⟦U+${character.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}⟧`) : "";
}
