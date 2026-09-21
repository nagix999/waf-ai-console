import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { candidateIssue, candidateUsesExternal, defaultCandidate, officialComparisonAllowed } from "./candidateConfiguration.js";
import { api } from "./api.js";

const agents = { profiles: [{ id: "p1", name: "internal", provider: "vllm", can_assign: true }, { id: "p2", provider: "openai", can_assign: true }, { id: "draft", can_assign: false }], assignments: { test: { primary_profile_id: "p1", verifier_profile_id: null, evidence_editor_enabled: false } } };
const prompts = { active_version_id: "v1", items: [{ id: "v1" }, { id: "v2" }] };
const schemas = { active_version_id: "s1", items: [{ id: "s1" }, { id: "s2" }] };
const catalog = { agents, prompts, schemas };

function component(file, name = "default") {
  const source = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} }; runInNewContext(source, { module, exports: module.exports, require: createRequire(import.meta.url), console, URL, setTimeout, clearTimeout, AbortController }); return module.exports[name];
}

test("candidate defaults and explicit inactive versions never rewrite the catalog", () => {
  const before = structuredClone(catalog), value = defaultCandidate(agents, prompts, schemas);
  assert.equal(candidateIssue(catalog, value), "");
  assert.equal(value.verifier_profile_id, null);
  assert.equal(candidateIssue(catalog, { ...value, prompt_policy_version_id: "v2", input_schema_version_id: "s2" }), "");
  assert.deepEqual(catalog, before);
  assert.match(candidateIssue(catalog, { ...value, verifier_profile_id: "draft" }), /검증/);
  assert.match(candidateIssue(catalog, { ...value, prompt_policy_version_id: "missing" }), /저장된/);
  assert.equal(candidateUsesExternal(catalog, { ...value, verifier_profile_id: "p2" }), true);
  assert.equal(candidateUsesExternal(catalog, { ...value, evidence_editor_enabled: false, evidence_editor_profile_id: "p2" }), false);
  assert.throws(() => defaultCandidate({}, prompts, schemas));
});

test("comparison UI independently checks official membership and metric versions", () => {
  const run = { evaluation_mode: "ground_truth", dataset_version_id: "dataset-v1", ground_truth: { membership_hash: "approved", metrics_version: "metric-v1" } };
  assert.equal(officialComparisonAllowed({ comparable: true, baseline: run, candidate: run }), true);
  for (const changed of [{ ...run, evaluation_mode: "reference" }, { ...run, dataset_version_id: "dataset-v2" }, { ...run, ground_truth: { ...run.ground_truth, metrics_version: "metric-v2" } }, { ...run, ground_truth: null }]) {
    assert.equal(officialComparisonAllowed({ comparable: true, baseline: run, candidate: changed }), false);
  }
  assert.equal(officialComparisonAllowed({ comparable: false, baseline: run, candidate: run }), false);
  assert.equal(officialComparisonAllowed({ baseline: {}, candidate: {} }), true);
  const published = { ...run, ground_truth: { ...run.ground_truth, published: true, comparison_key: "r3-context" } };
  assert.equal(officialComparisonAllowed({ baseline: published, candidate: published }), true);
  assert.equal(officialComparisonAllowed({ baseline: published, candidate: run }), false);
  assert.equal(officialComparisonAllowed({ baseline: published, candidate: { ...published, ground_truth: { ...published.ground_truth, comparison_key: "different-scope" } } }), false);
  const Result = component("./TestRunComparison.jsx", "TestComparisonResult");
  const html = renderToStaticMarkup(createElement(Result, { data: { comparable: false }, state: {} }));
  assert.match(html, /평가 기준이 달라/); assert.doesNotMatch(html, /Accuracy|F1|<table/);
});

test("official result view only offers frozen evaluations; manual answer action is hidden", () => {
  const Reevaluation = component("./TestReevaluation.jsx");
  const html = renderToStaticMarkup(createElement(Reevaluation, { run: { id: "run", evaluation_mode: "ground_truth", official_evaluation_pending: true } }));
  assert.match(html, /공식 평가 기록/); assert.match(html, /당시 답안 고정/); assert.doesNotMatch(html, /평가 기록 저장<|최신 답안/);
  const Actions = component("./AnalysisSelection.jsx");
  const actions = renderToStaticMarkup(createElement(Actions, { ids: ["fixture"], allowReferences: false }));
  assert.match(actions, /데이터셋에 추가/); assert.doesNotMatch(actions, /참고 답안 일괄 입력/);
});

test("single, upload and dataset requests carry selected configuration outside events", async () => {
  const previous = globalThis.fetch, calls = [], candidate = defaultCandidate(agents, prompts, schemas);
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({}) }; };
  try {
    await api.createTestRun({ event: { event_id: "fixture" }, candidate_configuration: candidate });
    await api.uploadTestRun(new Blob(['[]']), "fixture", "fixture-key", candidate);
    await api.runValidationDataset("fixture", { candidate_configuration: candidate, evaluation_mode: "ground_truth" });
    await api.searchValidationDatasets({ query: "PRIVATE_SEARCH", offset: 20 });
    assert.deepEqual(JSON.parse(calls[0].options.body).candidate_configuration, candidate);
    assert.deepEqual(JSON.parse(calls[1].options.body.get("candidate_configuration")), candidate);
    assert.equal(JSON.parse(calls[2].options.body).evaluation_mode, "ground_truth");
    assert.ok(!calls[3].url.includes("PRIVATE_SEARCH"));
    assert.equal(JSON.parse(calls[3].options.body).query, "PRIVATE_SEARCH");
  } finally { globalThis.fetch = previous; }
});
