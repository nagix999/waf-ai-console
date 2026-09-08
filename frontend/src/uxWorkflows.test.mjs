import assert from "node:assert/strict";
import test from "node:test";
import { api } from "./api.js";
import { autoTestName, emptySingleTest, singleTestEvent } from "./testRuns.js";
import { dashboardAnalysisQuery, metricSegments } from "./dashboardView.js";
import { retryError, retryPayload } from "./analysisRetry.js";

test("optional test names use one stable request ID and all sample inputs remain empty", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  assert.equal(autoTestName("  ", id), id);
  assert.equal(autoTestName(" name ", id), "name");
  const form = emptySingleTest();
  assert.ok(Object.values(form).every(value => value === ""));
  const event = singleTestEvent({ ...form, payload: "GET /user HTTP/1.1\r\n\r\n", src_port: "0" }, id);
  assert.equal(event.payload, "GET /user HTTP/1.1\r\n\r\n");
  assert.equal(event.event_id, `event-${id}`);
  assert.equal(event.src_port, 0);
  assert.ok(!Object.hasOwn(event, "dest_port"));
  assert.ok(!Object.hasOwn(event, "signature"));
  assert.equal(form.event_id, "");
});

test("dashboard recent results use precisely the chart window and key", () => {
  const window = { created_from: "2026-09-01T00:00:00Z", created_to: "2026-09-08T00:00:00Z" };
  assert.equal(dashboardAnalysisQuery(null), null);
  assert.deepEqual(dashboardAnalysisQuery({ window }, "key-1"), { analysis_purpose: "production", limit: 10, offset: 0, ...window, service_api_key_id: "key-1" });
  assert.ok(!Object.hasOwn(dashboardAnalysisQuery({ window }), "service_api_key_id"));
});

test("missing metric days break graph lines instead of becoming zeros", () => {
  const trend = [0, null, 1, .5, NaN, undefined, -1].map((value, index) => ({ date: `day-${index}`, evaluation_summary: { metrics: { accuracy: value } } }));
  const segments = metricSegments(trend, "accuracy");
  assert.deepEqual(segments.map(points => points.map(point => point.value)), [[0], [1, .5]]);
  assert.deepEqual(metricSegments([], "accuracy"), []);
  assert.equal(metricSegments([trend[0]], "accuracy")[0][0].x, 311);
});

test("rerun payload requires explicit confirmation and backend error text never reaches UI", () => {
  assert.throws(() => retryPayload("request-1234", false));
  assert.throws(() => retryPayload("short", true));
  assert.deepEqual(retryPayload("request-1234", true), { idempotency_key: "request-1234", cost_acknowledged: true });
  assert.ok(!retryError("<img src=x onerror=alert(1)>").includes("<img"));
  assert.match(retryError("retry_original_profile_changed"), /같은 조건/);
  assert.match(retryError("retry_event_reference_contamination"), /답안/);
});

test("key dashboard and rerun API bindings preserve scope, cache and explicit writes", async () => {
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, status: 200, json: async () => ({}) }; };
  try {
    await api.dashboard(30, {}, "key-1");
    await api.retryEligibility("analysis-1");
    await api.retryAnalysis("analysis-1", retryPayload("request-1234", true));
    assert.equal(calls[0].url, "/api/v1/dashboard/summary?days=30&service_api_key_id=key-1");
    assert.equal(calls[1].url, "/api/v1/analyses/analysis-1/retry-eligibility");
    assert.equal(calls[1].options.method, undefined);
    assert.equal(calls[2].options.method, "POST");
    assert.ok(calls.every(call => call.options.credentials === "include" && call.options.cache === "no-store"));
  } finally { globalThis.fetch = original; }
});
