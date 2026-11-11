import { analystText } from "./analystText.js";
import { presentationItems } from "./evidencePresentation.js";
import { evidenceDisplayView } from "./evidenceDisplayPlan.js";
import { isCoreEvidence, receivedEvidence } from "./analysisEvidenceMeta.js";

export const evidenceLabels = { attack_request: "공격 요청 근거", normal_request: "정상 요청 근거", context: "참고 내용", unclassified: "구분 미기록" };
export const evidenceNotice = "로그에서 확인한 발췌와 그에 대한 해석입니다. 근거 개수로 판정을 결정하지 않습니다.";
export const legacyEvidenceNotice = "기존 분석에는 공격·정상 요청 근거 구분이 기록되지 않았습니다.";
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const list = value => Array.isArray(value) ? value : [];
export const isResultV3 = result => result?.schema_version === "waf-analysis-v3";

// Allowlist only the saved presentation record. Never classify older evidence
// from a verdict, or read intermediate role outputs to manufacture a cause.
export function assessmentView(result, { preserveEvidence = false, display = null, relations = display?.relations ?? null, evidenceView = null } = {}) {
  const modern = isResultV3(result);
  const assessment = modern ? result : result?.analyst_assessment;
  if (!record(assessment) || (!modern && assessment.version !== "analyst-assessment-v1")) return null;
  if (isCoreEvidence(result, evidenceView)) {
    const evidence = receivedEvidence(result), numbers = new Map(evidence.map(item => [item.evidence_id, item.number]));
    const issues = list(result.decision_issues).map(item => ({
      point_ko: item.point_ko, missing_condition_ko: item.missing_condition_ko ?? null,
      evidence_numbers: list(item.evidence_ids).map(id => numbers.get(id)).filter(number => number !== undefined),
    }));
    return { evidence, issues, core: true };
  }
  const evidence = [], seen = new Map(), references = new Map();
  const displayView = modern ? evidenceDisplayView(result, display, relations) : null;
  const selected = modern && displayView ? (preserveEvidence ? displayView.items : displayView.groups)
    : preserveEvidence && modern ? list(assessment.evidence) : presentationItems(assessment, result.evidence_presentation);
  for (const item of selected) {
    if (!record(item) || !["field", "excerpt", "interpretation_ko"].every(key => typeof item[key] === "string" && item[key].trim())) continue;
    // Historical directions were recorded by the model. Translate only those
    // explicit values; never infer a direction from prose or the final verdict.
    const direction = modern ? item.supports : ({ true_positive: "attack_request", false_positive: "normal_request", context: "context" })[item.supports];
    const supports = typeof direction === "string" && Object.hasOwn(evidenceLabels, direction) ? direction : "unclassified";
    // A validated plan already owns grouping. Identical prose may intentionally
    // remain separate because its decision conditions or dependencies differ.
    const key = JSON.stringify([item.field, item.excerpt, item.interpretation_ko, supports,
      ...(modern && (displayView || preserveEvidence) ? [item.group_id || item.evidence_id] : [])]);
    if (!seen.has(key)) {
      seen.set(key, { number: modern && displayView ? item.number : evidence.length + 1, field: item.field, excerpt: item.excerpt, interpretation_ko: analystText(item.interpretation_ko), supports });
      if (modern && displayView) Object.assign(seen.get(key), { ...item, supports });
      else if (preserveEvidence && modern) Object.assign(seen.get(key), { evidence_id: item.evidence_id, origins: item.origins });
      evidence.push(seen.get(key));
    }
    for (const id of item._evidence_ids || [item.evidence_id]) {
      if (typeof id === "string" && id) references.set(id, references.has(id) ? null : seen.get(key).number);
    }
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
  return { evidence, issues, ...(displayView ? { display: displayView } : {}) };
}
