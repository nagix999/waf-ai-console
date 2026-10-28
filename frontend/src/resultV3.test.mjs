import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { assessmentView } from "./assessmentData.js";
import { analystGuidance, analystSummary } from "./analystView.js";
import { detailEvidence, decisionConditions } from "./decisionDetail.js";
import { evidenceSections } from "./decisionSemantics.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("./AnalysisDecision.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const module = { exports: {} };
runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date, URL });
const { DecisionHero, EvidenceCards, DecisionConditions, FinalDecisionReason, PolicySuggestion } = module.exports;
const render = (component, detail) => renderToStaticMarkup(createElement(component, { detail }));
function fixture() {
  return { id: "private-v3", status: "completed", analysis_purpose: "test", waf_action: "D", result: {
    schema_version: "waf-analysis-v3", verdict: "inconclusive", confidence_score: .7,
    summary_ko: "문서 저장 요청의 문자열에 셸 명령 치환 표기가 포함되어 있습니다.", input_truncated: false,
    threat_analysis: { severity: "UNKNOWN", category: "command_injection", target: "payload.body", technique_ko: "실행 문맥에 따라 다르게 해석됩니다.", potential_impact_ko: "실행 여부는 확인되지 않았습니다.", obfuscations: [] },
    signature_assessment: { version: "signature-assessment-v2", relation: "partial", matched_points: ["명령 치환 표기가 있습니다."], mismatched_points: ["문서 저장 요청입니다."], uncertainty_ko: "서버의 처리 경로는 확인되지 않았습니다." },
    evidence: [
      { evidence_id: "e1", field: "payload.body", excerpt: "$(id)", interpretation_ko: "셸에서 처리하면 명령 치환으로 실행될 수 있습니다.", supports: "attack_request", origins: ["primary"], source_spans: [{ start: 10, end: 15 }] },
      { evidence_id: "e2", field: "payload.body", excerpt: "$(id)", interpretation_ko: "문서 내용으로 저장하는 문자열일 수 있습니다.", supports: "normal_request", origins: ["verifier"], source_spans: [{ start: 10, end: 15 }] },
      { evidence_id: "e3", field: "waf_action", excerpt: "D", interpretation_ko: "WAF에 차단으로 기록되었습니다.", supports: "context", origins: ["primary"], source_spans: [] },
    ],
    decision_issues: [{ point_ko: "해당 값의 처리 경로를 구분하지 못했습니다.", evidence_ids: ["e1", "e2"], missing_condition_ko: "문자열이 셸 실행에 전달되는지 확인하세요.", origins: ["primary"] }],
    analyst_guidance: { summary_ko: "STALE_GUIDANCE_SUMMARY", limitations: ["처리 경로는 입력에 없습니다."] },
    diagnostics: { inconclusive_reasons: ["primary_model_inconclusive"] }, agent: { framework: "moduagent" },
  } };
}

test("V3 reads root classified evidence and linked conditions without extension fields or mutation", () => {
  const detail = fixture(), before = JSON.stringify(detail);
  for (const key of ["analyst_assessment", "analyst_checks", "recommended_checks", "conflicting_evidence", "follow_up_presentation"]) {
    Object.defineProperty(detail.result, key, { get() { throw Error("V3 must not use legacy " + key); } });
  }
  const view = assessmentView(detail.result);
  assert.deepEqual(view.evidence.map(item => item.supports), ["attack_request", "normal_request", "context"]);
  assert.deepEqual(view.evidence[0].related_numbers, [2]);
  assert.deepEqual(view.issues[0].evidence_numbers, [1, 2]);
  assert.equal(decisionConditions(detail).items.length, 1);
  assert.equal(analystGuidance(detail).checks.length, 0);
  assert.equal(JSON.stringify(detail), before);
});

for (const verdict of ["true_positive", "false_positive", "inconclusive"]) test(`V3 ${verdict}: fixed headings do not derive direction or order from the final verdict`, () => {
  const detail = fixture(); detail.result.verdict = verdict;
  const html = render(EvidenceCards, detail), groups = evidenceSections(verdict);
  assert.deepEqual(groups.slice(0, 2).map(item => item.side), ["attack_request", "normal_request"]);
  assert.match(html, /공격 요청 근거/); assert.match(html, /정상 요청 근거/);
  assert.doesNotMatch(html, /반대 근거|정탐 근거|오탐 근거|공격 해석|정상 해석/);
  assert.ok(html.indexOf("evidence-attack_request") < html.indexOf("evidence-normal_request"));
});

for (const reason of ["primary_model_inconclusive", "verdict_disagreement", "verifier_failed", "primary_evidence_rejected"]) test(`V3 ${reason}: summary remains observations, hold reason is separate`, () => {
  const detail = fixture(); detail.result.diagnostics.inconclusive_reasons = [reason];
  assert.equal(analystSummary(detail), detail.result.summary_ko);
  const hero = render(DecisionHero, detail), reasonHtml = render(FinalDecisionReason, detail);
  assert.ok(hero.includes(detail.result.summary_ko));
  assert.doesNotMatch(hero, /STALE_GUIDANCE_SUMMARY|現在|현재 분석에서는/);
  assert.match(reasonHtml, /최종 판정 이유/);
  assert.ok(!reasonHtml.includes(detail.result.summary_ko));
});

test("V3 does not invent generic checks or response-code follow-ups for empty held or decisive conditions", () => {
  const detail = fixture(); detail.result.decision_issues = [];
  for (const verdict of ["true_positive", "false_positive", "inconclusive"]) {
    detail.result.verdict = verdict;
    assert.equal(decisionConditions(detail).visible, false);
    assert.equal(render(DecisionConditions, detail), "");
  }
});

test("V3 explicit execution/integrity failures produce technical checks without claiming missing business data", () => {
  const detail = fixture();
  detail.result.diagnostics.inconclusive_reasons = ["verifier_failed"];
  const conditions = decisionConditions(detail);
  assert.equal(conditions.items.length, 1);
  assert.match(conditions.items[0].title, /실행 오류/);
  assert.doesNotMatch(conditions.items[0].title, /응답 상태|본문을 제공|처리 규격/);
  assert.ok(!conditions.items.some(item => item.title === detail.result.decision_issues[0].missing_condition_ko));
  detail.result.diagnostics = { inconclusive_reasons: ["input_integrity_limited"], request_integrity: { downgraded_to_inconclusive: true, affected_issue_codes: ["declared_body_not_captured"] } };
  assert.match(render(FinalDecisionReason, detail), /수집된 로그에는 본문이 없습니다/);
});

test("V3 preserves source-linked editor groups and prevents cross-direction grouping", () => {
  const detail = fixture(), first = structuredClone(detail.result.evidence[0]);
  first.evidence_id = "e4"; first.interpretation_ko = "명령 치환 구문은 실행 경로가 있을 때 동작합니다.";
  detail.result.evidence.push(first);
  detail.result.evidence_presentation = { version: "evidence-editor-v1", status: "completed", groups: [
    { member_ids: ["e1", "e4"], representative_id: "e4" }, { member_ids: ["e2"], representative_id: "e2" }, { member_ids: ["e3"], representative_id: "e3" },
  ] };
  assert.equal(detailEvidence(detail.result).length, 3);
  assert.equal(detailEvidence(detail.result)[0].interpretation_ko, first.interpretation_ko);
  assert.deepEqual(assessmentView(detail.result).issues[0].evidence_numbers, [1, 2]);
  detail.result.evidence_presentation.groups = [{ member_ids: ["e1", "e2", "e4"], representative_id: "e1" }, { member_ids: ["e3"], representative_id: "e3" }];
  assert.equal(detailEvidence(detail.result).length, 4);
});

test("V3 ignores unknown legacy direction values rather than guessing a request classification", () => {
  const detail = fixture(); detail.result.evidence[0].supports = "true_positive";
  assert.equal(detailEvidence(detail.result)[0].supports, "unclassified");
  detail.result.decision_issues[0].evidence_ids = ["missing"];
  assert.deepEqual(assessmentView(detail.result).issues, []);
});

test("V3 web/report share observations, fixed evidence labels and conditions without tuning or duplicate check structures", () => {
  const detail = fixture();
  Object.defineProperty(detail.result, "tuning_recommendation", { get() { throw Error("V3 has no tuning"); } });
  // The report has a fixed allowlist and must not read deprecated output fields.
  const html = render(EvidenceCards, detail), report = decodeReportText(buildAnalysisReport(detail));
  assert.equal(render(PolicySuggestion, detail), "");
  for (const title of ["공격 요청 근거", "정상 요청 근거", "참고 내용"]) { assert.ok(html.includes(title)); assert.ok(report.includes(title)); }
  assert.ok(report.includes(detail.result.summary_ko));
  assert.ok(report.indexOf("## 보안 분석 근거") < report.indexOf("## 세부 분석"));
  assert.ok(report.indexOf("## 보안 분석 근거") < report.indexOf("## 최종 판정 이유"));
  assert.ok(report.indexOf("## 최종 판정 이유") < report.indexOf("## 판정에 필요한 확인"));
  assert.equal(report.split(detail.result.decision_issues[0].missing_condition_ko).length - 1, 1);
  assert.doesNotMatch(report, /WAF 정책 검토|200 응답|STALE_GUIDANCE_SUMMARY/);
});
