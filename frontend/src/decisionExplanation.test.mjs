import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { decisionExplanation, genericCheck, genericHoldSummary, genericTuningRisk, threatCategoryLabel } from "./decisionExplanation.js";
import { analystFollowUp, analystGuidance, analystSummary, hasTuningContent, isTechnicalText } from "./analystView.js";
import { buildAnalysisReport } from "./analysisReport.js";

const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/decision_explanation_cases.json", import.meta.url), "utf8"));
for (const item of cases) test(item.name, () => {
  const before = JSON.stringify(item.detail);
  const value = decisionExplanation(item.detail);
  assert.equal(value?.code ?? null, item.expected_code);
  assert.equal(JSON.stringify(item.detail), before);
  if (value) {
    for (const key of ["title_ko", "reason_ko", "action_ko"]) assert.equal(isTechnicalText(value[key]), false);
    assert.ok(!JSON.stringify(value).includes("synthetic-do-not-display"));
  }
});

test("default checklist disappears; explicit case-specific checks and saved data survive", () => {
  const detail = structuredClone(cases[1].detail);
  detail.result.analyst_guidance = { summary_ko: genericHoldSummary, checks: [genericCheck] };
  const before = JSON.stringify(detail);
  assert.deepEqual(analystGuidance(detail).checks, []);
  assert.equal(analystSummary(detail), decisionExplanation(detail).reason_ko);
  assert.match(analystFollowUp(detail).introduction_ko, /추가 자료가 반드시 필요한지는/);
  assert.equal(JSON.stringify(detail), before);
  detail.result.analyst_guidance.checks.push({ ...genericCheck, check_ko: "요청 본문이 전부 수집됐는지 확인하세요." });
  assert.equal(analystGuidance(detail).checks.length, 1);
  assert.ok(!buildAnalysisReport(detail).includes(genericCheck.check_ko));
});

test("recorded hold reasons remain literal and are not promoted from intermediate prose", () => {
  const detail = structuredClone(cases[0].detail);
  detail.result.summary_ko = "요청 본문이 수집되지 않아 전송 내용을 판단하지 못했습니다.";
  Object.defineProperty(detail.result, "primary", { get() { throw Error("Do not read role output"); } });
  detail.result.verifier = {};
  Object.defineProperty(detail.result.verifier, "output", { get() { throw Error("Do not read role output"); } });
  assert.equal(analystSummary(detail), detail.result.summary_ko);
  assert.doesNotThrow(() => analystGuidance(detail));
  assert.doesNotThrow(() => buildAnalysisReport(detail));
});

test("one rejected quotation and partial parsing do not become the final cause", () => {
  const detail = structuredClone(cases[4].detail);
  detail.result.diagnostics.roles = { primary: { grounding_rejected_count: 1, grounding_downgraded: false } };
  assert.equal(decisionExplanation(detail).code, "reason_unrecorded");
  assert.ok(!analystSummary(detail).includes("원문 대조"));
});

test("saved semantic conflict remains a hold, not an asserted normal request or missing business rule", () => {
  const detail = structuredClone(cases[1].detail);
  detail.result.summary_ko = "정상 요청으로 판단합니다.";
  detail.result.threat_analysis = { severity: "UNKNOWN", category: "XSS", technique_ko: "저장된 해석", potential_impact_ko: "저장된 설명" };
  const before = JSON.stringify(detail);
  const report = buildAnalysisReport(detail);
  assert.ok(!analystSummary(detail).includes("정상 요청으로"));
  assert.ok(!report.includes("업무 규격"));
  assert.ok(report.includes("검토한 유형"));
  assert.ok(report.includes("확정된 결론으로 사용하지 마세요"));
  assert.equal(JSON.stringify(detail), before);
});

test("type names do not imply attack for false positives and only exact boilerplate tuning is hidden", () => {
  assert.equal(threatCategoryLabel("false_positive"), "탐지 유형");
  assert.equal(threatCategoryLabel("true_positive"), "공격 유형");
  assert.equal(threatCategoryLabel("inconclusive"), "검토한 유형");
  assert.equal(hasTuningContent({ recommended: false, risk_ko: genericTuningRisk }), false);
  assert.equal(hasTuningContent({ recommended: false, risk_ko: genericTuningRisk, validation_ko: "회귀 검증이 필요합니다." }), true);
});
