export const importCategories = {
  new: ["신규 · 기대 판정 있음", "New · expected verdict set"],
  missing_reference: ["신규 · 기대 판정 없음", "New · no expected verdict"],
  duplicate: ["중복", "Duplicate"], reference_conflict: ["판정 충돌", "Verdict conflict"],
  unavailable: ["가져올 수 없음", "Unavailable"],
};
export function importCounts(preview) {
  // The existing API's new_count includes missing_reference_count.
  return { new: preview.new_count - preview.missing_reference_count,
    missing_reference: preview.missing_reference_count, duplicate: preview.duplicate_count,
    reference_conflict: preview.reference_conflict_count, unavailable: preview.unavailable_count };
}
const messages = {
  test_ingestion_open: ["문항 접수가 진행 중입니다. 접수를 닫은 뒤 복사하세요. 모델 처리가 끝날 때까지 기다릴 필요는 없습니다.", "Case ingestion is still open. Close ingestion before copying; model processing does not need to finish."],
  test_must_finish_before_import: ["테스트 처리가 끝나야 가져올 수 있습니다. API 테스트는 접수도 닫아 주세요.", "Finish the Test first. Close ingestion for an API Test as well."],
  test_run_not_found: ["테스트를 찾을 수 없습니다. 테스트 목록을 확인하세요.", "Test not found. Check the Test list."],
  test_import_membership_mismatch: ["선택한 문항이 이 테스트에 없습니다. 문항을 다시 선택하세요.", "A selected case is not in this Test. Select the cases again."],
  ground_truth_target_required: ["추가할 평가 데이터셋을 선택하세요.", "Choose a destination dataset."],
  invalid_ground_truth_target: ["저장 위치를 다시 선택하세요.", "Select the destination again."],
  dataset_not_found: ["평가 데이터셋을 찾을 수 없습니다. 다시 검색하세요.", "Dataset not found. Search again."],
  dataset_item_limit: ["한 평가 데이터셋에는 최대 5,000문항을 저장할 수 있습니다. 다른 평가 데이터셋을 선택하세요.", "A dataset holds up to 5,000 cases. Choose another dataset."],
  ground_truth_working_changed: ["평가 데이터셋 초안이 변경됐습니다. 다시 미리보기하세요.", "The dataset Draft changed. Preview again."],
  ground_truth_preview_not_found: ["미리보기 기록을 찾을 수 없습니다. 다시 미리보기하세요.", "Preview not found. Preview again."],
  ground_truth_preview_expired: ["미리보기가 만료됐습니다. 다시 미리보기하세요.", "The preview expired. Preview again."],
  ground_truth_import_source_changed: ["원본 테스트 자료가 변경됐습니다. 다시 미리보기하세요.", "The source Test data changed. Preview again."],
  ground_truth_import_already_confirmed: ["이미 저장한 미리보기입니다. 평가 데이터셋을 확인하세요.", "This preview has already been saved. Check the dataset."],
  ground_truth_import_idempotency_conflict: ["저장 요청이 이미 사용됐습니다. 다시 미리보기하세요.", "This save request has already been used. Preview again."],
};
export function importError(error, phase) {
  if (Object.hasOwn(messages, error?.message)) return messages[error.message];
  if (error?.status === 401) return ["로그인 후 다시 시도하세요.", "Sign in and retry."];
  if (error?.status === 422) return ["입력 형식과 선택한 저장 위치를 확인하세요.", "Check the input format and destination."];
  if (error?.status === 409) return ["자료가 변경됐습니다. 다시 미리보기하세요.", "The data changed. Preview again."];
  return phase === "confirm" ? ["저장을 확인하지 못했습니다. 같은 요청으로 다시 시도할 수 있습니다.", "Could not confirm the save. Retry uses the same request."]
    : ["사례를 확인하지 못했습니다. 다시 시도하세요.", "Could not inspect the cases. Please retry."];
}
