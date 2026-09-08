import assert from "node:assert/strict";
import test from "node:test";
import { applyAppRoute, isDetailOrigin, readAppHash, writeAppHash } from "./appRoutes.js";
import { initialListState } from "./analysisView.js";

const id = "10000000-0000-4000-8000-000000000001";
const initial = () => ({ page: "dashboard", resultsPurpose: "", listState: initialListState(), testResults: { view: "runs", runId: null, filters: {} }, settingsTab: "models", detailTab: "result" });

test("safe screen, settings, result and run routes round trip", () => {
  for (const hash of ["#dashboard", "#production-api", "#test", "#analyses", "#analyses/production", "#analyses/test", "#analyses/test/items",
    ...["models", "prompts", "schema", "egress", "keys"].map(tab => `#settings/${tab}`),
    `#test-runs/${id}`, `#analyses/${id}`, `#analyses/${id}/raw`, `#analyses/${id}/report`]) {
    const route = readAppHash(hash);
    assert.ok(route, hash);
    assert.equal(writeAppHash(applyAppRoute(initial(), route)), hash);
  }
});

test("unknown, malformed and non-UUID fragments cannot become API identifiers", () => {
  for (const hash of ["#workspace-content", "#unknown", "#analyses/../../admin", "#analyses/%2f", "#test-runs/secret", `#analyses/${id}/agent`, "#settings/__proto__", "#analyses?payload=secret", `#analyses/${id}/raw/extra`]) {
    assert.equal(readAppHash(hash), null);
  }
  assert.deepEqual(readAppHash(""), { page: "dashboard" });
  assert.deepEqual(readAppHash("#settings"), { page: "settings", tab: "models" });
});

test("URL serialization ignores searches, drafts, source fields and arbitrary metadata", () => {
  const state = { ...initial(), page: "analyses", resultsPurpose: "test", secret: "not-in-url", testResults: { view: "items", runId: null, filters: { q: "not-in-url" }, items: { source_system: "not-in-url" } } };
  state.listState.draft.q = "not-in-url";
  assert.equal(writeAppHash(state), "#analyses/test/items");
  assert.equal(writeAppHash({ ...state, page: "detail", selectedId: "not-in-url" }), "#analyses");
  assert.equal(writeAppHash({ ...state, page: "settings", settingsTab: "not-in-url" }), "#settings/models");
});

test("direct URL restoration uses the exact scope without mixing Test and Production", () => {
  const old = initial();
  old.testResults.runId = id;
  const next = applyAppRoute(old, readAppHash("#analyses/production"));
  assert.equal(next.resultsPurpose, "production");
  assert.equal(next.listState.applied.analysis_purpose, "production");
  assert.equal(next.listState.draft.analysis_purpose, "production");
  assert.equal(next.testResults.runId, null);
  assert.equal(old.testResults.runId, id);
  assert.equal(old.listState.applied.analysis_purpose, "");
  const detail = applyAppRoute(initial(), readAppHash(`#analyses/${id}/raw`));
  assert.equal(detail.detailReturnPage, "analyses");
  assert.equal(detail.selectedId, id);
  assert.equal(detail.detailTab, "raw");
});

test("Production and all-list Back ignore unrelated remembered Test execution state", () => {
  for (const purpose of ["", "production"]) {
    const detail = { ...initial(), page: "detail", detailReturnPage: "analyses", resultsPurpose: purpose, testResults: { runId: id, view: "runs" } };
    assert.equal(isDetailOrigin(detail, { ...detail, page: "analyses" }), true);
    assert.equal(isDetailOrigin(detail, { ...detail, page: "analyses", testResults: { runId: null, view: "items" } }), true);
    assert.equal(isDetailOrigin(detail, { ...detail, page: "analyses", resultsPurpose: "test" }), false);
  }
});

test("Test detail Back matches the originating execution or whole-item list only", () => {
  const detail = { ...initial(), page: "detail", resultsPurpose: "test", detailReturnPage: "testRun", testResults: { runId: id, view: "runs" } };
  assert.equal(isDetailOrigin(detail, { ...detail, page: "analyses" }), true);
  assert.equal(isDetailOrigin(detail, { ...detail, page: "analyses", testResults: { runId: null, view: "runs" } }), false);
  const item = { ...detail, detailReturnPage: "analyses", testResults: { runId: null, view: "items" } };
  assert.equal(isDetailOrigin(item, { ...item, page: "analyses" }), true);
  assert.equal(isDetailOrigin(item, { ...item, page: "analyses", testResults: { runId: null, view: "runs" } }), false);
  assert.equal(isDetailOrigin({ ...detail, detailReturnPage: "dashboard" }, { page: "dashboard" }), true);
});
