const fields = ["initial_verdict", "initial_probability", "initial_model_version"];
const messages = {
  initial_assessment_pair_required: ["1차 판정과 신뢰도는 함께 입력하세요.", "Enter both the initial verdict and confidence."],
  initial_verdict_invalid: ["1차 판정은 정탐 또는 오탐으로 입력하세요.", "The initial verdict must be true positive or false positive."],
  initial_probability_invalid: ["1차 신뢰도는 0~1 사이의 숫자로 입력하세요. 90%는 0.9입니다.", "Initial confidence must be a number from 0 to 1. Use 0.9 for 90%."],
  initial_model_version_invalid: ["1차 모델 버전은 1~255자로 입력하거나 비워두세요.", "Use 1–255 characters for the initial model version, or leave it blank."],
  initial_assessment_conflict: ["같은 이벤트에 저장된 1차 판정 정보와 다릅니다. 기존 값으로 재전송하거나 새 테스트로 접수하세요.", "Initial assessment differs from the saved event. Resend the original values or create a new Test."],
  duplicate_test_metadata_location: ["같은 접수 정보를 두 곳에 입력했습니다. 전용 입력란이나 추가 JSON 중 한 곳만 사용하세요.", "Admission metadata appears twice. Use either the dedicated fields or additional JSON, not both."],
  initial_assessment_location_invalid: ["1차 판정 정보는 extra_fields 내부가 아닌 별도 접수 항목으로 입력하세요.", "Provide initial assessment as admission metadata, not inside extra_fields."],
  initial_assessment_single_request_only: ["1차 판정 정보는 단건 요청에서만 지원합니다. 파일과 데이터셋 입력에서는 제거하세요.", "Initial assessment is supported only for single requests. Remove it from files and dataset inputs."],
};

export function initialAssessmentError(error, words = ko => ko) {
  let code = error?.message;
  if (!Object.hasOwn(messages, code || "")) {
    const issue = error?.issues?.find(issue => fields.includes(issue.field?.split(".").at(-1)) || issue.code === "initial_assessment_pair_required");
    code = issue?.code || ({ initial_verdict: "initial_verdict_invalid", initial_probability: "initial_probability_invalid", initial_model_version: "initial_model_version_invalid" })[issue?.field?.split(".").at(-1)];
  }
  return Object.hasOwn(messages, code || "") ? words(...messages[code]) : null;
}

export const emptyInitialAssessment = () => Object.fromEntries(fields.map(key => [key, ""]));

export function singleTestAdmission(observation, form = {}) {
  const event = { ...observation }, metadata = {};
  for (const key of fields) {
    if (Object.hasOwn(event, key)) { metadata[key] = event[key]; delete event[key]; }
    if (form[key] !== undefined && form[key] !== "") {
      if (Object.hasOwn(metadata, key)) throw new Error("duplicate_test_metadata_location");
      metadata[key] = key === "initial_probability" ? (String(form[key]).trim() ? Number(form[key]) : NaN) : form[key];
    }
  }
  if (event.extra_fields && fields.some(key => Object.hasOwn(event.extra_fields, key))) throw new Error("initial_assessment_location_invalid");
  const { initial_verdict: verdict, initial_probability: probability, initial_model_version: version } = metadata;
  if (verdict != null && !["true_positive", "false_positive"].includes(verdict)) throw new Error("initial_verdict_invalid");
  if (probability != null && (typeof probability !== "number" || !Number.isFinite(probability) || probability < 0 || probability > 1)) throw new Error("initial_probability_invalid");
  if (version != null && (typeof version !== "string" || !version.length || version.length > 255)) throw new Error("initial_model_version_invalid");
  if ((verdict == null) !== (probability == null) || version != null && verdict == null) throw new Error("initial_assessment_pair_required");
  return { event, ...metadata };
}
