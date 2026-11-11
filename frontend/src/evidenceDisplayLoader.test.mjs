import assert from "node:assert/strict";
import test from "node:test";
import { createDetailLoader } from "./detailLoader.js";
import { rememberEvidenceMeta } from "./analysisEvidenceMeta.js";

const flush = async () => { for (let index = 0; index < 30; index++) await Promise.resolve(); };
function harness() {
  let now = 0, sequence = 0, state;
  const timers = new Map(), requests = [];
  const setTimer = (fn, delay) => { const id = ++sequence; timers.set(id, { fn, at: now + delay }); return id; };
  const clearTimer = id => timers.delete(id);
  const api = Object.fromEntries(["analysis", "evaluationLabels", "evidenceDisplay", "evidenceRelations", "agentRuns"].map(name => [name, (id, options) => new Promise((resolve, reject) => requests.push({ name, id, resolve, reject, signal: options.signal, settled: false }))]));
  const loader = createDetailLoader({ id: "synthetic", api, setTimer, clearTimer, onChange: value => { state = value; } });
  const h = { loader, requests, get state() { return state; }, get timers() { return timers.size; },
    calls(name) { return requests.filter(request => request.name === name); },
    async answer(name, value) { const request = requests.find(request => request.name === name && !request.settled && !request.signal.aborted); assert.ok(request, name); request.settled = true; request.resolve(value); await flush(); },
    async advance(delay) { const end = now + delay; for (;;) { const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a,b) => a[1].at-b[1].at)[0]; if (!next) break; timers.delete(next[0]); now = next[1].at; next[1].fn(); await flush(); } now = end; await flush(); },
  };
  loader.start(); loader.setEvidenceVisible(true);
  return h;
}
const completed = () => ({ id: "synthetic", status: "completed", result: { schema_version: "waf-analysis-v3", evidence: [] } });
const tagged = (value, status) => { rememberEvidenceMeta(value, { view: "core-v1", status }); return value; };

test("completed core analysis never fetches an original display plan, relations or full Agent I/O", async () => {
  const h = harness(), value = tagged(completed(), "completed"), before = JSON.stringify(value);
  await h.answer("analysis", value); await h.answer("evaluationLabels", { items: [] });
  await h.advance(60000);
  assert.equal(h.state.evidenceMeta.status, "completed");
  assert.equal(h.state.detail, value); assert.equal(JSON.stringify(value), before);
  assert.equal(h.calls("analysis").length, 1); assert.equal(h.calls("evidenceDisplay").length, 0);
  assert.equal(h.calls("agentRuns").length, 0); assert.equal(h.calls("evidenceRelations").length, 0);
  assert.equal(h.timers, 0); h.loader.dispose();
});

for (const status of ["completed", "skipped", "fallback"]) test(`${status} preserves the authoritative received list without a second endpoint`, async () => {
  const h = harness(); await h.answer("analysis", tagged(completed(), status)); await h.answer("evaluationLabels", { items: [] });
  await h.advance(60000); assert.equal(h.calls("evidenceDisplay").length, 0); assert.equal(h.state.detail.status, "completed"); assert.equal(h.state.evidenceMeta.status, status); assert.equal(h.timers, 0); h.loader.dispose();
});

test("pending core evidence polls the analysis endpoint until completed without exposing an old result", async () => {
  const h = harness(); await h.answer("analysis", tagged({ id: "synthetic", status: "processing", result: null }, "pending")); await h.answer("evaluationLabels", { items: [] });
  assert.equal(h.state.detail.result, null); assert.equal(h.state.evidenceMeta.status, "pending");
  await h.advance(3000); await h.answer("analysis", tagged(completed(), "completed")); await h.answer("evaluationLabels", { items: [] });
  await h.advance(60000); assert.equal(h.calls("analysis").length, 2); assert.equal(h.calls("evidenceDisplay").length, 0); assert.equal(h.timers, 0); h.loader.dispose();
});

test("switching evidence, report and execution views never loads a second source of truth", async () => {
  const h = harness(); await h.answer("analysis", tagged(completed(), "completed")); await h.answer("evaluationLabels", { items: [] });
  h.loader.setEvidenceVisible(false); h.loader.setEvidenceVisible(true); h.loader.setEvidenceVisible(false); h.loader.setEvidenceVisible(true);
  assert.equal(h.calls("evidenceDisplay").length, 0); assert.equal(h.calls("evidenceRelations").length, 0);
  h.loader.refreshRelations(); await h.answer("analysis", tagged(completed(), "skipped"));
  assert.equal(h.calls("analysis").length, 2); h.loader.dispose(); assert.equal(h.timers, 0);
});

test("missing header compatibility still displays only the received v3 list", async () => {
  const h = harness(); await h.answer("analysis", completed()); await h.answer("evaluationLabels", { items: [] });
  await h.advance(60000); assert.equal(h.state.evidenceMeta.view, "core-v1");
  assert.equal(h.calls("evidenceDisplay").length, 0); assert.equal(h.calls("agentRuns").length, 0); h.loader.dispose();
});

test("legacy result does not request a new display record", async () => {
  const h = harness(); await h.answer("analysis", { ...completed(), result: { schema_version: "waf-analysis-v2" } }); await h.answer("evaluationLabels", { items: [] });
  assert.equal(h.calls("evidenceDisplay").length, 0); h.loader.dispose();
});
