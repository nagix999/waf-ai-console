import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { assessmentView, decisionIssues, evidenceLabels } from "./analystAssessment.js";
import { analystText, analystGuidance } from "./analystView.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";

const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/analyst_assessment_cases.json", import.meta.url), "utf8"));
for (const item of cases) test(`shared analyst presentation: ${item.name}`, () => {
  const before = JSON.stringify(item.result);
  assert.deepEqual(assessmentView(item.result), item.expected);
  assert.equal(JSON.stringify(item.result), before);
});

const detail = () => ({ status: "completed", result: { ...structuredClone(cases[0].result), verdict: "inconclusive", schema_version: "waf-analysis-v2",
  summary_ko: "현재 분석에서는 정탐·오탐 판정을 보류했습니다.", diagnostics: { inconclusive_reasons: ["verdict_disagreement"] },
  analyst_guidance: { checks: [], limitations: [] }, agent: { framework: "moduagent" },
} });

test("hold context is anchored to evidence, never shown as a service failure cause", () => {
  const input = detail();
  assert.equal(decisionIssues(input).length, 1);
  for (const reason of ["verifier_failed", "primary_evidence_rejected"]) {
    input.result.diagnostics.inconclusive_reasons = [reason];
    assert.deepEqual(decisionIssues(input), []);
  }
  input.result.verdict = "true_positive";
  assert.deepEqual(decisionIssues(input), []);
  for (const status of ["pending", "processing", "failed"]) assert.deepEqual(decisionIssues({ ...detail(), status }), []);
});

test("markdown shows both evidence groups, linked hold issue, context and exact field words", () => {
  const input = detail();
  Object.defineProperty(input.result, "primary", { get() { throw Error("No role read"); } });
  const report = decodeReportText(buildAnalysisReport(input));
  for (const label of [evidenceLabels.true_positive, evidenceLabels.false_positive, evidenceLabels.context, "판단이 필요한 부분", "관련 근거", "아직 확인되지 않은 조건", "로그 발췌", "판단 이유", "같은 로그 발췌: 근거 2"]) assert.ok(report.includes(label), label);
  for (const item of cases[0].expected.evidence) assert.ok(report.includes(item.interpretation_ko));
  assert.ok(!report.includes("연결할 수 없는 쟁점"));
  assert.ok(report.indexOf("판단이 필요한 부분") < report.indexOf("## 세부 분석"));
  assert.ok(report.lastIndexOf("## 추가 확인 사항") > report.indexOf("## 판정 근거"));
});

test("parameter words and real explanations containing old diagnostic phrases are retained", () => {
  for (const value of ["code_verifier 파라미터는 인증 요청의 검증 값입니다.", "primary_email 필드는 연락처 데이터입니다.", "검증 실패 값은 오류 코드 데이터이며 실행 명령이 아닙니다.", "분석 결과가 서로 다릅니다. q의 실제 값과 SQL 문법을 대조해야 합니다."]) {
    assert.equal(analystText(value), value);
    const input = detail();
    input.result.analyst_guidance.checks = [{ source_ko: value, check_ko: value, why_ko: value }];
    assert.equal(analystGuidance(input).checks[0].check_ko, value);
  }
});
