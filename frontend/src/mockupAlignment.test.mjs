import assert from "node:assert/strict";
import test from "node:test";
import { buildSync } from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

function load(file) {
  const result = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" });
  const module = { exports: {} }; runInNewContext(result.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date }); return module.exports;
}
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));
const { runtimeOutcomes, RuntimeOutcomes, RuntimeStatus } = load("./RuntimeViews.jsx");

test("runtime failure rate uses terminal statuses only, not holds or unfinished requests", () => {
  const counts = { completed: 18, failed: 2, inconclusive: 12, pending: 7, processing: 5 };
  assert.equal(runtimeOutcomes(counts).failureRate, .1);
  assert.equal(runtimeOutcomes(counts).finished, 20);
  const html = render(RuntimeOutcomes, { counts, onFilter() {} });
  assert.match(html, /10.0%/); assert.match(html, /완료·실패 20건/); assert.match(html, /판정 보류는 실패가 아닙니다/);
  assert.doesNotMatch(html, /재시도율|정확도|Cost|GPU/);
});

test("unknown and empty counts never generate a zero-percent chart", () => {
  for (const counts of [null, {}, { completed: 1, failed: null, pending: 0, processing: 0 }, { completed: -1, failed: 0, pending: 0, processing: 0 }]) {
    assert.equal(runtimeOutcomes(counts), null);
    assert.doesNotMatch(render(RuntimeOutcomes, { counts }), /<svg|0.0%/);
  }
  assert.equal(runtimeOutcomes({ completed: 0, failed: 0, pending: 0, processing: 0 }).failureRate, null);
});

test("mockup latency and worker figures are explicitly unmeasured", () => {
  const html = render(RuntimeStatus, { summary: { counts: { completed: 10, failed: 0, pending: 0, processing: 0 }, trend: [] }, onUnauthorized() {} });
  assert.match(html, /미측정/); assert.match(html, /p50/); assert.match(html, /p95/); assert.match(html, /p99/);
  assert.doesNotMatch(html, /4 \/ 4|128ms|Gemma-12B|prod-20241201|0.8s|Notification|Cost|Resource/);
});

test("trace metric cards show only recorded metadata with last-call usage semantics", () => {
  const { StepMetrics } = load("./AgentHistory.jsx");
  const html = render(StepMetrics, { metadata: { model_profile: "<saved-model>", llm_provider: "vllm", usage: { input_tokens: 120, output_tokens: 0 }, secret: "PRIVATE_METADATA" } });
  assert.match(html, /&lt;saved-model&gt;/); assert.match(html, /120/); assert.match(html, />0</); assert.match(html, /마지막 호출/);
  assert.doesNotMatch(html, /PRIVATE_METADATA|<saved-model>/);
  const legacy = render(StepMetrics, { metadata: { usage: "Usage(input=123)" } }); assert.match(legacy, /미기록/); assert.doesNotMatch(legacy, /123/);
  assert.equal(render(StepMetrics, {}), "");
});
