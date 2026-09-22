import assert from "node:assert/strict";
import test from "node:test";
import { api } from "./api.js";
import { configurationChanges, comparisonContext } from "./testConfigurationIdentity.js";
import { readAppHash, writeAppHash, applyAppRoute } from "./appRoutes.js";
import { initialListState } from "./analysisView.js";
import { metricHelpEnglish } from "./evaluationMetricEnglish.js";
import { metricHelp } from "./evaluationMetrics.js";
import { candidatePrefill } from "./candidateConfiguration.js";

test("clone prefill never replaces a missing or invalid source with current defaults", () => {
  const saved = { primary_profile_id: "current" }, source = { primary_profile_id: "deleted-source" };
  assert.deepEqual(candidatePrefill(undefined, saved), saved);
  assert.notEqual(candidatePrefill(undefined, saved), saved);
  assert.equal(candidatePrefill(null, saved).primary_profile_id, "");
  assert.deepEqual(candidatePrefill(source, saved), source);
  assert.equal(candidatePrefill(undefined, null).prompt_policy_version_id, "");
});

test("R5 defaults and import APIs send explicit optimistic and idempotent controls", async () => {
  const old = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, status: 200, json: async () => ({}) }; };
  try {
    const candidate = { primary_profile_id: "primary", verifier_profile_id: null, evidence_editor_enabled: false, evidence_editor_profile_id: null, prompt_policy_version_id: "policy", input_schema_version_id: "schema" };
    await api.saveTestDefaults({ expected_revision: 3, candidate_configuration: candidate });
    await api.previewTestImport("run", { target: "append_to_existing_dataset", dataset_id: "dataset", expected_working_revision: 2, test_run_item_ids: ["case"] });
    await api.confirmTestImport("run", { preview_token: "token", idempotency_key: "request" });
    assert.equal(calls[0].options.method, "PATCH");
    assert.deepEqual(JSON.parse(calls[0].options.body).candidate_configuration, candidate);
    assert.equal(JSON.parse(calls[1].options.body).expected_working_revision, 2);
    assert.deepEqual(JSON.parse(calls[2].options.body), { preview_token: "token", idempotency_key: "request" });
    assert.ok(calls.every(row => !row.url.includes("promote")));
  } finally { globalThis.fetch = old; }
});
test("ungrouped legacy Tests stay in Evaluation without inventing a Test Run", () => {
  const initial = { listState: initialListState(), testResults: { items: initialListState("test") } }, id = "11111111-1111-4111-8111-111111111111";
  for (const route of ["#evaluate/tests/items", `#evaluate/tests/items/${id}/report`]) {
    const state = applyAppRoute(initial, readAppHash(route));
    assert.equal(state.resultsPurpose, "test");
    assert.equal(state.testResults.runId, null);
    assert.equal(writeAppHash({ ...state, privateDraft: "never-in-url" }), route);
  }
});
test("comparison context never equates different datasets or missing baselines", () => {
  assert.deepEqual(configurationChanges({ prompt: { id: 1 } }, { prompt: { id: 2 } }), ["prompt"]);
  assert.equal(configurationChanges(null, {}), null);
  assert.equal(comparisonContext({ ground_truth: { published: true, comparison_key: "a:r1" } }, { comparison_key: "b:r1" }), "different");
  assert.equal(comparisonContext({ ground_truth: { published: true } }, {}), "no_baseline");
  assert.equal(comparisonContext({}, {}), "not_official");
});
test("all metric definitions also have English keyboard-accessible tooltip copy", () => {
  assert.deepEqual(Object.keys(metricHelpEnglish).sort(), Object.keys(metricHelp).sort());
  for (const value of Object.values(metricHelpEnglish)) assert.ok(value.length > 60);
});
