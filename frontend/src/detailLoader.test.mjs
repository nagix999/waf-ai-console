import test from "node:test";
import assert from "node:assert/strict";
import { createDetailLoader } from "./detailLoader.js";

const flush = async () => { for (let index = 0; index < 20; index += 1) await Promise.resolve(); };

function clock() {
  let now = 0;
  let sequence = 0;
  const timers = new Map();
  return {
    setTimer(fn, delay) { const id = ++sequence; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimer(id) { timers.delete(id); },
    get count() { return timers.size; },
    async advance(delay) {
      const target = now + delay;
      for (;;) {
        const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        timers.delete(next[0]);
        now = next[1].at;
        next[1].fn();
        await flush();
      }
      now = target;
      await flush();
    },
  };
}

function harness(id = "synthetic-a", sharedClock = clock()) {
  const requests = [];
  const updates = [];
  const api = Object.fromEntries(["analysis", "evaluationLabels", "agentRuns"].map((name) => [name, (requestedId, options) => new Promise((resolve, reject) => {
    // Intentionally ignore abort at the transport layer to exercise stale-result
    // guards too, not just fetch's normal AbortError behavior.
    requests.push({ name, id: requestedId, signal: options.signal, resolve, reject, settled: false });
  })]));
  const loader = createDetailLoader({ id, api, onChange: (value) => updates.push(value), setTimer: sharedClock.setTimer, clearTimer: sharedClock.clearTimer });
  return {
    loader, clock: sharedClock, requests, updates,
    get state() { return updates.at(-1); },
    calls(name) { return requests.filter((request) => request.name === name); },
    next(name) { return requests.find((request) => request.name === name && !request.settled && !request.signal.aborted); },
    async answer(name, value) { const request = this.next(name); assert.ok(request, `waiting ${name}`); request.settled = true; request.resolve(value); await flush(); },
    async fail(name, status) { const request = this.next(name); assert.ok(request, `waiting ${name}`); request.settled = true; const error = new Error(status ? `HTTP ${status}` : "network_error"); error.status = status; request.reject(error); await flush(); },
  };
}

const analysis = (status, extra = {}) => ({ id: "synthetic-a", status, ...extra });
const history = (revision = 1) => ({ items: [{ id: `synthetic-label-${revision}`, revision }] });
const runs = (status = "completed") => [{ id: "synthetic-run", status, steps: [] }];

test("completed detail loads independently of Label history and never polls or reads Agent I/O", async () => {
  const h = harness(); h.loader.start();
  await h.answer("analysis", analysis("completed"));
  assert.equal(h.state.detail.status, "completed");
  assert.equal(h.state.labelHistory, null);
  await h.answer("evaluationLabels", history());
  assert.equal(h.clock.count, 0);
  await h.clock.advance(60000);
  assert.equal(h.calls("analysis").length, 1);
  assert.equal(h.calls("evaluationLabels").length, 1);
  assert.equal(h.calls("agentRuns").length, 0);
  h.loader.dispose();
});

test("pending → processing → completed refreshes final data and Label once, then stops", async () => {
  const h = harness(); h.loader.start();
  await h.answer("analysis", analysis("pending")); await h.answer("evaluationLabels", history());
  await h.clock.advance(3000); await h.answer("analysis", analysis("processing"));
  assert.equal(h.calls("evaluationLabels").length, 1);
  await h.clock.advance(3000); await h.answer("analysis", analysis("completed", { result: { verdict: "true_positive" } }));
  await h.answer("evaluationLabels", history(2));
  await h.clock.advance(60000);
  assert.equal(h.calls("analysis").length, 3);
  assert.equal(h.calls("agentRuns").length, 0);
  assert.equal(h.state.labelHistory[0].revision, 2);
  assert.equal(h.clock.count, 0);
  h.loader.dispose();
});

test("failed is terminal, both initially and after processing", async () => {
  for (const initiallyRunning of [false, true]) {
    const h = harness(); h.loader.start();
    await h.answer("evaluationLabels", history());
    if (initiallyRunning) { await h.answer("analysis", analysis("processing")); await h.clock.advance(3000); }
    await h.answer("analysis", analysis("failed", { error_code: "synthetic_error" }));
    if (initiallyRunning) await h.answer("evaluationLabels", history());
    await h.clock.advance(60000);
    assert.equal(h.calls("analysis").length, initiallyRunning ? 2 : 1);
    assert.equal(h.clock.count, 0);
    h.loader.dispose();
  }
});

test("Agent history is lazy; completion invalidates in-flight running history and reads final steps once", async () => {
  const h = harness(); h.loader.start();
  await h.answer("analysis", analysis("processing")); await h.answer("evaluationLabels", history());
  h.loader.setAgentVisible(true);
  await h.answer("agentRuns", runs("running"));
  await h.clock.advance(3000);
  const staleHistory = h.next("agentRuns");
  assert.ok(staleHistory);
  await h.answer("analysis", analysis("completed"));
  assert.equal(staleHistory.signal.aborted, true);
  await h.answer("agentRuns", runs()); await h.answer("evaluationLabels", history());
  staleHistory.resolve(runs("running")); await flush();
  assert.equal(h.state.runs[0].status, "completed");
  await h.clock.advance(60000);
  assert.equal(h.calls("agentRuns").length, 3);
  assert.equal(h.clock.count, 0);
  h.loader.dispose();
});

test("leaving Agent tab aborts its request and ignores late data; reopening terminal history reads once", async () => {
  const h = harness(); h.loader.start();
  await h.answer("analysis", analysis("completed")); await h.answer("evaluationLabels", history());
  h.loader.setAgentVisible(true);
  const staleHistory = h.next("agentRuns");
  h.loader.setAgentVisible(false);
  assert.equal(staleHistory.signal.aborted, true);
  staleHistory.resolve(runs("running")); await flush();
  assert.equal(h.state.runs, null);
  await h.clock.advance(60000);
  assert.equal(h.calls("agentRuns").length, 1);
  h.loader.setAgentVisible(true); await h.answer("agentRuns", runs());
  h.loader.setAgentVisible(false); await h.clock.advance(60000);
  assert.equal(h.calls("agentRuns").length, 2);
  assert.equal(h.clock.count, 0);
  h.loader.dispose();
});

test("closing Agent tab cancels a scheduled poll without stopping detail status polling", async () => {
  const h = harness(); h.loader.start();
  await h.answer("analysis", analysis("pending")); await h.answer("evaluationLabels", history());
  h.loader.setAgentVisible(true); await h.answer("agentRuns", []);
  h.loader.setAgentVisible(false);
  await h.clock.advance(3000);
  assert.equal(h.calls("analysis").length, 2);
  assert.equal(h.calls("agentRuns").length, 1);
  h.loader.dispose();
  assert.equal(h.clock.count, 0);
});

test("switching analysis / unmount aborts all requests, clears timers and blocks old ID updates", async () => {
  const sharedClock = clock();
  const old = harness("synthetic-a", sharedClock); old.loader.start(); old.loader.setAgentVisible(true);
  const oldUpdateCount = old.updates.length;
  old.loader.dispose();
  assert.equal(sharedClock.count, 0);
  assert.ok(old.requests.every((request) => request.signal.aborted));
  const next = harness("synthetic-b", sharedClock); next.loader.start();
  await next.answer("analysis", { id: "synthetic-b", status: "completed" }); await next.answer("evaluationLabels", history());
  for (const request of old.requests) request.resolve(request.name === "analysis" ? analysis("processing") : request.name === "evaluationLabels" ? history() : runs());
  await flush(); await sharedClock.advance(60000);
  assert.equal(old.updates.length, oldUpdateCount);
  assert.equal(next.state.detail.id, "synthetic-b");
  assert.ok(next.requests.every((request) => request.id === "synthetic-b"));
  assert.equal(sharedClock.count, 0);
  old.loader.refresh(); old.loader.setAgentVisible(true);
  assert.equal(old.requests.length, 3);
  next.loader.dispose();
});

test("transient detail failures retry only three attempts with backoff; manual refresh recovers", async () => {
  const h = harness(); h.loader.start(); await h.answer("evaluationLabels", history());
  await h.fail("analysis", 503);
  await h.clock.advance(2999); assert.equal(h.calls("analysis").length, 1);
  await h.clock.advance(1); await h.fail("analysis");
  await h.clock.advance(5999); assert.equal(h.calls("analysis").length, 2);
  await h.clock.advance(1); await h.fail("analysis", 503);
  await h.clock.advance(60000);
  assert.equal(h.calls("analysis").length, 3);
  assert.match(h.state.error, /자동 조회를 중지/);
  assert.equal(h.clock.count, 0);
  h.loader.refresh(); await h.answer("analysis", analysis("completed")); await h.answer("evaluationLabels", history(2));
  assert.equal(h.state.error, ""); assert.equal(h.state.labelHistory[0].revision, 2);
  assert.equal(h.clock.count, 0);
  h.loader.dispose();
});

test("Label and Agent failures never hide a loaded detail; retry is bounded and explicit refresh updates saved Labels", async () => {
  const h = harness(); h.loader.start();
  await h.answer("analysis", analysis("completed")); await h.fail("evaluationLabels", 500);
  h.loader.setAgentVisible(true); await h.fail("agentRuns", 500);
  for (const delay of [3000, 6000]) {
    await h.clock.advance(delay); await h.fail("evaluationLabels", 500); await h.fail("agentRuns", 500);
  }
  assert.equal(h.state.detail.status, "completed");
  assert.equal(h.state.error, "");
  assert.match(h.state.labelHistoryError, /자동 조회를 중지/); assert.match(h.state.runsError, /자동 조회를 중지/);
  await h.clock.advance(60000); assert.equal(h.clock.count, 0);
  h.loader.refreshAgent(); await h.answer("agentRuns", runs());
  assert.equal(h.state.runsError, ""); assert.equal(h.calls("analysis").length, 1);
  h.loader.refresh(); await h.answer("analysis", analysis("completed", { evaluation: { revision: 2 } }));
  await h.answer("evaluationLabels", history(2)); await h.answer("agentRuns", runs());
  assert.equal(h.state.detail.evaluation.revision, 2); assert.equal(h.state.labelHistory[0].revision, 2);
  assert.equal(h.state.labelHistoryError, ""); assert.equal(h.clock.count, 0);
  h.loader.dispose();
});

test("authorization/not-found errors do not automatically retry", async () => {
  for (const status of [401, 403, 404]) {
    const h = harness(); h.loader.start();
    await h.fail("analysis", status); await h.fail("evaluationLabels", status);
    await h.clock.advance(60000);
    assert.equal(h.calls("analysis").length, 1); assert.equal(h.calls("evaluationLabels").length, 1);
    assert.equal(h.clock.count, 0);
    h.loader.dispose();
  }
});

test("slow requests never overlap; refreshing keeps old detail and ignores the canceled response", async () => {
  const h = harness(); h.loader.start();
  await h.answer("analysis", analysis("processing")); await h.answer("evaluationLabels", history());
  await h.clock.advance(3000);
  const staleDetail = h.next("analysis");
  await h.clock.advance(9000); assert.equal(h.calls("analysis").length, 2);
  assert.equal(h.state.detail.status, "processing");
  h.loader.refresh();
  assert.equal(staleDetail.signal.aborted, true); assert.equal(h.state.detail.status, "processing");
  await h.answer("analysis", analysis("completed")); await h.answer("evaluationLabels", history(2));
  staleDetail.resolve(analysis("processing")); await flush();
  assert.equal(h.state.detail.status, "completed"); assert.equal(h.clock.count, 0);
  h.loader.dispose();
});

test("hung requests time out and abort; three timeouts stop until manual refresh", async () => {
  const h = harness(); h.loader.start(); await h.answer("evaluationLabels", history());
  const first = h.next("analysis");
  await h.clock.advance(30000);
  assert.equal(first.signal.aborted, true); assert.match(h.state.error, /조회 시간 초과/);
  first.resolve(analysis("processing")); await flush(); assert.equal(h.state.detail, null);
  await h.clock.advance(3000 + 30000 + 6000 + 30000);
  assert.equal(h.calls("analysis").length, 3);
  assert.ok(h.calls("analysis").every((request) => request.signal.aborted));
  assert.match(h.state.error, /자동 조회를 중지/); assert.equal(h.clock.count, 0);
  h.loader.dispose();
});

test("a stale running detail does not keep Agent polling forever while detail lookup is failing", async () => {
  const h = harness(); h.loader.start();
  await h.answer("analysis", analysis("processing")); await h.answer("evaluationLabels", history());
  h.loader.setAgentVisible(true); await h.answer("agentRuns", runs("running"));
  await h.clock.advance(3000); await h.fail("analysis", 404); await h.answer("agentRuns", runs("running"));
  await h.clock.advance(60000); assert.equal(h.clock.count, 0);
  h.loader.refresh(); await h.answer("evaluationLabels", history());
  await h.answer("analysis", analysis("processing")); await h.answer("agentRuns", runs("running"));
  await h.clock.advance(3000);
  assert.ok(h.next("analysis")); assert.ok(h.next("agentRuns"));
  h.loader.dispose(); assert.equal(h.clock.count, 0);
});
