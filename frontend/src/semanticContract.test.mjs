import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { decisionConditions } from "./decisionDetail.js";
import { analystGuidance } from "./analystView.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { presentFollowUpChecks } from "./followUpPresentation.js";

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./AnalysisDecision.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} };
runInNewContext(bundled.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process });
const check = (purpose, check_ko) => ({ purpose, source_ko: "애플리케이션 입력 규격", check_ko, why_ko: "기록된 요청의 처리 경로를 검토합니다." });
function detail(verdict = "inconclusive") {
  return { status: "completed", waf_action: "D", result: { verdict, summary_ko: "요청을 검토했습니다.",
    threat_analysis: { severity: verdict === "true_positive" ? "HIGH" : verdict === "false_positive" ? "NONE" : "UNKNOWN" },
    signature_assessment: { version: "signature-assessment-v2", relation: "mismatch", matched_points: [], mismatched_points: ["SQL 탐지 설명과 달리 HTML 실행 구문입니다."], uncertainty_ko: null, explanation_ko: null },
    analyst_guidance: { checks: [ ...(verdict === "inconclusive" ? [check("decision_condition", "이 값이 실행되는지 데이터로 저장되는지 확인합니다.")] : []),
      check("impact_followup", "같은 대상의 후속 피해를 확인합니다."), check("tuning_validation", "정책 검토 제안의 공격 회귀 사례를 확인합니다.") ] },
    recommended_checks: ["LEGACY_NO_NEW_FALLBACK"],
  } };
}

for (const verdict of ["true_positive", "false_positive", "inconclusive"]) test(`${verdict}: purpose sections agree in UI and report without changing records`, () => {
  const value = detail(verdict), before = JSON.stringify(value), conditions = decisionConditions(value);
  assert.deepEqual(conditions.sections.map(section => section.key), verdict === "inconclusive" ? ["conditions", "impact", "tuning"] : ["impact", "tuning"]);
  const html = renderToStaticMarkup(createElement(module.exports.DecisionConditions, { detail: value }));
  const report = buildAnalysisReport(value);
  for (const text of ["영향·대응 확인", "튜닝 전 검증"]) { assert.ok(html.includes(text)); assert.ok(report.includes(text)); }
  if (verdict === "inconclusive") {
    assert.ok(report.indexOf("## 판정에 필요한 확인") < report.indexOf("## 영향·대응 확인"));
    assert.ok(!conditions.sections[0].items.some(item => /피해|회귀/.test(item.title)));
  } else { assert.doesNotMatch(html, /판정에 필요한 확인/); assert.doesNotMatch(report, /판정에 필요한 확인/); }
  assert.doesNotMatch(html+report, /LEGACY_NO_NEW_FALLBACK/);
  assert.equal(JSON.stringify(value), before);
});

test("structured signature shows observed comparison details, never a new free summary", () => {
  const value = detail("true_positive");
  const html = renderToStaticMarkup(createElement(module.exports.TechnicalInterpretation, { detail: value }));
  for (const text of ["다른 부분", "SQL 탐지 설명과 달리 HTML 실행 구문입니다.", "탐지 설명과 요청의 핵심 동작이 다릅니다."]) {
    assert.ok(html.includes(text)); assert.ok(decodeReportText(buildAnalysisReport(value)).includes(text));
  }
  assert.doesNotMatch(html, /기존 분석 설명/);
});

test("v2.14 final empty checks stay hidden in UI and report despite preserved role checks", () => {
  const value = detail("true_positive");
  value.result.policy = { fixed_rules_version: "waf-system-v2.14" };
  value.result.primary = { analyst_checks: structuredClone(value.result.analyst_guidance.checks) };
  value.result.verifier = { output: structuredClone(value.result.primary) };
  value.result.analyst_guidance.checks = [];
  value.result.analyst_checks = [];
  const before = JSON.stringify(value);
  assert.equal(renderToStaticMarkup(createElement(module.exports.DecisionConditions, { detail: value })), "");
  assert.doesNotMatch(buildAnalysisReport(value), /## 영향·대응 확인|## 튜닝 전 검증|같은 대상의 후속 피해/);
  assert.equal(JSON.stringify(value), before);
});

test("historical v2.13 denied attacks retain recorded checks; hold impact never claims a final verdict", () => {
  const value = detail("true_positive");
  value.result.policy = { fixed_rules_version: "waf-system-v2.13" };
  assert.deepEqual(decisionConditions(value).sections.map(s => s.key), ["impact", "tuning"]);
  assert.match(decisionConditions(value).sections[0].introduction, /판정은 이미 확정되었습니다/);
  const held = decisionConditions(detail());
  assert.doesNotMatch(held.sections.find(s => s.key === "impact").introduction, /판정은 이미 확정되었습니다/);
});

test("new empty checks neither use legacy suggestions nor invent a missing condition", () => {
  const value = detail();
  value.result.analyst_guidance.checks = [];
  assert.deepEqual(analystGuidance(value).checks, []);
  assert.deepEqual(decisionConditions(value).sections, []);
  assert.equal(decisionConditions(value).visible, false);
  delete value.result.analyst_guidance;
  assert.deepEqual(analystGuidance(value).checks, []);
});

test("new editor mapping preserves purpose and cannot cross purposes or change source values", () => {
  const checks = [check("decision_condition", "원문 처리 방식 확인"), check("impact_followup", "후속 영향 확인"), check("decision_condition", "입력 처리 방식 확인")];
  const saved = { version: "follow-up-editor-v2", status: "completed", items: checks.map((row, i) => ({ check_id: `c${i+1}`, ...row })), groups: [
    { member_ids: ["c1", "c3"], representative_id: "c3" }, { member_ids: ["c2"], representative_id: "c2" },
  ] };
  const before = JSON.stringify(checks);
  assert.deepEqual(presentFollowUpChecks(checks, saved), [checks[2], checks[1]]);
  assert.deepEqual(presentFollowUpChecks(checks, { ...saved, version: "follow-up-editor-v1" }), checks);
  assert.deepEqual(presentFollowUpChecks(checks, { ...saved, groups: [{ member_ids: ["c1", "c2", "c3"], representative_id: "c1" }] }), checks);
  const stale = structuredClone(saved); stale.items[0].purpose = "impact_followup";
  assert.deepEqual(presentFollowUpChecks(checks, stale), checks);
  assert.equal(JSON.stringify(checks), before);
});
