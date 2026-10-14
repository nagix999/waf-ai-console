// Explicit metadata fields only: raw payload/JSON paths are not searchable.
const verdicts = [["true_positive", "정탐", "True positive"], ["false_positive", "오탐", "False positive"], ["inconclusive", "보류", "Inconclusive"]];
const outcomes = [["false_positive", "과탐 방향", "False alarm"], ["false_negative", "미탐 방향", "Missed attack"],
  ["match", "일치", "Match"], ["abstained", "참고 정탐·오탐 → AI 보류", "Decisive reference → AI inconclusive"],
  ["expected_abstention_match", "참고 보류 → AI 보류", "Inconclusive reference → AI inconclusive"],
  ["expected_abstention_mismatch", "참고 보류 → AI 확정", "Inconclusive reference → AI decisive"],
  ["unlabeled", "답안 없음", "Unlabeled"], ["pending", "미완료", "Pending"], ["failed", "실행 실패", "Failed"],
  ["canceled", "중지", "Stopped"], ["stub", "모의 실행", "Stub"], ["unknown_provenance", "실행 출처 미확인", "Unverified provenance"],
  ["input_contaminated", "답안 포함 입력", "Contaminated input"]];
const text = (key, ko, en) => ({ key, ko, en, type: "text" });
const choice = (key, ko, en, options) => ({ key, ko, en, type: "enum", options });
const number = (key, ko, en, max, integer) => ({ key, ko, en, type: "number", min: 0, max, integer });
export const caseFilterFields = [
  choice("reference_verdict", "참고 판정", "Reference Verdict", verdicts), choice("verdict", "AI 판정", "AI Verdict", verdicts),
  choice("evaluation_outcome", "참고 판정 비교", "Reference comparison", outcomes),
  text("company_name", "회사", "Company"), text("signature", "시그니처", "Signature"),
  choice("waf_action", "WAF 조치", "WAF action", [["D", "차단", "Deny"], ["A", "허용", "Allow"]]),
  text("waf_vendor", "WAF 벤더", "WAF vendor"), text("event_name", "이벤트명", "Event name"), text("event_id", "이벤트 ID", "Event ID"),
  text("src_ip", "출발지 IP", "Source IP"), text("dest_ip", "목적지 IP", "Destination IP"),
  number("src_port", "출발지 포트", "Source port", 65535, true), number("dest_port", "목적지 포트", "Destination port", 65535, true),
  text("difficulty", "난이도", "Difficulty"), text("test_category", "테스트 유형", "Category"), text("case_name", "문항명", "Case name"),
  choice("status", "처리 상태", "Execution status", [["completed", "완료", "Completed"], ["failed", "실패", "Failed"], ["pending", "대기", "Pending"], ["processing", "처리 중", "Processing"], ["canceled", "중지", "Stopped"]]),
  choice("severity", "심각도", "Severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"].map(value => [value, value, value])),
  text("threat_category", "위협 유형", "Threat category"), number("confidence_score", "판정 신뢰도 (0~1)", "Verdict confidence (0–1)", 1, false),
];
export const filterOperators = { eq: ["같음", "Equals"], ne: ["다름", "Not equal"], contains: ["포함", "Contains"], not_contains: ["포함하지 않음", "Does not contain"],
  starts_with: ["시작", "Starts with"], ends_with: ["끝남", "Ends with"], gt: ["초과", "Greater than"], gte: ["이상", "At least"], lt: ["미만", "Less than"], lte: ["이하", "At most"],
  is_empty: ["값 없음", "Empty"], is_not_empty: ["값 있음", "Not empty"] };
export const fieldDefinition = key => caseFilterFields.find(field => field.key === key);
export function filterFieldName(field, w, official = false) {
  return official && field.key === "reference_verdict" ? w("기대 판정", "Expected Verdict")
    : official && field.key === "evaluation_outcome" ? w("기대 판정 비교", "Expected-verdict comparison") : w(field.ko, field.en);
}
export function filterOptionName(field, option, w, official = false) {
  return official && field.key === "evaluation_outcome" ? w(option[1].replaceAll("참고", "기대"), option[2].replaceAll("reference", "expected")) : w(option[1], option[2]);
}
export const operatorsFor = field => [...(field.type === "text" ? ["eq", "ne", "contains", "not_contains", "starts_with", "ends_with"]
  : field.type === "number" ? ["eq", "ne", "gt", "gte", "lt", "lte"] : ["eq", "ne"]), "is_empty", "is_not_empty"];
export const emptyOperator = operator => ["is_empty", "is_not_empty"].includes(operator);
export function newFilterRule(key = "company_name") {
  const field = fieldDefinition(key);
  return { field: key, operator: field.type === "text" ? "contains" : field.type === "number" ? "gte" : "eq", value: "" };
}
export const newCaseFilter = () => ({ op: "and", rules: [{ op: "and", rules: [newFilterRule()] }] });
export const filterRuleCount = expression => expression?.rules?.reduce((sum, group) => sum + group.rules.length, 0) || 0;
export function presetCaseFilter(preset) {
  if (preset === "all") return null;
  const values = preset === "different" ? ["false_positive", "false_negative", "abstained", "expected_abstention_mismatch"] : [preset];
  return { op: "and", rules: [{ op: "or", rules: values.map(value => ({ field: "evaluation_outcome", operator: "eq", value })) }] };
}
export function normalizeCaseFilter(draft) {
  const invalid = () => { throw Error("invalid_test_case_filter"); };
  if (!draft || !["and", "or"].includes(draft.op) || !draft.rules?.length || draft.rules.length > 5 || filterRuleCount(draft) > 20) invalid();
  const expression = { op: draft.op, rules: draft.rules.map(group => {
    if (!["and", "or"].includes(group.op) || !group.rules?.length) invalid();
    return { op: group.op, rules: group.rules.map(rule => {
      const field = fieldDefinition(rule.field);
      if (!field || !operatorsFor(field).includes(rule.operator)) invalid();
      const base = { field: rule.field, operator: rule.operator };
      if (emptyOperator(rule.operator)) return base;
      let value = rule.value;
      if (field.type === "number") {
        if ((typeof value !== "string" && typeof value !== "number") || String(value).trim() === "") invalid();
        value = Number(value);
        if (!Number.isFinite(value) || value < field.min || value > field.max || (field.integer && !Number.isInteger(value))) invalid();
      } else if (typeof value !== "string" || !value.length || value.length > 4096 ||
          (field.type === "enum" && !field.options.some(([option]) => option === value))) invalid();
      return { ...base, value };
    }) };
  }) };
  if (new TextEncoder().encode(JSON.stringify(expression)).length > 32768) invalid();
  return expression;
}
export function formatCaseFilter(expression, w, official = false) {
  if (!expression) return "";
  return expression.rules.map(group => `(${group.rules.map(rule => {
    const field = fieldDefinition(rule.field), value = field?.options?.find(([key]) => key === rule.value);
    return `${field ? filterFieldName(field, w, official) : rule.field} ${w(...filterOperators[rule.operator])}${emptyOperator(rule.operator) ? "" : ` ${value ? filterOptionName(field, value, w, official) : String(rule.value)}`}`;
  }).join(group.op === "and" ? " AND " : " OR ")})`).join(expression.op === "and" ? " AND " : " OR ");
}
