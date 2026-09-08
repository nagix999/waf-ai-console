import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { buildAnalysisReport } from "./analysisReport.js";

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./AnalysisReport.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date });
const Report = module.exports.default;
const detail = { id: "HIDDEN_ANALYSIS_ID", event_id: "HIDDEN_EVENT_ID", source_system: "HIDDEN_SOURCE", status: "completed", company_name: "테스트 회사", result: { schema_version: "waf-analysis-v2", verdict: "true_positive", summary_ko: "원문에서 공격 구문을 확인했습니다.", threat_analysis: { severity: "HIGH" }, evidence: [{ field: "payload.path", excerpt: "../example\r\n<img src=https://never.invalid>", interpretation_ko: "파일 탐색 구문과 판정을 연결한 근거입니다." }], analyst_guidance: { checks: [], limitations: [] }, agent: { framework: "moduagent" } } };

test("report explains appendix scope as plain text and keeps its full evidence body directly readable", () => {
  const html = renderToStaticMarkup(createElement(Report, { detail, mode: "preview", onModeChange() {} }));
  assert.doesNotMatch(html, /aria-label="문서 포함 범위 설명"|aria-label="보고서 설명"/);
  assert.doesNotMatch(html, /metric-help-trigger/);
  assert.match(html, /화면과 Markdown에만 포함 · PDF·Excel 제외/);
  assert.match(html, /aria-label="보고서 목차"/);
  assert.match(html, /파일 탐색 구문과 판정을 연결한 근거입니다/);
  assert.match(html, /&lt;img src=https:\/\/never.invalid&gt;/);
  assert.doesNotMatch(html, /<details|<summary|<img|HIDDEN_ANALYSIS_ID|HIDDEN_EVENT_ID|HIDDEN_SOURCE/);
});

test("report IDs exist only when the explicit technical appendix is requested", () => {
  const before = JSON.stringify(detail);
  const base = buildAnalysisReport(detail), extended = buildAnalysisReport(detail, { includeAppendix: true });
  assert.doesNotMatch(base, /HIDDEN|분석 ID|이벤트 ID/);
  assert.match(extended, /분석 ID/); assert.match(extended, /이벤트 ID/);
  assert.match(extended, /## 부록 · 참고 답안 평가/);
  assert.equal(JSON.stringify(detail), before);
});

test("Markdown mode uses exactly the same report without rendering active code or a misleading document jump", () => {
  const html = renderToStaticMarkup(createElement(Report, { detail, mode: "source", onModeChange() {} }));
  assert.match(html, /aria-label="보고서 Markdown 원본"/);
  assert.match(html, /# WAF 분석 보고서/);
  assert.match(html, /파일 탐색 구문과 판정을 연결한 근거입니다/);
  assert.doesNotMatch(html, /aria-label="보고서 목차"|<img|HIDDEN_ANALYSIS_ID/);
});
