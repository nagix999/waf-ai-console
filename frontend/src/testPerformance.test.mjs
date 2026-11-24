import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { durationText, performanceCount, performanceFieldLabel, performanceIsRunning, performanceIssueLabel, performanceMetric, performanceSummary, retryFraction, testPerformanceScope, topPerformanceIssues } from "./testPerformance.js";

const id = "11111111-1111-4111-8111-111111111111";
test("flat evidence link errors have fixed KR and EN labels", () => {
  for (const code of ["evidence_id_repeated", "evidence_link_repeated", "evidence_link_unknown", "evidence_link_self", "evidence_link_cycle", "decision_evidence_unselected", "decision_evidence_repeated"]) {
    assert.notEqual(performanceIssueLabel(code), "기타 출력 검증 오류");
    assert.notEqual(performanceIssueLabel(code, (ko, en) => en), "Other output validation issue");
    assert.ok(!performanceIssueLabel(code).includes(code));
  }
});
test("decision review errors and retained evidence use only fixed KR and EN labels", () => {
  for (const code of ["decision_review_context_invalid", "review_evidence_repeated", "review_evidence_not_eligible", "review_evidence_omitted", "review_evidence_dependency_missing"]) {
    assert.notEqual(performanceIssueLabel(code), "기타 출력 검증 오류");
    assert.notEqual(performanceIssueLabel(code, (ko, en) => en), "Other output validation issue");
    assert.ok(!performanceIssueLabel(code).includes(code));
  }
  assert.equal(performanceFieldLabel("retained_evidence_ids"), "재검토 근거");
  assert.equal(performanceFieldLabel("retained_evidence_ids", (ko, en) => en), "Review evidence");
});
const duration = (mean = 12300, recorded = 10) => ({ recorded, unrecorded: 0, reported_sum_ms: mean * recorded, min_ms: mean / 2, p50_ms: mean, p95_ms: mean * 2, max_ms: mean * 3, mean_ms: mean });
const tokens = mean => ({ recorded: 10, unrecorded: 0, sum: mean * 10, mean, p50: mean, p95: mean * 2, max: mean * 3 });
const role = () => ({ accounting: { recorded_cases: 10, unrecorded_cases: 0, reported_output_attempts: 13, reported_output_retries: 3, reported_citation_repairs: 1, cases_with_output_retries: 2, cases_with_citation_repairs: 1 },
  first_attempt_ms: duration(), output_retry_ms: duration(2000), citation_repair_ms: duration(1000), slot_wait_ms: duration(400), invocation_ms: duration(15300),
  tokens: { input_tokens: tokens(1400), output_tokens: tokens(850), first_attempt_output_tokens: tokens(600) },
  output_failures: { recorded_cases: 10, unrecorded_cases: 0, cases_with_output_failure: 2, completed_cases_with_output_failure: 2, validation_issues: [{ field: "evidence", type: "evidence_support_conflict", attempts: 3 }] } });
const fixture = () => ({ version: "test-performance-summary-v1", test_run_id: id, cases: 12, statuses: { completed: 12 },
  timings: { result_ready_elapsed_ms: duration(31000), analysis_processing_ms: duration(20000), analysis_queue_ms: duration(3000), decision_elapsed_ms: duration(23000) },
  phases: { llm_primary: { entered_cases: 12, not_observed_cases: 0, states: { completed: 12 } }, llm_verifier: { entered_cases: 10, not_observed_cases: 2, states: { completed: 10 } } },
  roles: { primary: role(), verifier: role() }, display: { states: { completed: 12 }, no_job_cases: 0, queue_ms: duration(2000), processing_ms: duration(6000) } });

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./TestPerformanceSummary.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} };
runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date, URL, setTimeout, clearTimeout, AbortController });
const render = (summary, extra = {}) => renderToStaticMarkup(createElement(module.exports.PerformanceSummaryContent, { summary, ...extra }));

test("output limit, validation and timeout attempts are distinct from affected case counts", () => {
  const source = fixture();
  source.roles.primary.output_failures.attempt_outcomes = { output_validation: 4, output_limit: 3, timeout: 1 };
  const html = render(source);
  for (const label of ["출력 검증 실패 4", "출력 한도 초과 3", "응답 시간 초과 1", "시도 기준", "기록 문항 수가 다르면"]) assert.ok(html.includes(label), label);
  const english = render(source, { words: (ko, en) => en });
  for (const label of ["Validation failure 4", "Output limit 3", "Response timeout 1"]) assert.ok(english.includes(label), label);
});

test("performance uses case scope without grouping, search, sort or pagination", () => {
  const case_filter = { mode: "and", rules: [{ field: "signature", operator: "contains", value: "PRIVATE_FILTER" }] };
  const scope = testPerformanceScope({ evaluation_id: id, difficulty_missing: false, test_category_missing: true, case_filter, q: "PRIVATE_GROUP", group_by: "signature", sort_by: "cases", offset: 100, limit: 10, status: "failed", payload: "PRIVATE_EVENT" });
  assert.deepEqual(scope, { evaluation_id: id, difficulty_missing: false, test_category_missing: true, case_filter });
  assert.deepEqual(testPerformanceScope({ reference_basis: "latest", difficulty: "", test_category: undefined, case_filter: null }), { reference_basis: "latest" });
  assert.equal(JSON.stringify(testPerformanceScope({ ...scope, q: "changed", offset: 500 })), JSON.stringify(scope));
});

test("performance API always posts a closed scope in an authenticated uncached body and preserves cancellation", async () => {
  const original = globalThis.fetch, controller = new AbortController(); let request;
  globalThis.fetch = async (path, options) => { request = { path, options }; return new Response(JSON.stringify(fixture()), { headers: { "content-type": "application/json" } }); };
  try {
    const result = await api.testPerformance(id, { reference_basis: "latest", case_filter: { rules: [] }, q: "PRIVATE_GROUP", offset: 10, payload: "PRIVATE_EVENT" }, { signal: controller.signal });
    assert.equal(result.test_run_id, id); assert.equal(request.path, `/api/v1/test-runs/${id}/performance-summary`);
    assert.equal(request.options.method, "POST"); assert.equal(request.options.cache, "no-store"); assert.equal(request.options.credentials, "include"); assert.strictEqual(request.options.signal, controller.signal);
    assert.deepEqual(JSON.parse(request.options.body), { reference_basis: "latest", case_filter: { rules: [] } });
    assert.doesNotMatch(request.path + request.options.body, /PRIVATE_GROUP|PRIVATE_EVENT|offset/);
    globalThis.fetch = async () => new Response("{}", { status: 401, headers: { "content-type": "application/json" } });
    await assert.rejects(api.testPerformance(id), error => error.status === 401);
  } finally { globalThis.fetch = original; }
});

test("missing timing and token telemetry stays unknown and a recorded zero stays zero", () => {
  for (const value of [null, undefined, false, true, "0", -1, 1.5, NaN, Infinity, {}, [], 2 ** 53]) assert.equal(performanceCount(value), null);
  assert.equal(performanceCount(0), 0); assert.equal(durationText(0), "0.0초"); assert.equal(durationText(60000, (ko, en) => en), "1.0 min"); assert.equal(durationText(null), "—");
  assert.equal(performanceMetric({ mean_ms: false, recorded: "0", reported_sum_ms: 0 }).mean_ms, null);
  const raw = { version: "test-performance-summary-v1", cases: 0, display: {} }, source = JSON.stringify(raw), summary = performanceSummary(raw);
  assert.equal(summary.cases, 0); assert.equal(summary.display.states.pending, null); assert.equal(summary.roles.primary.tokens.output_tokens.mean, null);
  assert.equal(summary.roles.primary.output_retry_ms.mean_ms, null); assert.equal(JSON.stringify(raw), source);
  assert.equal(summary.roles.primary.decision_review_ms.mean_ms, null);
  assert.equal(summary.roles.primary.accounting.reported_decision_reviews, null);
  assert.equal(summary.roles.primary.accounting.cases_with_decision_reviews, null);
  assert.equal(performanceSummary({ version: "future" }), null);
  assert.doesNotMatch(render(raw), /NaN|Infinity|0\.0초/);
});

test("optional decision review telemetry preserves missing values, recorded zero and independent case counts", () => {
  const source = fixture();
  source.roles.primary.accounting.reported_decision_reviews = 3;
  source.roles.primary.accounting.cases_with_decision_reviews = 2;
  source.roles.primary.decision_review_ms = duration(1500, 2);
  source.roles.verifier.accounting.reported_decision_reviews = 0;
  source.roles.verifier.accounting.cases_with_decision_reviews = 0;
  source.roles.verifier.decision_review_ms = duration(0);
  const before = JSON.stringify(source), normalized = performanceSummary(source);
  assert.equal(normalized.roles.primary.accounting.reported_decision_reviews, 3);
  assert.equal(normalized.roles.primary.accounting.cases_with_decision_reviews, 2);
  assert.equal(normalized.roles.primary.decision_review_ms.mean_ms, 1500);
  assert.equal(normalized.roles.verifier.accounting.reported_decision_reviews, 0);
  assert.equal(normalized.roles.verifier.decision_review_ms.mean_ms, 0);
  assert.equal(normalized.roles.legacy_evidence_editor.decision_review_ms.mean_ms, null);
  assert.equal(JSON.stringify(source), before);
  source.roles.primary.accounting.cases_with_decision_reviews = "PRIVATE_CASE_COUNT";
  source.roles.primary.accounting.reported_decision_reviews = "PRIVATE_ATTEMPT_COUNT";
  source.roles.primary.decision_review_ms.mean_ms = "PRIVATE_REVIEW_TIME";
  assert.doesNotMatch(JSON.stringify(performanceSummary(source)), /PRIVATE_/);
});

test("decision reviews appear only for affected roles inside existing cells and remain distinct from citation repair", () => {
  const source = fixture();
  assert.doesNotMatch(render(source), /근거 재검토/);
  source.roles.primary.accounting.reported_decision_reviews = 3;
  source.roles.primary.accounting.cases_with_decision_reviews = 2;
  source.roles.primary.decision_review_ms = duration(1500, 2);
  source.roles.verifier.accounting.cases_with_decision_reviews = 0;
  source.roles.verifier.decision_review_ms = duration(0);
  const html = render(source), english = render(source, { words: (ko, en) => en });
  assert.match(html, /근거 재검토 2문항/);
  assert.match(html, /근거 재검토 평균 1\.5초/);
  assert.equal((html.match(/근거 재검토/g) || []).length, 2);
  assert.doesNotMatch(html, /근거 재검토 (?:0|3)문항/);
  assert.match(html, /근거 연결 교정 1/);
  assert.match(html, /근거 연결 교정 1\.0초/);
  assert.match(english, /Evidence review 2 cases/);
  assert.match(english, /Evidence review mean 1\.5 s/);
  assert.match(english, /Citation repair 1/);
  assert.doesNotMatch(english, /[가-힣]/);
  assert.equal((html.match(/scope="col"/g) || []).length, (render(fixture()).match(/scope="col"/g) || []).length);
  source.roles.primary.decision_review_ms = undefined;
  assert.match(render(source), /근거 재검토 평균 —/);
  source.roles.primary.accounting.cases_with_decision_reviews = undefined;
  assert.doesNotMatch(render(source), /근거 재검토/);
});

test("review validation issues retain fixed field labels without reflecting arbitrary errors", () => {
  const source = fixture();
  source.roles.primary.output_failures.validation_issues = [
    { field: "retained_evidence_ids", type: "review_evidence_omitted", attempts: 3 },
    { field: "retained_evidence_ids.PRIVATE_FIELD", type: "PRIVATE_ERROR", attempts: 2 },
  ];
  const normalized = performanceSummary(source), html = render(source);
  assert.deepEqual(normalized.roles.primary.output_failures.validation_issues[0], { field: "retained_evidence_ids", type: "review_evidence_omitted", attempts: 3 });
  assert.match(html, /재검토 근거 누락/);
  assert.doesNotMatch(html + JSON.stringify(normalized), /PRIVATE_/);
  assert.doesNotMatch(html, /review_evidence_omitted/);
});

test("retry fraction uses recorded role cases and never treats absent counters as no retries", () => {
  assert.equal(retryFraction(role()), .2);
  assert.equal(retryFraction({ accounting: { cases_with_output_retries: 0, recorded_cases: 12 } }), 0);
  assert.equal(retryFraction({ accounting: { cases_with_output_retries: 0, recorded_cases: 0 } }), null);
  assert.equal(retryFraction({ accounting: { cases_with_output_retries: 13, recorded_cases: 12 } }), null);
  assert.equal(retryFraction({ accounting: { reported_output_retries: 0, recorded_cases: 12 } }), null);
});

test("polling includes pending analysis or presentation work but stops after completion", () => {
  const source = fixture(); assert.equal(performanceIsRunning(source), false);
  source.display.states = { pending: 1, completed: 11 }; assert.equal(performanceIsRunning(source), true);
  source.display.states = { running: 1 }; assert.equal(performanceIsRunning(source), true);
  source.display.states = {}; source.statuses = { processing: 1 }; assert.equal(performanceIsRunning(source), true);
  source.statuses = {}; assert.equal(performanceIsRunning(source), false);
  assert.equal(performanceIsRunning(null), false);
  assert.equal(performanceSummary(fixture()).display.states.pending, 0);
});

test("fixed validation labels and normalization never reflect arbitrary error text or field names", () => {
  const raw = fixture(); raw.roles.primary.output_failures.validation_issues = [{ field: "PRIVATE_PAYLOAD", type: "PRIVATE_ERROR", attempts: 7 }, { field: "evidence", type: "__proto__", attempts: 2 }, { field: "evidence", type: "missing", attempts: "PRIVATE_COUNT" }];
  raw.roles.primary.tokens.output_tokens.mean = "PRIVATE_TOKEN";
  const normalized = performanceSummary(raw), html = render(raw);
  assert.doesNotMatch(JSON.stringify(normalized), /PRIVATE_|__proto__/); assert.doesNotMatch(html, /PRIVATE_|__proto__/);
  assert.match(html, /기타 출력 검증 오류/); assert.match(html, /출력 항목/);
  assert.equal(performanceIssueLabel("evidence_source_repeated"), "근거 원문 중복");
  assert.equal(performanceIssueLabel("json_invalid", (ko, en) => en), "Invalid JSON");
  assert.equal(performanceFieldLabel("constructor"), "출력 항목");
  assert.equal(topPerformanceIssues(normalized, 1)[0].attempts, 7);
});

test("compact tables preserve role denominators, first versus all-attempt tokens and accessible labels", () => {
  const html = render(fixture());
  for (const phrase of ["평균 결과 준비 시간", "결과 준비 시간 P95", "첫 출력량", "전체 출력량", "출력 재시도 문항", "20.0%", "근거 정리", "평균 대기", "출력 문제와 교정", "근거 분류 충돌", "겹치는 단계 시간은 더하지 않습니다", "미기록은 0이 아니며"]) assert.ok(html.includes(phrase), phrase);
  assert.match(html, />600</); assert.match(html, />850</); assert.match(html, /2 \/ 10/);
  assert.match(html, /role="region" aria-label="단계별 처리 시간과 출력량" tabindex="0"/);
  assert.match(html, /scope="col"/); assert.doesNotMatch(html, /PRIVATE_|evidence_support_conflict|llm_primary/);
  const english = render(fixture(), { words: (ko, en) => en });
  for (const phrase of ["Mean result-ready time", "First output tokens", "All output tokens", "Stage recovered", "Conflicting evidence direction"]) assert.ok(english.includes(phrase), phrase);
  assert.doesNotMatch(english, /[가-힣]/);
});
