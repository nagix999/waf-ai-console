import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";

// Bundle the real components without writing generated files or contacting any
// service. External React shares the renderer's dispatcher for the tab hooks.
const bundled = buildSync({
  entryPoints: [fileURLToPath(new URL("./App.jsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", packages: "external",
  jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent",
}).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });
const { TestAnalysisPage, ResultView } = module.exports;

const fixture = () => ({ status: "completed", result: {
  verdict: "true_positive", threat_analysis: { category: "합성 공격 유형", target: "payload.query.q", technique_ko: "합성 요청 분석", potential_impact_ko: "실행될 경우의 영향", obfuscations: [] },
  signature_assessment: { relation: "partial", explanation_ko: "합성 탐지 연관성" },
  evidence: [{ field: "payload.query.q", excerpt: "<script>synthetic</script>", interpretation_ko: "합성 원문 근거" }],
  conflicting_evidence: [], tuning_recommendation: { recommended: false, risk_ko: null },
  analyst_guidance: { checks: [], limitations: [] },
} });

test("the unified test view mounts both workflows with one visible input panel and result navigation", () => {
  const html = renderToStaticMarkup(createElement(TestAnalysisPage, { onViewTests() {}, onOpen() {} }));
  assert.match(html, /aria-label="테스트 입력 방식"/);
  assert.match(html, /id="test-panel-direct"[^>]*role="tabpanel"[^>]*aria-labelledby="test-input-direct">/);
  assert.match(html, /id="test-panel-file"[^>]*hidden=""/);
  assert.match(html, /단건 분석/);
  assert.match(html, /HTTP 요청 1건/);
  assert.match(html, /배치 파일 분석/);
  assert.match(html, /배치 분석 시작/);
  assert.match(html, /테스트 결과 보기/);
  assert.match(html, /aria-label="답안 자동 비교 설명"/);
  assert.match(html, /UTF-8 CSV \/ JSON · 입력 스키마 기준/);
  assert.match(html, /답안·난이도·유형은 모델에 보내지 않습니다/);
  assert.doesNotMatch(html, /aria-label="(?:테스트 실행|파일 형식|참고 답안) 설명"/);
  assert.doesNotMatch(html, /테스트 프롬프트 선택|저장된 프롬프트 버전|간결 지침|시스템 지침<select/);
  assert.match(html, /10 MiB/);
  assert.match(html, /기대 답안 \(선택\)/);
  assert.doesNotMatch(html, /test-run-history|test-run-detail|role="tooltip"|합성/);
  // The two name fields start empty; a request ID supplies a name only when submitted.
  const nameFields = [...html.matchAll(/<label class="test-name-field">테스트명 <input([^>]*)>/g)];
  assert.equal(nameFields.length, 2);
  for (const [, attributes] of nameFields) {
    assert.match(attributes, /placeholder="비워두면 ID 자동 생성"/);
    assert.match(attributes, /value=""/);
  }
  assert.match(html, /<textarea[^>]*placeholder="GET \/search\?q=example HTTP\/1.1[^>]*><\/textarea>/);
  assert.match(html, /placeholder="예: 192.0.2.10"[^>]*value=""/);
  assert.match(html, /id="single-test-extra" hidden=""/);
});

test("actual result component uses analyst wording, ordered sections and precise source paths", () => {
  const detail = fixture();
  const html = renderToStaticMarkup(createElement(ResultView, { detail }));
  assert.ok(html.indexOf("세부 분석") < html.indexOf("판정 근거"));
  assert.match(html, /탐지 내용과 요청의 연관성/);
  assert.match(html, /요청 파라미터 · q/);
  assert.match(html, /payload.query.q/);
  assert.match(html, /&lt;script&gt;synthetic&lt;\/script&gt;/);
  assert.doesNotMatch(html, /시그니처 평가|판정 해석|관찰 근거와 해석|함께 고려할 정황|WAF 정책 검토|<script\b/);
  assert.equal(detail.result.evidence[0].excerpt, "<script>synthetic</script>");
});

test("actual result component retains unresolved status, distinct interpretations and non-recommended policy warnings", () => {
  const detail = fixture();
  detail.result.verdict = "inconclusive";
  detail.result.evidence.push({ ...detail.result.evidence[0], interpretation_ko: "문자열 저장 요청이라면 정상 동작일 수 있습니다." });
  detail.result.tuning_recommendation.risk_ko = "예외를 넓히면 다른 공격이 허용될 수 있습니다.";
  detail.result.analyst_guidance.limitations = ["분석 입력 일부 생략"];
  const html = renderToStaticMarkup(createElement(ResultView, { detail }));
  assert.match(html, /판정 보류 상태입니다/);
  assert.match(html, /합성 원문 근거/);
  assert.match(html, /정상 동작일 수 있습니다/);
  assert.equal(html.split("원문 발췌").length - 1, 1);
  assert.match(html, /예외를 넓히면 다른 공격이 허용될 수 있습니다/);
  assert.match(html, /분석 입력 일부 생략/);
  assert.match(html, /주의사항/);
  assert.doesNotMatch(html, /status-pending|status-disabled/);
});

test("test input APIs retain separate administrator endpoints and never send production overrides", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    return { status: 200, ok: true, json: async () => ({ id: "synthetic-id" }) };
  };
  try {
    const event = { event_id: "synthetic-input", payload: "GET / HTTP/1.1\r\n\r\n" };
    await api.createAnalysis(event);
    await api.upload(new File(["[]"], "synthetic.json", { type: "application/json" }));
    assert.equal(calls[0].path, "/api/v1/test-analyses");
    assert.deepEqual(JSON.parse(calls[0].options.body), event);
    assert.equal(calls[1].path, "/api/v1/test-uploads");
    assert.deepEqual([...calls[1].options.body.keys()], ["file"]);
  } finally { globalThis.fetch = originalFetch; }
});
