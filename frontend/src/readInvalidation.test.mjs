import assert from "node:assert/strict";
import test from "node:test";
import { affectsHome, testRunActive, HOME_INVALIDATED } from "./readInvalidation.js";
import { startVisiblePolling } from "./visiblePolling.js";

test("only state changes invalidate Home; POST searches do not create a refresh loop", () => {
  for (const path of ["/api/v1/validation-datasets/search", "/api/v1/validation-datasets/id/working/search", "/api/v1/evaluation-labels/selection", "/api/v1/test-runs/id/ground-truth-import/preview", "/api/v1/auth/login"]) assert.equal(affectsHome(path, "POST"), false);
  for (const [path, method] of [["/api/v1/admin/production-configurations/promote", "POST"], ["/api/v1/validation-datasets/id/working/items/id", "PUT"], ["/api/v1/validation-datasets/id/publish", "POST"], ["/api/v1/evaluation-labels/bulk", "POST"]]) assert.equal(affectsHome(path, method), true);
  assert.equal(affectsHome("/api/v1/admin/production-configurations/overview"), false);
});

test("only running cases or pending official evaluation keep Test polling active", () => {
  for (const status of ["pending", "processing"]) assert.equal(testRunActive({ status }), true);
  for (const status of ["completed", "failed"]) {
    assert.equal(testRunActive({ status }), false);
    assert.equal(testRunActive({ status, official_evaluation_pending: true }), true);
  }
});

test("static Home read has no idle timer and coalesces an invalidation during a request", async () => {
  const handlers = new Map(); let calls = 0, resolve;
  const doc = { hidden: false, addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener: name => handlers.delete(name) };
  const stop = startVisiblePolling(async () => { calls++; if (calls === 1) await new Promise(done => { resolve = done; }); return false; }, { document: doc, interval: null, invalidationEvent: HOME_INVALIDATED });
  handlers.get(HOME_INVALIDATED)(); handlers.get(HOME_INVALIDATED)();
  assert.equal(calls, 1); resolve();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(calls, 2);
  handlers.get(HOME_INVALIDATED)();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(calls, 3);
  doc.hidden = true; handlers.get(HOME_INVALIDATED)(); assert.equal(calls, 3);
  doc.hidden = false; handlers.get("visibilitychange")();
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(calls, 4); stop(); assert.equal(handlers.size, 0);
});
