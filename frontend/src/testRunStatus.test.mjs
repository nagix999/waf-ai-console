import assert from "node:assert/strict";
import test from "node:test";
import { caseStatuses, runStatuses, testRunDisplayState } from "./testRuns.js";

const finishedRun = overrides => ({
  status: "failed", accepting_items: false, total: 500, accepted: 498,
  completed: 497, failed: 1, rejected: 1, duplicates: 1, canceled: 0,
  pending: 0, processing: 0, official_evaluation_pending: false, ...overrides,
});

test("terminal batch displays completion without changing failures or raw state", () => {
  const run = Object.freeze(finishedRun());
  assert.deepEqual(testRunDisplayState(run), { status: "completed", labelKo: "처리 완료", labelEn: "Completed" });
  assert.equal(run.status, "failed"); assert.equal(run.failed, 1); assert.equal(run.rejected, 1);
  assert.equal(runStatuses.failed, "실행 실패"); assert.equal(caseStatuses.failed, "실행 실패");
});

test("all failed and all rejected batches still finish processing, not qualification", () => {
  for (const run of [
    finishedRun({ completed: 0, failed: 498 }),
    finishedRun({ total: 3, accepted: 0, completed: 0, failed: 0, rejected: 3, duplicates: 0 }),
  ]) assert.equal(testRunDisplayState(run).status, "completed");
});

test("stopped state has priority over stale running, admission and evaluation flags", () => {
  for (const run of [
    finishedRun({ status: "stopped" }),
    finishedRun({ status: "processing", stopped_at: "2026-11-26T03:00:00Z", accepting_items: true, processing: 1, official_evaluation_pending: true }),
    finishedRun({ status: "canceled" }),
  ]) assert.equal(testRunDisplayState(run).status, "stopped");
});

test("open intake never claims completion, including when its current items finished", () => {
  for (const status of ["failed", "completed", "pending", "processing"]) {
    const state = testRunDisplayState(finishedRun({ kind: "api", status, accepting_items: true }));
    assert.deepEqual(state, { status: "accepting", labelKo: "접수 중", labelEn: "Accepting requests" });
  }
});

test("remaining items and pending raw state keep the displayed lifecycle active", () => {
  for (const status of ["completed", "failed", "unknown"]) {
    assert.equal(testRunDisplayState(finishedRun({ status, processing: 1 })).status, "processing");
    assert.equal(testRunDisplayState(finishedRun({ status, pending: 1 })).status, "pending");
  }
  assert.equal(testRunDisplayState(finishedRun({ status: "pending" })).status, "pending");
  assert.equal(testRunDisplayState(finishedRun({ status: "processing" })).status, "processing");
  assert.equal(testRunDisplayState(finishedRun({ pending: 1, processing: 1 })).status, "processing");
});

test("finished items do not hide official evaluation finalization", () => {
  for (const status of ["completed", "failed"]) {
    assert.deepEqual(testRunDisplayState(finishedRun({ status, official_evaluation_pending: true })), {
      status: "evaluating", labelKo: "평가 집계 중", labelEn: "Finalizing evaluation",
    });
  }
  assert.equal(testRunDisplayState(finishedRun({ status: "processing", official_evaluation_pending: true })).status, "processing");
});

test("canceled cases do not turn into a completed run when stop metadata is absent", () => {
  for (const run of [
    finishedRun({ completed: 496, canceled: 1 }),
    finishedRun({ total: 3, accepted: 3, completed: 0, failed: 0, rejected: 0, duplicates: 0, canceled: 3 }),
    finishedRun({ status: "completed", completed: 496, canceled: 1 }),
  ]) assert.equal(testRunDisplayState(run).status, "stopped");
});

test("incomplete, invalid or mismatching counters cannot conceal a failed batch", () => {
  const fields = ["total", "accepted", "completed", "failed", "rejected", "duplicates", "canceled", "pending", "processing"];
  for (const field of fields) {
    for (const value of [undefined, null, -1, 0.5, "0", true, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      assert.equal(testRunDisplayState(finishedRun({ [field]: value })).status, "failed", `${field}: ${String(value)}`);
    }
  }
  for (const changes of [
    { total: 499 }, { accepted: 497 }, { completed: 496 }, { failed: 2 },
    { rejected: 2 }, { duplicates: 2 }, { canceled: 1 },
    { accepting_items: undefined }, { accepting_items: null }, { accepting_items: "false" },
  ]) assert.equal(testRunDisplayState(finishedRun(changes)).status, "failed");
});

test("empty batches and unknown states are not inferred as completed", () => {
  assert.equal(testRunDisplayState(finishedRun({ total: 0, accepted: 0, completed: 0, failed: 0, rejected: 0, duplicates: 0 })).status, "failed");
  assert.equal(testRunDisplayState(finishedRun({ status: "future_state" })).status, "unknown");
  assert.deepEqual(testRunDisplayState(null), { status: "unknown", labelKo: "미확인", labelEn: "Unknown" });
  assert.equal(testRunDisplayState(undefined).status, "unknown");
  assert.equal(testRunDisplayState({ status: "__proto__" }).status, "unknown");
});

test("model-profile validation retains its actual passed/failed result", () => {
  assert.deepEqual(testRunDisplayState({ status: "passed", mode: "full" }), {
    status: "passed", labelKo: "검증 통과", labelEn: "Passed",
  });
  assert.equal(testRunDisplayState({ status: "failed", mode: "full" }).status, "failed");
  assert.equal(testRunDisplayState({ status: "failed", kind: "model_validation", model_test_run_id: "synthetic-validation" }).status, "failed");
  assert.equal(testRunDisplayState(finishedRun({ kind: "model_validation", model_test_run_id: "synthetic-validation" })).status, "completed");
});

test("old completed responses remain readable but explicit active flags take precedence", () => {
  assert.equal(testRunDisplayState({ status: "completed" }).status, "completed");
  assert.equal(testRunDisplayState({ status: "completed", accepting_items: true }).status, "accepting");
  assert.equal(testRunDisplayState({ status: "completed", official_evaluation_pending: true }).status, "evaluating");
});
