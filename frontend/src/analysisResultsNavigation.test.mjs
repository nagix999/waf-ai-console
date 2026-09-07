import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { analysisQuery, initialListState } from "./analysisView.js";
import { datasetListState } from "./modelValidation.js";

const bundled = buildSync({
  entryPoints: [fileURLToPath(new URL("./App.jsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", packages: "external",
  jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent",
}).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date });
const { AnalysisResultsPage, initialTestResultsState } = module.exports;
const noop = () => {};
const props = (purpose = "test") => ({
  purpose, state: initialListState(purpose === "test" ? "production" : purpose),
  testState: initialTestResultsState(), setState: noop, setTestState: noop,
  onScopeChange: noop, onSelectRun: noop, onOpenRunItem: noop, onOpen: noop, onUnauthorized: noop,
});
const render = options => renderToStaticMarkup(createElement(AnalysisResultsPage, options));

test("analysis results Test tab starts with named runs, not individual result/evaluation panes", () => {
  const html = render(props());
  assert.match(html, /aria-label="분석 구분"/);
  assert.match(html, /aria-selected="true">테스트<\/button>/);
  assert.match(html, /테스트 목록/);
  assert.match(html, /테스트명 검색/);
  assert.match(html, /해당 실행의 분석 결과와 평가 지표/);
  assert.match(html, /테스트 문항 전체 보기/);
  assert.match(html, /테스트명 없이 저장된 이전 결과/);
  assert.doesNotMatch(html, /\bunified-analysis-table\b|analysis-evaluation-overview|name="source_system"/);
});

test("all and Production scopes retain the individual list, reference metrics and field searches", () => {
  for (const purpose of ["", "production"]) {
    const options = props(purpose);
    options.state.applied.q = "synthetic-production";
    options.state.draft = { ...options.state.applied };
    // A remembered test detail must not leak into either non-Test scope.
    options.testState.runId = "synthetic-old-test";
    const before = JSON.stringify(options.state);
    const html = render(options);
    assert.match(html, /<table class="[^"]*\bunified-analysis-table\b/);
    assert.match(html, /판정 평가 지표/);
    assert.match(html, /name="label_presence"/);
    assert.match(html, /name="evaluation_outcome"/);
    assert.match(html, /value="synthetic-production"/);
    assert.doesNotMatch(html, /\btest-run-history\b|class="page-stack test-run-detail"|테스트명 검색/);
    assert.equal(JSON.stringify(options.state), before);
  }
});

test("individual Test fallback includes named and previous items with latest-reference evaluation", () => {
  const options = props();
  options.testState.view = "items";
  const html = render(options);
  assert.match(html, /← 테스트 목록/);
  assert.match(html, /이름 있는 실행과 실행 묶음이 없는 이전 결과를 함께 조회/);
  assert.match(html, /최신 참고 답안 기준/);
  assert.match(html, /<table class="[^"]*\bunified-analysis-table\b/);
  assert.match(html, /판정 평가 지표/);
  assert.doesNotMatch(html, /테스트명 검색/);
});

test("selected Test execution uses the shared detail within analysis results", () => {
  const options = props();
  options.testState.runId = "synthetic-run-1";
  options.testState.filters.difficulty = "hard";
  options.testState.filters.offset = 25;
  const before = JSON.stringify(options.testState);
  const html = render(options);
  assert.match(html, /class="page-stack test-run-detail"/);
  assert.match(html, /← 테스트 목록/);
  assert.match(html, /실행 정보를 불러오는 중/);
  assert.doesNotMatch(html, /테스트명 검색|\bunified-analysis-table\b/);
  assert.equal(JSON.stringify(options.testState), before);
});

test("run name query, test item filters and Production filters are independent values", () => {
  const a = initialTestResultsState();
  const b = initialTestResultsState();
  const production = initialListState("production");
  a.history.query.q = "synthetic-name";
  a.history.query.offset = 10;
  a.filters.test_category = "synthetic-category";
  a.items.applied.source_system = "waf-internal-test-run-synthetic";
  a.items.applied.test_run_id = "synthetic-run";
  assert.equal(b.history.query.q, "");
  assert.equal(b.filters.test_category, "");
  assert.equal(b.items.applied.source_system, "");
  assert.deepEqual(analysisQuery(production.applied, 25, 0), { limit: 25, offset: 0, analysis_purpose: "production" });
});

test("older dataset links retain source-limited item queries instead of silently opening unfiltered runs", () => {
  const options = props();
  options.testState.view = "items";
  options.testState.items = datasetListState("waf-internal-model-test-synthetic");
  const html = render(options);
  assert.match(html, /waf-internal-model-test-synthetic/);
  assert.match(html, /적용된 검색 조건/);
  assert.match(html, /← 테스트 목록/);
  assert.doesNotMatch(html, /테스트명 검색/);
  assert.equal(analysisQuery(options.testState.items.applied, 25, 0).source_system, "waf-internal-model-test-synthetic");
});
