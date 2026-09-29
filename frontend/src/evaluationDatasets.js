// Presentation helpers only. API/DB Evaluation Dataset field names stay unchanged.
export const initialDatasetList = () => ({ query: "", offset: 0, limit: 20 });
export function draftCount(row) {
  const counts = [row.ready_count, row.needs_attention_count, row.excluded_count];
  return counts.every(Number.isFinite) ? counts.reduce((sum, count) => sum + count, 0) : null;
}
export function productionDatasetRevision(datasetId, groundTruth) {
  return groundTruth?.dataset_id === datasetId && Number.isInteger(groundTruth.dataset_revision)
    && groundTruth.dataset_revision > 0 ? groundTruth.dataset_revision : null;
}
export function canCopyTest(run) {
  // Fail closed for an incomplete response, independent of execution status.
  return run?.accepting_items === false;
}
