import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { comparisonExclusions, comparisonWarnings, initialTestComparisonState, testComparisonChange, testComparisonQuery, tokenCountText, watchComparisonRead } from "./testComparison.js";
import { testRunQuery } from "./testRuns.js";

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./TestRunComparison.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), Date, AbortController });
const { default: Comparison, TestComparisonItems, TestComparisonMetrics, TestComparisonPerformance, TestComparisonResult } = module.exports;
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));
const timing = () => ({ count: 2, missing_count: 1, sum_ms: 3000, mean_ms: 1500, p50_ms: 1000, p95_ms: 2000 });
const tokens = () => ({ known_sum: 100, measured_steps: 1, missing_steps: 1 });
const performance = () => ({ processing_ms: timing(), llm_step_ms: timing(), tokens: { input_tokens: tokens(), output_tokens: tokens(), total_tokens: tokens() }, llm_steps: 2, output_repair_steps: 1, missing_agent_histories: 1 });
const summary = () => ({ total: 10, binary_decided: 8, outcomes: { false_negative: 1, false_positive: 1, abstained: 1, expected_abstention_match: 1, expected_abstention_mismatch: 0 }, metrics: { accuracy: .75, precision: .8, recall: .8, f1: .8, coverage: .8, abstention_rate: .2 } });
const dataFixture = () => ({ baseline: { id: "baseline", name: "합성 기준", prompt_version: "policy-1" }, candidate: { id: "candidate", name: "합성 후보", prompt_version: "policy-2" }, baseline_evaluation: summary(), candidate_evaluation: summary(), counts: { accepted_pairs: 12, comparable_pairs: 10, changed: 3, improved: 2, regressed: 1, exclusions: { reference_missing: 2 } }, warnings: ["comparison_is_not_causal_proof", "token_usage_incomplete"], performance: { scope: "comparable_pairs_all_recorded_attempts", baseline: performance(), candidate: performance() }, items: [], total_items: 0, limit: 25, offset: 0 });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

test("comparison state isolates search, selection and item pagination from main test filters", () => {
  const start = initialTestComparisonState(), second = initialTestComparisonState(); assert.notEqual(start.query, second.query); assert.equal(start.baselineId, ""); assert.equal(start.open, false);
  let state = testComparisonChange(start, { type: "open", value: true });
  state = testComparisonChange(state, { type: "draft", value: " 합성 기준 " }); state = testComparisonChange(state, { type: "search" });
  state = testComparisonChange(state, { type: "baseline", id: "baseline" }); state = testComparisonChange(state, { type: "page", direction: 1 });
  assert.equal(state.offset, 25); assert.equal(state.query.q, "합성 기준");
  const rowFilter = testComparisonChange(state, { type: "changes", value: true }); assert.equal(rowFilter.offset, 0); assert.equal(rowFilter.baselineId, "baseline"); assert.equal(rowFilter.query, state.query);
  const closed = testComparisonChange(rowFilter, { type: "open", value: false }); assert.equal(closed.baselineId, "baseline"); assert.equal(closed.queryText, " 합성 기준 "); assert.equal(closed.changes_only, true);
  const filters = { difficulty: "hard", offset: 50, comparison: closed }; assert.deepEqual(testRunQuery(filters), { limit: 25, offset: 50, difficulty: "hard" });
  assert.deepEqual(testComparisonQuery(closed), { baseline_id: "baseline", limit: 25, offset: 0, changes_only: true });
  assert.equal(start.queryText, ""); assert.equal(start.baselineId, "");
});

test("comparison starts folded without choosing a baseline or issuing an execution request", () => {
  const html = render(Comparison, { candidateId: "candidate", onOpen() {} });
  assert.match(html, /다른 테스트와 비교/); assert.doesNotMatch(html, /<details[^>]+open=|비교하는 중|분석 시작|프롬프트.*적용/);
  const open = render(Comparison, { candidateId: "candidate", state: { ...initialTestComparisonState(), open: true }, embedded: true, onStateChange() {}, onOpen() {} });
  assert.match(open, /비교 기준을 선택하면/); assert.match(open, /같은 입력·답안의 저장된 결과만 비교/); assert.match(open, /모델·지침 변경만의 효과를 입증하는 실험은 아닙니다/); assert.doesNotMatch(open, /비교 실행 설명|두 실행의 동일 문항을 비교하는 중|<details/);
});

test("comparison display uses whole paired cohort metrics even when changed-only page is empty", () => {
  const data = dataFixture(); const html = render(TestComparisonResult, { data, state: { ...initialTestComparisonState(), changes_only: true }, onChange() {}, onOpen() {}, onRefresh() {} });
  assert.match(html, /공통 접수 12쌍/); assert.match(html, /평가 가능한 10쌍/); assert.match(html, /75.0%/); assert.match(html, /미탐 방향/); assert.match(html, /기대 보류 일치/);
  assert.match(html, /답안 일치로 변경 2건/); assert.match(html, /답안 불일치로 변경 1건/); assert.match(html, /접수 당시 참고 답안 없음: 2건/);
  assert.match(html, /전체 비교 분모를 바꾸지 않습니다/); assert.match(html, /난이도·유형 필터는 이 비교에 적용되지 않습니다/);
  assert.match(html, /비교 조건 설명/); assert.match(html, /해당하는 비교 문항이 없습니다/);
  const tree = TestComparisonResult({ data, state: { ...initialTestComparisonState(), changes_only: true }, onChange() {}, onOpen() {}, onRefresh() {} });
  const helpText = node => Array.isArray(node) ? node.map(helpText).join(" ") : node && typeof node === "object" ? helpText(node.props?.children) : typeof node === "string" ? node : "";
  assert.match(helpText(tree), /프롬프트 변경 때문에 발생했다고 단정할 수 없습니다/);
  assert.equal(data.baseline_evaluation.total, 10);
});

test("comparison metrics keep undefined denominators distinct and expose accessible metric help", () => {
  const data = dataFixture(); data.candidate_evaluation.metrics.accuracy = null;
  const html = render(TestComparisonMetrics, { data });
  assert.match(html, /75.0%/); assert.match(html, /계산 불가/);
  for (const label of ["Accuracy", "Precision", "Recall", "F1 score", "판정 커버리지", "판정 보류율"]) assert.ok(html.includes(`aria-label="${label} 설명"`));
});

test("comparison conditions have no empty help while real and unknown warnings remain available", () => {
  for (const warnings of [undefined, []]) {
    const data = { ...dataFixture(), warnings };
    const html = render(TestComparisonResult, { data, state: initialTestComparisonState(), onChange() {}, onOpen() {}, onRefresh() {} });
    assert.doesNotMatch(html, /비교 조건 설명|비교 조건 0개/);
  }
  const html = render(TestComparisonResult, { data: { ...dataFixture(), warnings: ["future_warning"] }, state: initialTestComparisonState(), onChange() {}, onOpen() {}, onRefresh() {} });
  assert.match(html, /비교 조건 1개/); assert.match(html, /비교 조건 설명/);
});

test("token accounting labels complete-stage sums, partial measurement and absent histories", () => {
  assert.equal(tokenCountText({ known_sum: 0, measured_steps: 0, missing_steps: 2 }), "미측정");
  assert.equal(tokenCountText({ known_sum: 0, measured_steps: 1, missing_steps: 0 }), "0");
  assert.equal(tokenCountText({ known_sum: 5, measured_steps: 1, missing_steps: 1 }), "5 · 부분 측정");
  assert.equal(tokenCountText({ known_sum: 5, measured_steps: 1, missing_steps: 0 }, 1), "5 · 부분 측정");
  const left = performance(); left.tokens.input_tokens = { known_sum: 0, measured_steps: 0, missing_steps: 2 };
  const html = render(TestComparisonPerformance, { performance: { baseline: left, candidate: performance() } });
  assert.match(html, /완전 측정 단계 합계/); assert.match(html, /완전 측정 0 · 미측정 2단계/); assert.match(html, /미측정/); assert.match(html, /부분 측정/); assert.match(html, /실행 이력 미기록 문항/);
  assert.match(html, /두 실행 전체의 비용 또는 청구량은 아닙니다/);
  for (const label of ["처리 시간과 사용량", "문항 처리 시간 · 평균", "문항 처리 시간 · p50", "문항 처리 시간 · p95"]) assert.ok(html.includes(`aria-label="${label} 설명"`));
});

test("comparison case labels are inert text and both analysis links use immutable IDs", () => {
  const item = { event_id: "evt", case_name: "<script>synthetic</script>", baseline_analysis_id: "baseline-analysis", candidate_analysis_id: "candidate-analysis", baseline_verdict: "false_positive", candidate_verdict: "true_positive", reference_verdict: "true_positive", baseline_outcome: "false_negative", candidate_outcome: "match", comparison_status: "comparable", change: "improved" };
  const html = render(TestComparisonItems, { items: [item], onOpen() {} });
  assert.match(html, /&lt;script&gt;synthetic/); assert.doesNotMatch(html, /<script/); assert.match(html, /기준 분석 상세/); assert.match(html, /후보 분석 상세/);
  const buttons = node => Array.isArray(node) ? node.flatMap(buttons) : node && typeof node === "object" ? [...(node.type === "button" ? [node] : []), ...buttons(node.props?.children)] : [];
  const opened = []; buttons(TestComparisonItems({ items: [item], onOpen: id => opened.push(id) })).forEach(button => button.props.onClick());
  assert.deepEqual(opened, ["baseline-analysis", "candidate-analysis"]);
});

test("comparison read cancels stale success and errors without replaying any mutation", async () => {
  for (const fail of [false, true]) {
    const pending = deferred(), seen = []; let signal;
    const cancel = watchComparisonRead({ read: options => { signal = options.signal; return pending.promise; }, onUpdate: value => seen.push(value) });
    await flush(); cancel(); assert.equal(signal.aborted, true);
    if (fail) pending.reject(new Error("private response")); else pending.resolve(dataFixture());
    await flush(); assert.equal(seen.length, 0);
  }
  let calls = 0; const cancel = watchComparisonRead({ read: () => { ++calls; }, onUpdate() {} }); cancel(); await flush(); assert.equal(calls, 0);
});

test("comparison authorization errors are sanitized and propagate session expiry once", async () => {
  let expired = 0, state;
  watchComparisonRead({ read: async () => { throw Object.assign(new Error("PRIVATE-RESPONSE"), { status: 401 }); }, onUpdate: value => { state = value; }, onUnauthorized: () => ++expired });
  await flush(); await flush(); assert.equal(expired, 1); assert.match(state.error, /로그인 세션/); assert.doesNotMatch(JSON.stringify(state), /PRIVATE/);
});

test("every current backend exclusion and conditional warning has an analyst explanation", () => {
  for (const code of ["baseline_missing", "candidate_missing", "analysis_missing", "item_identity_mismatch", "fingerprint_missing", "input_mismatch", "reference_missing", "reference_mismatch", "both_not_evaluable", "baseline_not_evaluable", "candidate_not_evaluable"]) assert.ok(comparisonExclusions[code]);
  for (const code of ["run_source_system_differs", "prompt_budget_may_change_submitted_input", "comparison_is_not_causal_proof", "performance_excludes_noncomparable_analyses", "recorded_tokens_are_not_billing_total", "duplicate_event_rows_ignored", "reference_source_ref_different", "case_metadata_different", "model_configuration_unknown", "model_configuration_different", "execution_mode_unknown", "execution_mode_different", "verifier_threshold_unknown", "verifier_threshold_different", "input_schema_unknown", "input_schema_different", "fixed_rules_unknown", "fixed_rules_different", "input_truncation_present", "input_truncation_different", "execution_identity_unknown", "execution_identity_mismatch", "executed_verifier_threshold_unknown", "executed_verifier_threshold_mismatch", "token_usage_incomplete", "timing_incomplete"]) assert.ok(comparisonWarnings[code]);
});

test("comparison API performs only a no-store GET with paired row pagination", async () => {
  const previous = globalThis.fetch; let request;
  globalThis.fetch = async (path, options) => { request = { path, options }; return { status: 200, ok: true, json: async () => dataFixture() }; };
  try {
    await api.compareTestRuns("candidate/id", { baseline_id: "baseline", offset: 25, limit: 25, changes_only: true });
    const url = new URL(request.path, "https://synthetic.invalid"); assert.equal(url.pathname, "/api/v1/test-runs/candidate%2Fid/comparison"); assert.equal(url.searchParams.get("baseline_id"), "baseline"); assert.equal(url.searchParams.get("changes_only"), "true");
    assert.equal(request.options.cache, "no-store"); assert.equal(request.options.method, undefined); assert.equal(request.options.body, undefined);
  } finally { globalThis.fetch = previous; }
});
