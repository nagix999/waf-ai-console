import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { holdReview } from "./holdReview.js";
import { decisionExplanation, inputLimitExplanation, genericCheck } from "./decisionExplanation.js";
import { analystGuidance, analystSummary } from "./analystView.js";
import { decisionIssues } from "./analystAssessment.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";

const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/hold_review_cases.json", import.meta.url), "utf8"));
for (const item of cases) test(`shared follow-up: ${item.name}`, () => {
  const detail = structuredClone(item.detail), before = JSON.stringify(detail);
  const value = holdReview(detail, decisionExplanation(detail));
  assert.deepEqual(value.issues.map(issue => issue.evidence_numbers), item.expected.issue_numbers);
  assert.deepEqual(value.issues.map(issue => issue.missing_condition_ko), item.expected.conditions);
  assert.deepEqual(value.checks.map(check => check.source_ko), item.expected.check_sources);
  for (const fragment of item.expected.contains) assert.ok(JSON.stringify(value).includes(fragment));
  assert.equal(JSON.stringify(detail), before);
});

test("UI and Markdown fill empty hold checks from linked conditions, never read roles", () => {
  for (const name of ["linked_missing_condition", "missing_point_anchors_saved_body", "hostile_condition_is_literal_not_executed"]) {
    const detail = structuredClone(cases.find(item => item.name === name).detail);
    Object.defineProperty(detail.result, "primary", { get() { throw Error("No role read"); } });
    const review = holdReview(detail, decisionExplanation(detail));
    assert.deepEqual(analystGuidance(detail).checks, review.checks);
    assert.deepEqual(decisionIssues(detail), review.issues);
    const report = decodeReportText(buildAnalysisReport(detail));
    for (const check of review.checks) for (const value of Object.values(check)) assert.ok(report.includes(value), value);
    assert.ok(report.indexOf("## 추가 확인 사항") > report.indexOf("## 판정 근거"));
  }
});

test("specific checks win; only exact duplicates removed; definite and stub checks stay empty", () => {
  const detail = structuredClone(cases[0].detail);
  const check = { source_ko: "서비스 담당자", check_ko: "JSON 입력의 허용 형식을 확인하세요.", why_ko: "같은 요청의 본문 형식과 비교하세요." };
  detail.result.analyst_guidance.checks = [check, check, { ...check, why_ko: "다른 요청 위치는 따로 확인하세요." }];
  assert.deepEqual(analystGuidance(detail).checks, [check, { ...check, why_ko: "다른 요청 위치는 따로 확인하세요." }]);
  detail.result.analyst_guidance.checks = [genericCheck];
  assert.equal(analystGuidance(detail).checks[0].check_ko, "해당 필드의 허용 형식");
  detail.result.analyst_guidance.checks = [];
  for (const verdict of ["true_positive", "false_positive"]) {
    detail.result.verdict = verdict;
    assert.deepEqual(analystGuidance(detail).checks, []);
  }
  detail.result.verdict = "inconclusive";
  detail.result.agent.framework = "stub";
  assert.deepEqual(analystGuidance(detail).checks, []);
  assert.deepEqual(decisionIssues(detail), []);
});

for (const [code, title] of [["declared_body_not_captured", "요청 본문 확인 필요"], ["body_shorter_than_content_length", "본문 길이 확인 필요"], ["json_container_unclosed", "JSON 본문 확인 필요"]]) test(`specific input reason: ${code}`, () => {
  const detail = structuredClone(cases[0].detail);
  detail.result.diagnostics = { inconclusive_reasons: ["input_integrity_limited"], request_integrity: { downgraded_to_inconclusive: true, affected_issue_codes: [code] } };
  assert.deepEqual(decisionExplanation(detail), inputLimitExplanation([code]));
  assert.equal(decisionExplanation(detail).title_ko, title);
  assert.equal(analystSummary(detail), decisionExplanation(detail).reason_ko);
  detail.result.diagnostics.request_integrity.downgraded_to_inconclusive = false;
  assert.equal(decisionExplanation(detail).code, "reason_unrecorded");
});
