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
  const issues = held ? decisionIssues(detail) : (assessmentView(detail.result)?.issues || []);
  const items = [];
  for (const issue of issues) {
    const title = issue.missing_condition_ko || issue.point_ko;
    let item = items.find(row => row.title === title);
    if (!item) { item = { title, notes: [], evidence_numbers: [], checks: [] }; items.push(item); }
    if (issue.point_ko !== title && !item.notes.includes(issue.point_ko)) item.notes.push(issue.point_ko);
    item.evidence_numbers = [...new Set([...item.evidence_numbers, ...issue.evidence_numbers])];
  }
  for (const check of followUp.checks) {
    // Match only identical recorded text, not semantic guesses. Different
    // sources/reasons remain separate within the same condition.
    let item = items.find(row => row.title === check.check_ko || row.notes.includes(check.check_ko));
    if (!item) { item = { title: check.check_ko, notes: [], evidence_numbers: [], checks: [] }; items.push(item); }
    if (!item.checks.some(row => row.source_ko === check.source_ko && row.why_ko === check.why_ko)) item.checks.push(check);
  }
  return { ...followUp, items, visible: followUp.visible || items.length > 0, held };
}

// Preserve old deep links, including the former standalone JSON tab.
export const decisionTab = value => value === "json" ? "agent" : ["result", "raw", "agent", "report"].includes(value) ? value : "result";
