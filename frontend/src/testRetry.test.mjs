import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { readFileSync } from "node:fs";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterTestRetry, testRetryNotice, testRetryPayload } from "./testRetry.js";
import { api } from "./api.js";

function component(file, name = "default") {
  const code = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} };
  runInNewContext(code, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date });
  return module.exports[name];
}

test("batch requires consent and sends the complete preview, not a page or filter", () => {
  const info = { eligible_ids: Array.from({ length: 36 }, (_, index) => `analysis-${index}`) };
  assert.throws(() => testRetryPayload(info, "fixture-key", false));
  assert.deepEqual(testRetryPayload(info, "fixture-key", true), {
    idempotency_key: "fixture-key", cost_acknowledged: true, analysis_ids: info.eligible_ids });
  for (const ids of [[], ["a", "a"], [null], [""]]) assert.throws(() => testRetryPayload({ eligible_ids: ids }, "fixture-key", true));
});

test("accepted retry switches back to live metrics and all items so polling continues", () => {
  const filters = { difficulty: "hard", test_category: "xss", difficulty_missing: true, test_category_missing: true,
    status: "failed", evaluation_outcome: "failed", cell: "fn", evaluation_id: "saved-evaluation", offset: 50, limit: 25 };
  const next = afterTestRetry(filters);
  assert.equal(next.evaluation_id, "latest"); assert.equal(next.offset, 0); assert.equal(next.limit, 25);
  assert.equal(next.status, ""); assert.equal(next.difficulty, ""); assert.equal(next.cell, "");
  assert.equal(filters.offset, 50);
  assert.match(testRetryNotice({ enqueued: 36, skipped: 0, duplicate: false }), /36건.*자동 반영/);
  assert.match(testRetryNotice({ enqueued: 23, skipped: 13, duplicate: true }), /이미 접수한.*13건.*제외/);
});

test("dialog clearly explains count, original settings, cost and unchanged history", () => {
  const Confirmation = component("./RetryTestFailures.jsx", "TestRetryConfirmation");
  const html = renderToStaticMarkup(createElement(Confirmation, { info: { eligible_count: 36, failed_count: 37,
    eligible_ids: ["private-id"], blocked_counts: { retry_original_profile_changed: 1 }, external_calls: true },
    approved: false, busy: false, onApproval() {}, onSubmit() {} }));
  assert.match(html, /재실행 가능 36건/); assert.match(html, /36건 재실행/); assert.match(html, /disabled/);
  assert.match(html, /현재 페이지와 필터에 관계없이/); assert.match(html, /실패 당시 모델·지침/);
  assert.match(html, /총 문항 수와 저장한 평가 기록은 유지/); assert.match(html, /OpenAI/);
  assert.doesNotMatch(html, /private-id/);
  const empty = renderToStaticMarkup(createElement(Confirmation, { info: { eligible_count: 0, failed_count: 0 } }));
  assert.match(empty, /재실행할 수 있는 실패 항목이 없습니다/); assert.doesNotMatch(empty, /0건 재실행/);
});

test("batch API uses explicit POST payload and preview is read-only", async () => {
  const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, status: 200, json: async () => ({}) }; };
  try {
    await api.testRetryEligibility("fixture/run");
    await api.retryTestFailures("fixture/run", testRetryPayload({ eligible_ids: ["fixture-id"] }, "fixture-key", true));
    assert.match(calls[0].url, /fixture%2Frun\/retry-eligibility$/); assert.equal(calls[0].options.method, undefined);
    assert.match(calls[1].url, /\/retry-failed$/); assert.equal(calls[1].options.method, "POST");
    assert.equal(JSON.parse(calls[1].options.body).cost_acknowledged, true);
  } finally { globalThis.fetch = previous; }
});

test("test detail integrates retries and a successful submission refreshes live results", () => {
  const source = readFileSync(new URL("./TestRuns.jsx", import.meta.url), "utf8");
  assert.match(source, /<RetryTestFailures key=\{id\}/); assert.match(source, /setFilters\?\.\(afterTestRetry\)/);
  assert.match(source, /selection\.clear\(\)/); assert.match(source, /setReload\(value => value \+ 1\)/);
  const Retry = component("./RetryTestFailures.jsx");
  assert.match(renderToStaticMarkup(createElement(Retry, { run: { id: "fixture" }, onSubmitted() {} })), /실패 항목 모두 재실행/);
});
