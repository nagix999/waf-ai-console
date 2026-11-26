import assert from "node:assert/strict";
import test from "node:test";
import { buildSync } from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { analysisPerformanceIsRunning, analysisPerformanceScope, analysisPerformanceSummary } from "./analysisPerformance.js";

const metric = () => ({ recorded: 6, unrecorded: 1, reported_sum_ms: 6000, min_ms: 0, p50_ms: 800, p95_ms: 1600, p99_ms: 2100, mean_ms: 1000, max_ms: 2100 });
const fixture = () => ({ version: "analysis-performance-summary-v1", cases: 10, completed_cases: 7, statuses: { completed: 7, failed: 2, processing: 1, pending: 0, canceled: 0 }, completed_timings: Object.fromEntries(["decision_elapsed_ms", "analysis_queue_ms", "analysis_processing_ms", "result_ready_elapsed_ms"].map(key => [key, metric()])) });
function load(file) {
  const bundled = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} };
  runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date, URL, setTimeout, clearTimeout, AbortController });
  return module.exports;
}
const { ProductionPerformanceContent, ProductionTimingPreview } = load("./ProductionPerformanceSummary.jsx");
const { default: CompletedTimingSummary } = load("./TimingSummary.jsx");
const render = (component, props) => renderToStaticMarkup(createElement(component, props));

test("analysis timing scope preserves every applied filter but excludes pagination, sorting and arbitrary fields", () => {
  const filters = { analysis_purpose: "production", q: "SYNTHETIC_FILTER", search_field: "company_name", service_api_key_id: "example-key", initial_comparison: "different", input_truncated: false, src_port: 0, created_from: "2026-11-01T00:00:00Z", include_retries: true };
  assert.deepEqual(analysisPerformanceScope({ ...filters, offset: 150, limit: 25, sort_by: "company_name", sort_order: "asc", payload: "PRIVATE_RAW", statuses: [] }), filters);
  assert.deepEqual(analysisPerformanceScope({ status: "", verdict: null, severity: undefined, input_truncated: false }), { input_truncated: false });
  assert.deepEqual(analysisPerformanceScope(null), {});
  assert.equal(JSON.stringify(analysisPerformanceScope({ ...filters, offset: 0 })), JSON.stringify(analysisPerformanceScope({ ...filters, offset: 500 })));
});

test("analysis timing API posts full filter scope with administrator cookies, no-store and cancellation", async () => {
  const original = globalThis.fetch, controller = new AbortController(); let request;
  globalThis.fetch = async (path, options) => { request = { path, options }; return new Response(JSON.stringify(fixture()), { headers: { "content-type": "application/json" } }); };
  try {
    await api.analysisPerformance({ analysis_purpose: "production", status: "completed", q: "SYNTHETIC_FILTER", offset: 100, sort_by: "status" }, { signal: controller.signal });
    assert.equal(request.path, "/api/v1/analyses/performance-summary"); assert.equal(request.options.method, "POST"); assert.equal(request.options.cache, "no-store"); assert.equal(request.options.credentials, "include"); assert.strictEqual(request.options.signal, controller.signal);
    assert.deepEqual(JSON.parse(request.options.body), { analysis_purpose: "production", status: "completed", q: "SYNTHETIC_FILTER" });
    assert.doesNotMatch(request.path, /SYNTHETIC_FILTER/);
    globalThis.fetch = async () => new Response("{}", { status: 401, headers: { "content-type": "application/json" } });
    await assert.rejects(api.analysisPerformance(), error => error.status === 401);
  } finally { globalThis.fetch = original; }
});

test("Production and Runtime reuse completed-only queue, processing and result-ready cards with all percentiles", () => {
  const source = fixture(), production = render(ProductionPerformanceContent, { summary: source });
  const runtime = render(CompletedTimingSummary, { summary: { ...source, latency_summary: { p50: 999999, p95: 999999 }, recent_analyses: [{ total_elapsed_ms: 999999 }] } });
  for (const html of [production, runtime]) {
    for (const key of ["P50", "P95", "P99"]) assert.equal((html.match(new RegExp(`>${key}<`, "g")) || []).length, 4);
    for (const phrase of ["접수 → 판정 완료", "초기 대기", "분석 처리", "접수 → 결과 준비", "0.8초", "1.6초", "2.1초", "완료된 7문항", "기록 6 / 미기록 1", "초기 큐 대기만 제외", "HTTP 요청부터 응답까지"]) assert.ok(html.includes(phrase), phrase);
    assert.doesNotMatch(html, /999999|16\.7분|NaN|Infinity/);
  }
  assert.match(production, /전체 10건/);
  assert.match(production, /페이지와 정렬 변경에는 영향을 받지 않습니다/);
  const english = render(ProductionPerformanceContent, { summary: source, words: (ko, en) => en });
  assert.doesNotMatch(english, /[가-힣]/); assert.match(english, /7 completed cases/);
});

test("missing Runtime percentiles never fall back to legacy failure-inclusive or page-local measurements", () => {
  const raw = { completed_cases: 3, latency_summary: { p50: 999, p95: 999 }, recent_analyses: [{ total_elapsed_ms: 999 }] };
  const html = render(CompletedTimingSummary, { summary: raw });
  assert.match(html, /완료 문항의 시간 통계가 제공되지 않았습니다/);
  assert.doesNotMatch(html, /999|1\.0초|0\.0초/);
  const source = fixture(); source.completed_timings.analysis_processing_ms = {};
  const normalized = analysisPerformanceSummary(source);
  assert.equal(normalized.completed_timings.analysis_processing_ms.p99_ms, null);
  assert.equal(analysisPerformanceSummary({ version: "future" }), null);
  assert.equal(analysisPerformanceIsRunning(source), true);
  source.statuses = { completed: 7, processing: 0, pending: 0 };
  assert.equal(analysisPerformanceIsRunning(source), false);
  const preview = render(ProductionTimingPreview, { metric: source.completed_timings.analysis_processing_ms });
  assert.match(preview, /분석 처리/); assert.doesNotMatch(preview, /0\.0초|undefined|NaN/);
});
