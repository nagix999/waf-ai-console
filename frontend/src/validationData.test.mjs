import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { selectedPageIds, validationDataError, datasetImportMessage } from "./validationData.js";
import { readAppHash, writeAppHash, applyAppRoute } from "./appRoutes.js";
import { testRunQuery } from "./testRuns.js";
import { serviceKeyMetadata, createServiceKeysController } from "./serviceApiKeys.js";
import { api } from "./api.js";

function component(file, name = "default") {
  const built = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} };
  runInNewContext(built, { module, exports: module.exports, require: createRequire(import.meta.url), process, Date });
  return module.exports[name];
}

test("selection is deduplicated, page-scoped and excludes rejected rows", () => {
  assert.deepEqual(selectedPageIds(["a", "a", "b", "other"], [{ id: "a" }, { id: "b" }]), ["a", "b"]);
  assert.deepEqual(selectedPageIds(["a", "b"], [{ id: "row", analysis_id: "a" }, { id: "b", analysis_id: null }]), ["a"]);
  assert.deepEqual(selectedPageIds(["a"], []), []);
});

test("dataset URLs contain only UUIDs and browser route restores the selection", () => {
  const id = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa";
  assert.equal(writeAppHash({ page: "datasets", datasetId: id }), `#datasets/${id}`);
  assert.deepEqual(readAppHash(`#datasets/${id}`), { page: "datasets", datasetId: id });
  assert.equal(applyAppRoute({ datasetId: id }, readAppHash("#datasets")).datasetId, null);
  assert.equal(readAppHash("#datasets/SECRET_PAYLOAD"), null);
  assert.equal(writeAppHash({ page: "datasets", datasetId: "SECRET_PAYLOAD" }), "#datasets");
});

test("regrading has explicit version query and never changes ordinary defaults", () => {
  assert.equal(testRunQuery({ evaluation_id: "saved-evaluation" }).evaluation_id, "saved-evaluation");
  assert.equal(Object.hasOwn(testRunQuery(), "evaluation_id"), false);
});

test("analyst error messages are bounded and unknown server content is not echoed", () => {
  assert.match(validationDataError({ message: "dataset_internal_models_required" }), /Primary·Verifier·근거 정리/);
  assert.match(validationDataError({ message: "reference_changed_reload" }), /다시 확인/);
  for (const message of ["SECRET_PAYLOAD", "__proto__", "constructor", "toString"]) {
    const result = validationDataError({ message, status: 500 });
    assert.equal(typeof result, "string"); assert.ok(!result.includes(message));
  }
  assert.match(datasetImportMessage({ added: 2, duplicates: 1, conflicts: ["a"], rejected: [] }), /2건 추가 · 1건 중복 제외 · 1건 충돌/);
});

test("selection controls have both actions and no active mutation in initial rendering", () => {
  const Actions = component("./AnalysisSelection.jsx");
  const empty = renderToStaticMarkup(createElement(Actions, { ids: [] }));
  assert.match(empty, /참고 답안 일괄 입력/); assert.match(empty, /데이터셋에 추가/); assert.match(empty, /disabled/);
  const single = renderToStaticMarkup(createElement(Actions, { ids: ["fixture"], single: true }));
  assert.match(single, /참고 답안 입력/); assert.doesNotMatch(single, /일괄 입력/);
});

test("Test key creation sends purpose once and metadata preserves it without secrets", async () => {
  const calls = [];
  const controller = createServiceKeysController({ onChange() {}, api: {
    serviceApiKeys: async () => ({ items: [] }), createServiceApiKey: async payload => { calls.push(payload); return { item: { id: "key", ...payload }, api_key: "ONE_TIME_FIXTURE" }; },
  } });
  await controller.refresh(); controller.update("name", "test-key"); controller.update("source_system", "fixture");
  controller.update("scopes", ["ingest"]); controller.update("purpose", "test");
  assert.equal(await controller.issue(), true); assert.equal(calls.length, 1); assert.equal(calls[0].purpose, "test");
  assert.equal(controller.getState().issued.item.purpose, "test");
  assert.equal(serviceKeyMetadata({ purpose: "test", api_key: "SECRET" }).api_key, undefined);
  controller.dispose();
});

test("new clients use admin endpoints without raw inputs in URLs", async () => {
  const previous = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({}) }; };
  try {
    await api.saveReferences({ targets: [{ analysis_id: "fixture", expected_revision: 0 }], verdict: "inconclusive", comment: "PRIVATE_COMMENT" });
    await api.runValidationDataset("fixture", { expected_revision: 2, idempotency_key: "fixture-key" });
    await api.rescoreTest("fixture", "fixture-rescore");
    assert.equal(calls.length, 3); assert.ok(calls.every(call => call.options.method === "POST"));
    assert.ok(calls.every(call => !call.url.includes("PRIVATE_COMMENT")));
    assert.ok(calls[0].options.body.includes("PRIVATE_COMMENT"));
  } finally { globalThis.fetch = previous; }
});

test("report evidence groups have distinct presentation and full paragraph width", () => {
  const source = readFileSync(new URL("./analysisReport.css", import.meta.url), "utf8");
  assert.match(source, /\.report-paper p, \.report-paper li \{ max-width: 100%; \}/);
  assert.match(source, /report-evidence-group\.attack/); assert.match(source, /report-evidence-group\.normal/);
  const Report = component("./AnalysisReport.jsx");
  const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/analyst_assessment_cases.json", import.meta.url), "utf8"));
  const detail = { status: "completed", result: { ...cases[0].result, verdict: "inconclusive", agent: { framework: "moduagent" } } };
  // Dynamic HTML stays text even when the stored report contains hostile data.
  const html = renderToStaticMarkup(createElement(Report, { detail, mode: "preview", onModeChange() {} }));
  for (const group of ["attack", "normal", "context"]) assert.match(html, new RegExp(`report-evidence-group ${group}`));
  assert.doesNotMatch(html, /dangerouslySetInnerHTML|<script/);
});
