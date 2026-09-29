import { assessmentView, decisionIssues } from "./analystAssessment.js";
import { analystFollowUp, finalValue, groupedEvidence } from "./analystView.js";
import { isMockAnalysis } from "./labelEvaluation.js";

// Presentation only. Keep stored directions and numbering; never infer a side
// from the final verdict, rank evidence by strength, or manufacture a condition.
export function detailEvidence(result) {
  const assessment = assessmentView(result);
  if (assessment) return assessment.evidence;
  return groupedEvidence(result?.evidence)
    .filter(item => typeof item.field === "string" && typeof item.excerpt === "string")
    .map((item, index) => ({
    ...item, number: index + 1, supports: "unclassified", related_numbers: [],
    interpretation_ko: item.interpretations.filter(value => typeof value === "string").join("\n\n"),
  }));
}

export function decisionConditions(detail) {
  const followUp = analystFollowUp(detail);
  if (detail?.status !== "completed" || isMockAnalysis(detail)) return { ...followUp, items: [], visible: false };
  const held = finalValue(detail, "verdict") === "inconclusive";
  const semantic = detail.result?.signature_assessment?.version === "signature-assessment-v2";
  const issues = held ? (semantic ? (assessmentView(detail.result)?.issues || []).filter(issue => issue.missing_condition_ko) : decisionIssues(detail)) : [];
  const items = [];
  const optional = [], tuning = [], unclassified = [];
  for (const issue of issues) {
    const title = issue.missing_condition_ko || issue.point_ko;
    let item = items.find(row => row.title === title);
    if (!item) { item = { title, notes: [], evidence_numbers: [], checks: [] }; items.push(item); }
    if (issue.point_ko !== title && !item.notes.includes(issue.point_ko)) item.notes.push(issue.point_ko);
    item.evidence_numbers = [...new Set([...item.evidence_numbers, ...issue.evidence_numbers])];
  }
  for (const check of followUp.checks) {
    const target = check.purpose === "impact_followup" ? optional : check.purpose === "tuning_validation" ? tuning
      : semantic && check.purpose !== "decision_condition" ? unclassified : items;
    // Match only identical recorded text, not semantic guesses. Different
    // sources/reasons remain separate within the same condition.
    let item = target.find(row => row.title === check.check_ko || row.notes.includes(check.check_ko));
    if (!item) { item = { title: check.check_ko, notes: [], evidence_numbers: [], checks: [] }; target.push(item); }
    if (!item.checks.some(row => row.source_ko === check.source_ko && row.why_ko === check.why_ko)) item.checks.push(check);
  }
  const sections = [
    { key: "conditions", title: held ? ["판정 확정에 필요한 조건", "Conditions to reach a decision"] : ["후속 확인 · 선택사항", "Optional follow-up"], items,
      introduction: semantic && held && items.length ? "아래 조건을 원문·처리 규칙과 대조해 판정을 검토하세요." : followUp.introduction_ko,
      introductionEn: semantic && held && items.length ? "Review the conditions below against the source and handling rules." : undefined,
      empty: followUp.empty_ko },
    { key: "impact", title: ["후속 확인 · 선택사항", "Optional follow-up"], items: optional,
      introduction: "판정의 필수 조건이 아닙니다. 필요한 경우 영향 범위와 후속 대응을 확인하세요.", introductionEn: "Not required for the verdict. Review impact and response where useful." },
    { key: "tuning", title: ["튜닝 검증 · 선택사항", "Tuning validation · optional"], items: tuning,
      introduction: "정책 검토 제안과 함께 확인하세요. WAF 설정은 자동 변경하지 않습니다.", introductionEn: "Review alongside the policy suggestion. WAF settings are never changed automatically." },
    { key: "unclassified", title: ["기록된 확인 사항", "Recorded checks"], items: unclassified },
  ].filter(section => section.items.length || section.key === "conditions" && held);
  return { ...followUp, items, sections, visible: sections.length > 0, held };
}

// Preserve old deep links, including the former standalone JSON tab.
export const decisionTab = value => value === "json" ? "agent" : ["result", "raw", "agent", "report"].includes(value) ? value : "result";
