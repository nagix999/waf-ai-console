import { decisionExplanation } from "./decisionExplanation.js";
import { holdReview } from "./holdReview.js";
import { isMockAnalysis } from "./labelEvaluation.js";
export { assessmentView, evidenceLabels, evidenceNotice, legacyEvidenceNotice } from "./assessmentData.js";

export function decisionIssues(detail) {
  return isMockAnalysis(detail) ? [] : holdReview(detail, decisionExplanation(detail)).issues;
}
