import assert from "node:assert/strict";
import test from "node:test";
import { canCopyTest, draftCount, initialDatasetList, productionDatasetRevision } from "./evaluationDatasets.js";
import { importCategories, importCounts, importError } from "./groundTruthImport.js";
import { writeAppHash, readAppHash, applyAppRoute } from "./appRoutes.js";

test("draft total and current Production revision do not use latest published revision", () => {
  const row = { id: "dataset", ready_count: 103, needs_attention_count: 2, excluded_count: 1, latest_published_revision: { revision: 4 } };
  assert.equal(draftCount(row), 106);
  assert.equal(draftCount({ total: 100 }), null);
  assert.equal(productionDatasetRevision(row.id, { dataset_id: row.id, dataset_revision: 3 }), 3);
  assert.equal(productionDatasetRevision(row.id, null), null);
  assert.equal(productionDatasetRevision(row.id, { dataset_id: "other", dataset_revision: 3 }), null);
  assert.equal(productionDatasetRevision(row.id, { dataset_id: row.id }), null);
});

test("copy requires explicitly closed ingestion, never a completed model status", () => {
  for (const status of ["pending", "processing", "completed", "failed", "stopped"]) {
    assert.equal(canCopyTest({ status, accepting_items: false }), true);
    assert.equal(canCopyTest({ status, accepting_items: true }), false);
    assert.equal(canCopyTest({ status }), false);
  }
  assert.equal(canCopyTest(null), false);
  assert.match(importError({ message: "test_ingestion_open" })[0], /접수를 닫은 뒤/);
});

test("copy preview categories are mutually exclusive and sum to the source total", () => {
  const counts = importCounts({ new_count: 10, missing_reference_count: 3, duplicate_count: 2, reference_conflict_count: 1, unavailable_count: 1 });
  assert.deepEqual(counts, { new: 7, missing_reference: 3, duplicate: 2, reference_conflict: 1, unavailable: 1 });
  assert.equal(Object.values(counts).reduce((sum, n) => sum + n, 0), 14);
  assert.match(importCategories.new[0], /기대 판정 있음/);
  assert.match(importCategories.missing_reference[0], /기대 판정 없음/);
});

test("dataset detail deep links preserve IDs but exclude search terms and drafts", () => {
  const datasetId = "11111111-1111-4111-8111-111111111111";
  const state = { page: "datasets", datasetId, datasetListState: { ...initialDatasetList(), query: "private-search", offset: 20 }, draft: "private-input" };
  const hash = writeAppHash(state);
  assert.equal(hash, `#evaluate/ground-truth/${datasetId}`);
  assert.equal(applyAppRoute({}, readAppHash(hash)).datasetId, datasetId);
  assert.equal(writeAppHash({ ...state, datasetId: null }), "#evaluate/ground-truth");
  assert.equal(readAppHash("#evaluate/ground-truth/private-search"), null);
});
