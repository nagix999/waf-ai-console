import assert from "node:assert/strict";
import test from "node:test";
import { applyAppRoute, isDetailOrigin, readAppHash, writeAppHash } from "./appRoutes.js";
import { initialListState } from "./analysisView.js";

const id = "10000000-0000-4000-8000-000000000001";
const initial = () => ({ page: "dashboard", resultsPurpose: "", listState: initialListState(), testResults: { view: "runs", runId: null, filters: {} }, settingsTab: "models", detailTab: "result" });

test("canonical workspace routes round trip", () => {
  for (const hash of ["#overview", "#connect/production-api", "#evaluate/tests/new", "#operate/inference", "#evaluate/tests", `#promotion/${id}`, `#evaluate/tests/${id}/case/${id}`, "#operate/runtime", "#operate/activity",
    ...["llm-profiles", "agent-roles", "instructions", "input-schema"].map(tab => `#configure/${tab}`),
    ...["api-keys", "vllm-targets"].map(tab => `#connect/${tab}`),
    ...["result", "agent-trace", "input", "result-json", "report"].map(tab => `#evaluate/tests/${id}/case/${id}/${tab}`),
    `#evaluate/tests/${id}`, ...["result", "agent-trace", "input", "result-json", "report"].map(tab => `#operate/inference/${id}/${tab}`)]) {
    const route = readAppHash(hash);
    assert.ok(route, hash);
    assert.equal(writeAppHash(applyAppRoute(initial(), route)), hash);
  }
});

test("legacy promotion selects no candidate; dataset list and detail have distinct URLs", () => {
  assert.equal(writeAppHash(applyAppRoute(initial(), readAppHash("#promote"))), "#evaluate/tests");
  assert.equal(writeAppHash({ ...initial(), page: "datasets", datasetId: id }), `#evaluate/ground-truth/${id}`);
  assert.equal(writeAppHash({ ...initial(), page: "datasets", datasetId: null }), "#evaluate/ground-truth");
  assert.equal(readAppHash("#promotion/secret"), null);
});

test("unknown, malformed and non-UUID fragments cannot become API identifiers", () => {
  for (const hash of ["#workspace-content", "#unknown", "#analyses/../../admin", "#analyses/%2f", "#test-runs/secret", `#analyses/${id}/unknown`, "#settings/__proto__", "#analyses?payload=secret", `#analyses/${id}/raw/extra`]) {
    assert.equal(readAppHash(hash), null);
  }
  assert.deepEqual(readAppHash(""), { page: "dashboard" });
  assert.deepEqual(readAppHash("#settings"), { page: "settings", tab: "models" });
});

test("URL serialization ignores searches, drafts, source fields and arbitrary metadata", () => {
  const state = { ...initial(), page: "analyses", resultsPurpose: "test", secret: "not-in-url", testResults: { view: "items", runId: null, filters: { q: "not-in-url" }, items: { source_system: "not-in-url" } } };
  state.listState.draft.q = "not-in-url";
  assert.equal(writeAppHash(state), "#evaluate/tests/items");
  assert.equal(writeAppHash({ ...state, page: "detail", selectedId: "not-in-url" }), "#operate/inference");
  assert.equal(writeAppHash({ ...state, page: "settings", settingsTab: "not-in-url" }), "#configure/llm-profiles");
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

test("feedback filters round trip without leaking free-text metadata", () => {
  const state = { ...initial(), page: "feedback", feedbackState: { start: "2026-11-01", end: "2026-11-25", purpose: "production", version: id, category: "evidence_mismatch", analysis: id, query: "PRIVATE_SEARCH", limit: 50, offset: 100, selectedId: id } };
  const hash = writeAppHash(state);
  assert.ok(hash.startsWith("#operate/feedback?"));
  assert.ok(!hash.includes("PRIVATE_SEARCH"));
  const restored = applyAppRoute(initial(), readAppHash(hash));
  assert.deepEqual(restored.feedbackState, { ...state.feedbackState, query: "" });
  assert.equal(isDetailOrigin({ ...initial(), detailReturnPage: "feedback" }, restored), true);
});

test("execution selection is safe, shareable and scoped to the detail", () => {
  const state = { ...initial(), page: "detail", selectedId: id, detailTab: "agent", executionSelection: { runId: id, stepId: id, view: "input", payload: "PRIVATE_PAYLOAD" } };
  const hash = writeAppHash(state);
  assert.ok(!hash.includes("PRIVATE_PAYLOAD"));
  const restored = applyAppRoute(initial(), readAppHash(hash));
  assert.deepEqual(restored.executionSelection, { runId: id, stepId: id, view: "input" });
  const unsafe = readAppHash(`#operate/inference/${id}/agent-trace?run=secret&step=../admin&view=secret`);
  assert.deepEqual(unsafe.executionSelection, { runId: null, stepId: null, view: "output" });
  assert.equal(applyAppRoute(state, readAppHash(`#operate/inference/${id}/agent-trace`)).executionSelection, null);
  assert.ok(!writeAppHash({ ...state, detailTab: "report" }).includes("?"));
});

test("final JSON is a UI-only execution selection, not an arbitrary API step", () => {
  const state = { ...initial(), page: "detail", selectedId: id, detailTab: "agent", executionSelection: { runId: id, stepId: "result", view: "output" } };
  const hash = writeAppHash(state);
  assert.match(hash, /step=result/);
  assert.deepEqual(applyAppRoute(initial(), readAppHash(hash)).executionSelection, state.executionSelection);
  for (const step of ["final", "result/../admin", "result-json", "metadata", "__proto__"]) {
    assert.equal(readAppHash(`#operate/inference/${id}/agent-trace?step=${encodeURIComponent(step)}`).executionSelection.stepId, null);
  }
});
