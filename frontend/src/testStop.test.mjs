import assert from "node:assert/strict";
import test from "node:test";
import { canStopTest, isTestStopped, stopError } from "./testStop.js";
import { testRunActive, affectsHome } from "./readInvalidation.js";
import { api } from "./api.js";
import { caseStatuses, testRunQuery } from "./testRuns.js";
import { compactEvaluation, evaluationExplanation, evaluationTone } from "./labelEvaluation.js";
import { analysisRowState } from "./analysisView.js";

test("ordinary active tests stop; historical views use live eligibility, validation is excluded", () => {
  for (const status of ["pending", "processing"]) assert.equal(canStopTest({ status }), true);
  for (const status of ["completed", "failed", "stopped"]) assert.equal(canStopTest({ status }), false);
  assert.equal(canStopTest({ status: "completed", accepting_items: true }), true);
  assert.equal(canStopTest({ status: "completed", can_stop: true }), true);
  assert.equal(canStopTest({ status: "processing", model_test_run_id: "validation" }), false);
  assert.equal(canStopTest({ status: "processing", stopped_at: "2026-09-29", can_stop: true }), false);
  assert.equal(isTestStopped({ status: "completed", stopped_at: "2026-09-29" }), true);
});

test("stopped tests never keep polling even with stale pending flags", () => {
  assert.equal(testRunActive({ status: "processing", stopped_at: "2026-09-29", official_evaluation_pending: true }), false);
  assert.equal(testRunActive({ status: "stopped", accepting_items: true }), false);
  assert.equal(testRunActive({ status: "completed", can_stop: true }), true);
  assert.equal(affectsHome("/api/v1/test-runs/test/stop", "POST"), true);
});

test("stop API is a body-free POST and does not retry or echo untrusted errors", async () => {
  const saved = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { status: 200, ok: true, json: async () => ({ status: "stopped" }) }; };
  try {
    assert.equal((await api.stopTestRun("fixture/id")).status, "stopped");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/v1/test-runs/fixture%2Fid/stop");
    assert.equal(calls[0].options.method, "POST");
    assert.equal(calls[0].options.body, undefined);
  } finally { globalThis.fetch = saved; }
  assert.match(stopError(new Error("untrusted PRIVATE body"), ko => ko), /자동 재전송하지 않습니다/);
  assert.doesNotMatch(stopError(new Error("untrusted PRIVATE body"), ko => ko), /PRIVATE/);
});

test("canceled filters are distinct from stopped runs, failure and answer mismatches", () => {
  assert.equal(caseStatuses.canceled, "중지됨");
  assert.equal(caseStatuses.stopped, undefined);
  assert.equal(testRunQuery({ status: "canceled", difficulty: "hard", offset: 25 }).status, "canceled");
  assert.equal(evaluationTone("canceled"), "excluded");
  assert.equal(compactEvaluation({ outcome: "canceled" }).excluded, true);
  assert.equal(compactEvaluation({ outcome: "canceled" }).text, "중지됨");
  assert.match(evaluationExplanation({ outcome: "canceled" }), /실패·오답으로 집계하지 않으며/);
  assert.equal(analysisRowState("canceled").label, "중지됨");
});
