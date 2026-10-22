import assert from "node:assert/strict";
import test from "node:test";
import { testExportFilters, fetchTestExportPreview, fetchSelectedTestExport, testExportError } from "./testExport.js";
import { reportTypes } from "./analysisDownloads.js";

const id = "11111111-1111-4111-8111-111111111111";
const preview = { count: 73, scope: "a".repeat(64), columns: [{ name: "event_id", label_ko: "이벤트 ID", description: "이벤트 식별자", example: "sample-001", group: "input" }] };
const w = korean => korean;

test("export reuses case filters and order but excludes pagination, drafts and statistic setup", () => {
  const filter = { op: "and", rules: [{ field: "verdict", operator: "eq", value: "true_positive" }] };
  const selection = testExportFilters({ difficulty: "hard", cell: "fn", evaluation_id: id, limit: 1, offset: 500,
    case_filter: filter, group_filters: [{ field: "signature", value: "private value" }], sort_by: "case_name", sort_order: "desc", statistics: { case_filter: "not applied here" } });
  assert.equal(selection.limit, undefined); assert.equal(selection.offset, undefined); assert.equal(selection.statistics, undefined);
  assert.equal(selection.verdict, "false_positive"); assert.equal(selection.reference_verdict, "true_positive");
  assert.equal(selection.evaluation_outcome, "false_negative"); assert.equal(selection.evaluation_id, id);
  assert.equal(selection.sort_by, "case_name"); assert.equal(selection.sort_order, "desc");
  assert.deepEqual(selection.case_filter, filter); assert.equal(JSON.parse(selection.group_filters)[0].value, "private value");
});

test("preview posts private filters in its body, never query parameters or an export request", async () => {
  let request;
  const filters = testExportFilters({ group_field: "signature", group_value: "private value" });
  const result = await fetchTestExportPreview(id, filters, { fetchImpl: async (path, options) => {
    request = { path, options }; return Response.json(preview);
  } });
  assert.deepEqual(result, preview); assert.equal(request.path, `/api/v1/test-runs/${id}/export/preview`);
  assert.equal(request.options.method, "POST"); assert.equal(request.options.credentials, "include"); assert.equal(request.options.cache, "no-store");
  assert.deepEqual(JSON.parse(request.options.body), { filters });
});

test("download posts the confirmed scope and selected fields with the same filters", async () => {
  let request;
  const payload = { filters: { status: "failed" }, expected_count: 73, preview_scope: preview.scope, columns: ["event_id"] };
  const result = await fetchSelectedTestExport(id, payload, { fetchImpl: async (path, options) => {
    request = { path, options }; return new Response(new Blob(["fixture workbook"]), { headers: { "content-type": reportTypes.xlsx } });
  } });
  assert.ok(result.size); assert.equal(request.path, `/api/v1/test-runs/${id}/export.xlsx`);
  assert.equal(request.options.method, "POST"); assert.equal(request.options.credentials, "include");
  assert.deepEqual(JSON.parse(request.options.body), payload);
});

test("invalid catalog responses and arbitrary errors are safe", async () => {
  for (const value of [{ ...preview, count: -1 }, { ...preview, scope: "private" }, { ...preview, columns: [] },
    { ...preview, columns: [{ ...preview.columns[0], name: "한글" }] }, { ...preview, columns: [...preview.columns, ...preview.columns] }]) {
    await assert.rejects(fetchTestExportPreview(id, {}, { fetchImpl: async () => Response.json(value) }), /invalid_test_export_response/);
  }
  await assert.rejects(fetchTestExportPreview(id, {}, { fetchImpl: async () => Response.json({ detail: "private raw event" }, { status: 503 }) }), /test_export_unavailable/);
  await assert.rejects(fetchSelectedTestExport(id, { columns: ["event_id"] }, { fetchImpl: async () => Response.json({ detail: "test_export_selection_changed" }, { status: 409 }) }), /test_export_selection_changed/);
  assert.match(testExportError({ message: "test_export_selection_changed" }, w), /대상 다시 확인/);
  assert.doesNotMatch(testExportError({ message: "private raw event" }, w), /private/);
});
