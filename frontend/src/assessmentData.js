import { analystText } from "./analystText.js";

export const evidenceLabels = { true_positive: "정탐 근거", false_positive: "오탐 근거", context: "참고 내용", unclassified: "구분 미기록" };
export const evidenceNotice = "로그에서 확인한 발췌와 그에 대한 해석입니다. 근거 개수로 판정을 결정하지 않습니다.";
export const legacyEvidenceNotice = "기존 분석에는 정탐·오탐 근거 구분이 기록되지 않았습니다.";
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const list = value => Array.isArray(value) ? value : [];

// Allowlist only the saved presentation record. Never classify older evidence
// from a verdict, or read intermediate role outputs to manufacture a cause.
export function assessmentView(result) {
  const assessment = result?.analyst_assessment;
  if (!record(assessment) || assessment.version !== "analyst-assessment-v1") return null;
  const evidence = [], seen = new Map(), references = new Map();
  for (const item of list(assessment.evidence)) {
    if (!record(item) || !["field", "excerpt", "interpretation_ko"].every(key => typeof item[key] === "string" && item[key].trim())) continue;
    const supports = typeof item.supports === "string" && Object.hasOwn(evidenceLabels, item.supports) ? item.supports : "unclassified";
    const key = JSON.stringify([item.field, item.excerpt, item.interpretation_ko, supports]);
    if (!seen.has(key)) {
      seen.set(key, { number: evidence.length + 1, field: item.field, excerpt: item.excerpt, interpretation_ko: analystText(item.interpretation_ko), supports });
      evidence.push(seen.get(key));
    }
    if (typeof item.evidence_id === "string" && item.evidence_id) references.set(item.evidence_id, references.has(item.evidence_id) ? null : seen.get(key).number);
  }
  for (const item of evidence) item.related_numbers = evidence.filter(other => other.supports !== item.supports && other.field === item.field && other.excerpt === item.excerpt).map(other => other.number);
  const issues = [];
  for (const item of list(assessment.decision_issues)) {
    if (!record(item) || !analystText(item.point_ko, "")) continue;
    const ids = item.evidence_ids;
    if (!Array.isArray(ids) || !ids.length || ids.some(key => typeof key !== "string" || !references.get(key))) continue;
    const issue = { point_ko: item.point_ko, evidence_numbers: [...new Set(ids.map(key => references.get(key)))], missing_condition_ko: analystText(item.missing_condition_ko, "") || null };
    if (!issues.some(previous => JSON.stringify(previous) === JSON.stringify(issue))) issues.push(issue);
  }
  return { evidence, issues };
}
