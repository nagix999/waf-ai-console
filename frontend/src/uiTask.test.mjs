import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { runUiTask } from "./uiTask.js";

test("UI actions preserve results without creating a second request", async () => {
  let requests = 0, error = "";
  const result = await runUiTask(async () => { requests += 1; return "saved"; }, value => { error = value; }, "Fixed failure");
  assert.equal(result, "saved"); assert.equal(requests, 1); assert.equal(error, "");
});

test("UI boundaries contain synchronous, asynchronous and inner error-handler failures", async () => {
  for (const task of [
    () => { throw new Error("SYNTHETIC_PRIVATE_CALLBACK"); },
    async () => { throw new Error("SYNTHETIC_PRIVATE_ASYNC"); },
    async () => { try { throw new Error("request_failed"); } catch { throw new Error("SYNTHETIC_PRIVATE_HANDLER"); } },
  ]) {
    const errors = [];
    assert.equal(await runUiTask(task, value => errors.push(value), "작업을 완료하지 못했습니다."), undefined);
    assert.deepEqual(errors, ["작업을 완료하지 못했습니다."]);
  }
});

test("a stale-view guard prevents a late action error from replacing the current view", async () => {
  const errors = []; let visible = "first", reject;
  const waiting = new Promise((_, fail) => { reject = fail; });
  const action = runUiTask(() => waiting, value => { if (visible === "first") errors.push(value); }, "Fixed failure");
  visible = "next"; reject(new Error("SYNTHETIC_LATE_FAILURE"));
  await action;
  assert.deepEqual(errors, []);
});

test("all twelve reported form actions explicitly retain submit semantics", () => {
  const counts = { "App.jsx": 3, "DatasetAnalysis.jsx": 1, "GroundTruthWorkspace.jsx": 3,
    "ParserWorkbenchReview.jsx": 1, "ServiceApiKeys.jsx": 2, "TestGroundTruthImport.jsx": 1, "TestStatistics.jsx": 1 };
  for (const [file, count] of Object.entries(counts)) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.equal((source.match(/<button type="submit" className="(?:primary|secondary)"/g) || []).length, count, file);
  }
});

test("final event boundaries are connected to downloads, selection, draft save and session checks", () => {
  const read = file => readFileSync(new URL(file, import.meta.url), "utf8");
  assert.match(read("AnalysisDownloads.jsx"), /downloadAction\(close, "pdf"\)/);
  assert.match(read("AnalysisDownloads.jsx"), /downloadAction\(close, "xlsx"\)/);
  assert.match(read("AnalysisDownloads.jsx"), /visibleAnalysis\.current === id/);
  assert.match(read("AnalysisSelection.jsx"), /openAction\("reference", close\)/);
  assert.match(read("AnalysisSelection.jsx"), /openAction\("dataset", close\)/);
  assert.match(read("GroundTruthWorkspace.jsx"), /runUiTask\(\(\) => action\(async/);
  assert.match(read("App.jsx"), /checkSession\(\)\.catch\(/);
});
