import assert from "node:assert/strict";
import test from "node:test";
import { applyAppRoute, readAppHash, writeAppHash } from "./appRoutes.js";
import { initialStatistics, groupCases, percent, referenceQuery, statisticsQuery } from "./testStatistics.js";
import { testRunQuery } from "./testRuns.js";
import { fetchTestExport, testExportPath } from "./testExport.js";
import { reportTypes } from "./analysisDownloads.js";

const id = "11111111-1111-4111-8111-111111111111";

test("statistics excludes case-only filters while whole-Test export keeps only the selected reference basis", () => {
  const filters = { evaluation_id: "latest", difficulty: "hard", status: "failed", cell: "fp", limit: 1, offset: 50, group_field: "signature", group_value: "private value" };
  const query = statisticsQuery(filters, { ...initialStatistics(), q: "SQL", draft: "private draft" });
  assert.equal(query.group_by, "signature"); assert.equal(query.difficulty, "hard"); assert.equal(query.limit, 10);
  for (const key of ["status", "cell", "group_field", "group_value", "draft"]) assert.equal(Object.hasOwn(query, key), false);
  assert.deepEqual(referenceQuery(filters), { reference_basis: "latest" });
  assert.deepEqual(referenceQuery({ ...filters, evaluation_id: id }), { evaluation_id: id });
  assert.throws(() => testExportPath(id, { limit: 1 }), /invalid_test_export_request/);
});

test("missing and exact group drilldowns are distinct; a zero accuracy is not missing", () => {
  assert.equal(percent(0), "0.0%"); assert.equal(percent(null), "—");
  assert.deepEqual(testRunQuery(groupCases({}, "signature", null)).group_missing, true);
  assert.equal(testRunQuery(groupCases({}, "signature", "missing")).group_value, "missing");
  assert.equal(groupCases({ status: "failed", offset: 100, cell: "fp" }, "signature", "SQL").offset, 0);
});

test("test statistics/evaluation tabs survive reload without writing group values into URLs", () => {
  for (const view of ["statistics", "metrics"]) {
    const state = { page: "analyses", resultsPurpose: "test", testResults: { view: "runs", runId: id, filters: { view, statistics: { q: "private" } } } };
    const hash = writeAppHash(state); assert.equal(hash, `#evaluate/tests/${id}/${view}`);
    const restored = applyAppRoute({ ...state, testResults: { filters: {} } }, readAppHash(hash));
    assert.equal(restored.testResults.filters.view, view);
    assert.doesNotMatch(hash, /private/);
  }
  assert.equal(readAppHash(`#evaluate/tests/${id}/unknown`), null);
});

test("whole-Test download requests are authenticated, uncached and independent of row pagination", async () => {
  let request;
  const blob = await fetchTestExport(id, { reference_basis: "latest" }, { fetchImpl: async (path, options) => {
    request = { path, options };
    return new Response(new Blob(["fixture workbook"]), { headers: { "content-type": reportTypes.xlsx } });
  } });
  assert.ok(blob.size); assert.equal(request.options.credentials, "include"); assert.equal(request.options.cache, "no-store");
  assert.equal(request.path, `/api/v1/test-runs/${id}/export.xlsx?reference_basis=latest`);
  await assert.rejects(fetchTestExport(id, {}, { fetchImpl: async () => new Response("not excel", { headers: { "content-type": "text/html" } }) }), /invalid_test_export_response/);
  await assert.rejects(fetchTestExport(id, {}, { fetchImpl: async () => new Response(JSON.stringify({ detail: "private raw event" }), { status: 503, headers: { "content-type": "application/json" } }) }), /test_export_unavailable/);
});
