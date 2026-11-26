import assert from "node:assert/strict";
import test from "node:test";
import { beginScopedRead, emptyScopedRead, scopedRead, updateScopedRead } from "./scopedRead.js";

test("same-scope refresh retains content while replacing only newly read data", () => {
  const before = { ...emptyScopedRead(), key: "request-1", scopeKey: "scope-1", data: { completed: 10 }, loading: false };
  const started = beginScopedRead(before, "scope-1", "request-2");
  assert.strictEqual(started.data, before.data);
  assert.equal(started.loading, true);
  assert.strictEqual(scopedRead(started, "scope-1"), started);
  const next = updateScopedRead(started, "scope-1", "request-2", { data: { completed: 11 }, loading: false });
  assert.equal(next.data.completed, 11);
  assert.equal(before.data.completed, 10);
});

test("a different run, filter or page hides and clears old-scope content immediately", () => {
  const before = { ...emptyScopedRead(), key: "request-1", scopeKey: "run-1-filter-1", data: { private: "synthetic" }, loading: false };
  for (const scope of ["run-2-filter-1", "run-1-filter-2", "run-1-filter-1-page-2"]) {
    assert.equal(scopedRead(before, scope).data, null);
    assert.equal(beginScopedRead(before, scope, "request-2").data, null);
  }
});

test("late requests cannot overwrite a new request or a new scope", () => {
  const current = beginScopedRead(emptyScopedRead(), "new-scope", "new-request");
  assert.strictEqual(updateScopedRead(current, "old-scope", "old-request", { data: { stale: true } }), current);
  assert.strictEqual(updateScopedRead(current, "new-scope", "old-request", { data: { stale: true } }), current);
});

test("transient errors keep current content but authorization or removal errors clear it", () => {
  const before = { ...beginScopedRead(emptyScopedRead(), "scope-1", "request-1"), data: { value: 1 } };
  assert.strictEqual(updateScopedRead(before, "scope-1", "request-1", { error: "unavailable", loading: false }).data, before.data);
  assert.equal(updateScopedRead(before, "scope-1", "request-1", { data: null, error: "unauthorized", loading: false }).data, null);
});
