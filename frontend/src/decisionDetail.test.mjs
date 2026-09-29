import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { decisionTab, detailEvidence, decisionConditions } from "./decisionDetail.js";
import { buildAnalysisReport, decodeReportText } from "./analysisReport.js";
import { evidenceSections, signatureSummary, verifierDisagreed } from "./decisionSemantics.js";

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("./AnalysisDecision.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const module = { exports: {} };
runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });
const { DecisionHero, EvidenceCards, DecisionConditions, TechnicalInterpretation, DecisionContext, ExecutionView } = module.exports;
const render = (component, props) => renderToStaticMarkup(createElement(component, props));
const fixture = () => ({ id: "private-id", event_id: "private-event-id", status: "completed", analysis_purpose: "production", model_profile: "saved-model", prompt_version: "saved-prompt", result: {
  verdict: "inconclusive", summary_ko: "구분 조건을 확인해야 합니다.", input_truncated: false,
  agent: { framework: "moduagent" }, diagnostics: { inconclusive_reasons: ["primary_model_inconclusive"] },
  threat_analysis: { severity: "UNKNOWN", category: "Command Injection", target: "payload.body", technique_ko: "기술 해석 문구", potential_impact_ko: "영향 미확인" },
  analyst_assessment: { version: "analyst-assessment-v1", evidence: [
    { evidence_id: "e1", field: "payload.body", excerpt: "$(id)", supports: "true_positive", interpretation_ko: "명령 치환으로 해석될 수 있습니다." },
    { evidence_id: "e2", field: "payload.body", excerpt: "$(id)", supports: "false_positive", interpretation_ko: "문자열 저장일 수도 있습니다." },
    { evidence_id: "e3", field: "signature", excerpt: "example", supports: "context", interpretation_ko: "참고 정보입니다." },
  ], decision_issues: [{ point_ko: "값을 사용하는 위치를 확인합니다.", missing_condition_ko: "셸 실행 여부", evidence_ids: ["e1", "e2"] }] },
  analyst_guidance: { checks: [{ source_ko: "처리 규격", check_ko: "셸 실행 여부", why_ko: "실행 문맥 확인" }], limitations: ["주의 내용"] },
}, evaluation: { outcome: "unlabeled" } });

test("legacy URLs resolve to four tabs, JSON opens within execution", () => {
  assert.equal(decisionTab("json"), "agent");
  for (const key of ["result", "raw", "agent", "report"]) assert.equal(decisionTab(key), key);
  assert.equal(decisionTab("bad"), "result");
});
test("hero shows one summary without configuration, identifiers or calibrated probability claims", () => {
  const detail = fixture();
  const html = render(DecisionHero, { detail });
  assert.match(html, /판정 보류/);
  assert.equal(html.split("구분 조건을 확인해야 합니다.").length - 1, 1);
  assert.doesNotMatch(html, /saved-model|saved-prompt|private-id|private-event-id|100%|완전/);
  assert.match(html, /입력 생략/);
});
test("missing timing and omission metadata are not inferred as zero or complete input", () => {
  const detail = fixture(); delete detail.result.input_truncated;
  const html = render(DecisionHero, { detail });
  assert.match(html, /미측정/); assert.match(html, /입력 생략<\/dt><dd>미기록/);
  assert.doesNotMatch(html, /입력 생략<\/dt><dd>없음/);
});
test("pending and failed results never masquerade as final decisions", () => {
  for (const status of ["failed", "pending", "processing"]) {
    const detail = { ...fixture(), status };
    const html = render(DecisionHero, { detail });
    assert.doesNotMatch(html, /decision-verdict verdict-inconclusive|severity-UNKNOWN/);
  }
});
test("evidence keeps opposing interpretations and source numbers; legacy evidence stays unclassified", () => {
  const detail = fixture(), items = detailEvidence(detail.result);
  assert.deepEqual(items.map(item => item.supports), ["true_positive", "false_positive", "context"]);
  assert.deepEqual(items[0].related_numbers, [2]);
  assert.equal(detailEvidence({ verdict: "true_positive", evidence: [{ field: "payload", excerpt: "x", interpretation_ko: "y" }] })[0].supports, "unclassified");
});
test("reference evidence is folded, attack and benign evidence stay visible and inert", () => {
  const detail = fixture(); detail.result.analyst_assessment.evidence[0].excerpt = '<script>window.hostile=true</script>';
  const html = render(EvidenceCards, { detail, onViewInput() {} });
  assert.match(html, /공격 해석/); assert.match(html, /정상 해석/);
  assert.match(html, /<details class="decision-context-evidence">/);
  assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /<script/);
});
test("malformed historical excerpts do not crash React or become fabricated evidence", () => {
  const result = { evidence: [{ field: "payload", excerpt: { unexpected: true } }, { field: null, excerpt: "x" }, { field: "payload", excerpt: "kept", interpretation_ko: "original" }] };
  assert.equal(detailEvidence(result).length, 1);
  const html = render(EvidenceCards, { detail: { status: "completed", result } });
  assert.match(html, /kept/); assert.doesNotMatch(html, /unexpected/);
});
test("identical issue/check text is shown once with source, purpose and evidence links retained", () => {
  const detail = fixture(), conditions = decisionConditions(detail);
  assert.equal(conditions.items.length, 1);
  assert.deepEqual(conditions.items[0].evidence_numbers, [1, 2]);
  const html = render(DecisionConditions, { detail });
  assert.equal(html.split("셸 실행 여부").length - 1, 1);
  for (const text of ["판정에 필요한 확인", "처리 규격", "실행 문맥 확인", "값을 사용하는 위치", "관련 근거"]) assert.ok(html.includes(text), text);
});
test("different follow-up sources are retained, no approximate semantic deduplication", () => {
  const detail = fixture();
  detail.result.analyst_guidance.checks.push({ source_ko: "서버 담당자", check_ko: "셸 실행 여부", why_ko: "실제 설정 확인" });
  detail.result.analyst_guidance.checks.push({ source_ko: "다른 서버", check_ko: "셸 실행 여부 재확인", why_ko: "범위 확인" });
  const conditions = decisionConditions(detail);
  assert.equal(conditions.items.length, 2); assert.equal(conditions.items[0].checks.length, 2);
});
test("no empty follow-up is invented for decisive results; no missing-data claim invented for a hold", () => {
  const detail = fixture(); detail.result.verdict = "false_positive";
  detail.result.analyst_guidance.checks = []; detail.result.analyst_assessment.decision_issues = [];
  assert.equal(decisionConditions(detail).visible, false);
  detail.result.verdict = "inconclusive";
  const html = render(DecisionConditions, { detail });
  assert.equal(html, "");
});
test("technical interpretation and limitations are collapsed but retained", () => {
  const html = render(TechnicalInterpretation, { detail: fixture() });
  assert.match(html, /<details><summary>기술 해석 펼치기/);
  assert.match(html, /기술 해석 문구/); assert.match(html, /주의 내용/);
  assert.match(html, /확정된 결론으로 사용하지 마세요/);
});
test("context separates collector score from final decision and reference label", () => {
  const detail = fixture(); detail.initial_assessment = { verdict: "true_positive", probability: .72, comparison: "final_inconclusive" };
  const html = render(DecisionContext, { detail });
  assert.match(html, /수집기 점수 72.0%/); assert.match(html, /같은 확률 척도가 아닙니다/);
  assert.match(html, /참고 라벨/); assert.doesNotMatch(html, /saved-model/);
  assert.equal((html.match(/%/g) || []).length, 1);
});
test("execution starts with summary, not a full debugger; legacy JSON can start expanded", () => {
  const detail = fixture(), runs = [{ id: "hidden-run", steps: [{ id: "step", step_type: "llm_primary", status: "completed", duration_ms: 250, output: "private-output" }] }];
  const html = render(ExecutionView, { detail, runs });
  assert.match(html, /전체 단계 보기/); assert.match(html, /saved-model/);
  assert.doesNotMatch(html, /private-output|inspection-agent-grid|hidden-run/);
  assert.match(html, /<details><summary>결과 JSON/);
  assert.match(render(ExecutionView, { detail, runs, initialJson: true }), /<details open=""><summary>결과 JSON/);
});

for (const verdict of ["true_positive", "false_positive", "inconclusive"]) for (const action of ["D", "A"]) test(`WAF ${action} and ${verdict}: observation is not a verdict prerequisite`, () => {
  const detail = fixture(); detail.waf_action = action; detail.result.verdict = verdict;
  const check = verdict === "inconclusive" ? "셸 실행 여부" : "동일 요청 이후의 영향 범위";
  detail.result.analyst_guidance.checks = [{ source_ko: "서비스 기록", check_ko: check, why_ko: "입력 처리 범위를 검토합니다." }];
  const before = JSON.stringify(detail), html = render(DecisionConditions, { detail });
  const report = decodeReportText(buildAnalysisReport(detail));
  assert.match(html, action === "D" ? /차단으로 기록됐습니다/ : /허용으로 기록됐습니다/);
  assert.ok(html.includes(verdict === "inconclusive" ? "판정에 필요한 확인" : "영향·대응 확인"));
  assert.ok(report.includes(verdict === "inconclusive" ? "판정에 필요한 확인" : "영향·대응 확인"));
  assert.doesNotMatch(html, /공격 시도가 있었는지 확인|200 응답인지 확인 후/);
  if (verdict !== "inconclusive") {
    assert.doesNotMatch(html, /셸 실행 여부|값을 사용하는 위치/);
    assert.match(render(TechnicalInterpretation, { detail }), /판정 시 고려한 쟁점/);
  }
  assert.equal(JSON.stringify(detail), before);
});

for (const severity of ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]) test(`hero severity ${severity} uses the existing lowercase CSS`, () => {
  const detail = fixture(); detail.event_name = "중립 이벤트명"; detail.signature = "SQL Injection";
  detail.result.threat_analysis.severity = severity;
  const html = render(DecisionHero, { detail });
  assert.ok(html.includes(`severity-${severity.toLowerCase()}`));
  assert.match(html, /<h1>중립 이벤트명<\/h1>/);
  assert.match(html, /WAF 탐지.*SQL Injection/);
  assert.match(html, /잠재 영향 기준 · 실제 공격 성공 여부와 별도/);
});

for (const relation of ["exact", "partial", "mismatch", "unknown"]) test(`legacy signature ${relation} uses a deterministic summary without rewriting original explanation`, () => {
  const detail = fixture(); detail.result.verdict = "true_positive";
  detail.result.signature_assessment = { relation, explanation_ko: "명확히 일치합니다." };
  const before = JSON.stringify(detail), html = render(TechnicalInterpretation, { detail });
  assert.ok(html.includes(signatureSummary(relation)[0]));
  assert.match(html, /<details><summary>기존 분석 설명/);
  assert.match(html, /기존 분석 설명은 검증된 결론이 아닙니다/);
  const report = decodeReportText(buildAnalysisReport(detail));
  assert.ok(report.includes(signatureSummary(relation)[0]));
  assert.equal(JSON.stringify(detail), before);
  assert.equal(detail.result.verdict, "true_positive"); // mismatch does not mean benign.
});

test("disagreement keeps both evidence sides, exposes role verdicts and never invents missing role analysis", () => {
  const detail = fixture(); detail.result.diagnostics.inconclusive_reasons = ["verdict_disagreement"];
  detail.result.primary = { verdict: "true_positive" }; detail.result.verifier = { output: { verdict: "false_positive" } };
  const before = JSON.stringify(detail), html = render(TechnicalInterpretation, { detail, onExecution() {} });
  assert.equal(verifierDisagreed(detail), true);
  assert.match(html, /자동 분석 해석이 서로 달랐습니다/); assert.match(html, /Primary/); assert.match(html, /Verifier/);
  assert.doesNotMatch(html, /기술 해석 문구|Primary 해석 기록|Verifier 해석 기록/);
  assert.match(html, /실행 상세/);
  assert.match(render(EvidenceCards, { detail }), /공격 해석/); assert.match(render(EvidenceCards, { detail }), /정상 해석/);
  assert.doesNotMatch(decodeReportText(buildAnalysisReport(detail)), /기술 해석 문구/);
  assert.equal(JSON.stringify(detail), before);
  detail.result.primary.threat_analysis = { ...detail.result.threat_analysis, technique_ko: "역할별 기록" };
  const recorded = render(TechnicalInterpretation, { detail });
  assert.match(recorded, /Primary 해석 기록/); assert.match(recorded, /역할별 기록/); assert.doesNotMatch(recorded, /Verifier 해석 기록/);
});

for (const verdict of ["true_positive", "false_positive", "inconclusive"]) test(`${verdict} evidence is ordered for the verdict, original numbering is stable`, () => {
  const detail = fixture(); detail.result.verdict = verdict;
  const html = render(EvidenceCards, { detail }), sections = evidenceSections(verdict);
  assert.ok(html.indexOf(`evidence-${sections[0].side}`) < html.indexOf(`evidence-${sections[1].side}`));
  assert.deepEqual(detailEvidence(detail.result).map(item => item.number), [1, 2, 3]);
  for (const { label: [label] } of sections.slice(0, 2)) assert.ok(html.includes(label));
});
