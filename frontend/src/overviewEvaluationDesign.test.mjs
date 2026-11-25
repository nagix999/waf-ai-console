import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("./R3Overview.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
const module = { exports: {} };
runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, AbortController });
const words = (ko, en) => en;
const render = (component, props) => renderToStaticMarkup(createElement(module.exports[component], { words, ...props }));

test("dashboard status distribution uses server counts without inventing verdict metrics", () => {
  const runtime = { request_count: 17, outcome_summary: { completed: 9, failed: 2, processing: 3, pending: 2, canceled: 1 } };
  const before = JSON.stringify(runtime);
  const html = render("RuntimeDistribution", { runtime });
  for (const [label, number] of [["Completed", 9], ["Failed", 2], ["Processing", 3], ["Queued", 2], ["Stopped", 1]]) assert.match(html, new RegExp(`${label}</dt><dd>${number}</dd>`));
  assert.doesNotMatch(html, /true_positive|false_positive|Accuracy/);
  assert.equal(JSON.stringify(runtime), before);
});

test("dashboard request chart exposes each cohort count and its real admission-time basis", () => {
  const runtime = { request_volume_series: [{ from: "2026-11-25T00:00:00Z", completed: 3, failed: 1, processing: 2, pending: 4, canceled: 0 }] };
  const html = render("RuntimeVolume", { runtime });
  assert.match(html, /Current status of requests admitted over the last 24 hours/);
  assert.match(html, /Completed 3, Failed 1, Processing 2, Queued 4, Stopped 0/);
  assert.match(html, /not by completion time/);
  assert.match(html, /height:30%/);
  assert.match(html, /height:40%/);
});

test("empty and missing dashboard values are not synthesized as successful requests", () => {
  assert.match(render("OverviewMetric", { label: "P95", note: "Includes failed records" }), /<strong>—<\/strong>/);
  assert.match(render("RuntimeDistribution", { runtime: null }), /<strong>—<\/strong>/);
  assert.match(render("RuntimeVolume", { runtime: null }), /Loading request volume/);
  assert.doesNotMatch(render("RuntimeVolume", { runtime: { request_volume_series: [] } }), /NaN|Infinity/);
  assert.match(render("RuntimeVolume", { runtime: { request_count: 0, request_volume_series: [] } }), /No requests were admitted/);
  assert.match(render("RuntimeVolume", { runtime: { request_count: 3, request_volume_series: [] } }), /Hourly request data is not available/);
  assert.doesNotMatch(render("RuntimeVolume", { runtime: { request_count: 3, request_volume_series: [] } }), /Loading request volume/);
});

test("live verdict donut uses the existing dashboard's three verdict counts, not execution statuses", () => {
  const counts = { total: 12, completed: 9, failed: 1, pending: 2, processing: 0, true_positive: 3, false_positive: 4, inconclusive: 2 };
  const before = JSON.stringify(counts);
  const html = render("HomeVerdictDistribution", { counts });
  assert.match(html, /True positive 3, False positive 4, Inconclusive 2/);
  assert.match(html, /<strong>9<\/strong>/);
  assert.match(html, /33%/); assert.match(html, /44%/); assert.match(html, /22%/);
  assert.doesNotMatch(html, /Execution failures|Queued/);
  assert.equal(JSON.stringify(counts), before);
});

test("home request volume uses the dashboard admission-day cohorts without filling invented hourly buckets", () => {
  const summary = { trend: [{ date: "2026-11-24", total: 0 }, { date: "2026-11-25", total: 7 }] };
  const html = render("HomeRequestVolume", { summary });
  assert.match(html, /Production requests by admission day/);
  assert.match(html, /2026-11-24: 0/); assert.match(html, /2026-11-25: 7/);
  assert.match(html, /retries excluded/); assert.match(html, /admission dates in UTC/);
  assert.match(html, /height:100%/); assert.doesNotMatch(html, /hourly|NaN|Infinity/);
  assert.match(render("HomeVerdictDistribution", { counts: null }), /<strong>—<\/strong>/);
});
