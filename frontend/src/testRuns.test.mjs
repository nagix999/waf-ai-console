import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { extraMetrics, mainMetrics, matrixDrilldown, metricHelp, metricText } from "./evaluationMetrics.js";
import { newTestRequestKey, testRunQuery, testScopeSelection, validateTestName } from "./testRuns.js";
import { createFullValidationController, modelTestPayload } from "./modelValidation.js";

function bundle(path) {
  const code = buildSync({ entryPoints: [fileURLToPath(new URL(path, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} }; runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date, AbortController, setTimeout, clearTimeout }); return module.exports;
}
const { EvaluationSummary } = bundle("./ReferenceLabels.jsx");
const { MetricCards, ConfusionMatrix, AdditionalMetrics, MetricHelp } = bundle("./EvaluationMetrics.jsx");
const { TestRunRows, TestRunItemRows, TestRunHistory, TestRunHistoryEmpty, TestRunDetail, initialTestRunHistoryState, initialTestRunFilters, testRunHistoryChange, testRunFilterChange, watchTestRunRead } = bundle("./TestRuns.jsx");
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));
function descendants(node) {
  if (Array.isArray(node)) return node.flatMap(descendants);
  if (!node || typeof node !== "object") return [];
  return [node, ...descendants(node.props?.children)];
}
const summaryFixture = () => ({ total: 13, labeled: 11, evaluable: 8, matches: 5, binary_evaluable: 7, binary_decided: 5, support_positive: 4, support_negative: 3, label_coverage: 11 / 13,
  outcomes: { match: 4, false_negative: 1, abstained: 2, expected_abstention_match: 1, pending: 1, failed: 1, stub: 1, unlabeled: 2 }, source_groups: [],
  confusion_matrix: { tp: 2, fn: 1, fp: 0, tn: 2, abstained_positive: 1, abstained_negative: 1 },
  metrics: { basis: "decided_binary", accuracy: .8, precision: 1, recall: 2 / 3, f1: .8, specificity: 1, false_positive_rate: 0, false_negative_rate: 1 / 3, balanced_accuracy: 5 / 6, macro_f1: .8, mcc: 2 / 3, coverage: 5 / 7, abstention_rate: 2 / 7, overall_binary_correct_rate: 4 / 7 },
});

test("metric formatting preserves undefined, zero, perfect and negative MCC distinctly", () => {
  for (const value of [null, undefined, NaN, Infinity, "0"]) assert.equal(metricText(value), "계산 불가");
  assert.equal(metricText(0), "0.0%"); assert.equal(metricText(1), "100.0%"); assert.equal(metricText(.12345), "12.3%");
  assert.equal(metricText(-1, "mcc"), "-1.000"); assert.equal(metricText(0, "mcc"), "0.000");
});

test("every quality metric has an accessible help button and meaningful directional documentation", () => {
  for (const [key, label] of [...mainMetrics, ...extraMetrics, ["label_coverage", "참고 답안 연결률"]]) {
    assert.ok(metricHelp[key].length > 80, `${key} missing substantive help`);
    const html = render(MetricHelp, { metric: key, label });
    assert.ok(html.includes(`aria-label="${label} 설명"`)); assert.match(html, /type="button"/); assert.match(html, />\?<\/button>/);
  }
  assert.match(metricHelp.accuracy, /낮으면/); assert.match(metricHelp.coverage, /높다고 정확한 것은 아닙니다/); assert.match(metricHelp.abstention_rate, /낮아도 무조건 좋은 것은 아니며/);
  assert.match(metricHelp.mcc, /0은 상관 없음/); assert.match(metricHelp.recall, /전체 공격 탐지율이 아닙니다/);
});

test("main and extra metric cards render server values without deriving scores from visible rows", () => {
  const { metrics } = summaryFixture(); const html = render(MetricCards, { metrics });
  for (const [, label] of mainMetrics) assert.ok(html.includes(label));
  assert.match(html, /80.0%/); assert.match(html, /71.4%/); assert.match(html, /28.6%/);
  const closed = render(AdditionalMetrics, { metrics: { ...metrics, mcc: -.25 } });
  assert.match(closed, /추가 지표/); assert.doesNotMatch(closed, /-0.250|Macro F1/);
  // Native dialog content is not part of SSR. Inspect the actual prepared body,
  // without changing component state or pretending the dialog starts open.
  let additionalTree;
  function CaptureAdditional() { additionalTree = AdditionalMetrics({ metrics: { ...metrics, mcc: -.25 } }); return null; }
  render(CaptureAdditional);
  const dialog = additionalTree.props.children.find(child => child.props?.title === "추가 평가 지표");
  assert.equal(dialog.props.open, false);
  const additional = renderToStaticMarkup(dialog.props.children);
  assert.match(additional, /-0.250/); assert.match(additional, /Macro F1/); assert.match(additional, /보류 포함 정답률/);
  const unavailable = render(MetricCards, { metrics: {} }); assert.equal((unavailable.match(/계산 불가/g) || []).length, 6); assert.doesNotMatch(unavailable, /0.0%/);
});

test("confusion matrix includes six cells, separate abstentions and precise drilldown predicates", () => {
  const matrix = summaryFixture().confusion_matrix; const html = render(ConfusionMatrix, { matrix, onCell() {}, selectedCell: "fn" });
  assert.equal((html.match(/<td /g) || []).length, 6); assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
  assert.match(html, /FN · 미탐 방향 1건 문항 보기/); assert.match(html, /참고 정탐 · AI 보류 1건 문항 보기/); assert.match(html, /분모는 유지/);
  assert.deepEqual(matrixDrilldown("tp"), { reference_verdict: "true_positive", verdict: "true_positive", evaluation_outcome: "match" });
  assert.deepEqual(matrixDrilldown("tn"), { reference_verdict: "false_positive", verdict: "false_positive", evaluation_outcome: "match" });
  assert.deepEqual(matrixDrilldown("abstained_positive"), { reference_verdict: "true_positive", verdict: "inconclusive", evaluation_outcome: "abstained" });
  assert.deepEqual(matrixDrilldown("__proto__"), {});
});

test("test subgroup scopes and row-only matrix filters are transmitted separately with server pagination", () => {
  const query = testRunQuery({ difficulty: "hard", test_category: "xss", cell: "fp", offset: 25 });
  assert.deepEqual(query, { difficulty: "hard", test_category: "xss", reference_verdict: "false_positive", verdict: "true_positive", evaluation_outcome: "false_positive", limit: 25, offset: 25 });
  assert.deepEqual(testRunQuery({ difficulty_missing: true, test_category_missing: true }), { limit: 25, offset: 0, difficulty_missing: true, test_category_missing: true });
});

test("literal subgroup names never collide with the separate missing-metadata option", () => {
  for (const value of ["__unclassified__", "missing:", "value:hard"]) {
    const selected = testScopeSelection("difficulty", `value:${value}`);
    assert.deepEqual(selected, { difficulty: value, difficulty_missing: false });
    assert.equal(testRunQuery(selected).difficulty, value); assert.equal(testRunQuery(selected).difficulty_missing, undefined);
  }
  assert.deepEqual(testRunQuery(testScopeSelection("test_category", "missing:")), { limit: 25, offset: 0, test_category_missing: true });
  assert.deepEqual(testScopeSelection("test_category", ""), { test_category: "", test_category_missing: false });
});

test("test and production quality summary retain provenance and all exclusion and hold counts", () => {
  const summary = summaryFixture(); summary.source_groups = [{ ...summary, source_kind: "reference", ai_visible: false, false_negatives: 1, false_positives: 0, abstained: 2, expected_abstention_matches: 0, expected_abstention_mismatches: 0 }];
  const before = JSON.stringify(summary);
  for (const scopeLabel of ["프로덕션", "선택한 테스트 실행·난이도·유형"]) {
    const html = render(EvaluationSummary, { summary, scopeLabel });
    assert.ok(html.includes(scopeLabel)); assert.match(html, /전체 13건 · 현재 페이지 한정 아님/); assert.match(html, /확정 판정 기준/);
    assert.match(html, /답안 없음/); assert.match(html, /제외·보류 내역/); assert.match(html, /출처별 지표/);
    let tree;
    function CaptureSummary() { tree = EvaluationSummary({ summary, scopeLabel }); return null; }
    render(CaptureSummary);
    const nodes = descendants(tree);
    const excluded = nodes.find(node => node.props?.title === "평가 제외·보류 내역");
    assert.equal(excluded.props.open, false);
    const exclusions = renderToStaticMarkup(excluded.props.children);
    assert.match(exclusions, /진행 중 1 · 실행 실패 1 · 모의 실행 1/); assert.match(exclusions, /정답 포함 입력/);
    assert.match(exclusions, /기대 보류 일치 1건/); assert.match(exclusions, /일치율 분모에서 제외/);
    const sources = nodes.find(node => node.props?.title === "답안 출처별 지표");
    assert.equal(sources.props.open, false);
    const provenance = renderToStaticMarkup(sources.props.children);
    assert.match(provenance, /답안 출처·AI 열람 여부별 표본입니다/); assert.match(provenance, /서로 다른 그룹을 하나의 독립 정확도로 해석하지 마세요/);
    assert.doesNotMatch(provenance, /답안 출처·AI 열람 여부별 비교 설명/); assert.match(provenance, /미탐 방향 1 · 과탐 방향 0 · 모델 보류 2건/);
    const scopeHelp = nodes.find(node => node.props?.label === "평가 기준");
    assert.match(scopeHelp.props.children, /프로덕션 전체 품질로 일반화하지/);
    assert.match(scopeHelp.props.children, /일치율 = 기준과 같은 최종 판정/);
    assert.match(scopeHelp.props.children, /서로 다른 답안 출처가 섞인 경우 출처별 지표/);
    assert.equal(nodes.filter(node => node.props?.label === "평가 범위").length, 0);
  }
  assert.equal(JSON.stringify(summary), before);
});

test("named run rows and case names are escaped and rejected rows do not display a verdict", () => {
  const hostile = "<script>synthetic</script>";
  const html = render(TestRunRows, { items: [{ id: "run", name: hostile, kind: "upload", status: "completed", execution_mode: "moduagent", created_at: "2026-09-07T00:00:00Z", profile_metadata: { model_name: hostile }, prompt_version: "synthetic-policy", accepted: 1, total: 1, rejected: 0, failed: 0, completed: 1, total_elapsed_ms: 1000, evaluation_summary: summaryFixture() }], onSelect() {} });
  assert.match(html, /&lt;script&gt;synthetic/); assert.doesNotMatch(html, /<script/); assert.match(html, /80.0%/); assert.match(html, /1.00초/);
  const cases = render(TestRunItemRows, { items: [{ id: "case", row_number: 1, case_name: hostile, ingest_status: "rejected", verdict: "true_positive", error_code: "invalid_expected_verdict" }], onOpen() {} });
  assert.match(cases, /접수 거부 · 평가 제외/); assert.doesNotMatch(cases, /<script|>정탐</); assert.match(cases, /오류 정보/); assert.doesNotMatch(cases, /invalid_expected_verdict/);
  const unmeasured = render(TestRunRows, { items: [{ id: "missing", name: "정보 미기록", kind: "direct", status: "completed", evaluation_summary: null }], onSelect() {} });
  assert.match(unmeasured, /평가 정보 미기록/); assert.doesNotMatch(unmeasured, /확정 판정 0건/);
});

test("test list state preserves draft, applied search and page independently across navigation", () => {
  const initial = initialTestRunHistoryState(); const second = initialTestRunHistoryState();
  assert.notEqual(initial, second); assert.notEqual(initial.query, second.query);
  const applied = testRunHistoryChange({ ...initial, queryText: " SQLi 1차 " }, { type: "search" });
  assert.equal(applied.query.q, "SQLi 1차"); assert.equal(applied.query.offset, 0); assert.equal(applied.queryText, " SQLi 1차 ");
  const page = testRunHistoryChange(applied, { type: "page", direction: 1 });
  const draft = testRunHistoryChange(page, { type: "draft", value: "아직 적용하지 않은 검색" });
  assert.equal(draft.query, page.query); assert.equal(draft.query.q, "SQLi 1차"); assert.equal(draft.query.offset, 10);
  assert.equal(initial.queryText, ""); assert.equal(initial.query.offset, 0);
  const cleared = testRunHistoryChange(draft, { type: "clear" });
  assert.equal(cleared.query.q, ""); assert.equal(cleared.queryText, ""); assert.equal(cleared.query.offset, 0); assert.equal(cleared.query.limit, 10);
  assert.equal(testRunHistoryChange(cleared, { type: "page", direction: -1 }).query.offset, 0);
});

test("run detail filters preserve population scope during row and page changes and reset only dependent fields", () => {
  const initial = initialTestRunFilters();
  const selected = { ...initial, difficulty: "hard", test_category_missing: true, status: "completed", evaluation_outcome: "match", offset: 50 };
  const cell = testRunFilterChange(selected, { type: "cell", value: "fn" });
  assert.equal(cell.difficulty, "hard"); assert.equal(cell.test_category_missing, true); assert.equal(cell.cell, "fn"); assert.equal(cell.status, ""); assert.equal(cell.evaluation_outcome, ""); assert.equal(cell.offset, 0);
  const page = testRunFilterChange(cell, { type: "page", direction: 1 });
  assert.equal(page.cell, "fn"); assert.equal(page.offset, 25); assert.equal(page.difficulty, "hard");
  const row = testRunFilterChange(page, { type: "row", name: "status", value: "failed" });
  assert.equal(row.cell, ""); assert.equal(row.status, "failed"); assert.equal(row.offset, 0); assert.equal(row.test_category_missing, true);
  const scope = testRunFilterChange(row, { type: "scope", name: "difficulty", value: "missing:" });
  assert.equal(scope.difficulty, ""); assert.equal(scope.difficulty_missing, true); assert.equal(scope.test_category_missing, true); assert.equal(scope.status, "");
  const limit = testRunFilterChange(page, { type: "limit", value: 100 });
  assert.equal(limit.limit, 100); assert.equal(limit.offset, 0); assert.equal(limit.cell, "fn"); assert.equal(selected.offset, 50);
  assert.equal(testRunFilterChange(cell, { type: "cell", value: "fn" }).cell, "");
  assert.equal(initial.difficulty, ""); assert.equal(initial.offset, 0);
  const clearedRows = testRunFilterChange(page, { type: "clear_rows" });
  assert.equal(clearedRows.difficulty, "hard"); assert.equal(clearedRows.test_category_missing, true);
  assert.equal(clearedRows.cell, ""); assert.equal(clearedRows.status, ""); assert.equal(clearedRows.evaluation_outcome, ""); assert.equal(clearedRows.offset, 0);
  const firstPage = testRunFilterChange(page, { type: "first_page" });
  assert.equal(firstPage.offset, 0); assert.equal(firstPage.cell, "fn"); assert.equal(firstPage.difficulty, "hard");
});

test("test list supports preserved controlled search, default navigation guidance and custom headings as inert text", () => {
  const controlled = { queryText: "draft <script>", query: { q: "applied <script>", limit: 10, offset: 20 } };
  const html = render(TestRunHistory, { state: controlled, onStateChange() {}, onSelect() {} });
  assert.match(html, /<h2>테스트 목록<\/h2>/); assert.match(html, /분석 결과와 평가 지표/); assert.match(html, /value="draft &lt;script&gt;"/); assert.match(html, /applied &lt;script&gt;/); assert.doesNotMatch(html, /<script/);
  const custom = render(TestRunHistory, { title: "<img> 제목", description: "<script> 설명", onSelect() {} });
  assert.match(custom, /&lt;img&gt; 제목/); assert.match(custom, /&lt;script&gt; 설명/); assert.doesNotMatch(custom, /<img|<script/);
  const detail = render(TestRunDetail, { id: "synthetic-run", filters: { ...initialTestRunFilters(), offset: 50 }, onFiltersChange() {}, onBack() {}, onOpen() {} });
  assert.match(detail, /← 테스트 목록/);
  assert.match(render(TestRunDetail, { id: "synthetic-run", backLabel: "직전 테스트 목록", onBack() {}, onOpen() {} }), /← 직전 테스트 목록/);
});

test("empty test list distinguishes no runs, no search match and an empty page with an optional real legacy navigation action", () => {
  const empty = render(TestRunHistoryEmpty, { query: { q: "", offset: 0 } });
  assert.match(empty, /아직 접수한 테스트가 없습니다/); assert.match(empty, /이전 개별 분석은 별도 목록에서 확인/); assert.doesNotMatch(empty, /개별 테스트 분석 보기|이전 테스트 자료 설명|help-trigger/);
  const searched = render(TestRunHistoryEmpty, { query: { q: "없는 이름", offset: 0 }, onClear() {}, onViewAnalyses() {} });
  assert.match(searched, /검색한 이름에 해당하는 테스트가 없습니다/); assert.match(searched, /검색 초기화/); assert.match(searched, /개별 테스트 분석 보기/); assert.doesNotMatch(searched, /아직 접수한/);
  const page = render(TestRunHistoryEmpty, { query: { q: "", offset: 10 } });
  assert.match(page, /이 페이지에 표시할 테스트가 없습니다/); assert.doesNotMatch(page, /아직 접수한/);
});

test("test and case navigation uses immutable IDs even when names repeat and legacy navigation calls its supplied action", () => {
  function buttons(node) {
    if (Array.isArray(node)) return node.flatMap(buttons);
    if (!node || typeof node !== "object") return [];
    return [...(node.type === "button" ? [node] : []), ...buttons(node.props?.children)];
  }
  const selected = [], opened = []; let legacy = 0;
  const base = { name: "같은 테스트명", status: "completed", kind: "direct", created_at: "2026-09-07T00:00:00Z" };
  const rows = TestRunRows({ items: [{ ...base, id: "first-run" }, { ...base, id: "second-run" }], onSelect: id => selected.push(id) });
  buttons(rows).filter(button => button.props.children === "같은 테스트명").forEach(button => button.props.onClick());
  assert.deepEqual(selected, ["first-run", "second-run"]);
  const cases = TestRunItemRows({ items: [{ id: "item-id", analysis_id: "analysis-id", case_name: "문항명", row_number: 1 }], onOpen: id => opened.push(id) });
  buttons(cases).find(button => button.props.children === "문항명").props.onClick(); assert.deepEqual(opened, ["analysis-id"]);
  const empty = TestRunHistoryEmpty({ query: { q: "", offset: 0 }, onViewAnalyses: () => legacy++ });
  buttons(empty).find(button => button.props.children === "개별 테스트 분석 보기 →").props.onClick(); assert.equal(legacy, 1);
});

const deferredRead = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flushRead = async () => { await Promise.resolve(); await Promise.resolve(); };

test("changing run or query cancels its request and blocks late success and failure from publishing", async () => {
  for (const reject of [false, true]) {
    const old = deferredRead(); const updates = []; let oldSignal;
    const cancelOld = watchTestRunRead({ read: ({ signal }) => { oldSignal = signal; return old.promise; }, onUpdate: value => updates.push(value), isRunning: () => false, errorMessage: "조회 실패" });
    cancelOld(); assert.equal(oldSignal.aborted, true);
    const cancelNew = watchTestRunRead({ read: async () => ({ id: "new-run", status: "completed" }), onUpdate: value => updates.push(value), isRunning: () => false, errorMessage: "조회 실패" });
    await flushRead(); if (reject) old.reject(new Error("OLD_PRIVATE_FAILURE")); else old.resolve({ id: "old-run", status: "completed" }); await flushRead();
    assert.equal(updates.length, 1); assert.equal(updates[0].data.id, "new-run"); assert.equal(updates[0].error, ""); cancelNew();
  }
});

test("run readers poll only unfinished reads, never overlap, stop at terminal state and clear timers on departure", async () => {
  const first = deferredRead(); const scheduled = []; const cleared = []; let calls = 0;
  const cancel = watchTestRunRead({ read: () => { calls++; return calls === 1 ? first.promise : Promise.resolve({ status: "completed" }); }, onUpdate() {}, isRunning: data => data.status === "processing", errorMessage: "조회 실패", setTimer: (callback, ms) => { scheduled.push({ callback, ms }); return scheduled.length; }, clearTimer: id => cleared.push(id) });
  assert.equal(calls, 1); assert.equal(scheduled.length, 0);
  first.resolve({ status: "processing" }); await flushRead(); assert.equal(scheduled.length, 1); assert.equal(scheduled[0].ms, 5000);
  scheduled[0].callback(); await flushRead(); assert.equal(calls, 2); assert.equal(scheduled.length, 1);
  cancel(); assert.equal(cleared.at(-1), 1);
});

test("read errors expose no server text, never retry automatically and propagate session expiry exactly once", async () => {
  for (const status of [401, 403, 500]) {
    const updates = []; let unauthorized = 0, calls = 0, timers = 0;
    const cancel = watchTestRunRead({ read: async () => { calls++; throw Object.assign(new Error("PRIVATE_RESPONSE"), { status }); }, onUpdate: state => updates.push(state), onUnauthorized: () => unauthorized++, isRunning: () => true, errorMessage: "조회하지 못했습니다.", setTimer: () => { timers++; } });
    await flushRead(); assert.equal(calls, 1); assert.equal(timers, 0); assert.equal(unauthorized, status === 401 ? 1 : 0); assert.equal(updates.length, 1); assert.doesNotMatch(updates[0].error, /PRIVATE_RESPONSE/); assert.equal(updates[0].loading, false); cancel();
  }
});

test("test names are validated after automatic naming and model validation preserves explicit names", async () => {
  for (const name of ["", "   ", null, "a".repeat(121)]) assert.match(validateTestName(name), /1~120자/);
  assert.equal(validateTestName(" 합성 검증 "), ""); assert.notEqual(newTestRequestKey(), newTestRequestKey());
  const requests = []; const controller = createFullValidationController({ api: { runModelProfileTest: async (...args) => { requests.push(args); return {}; } }, onChange() {} });
  const profile = { id: "model", name: "synthetic", provider: "vllm", model_name: "model", profile_fingerprint: "a".repeat(64) };
  controller.open(profile);
  const generatedKey = controller.getState().idempotencyKey;
  assert.equal(await controller.submit(true), true); assert.equal(requests.length, 1);
  assert.equal(requests[0][4].name, generatedKey); assert.equal(requests[0][4].idempotency_key, generatedKey);
  controller.open(profile); controller.setName("a".repeat(121));
  assert.equal(await controller.submit(true), false); assert.equal(requests.length, 1); assert.match(controller.getState().error, /테스트명/);
  controller.setName("합성 150건 1차"); assert.equal(await controller.submit(true), true); assert.equal(requests.length, 2);
  assert.equal(requests[1][4].name, "합성 150건 1차"); controller.dispose();
  assert.deepEqual(modelTestPayload("full", true, "a".repeat(64), { name: " 합성 150건 ", idempotency_key: "synthetic-key" }), { mode: "full", include_dataset: true, expected_profile_fingerprint: "a".repeat(64), name: "합성 150건", idempotency_key: "synthetic-key" });
});

test("named test APIs separate metadata from event inputs and never invoke production endpoints", { concurrency: false }, async () => {
  const saved = globalThis.fetch; const calls = []; globalThis.fetch = async (path, options) => { calls.push({ path, options }); return new Response(JSON.stringify({ id: "synthetic-run" }), { status: 200 }); };
  try {
    const event = { event_id: "synthetic", payload: "GET / HTTP/1.1\r\n\r\n" };
    await api.createTestRun({ name: "직접 검증", idempotency_key: "synthetic-one", event, expected_verdict: "true_positive", difficulty: "easy" });
    await api.uploadTestRun(new File(["[]"], "synthetic.json"), "파일 검증", "synthetic-two");
    await api.testRuns({ q: "검증", offset: 10, limit: 10 }); await api.testRun("run/id", { difficulty: "easy", verdict: "true_positive" });
    assert.equal(calls[0].path, "/api/v1/test-runs"); assert.deepEqual(JSON.parse(calls[0].options.body).event, event);
    assert.equal(calls[1].path, "/api/v1/test-runs/uploads"); assert.deepEqual([...calls[1].options.body.keys()], ["name", "idempotency_key", "file"]); assert.equal(calls[1].options.body.get("name"), "파일 검증");
    assert.match(calls[3].path, /test-runs\/run%2Fid\?/); assert.ok(calls.every(({ path }) => !path.includes("/analyses")));
  } finally { globalThis.fetch = saved; }
});
