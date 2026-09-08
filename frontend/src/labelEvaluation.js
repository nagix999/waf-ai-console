// Presentation only: eligibility and comparisons are decided by the server.
// Reference answers must never be attached to analysis/LLM input documents.
export const referenceVerdicts = { true_positive: "정탐", false_positive: "오탐", inconclusive: "판단 보류" };
export const labelSources = { synthetic_expected: "기대 답안", reference: "참고 답안" };
export const evaluationOutcomes = {
  match: "답안 일치", false_negative: "불일치 · 미탐 방향", false_positive: "불일치 · 과탐 방향",
  abstained: "모델 판단 보류", expected_abstention_match: "기대 보류 일치", expected_abstention_mismatch: "기대 보류 불일치",
  unlabeled: "답안 없음", pending: "평가 대기", failed: "실행 실패 · 평가 제외", stub: "모의 실행 · 평가 제외",
  unknown_provenance: "실행 출처 미확인 · 평가 제외",
  input_contaminated: "정답 포함 입력 · 평가 제외",
};

// Detect explicit persisted mock markers for display even when the comparison
// outcome is "unlabeled". Absence of these markers does not prove a real call.
export function isMockAnalysis(detail) {
  const agent = detail?.result?.agent;
  return ["stub", "stub-no-llm"].includes(detail?.model_profile)
    || ["stub", "stub-v0"].includes(detail?.prompt_version)
    || ["stub", "stub-v0"].includes(detail?.result?.policy?.prompt_version)
    || ["stub", "local-stub"].includes(agent?.framework)
    || agent?.execution === "stub" || agent?.agent_mode === "stub" || agent?.llm_called === false
    || detail?.evaluation?.outcome === "stub";
}

export function referenceSourceText(reference) {
  return labelSources[reference?.source_kind] || "출처 미기록";
}

export function referenceVisibilityText(value) {
  if (value === true) return "AI 결과 열람 · 지원 판정";
  if (value === false) return "AI 결과 미열람 (입력자 신고)";
  return "AI 열람 여부 미확인";
}

export function evaluationOutcomeText(evaluation) {
  const outcome = evaluation?.outcome;
  const reference = evaluation?.reference_label;
  if (outcome === "match") {
    if (reference?.source_kind === "synthetic_expected") return "기대 일치";
    if (reference?.ai_visible === true) return "지원 판정 일치";
    return "맞음 · 답안 기준";
  }
  if (["false_negative", "false_positive"].includes(outcome)) {
    const direction = outcome === "false_negative" ? "미탐 방향" : "과탐 방향";
    return `${reference?.source_kind === "synthetic_expected" ? "기대 불일치" : "틀림 · 답안 기준"} · ${direction}`;
  }
  return evaluationOutcomes[outcome] || "평가 정보 없음";
}

export function evaluationTone(outcome) {
  if (["match", "expected_abstention_match"].includes(outcome)) return "match";
  if (["false_negative", "false_positive", "expected_abstention_mismatch"].includes(outcome)) return "mismatch";
  if (["abstained", "pending"].includes(outcome)) return "waiting";
  return "excluded";
}

export function evaluationExplanation(evaluation) {
  const outcome = evaluation?.outcome;
  if (outcome === "unlabeled") return "연결된 답안이 없어 평가하지 않습니다. 답안 연결은 분석 결과 목록에서 할 수 있습니다.";
  if (outcome === "pending") return "분석 완료 후 저장된 최종 판정으로 비교합니다. 현재는 일치율 분모에서 제외합니다.";
  if (outcome === "failed") return "실행 실패는 잘못된 보안 판정과 구분하며 일치율 분모에서 제외합니다.";
  if (outcome === "stub") return "모의 실행은 실제 LLM 판정이 아니므로 답안이 같아도 평가하지 않습니다.";
  if (outcome === "unknown_provenance") return "실제 모델 실행 여부를 확인할 수 없어 품질 평가에서 제외합니다.";
  if (outcome === "input_contaminated") return "기존 분석 입력의 추가 필드에서 답안 관련 키가 확인되어 평가에서 제외합니다. 이 검사는 임의의 원문에 숨긴 정답까지 탐지한다고 보장하지 않습니다.";
  if (outcome === "abstained") return "답안은 정탐/오탐이지만 모델 최종 판정은 보류입니다. 미탐·과탐과 구분하며 확정 판정 비율에 반영합니다.";
  if (outcome?.startsWith("expected_abstention_")) return "테스트 시나리오의 기대 보류 여부를 비교합니다. 정탐/오탐 확정 판정 지표와 분리하며 검증된 운영 정답이 아닙니다.";
  return "제공된 답안과 저장된 AI 최종 판정을 비교합니다. 답안 자체의 정확성이나 실제 운영 성능을 보증하지 않습니다.";
}

const compactOutcomes = {
  match: "일치", false_negative: "다름", false_positive: "다름",
  abstained: "판정 보류", expected_abstention_match: "일치", expected_abstention_mismatch: "다름",
  pending: "평가 대기", failed: "제외", stub: "제외", unknown_provenance: "제외", input_contaminated: "제외",
};

// Compact wording never recalculates eligibility or equality from displayed
// verdicts. The server outcome remains authoritative, including exclusions.
// Display wording may change; server outcomes and stored records stay intact.
export function compactEvaluation(evaluation) {
  const outcome = evaluation?.outcome;
  const reference = evaluation?.reference_label;
  const unlabeled = outcome === "unlabeled" && !reference;
  const known = typeof outcome === "string" && Object.hasOwn(compactOutcomes, outcome);
  const referenceText = typeof reference?.verdict === "string" && Object.hasOwn(referenceVerdicts, reference.verdict)
    ? referenceVerdicts[reference.verdict] : "미기록";
  const source = reference?.source_kind === "synthetic_expected" ? "기대 답안"
    : reference?.source_kind === "reference" ? "참고 답안" : "출처 미기록";
  const visibility = reference?.ai_visible === true ? "지원 판정"
    : reference?.ai_visible === false ? "AI 미열람 (신고)" : "AI 열람 미확인";
  const direction = outcome === "false_negative" ? "미탐 방향"
    : outcome === "false_positive" ? "과탐 방향"
      : typeof outcome === "string" && outcome.startsWith("expected_abstention_") ? "기대 보류 비교" : "";
  return {
    unlabeled,
    text: unlabeled ? "—" : known ? compactOutcomes[outcome] : "평가 정보 없음",
    tone: known ? evaluationTone(outcome) : "excluded",
    referenceText,
    sourceText: reference ? `${source} · ${visibility}` : "기준 정보 없음",
    direction,
    excluded: ["failed", "stub", "unknown_provenance", "input_contaminated"].includes(outcome),
    explanation: unlabeled ? "연결된 참고 답안이 없습니다." : known ? evaluationExplanation(evaluation)
      : "평가 정보를 확인할 수 없습니다. 답안 없음이나 평가 제외로 추정하지 않으며 새로고침으로 다시 확인하세요.",
  };
}

export function ratioText(numerator, denominator) {
  if (!Number.isInteger(numerator) || !Number.isInteger(denominator) || numerator < 0 || denominator < numerator) return "미기록";
  if (!denominator) return "산출 대상 없음 (0 / 0건)";
  return `${(numerator / denominator * 100).toFixed(1)}% (${numerator.toLocaleString()} / ${denominator.toLocaleString()}건)`;
}

const attachmentErrors = {
  invalid_label_source_system: "분석 출처의 정확한 값과 길이를 확인하세요.",
  invalid_label_source_ref: "답안 출처/버전은 120자 이내의 문자·숫자·공백·._-만 허용합니다.",
  label_file_too_large: "참고 답안 파일은 최대 2 MiB입니다.",
  label_file_must_be_json: ".json 확장자의 JSON 배열 파일을 선택하세요.",
  invalid_label_json: "UTF-8 JSON 문법을 확인하세요. 분석 로그가 아닌 참고 답안 파일을 선택해야 합니다.",
  label_file_requires_nonempty_array: "파일의 최상위 값은 1개 이상의 답안이 있는 JSON 배열이어야 합니다.",
  too_many_label_rows: "한 번에 최대 500행까지 연결할 수 있습니다. 파일을 나누어 주세요.",
  duplicate_answer_json_key: "같은 JSON 객체 안에 중복된 필드명이 있습니다.",
  label_json_too_deep: "JSON 중첩이 너무 깊습니다. 답안 배열 형식을 확인하세요.",
  answer_row_must_be_object: "이 행은 event_id와 expected_verdict를 가진 객체여야 합니다.",
  invalid_answer_event_id: "이벤트 ID의 누락·앞뒤 공백·길이를 확인하세요.",
  invalid_reference_verdict: "기대 판정은 true_positive, false_positive, inconclusive 중 하나여야 합니다.",
  inconclusive_requires_synthetic_expected: "기대 보류(inconclusive)는 기대 답안에서만 허용합니다.",
  duplicate_answer_event_id: "같은 이벤트 ID가 답안 파일 안에 반복되었습니다.",
  analysis_not_found_in_source: "지정한 분석 출처에서 이 이벤트 ID를 찾지 못했습니다. 분석 접수와 source 범위를 확인하세요.",
  label_preview_stale: "미리보기 이후 연결 대상이나 답안 이력이 변경되었습니다.",
  label_preview_expired: "미리보기 유효기간 15분이 지났습니다.",
  invalid_label_preview: "미리보기 확인 정보를 사용할 수 없습니다.",
  label_attachment_conflict: "같은 연결 요청의 저장 정보가 충돌합니다.",
  label_confirmation_busy: "다른 저장 작업이 진행 중입니다. 잠시 뒤 다시 확인하세요.",
};

export function labelAttachmentError(code) {
  return Object.hasOwn(attachmentErrors, code) ? attachmentErrors[code] : code || "답안 연결에 실패했습니다.";
}

export function validateLabelAttachment(file, form) {
  if (!file) return "참고 답안 JSON 파일을 선택하세요.";
  if (!file.name?.toLowerCase().endsWith(".json")) return "JSON 배열 형식의 .json 파일을 선택하세요.";
  if (file.size > 2 * 1024 * 1024) return "참고 답안 파일은 최대 2 MiB까지 연결할 수 있습니다.";
  if (!form.source_system.trim()) return "연결할 분석 출처의 정확한 값을 입력하세요.";
  if (form.source_system.trim().length > 120) return "분석 출처은 120자 이내로 입력하세요.";
  if (!Object.hasOwn(labelSources, form.source_kind)) return "답안 출처를 선택하세요.";
  if (!form.source_ref.trim()) return "답안 출처/버전을 입력하세요.";
  if (form.source_ref.trim().length > 120 || !/^[\p{L}\p{N} ._-]+$/u.test(form.source_ref.trim())) return "답안 출처/버전은 120자 이내의 문자·숫자·공백·마침표·밑줄·하이픈만 사용하세요.";
  if (!["unknown", "true", "false"].includes(form.ai_visible)) return "답안 작성 시 AI 결과 열람 여부를 명시적으로 선택하세요.";
  return "";
}

export function labelPreviewForm(file, form) {
  const body = new FormData();
  body.append("file", file);
  body.append("source_system", form.source_system.trim());
  body.append("source_kind", form.source_kind);
  body.append("source_ref", form.source_ref.trim());
  body.append("ai_visible", form.ai_visible);
  return body;
}
