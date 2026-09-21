export const answerNames = { true_positive: "정탐", false_positive: "오탐", inconclusive: "보류" };
export const reviewNames = { draft: "미검토", reviewed: "검토 완료", approved: "승인됨" };

export function reviewActions(status) {
  const actions = { draft: ["reviewed"], reviewed: ["approved", "draft"], approved: ["draft"] };
  return Object.hasOwn(actions, status) ? actions[status] : [];
}

export function validationDataError(error) {
  const messages = {
    ground_truth_working_changed: "다른 작업으로 초안이 변경되었습니다. 새로고침 후 다시 확인하세요.",
    ground_truth_published_revision_required: "발행된 리비전이 필요합니다. Ground Truth에서 리비전을 먼저 발행하세요.",
    ground_truth_ready_cases_required: "입력과 답안이 준비된 문항이 하나 이상 있어야 발행할 수 있습니다.",
    ground_truth_exclusion_ack_required: "평가에서 빠지는 문항 수와 포함 비율을 확인해 주세요.",
    ground_truth_case_not_in_published_revision: "이 문항은 최근 발행 리비전에 없어 되돌릴 수 없습니다.",
    approved_ground_truth_required: "공식 평가에 사용할 발행 리비전이 없습니다. Ground Truth에서 문항을 확인하고 발행하세요.",
    official_evaluation_requires_llm: "공식 평가는 실제 LLM 실행 모드에서만 가능합니다.",
    official_ground_truth_fixed: "공식 평가 답안은 접수 당시 리비전으로 고정되어 있습니다.",
    dataset_changed_reload: "데이터셋이 변경되었습니다. 새로고침 후 다시 선택하세요.",
    reference_changed_reload: "선택한 분석의 답안이 변경되었습니다. 다시 확인한 뒤 저장하세요.",
    dataset_duplicate_input: "같은 입력의 문항이 이미 있습니다. 기존 문항을 수정하세요.",
    dataset_internal_models_required: "운영에서 가져온 문항은 내부 모델 전용입니다. Test의 Primary·Verifier·근거 정리 모델을 모두 vLLM으로 설정하세요.",
    dataset_current_schema_invalid: "현재 입력 스키마에 맞지 않는 문항이 있습니다. 문항 입력과 설정 → 입력 스키마를 확인하세요. 이번 테스트는 접수되지 않았습니다.",
    dataset_event_schema_invalid: "현재 입력 스키마에 맞지 않습니다. 필수 필드·타입·허용값을 확인하세요.",
    invalid_dataset_event: "이벤트 입력을 확인하세요. 답안·메모는 이벤트 JSON 밖의 전용 항목에 입력하세요.",
    dataset_empty: "먼저 문항을 추가하세요.",
    dataset_item_limit: "한 데이터셋에는 최대 5,000건을 저장할 수 있습니다.",
    dataset_not_found: "데이터셋을 찾을 수 없습니다. 목록을 새로고침하세요.",
    dataset_item_not_found: "문항을 찾을 수 없습니다. 데이터셋을 새로고침하세요.",
    dataset_review_verdict_required: "답안을 저장한 뒤 검토를 완료하세요. 보류도 답안으로 선택할 수 있습니다.",
    dataset_review_transition_invalid: "미검토 → 검토 완료 → 승인 순서로 진행하세요. 수정이 필요하면 미검토로 되돌릴 수 있습니다.",
    test_must_finish_before_rescoring: "모든 문항의 처리가 끝나야 재평가할 수 있습니다. API 테스트는 접수도 닫아 주세요.",
    test_model_profile_required: "설정에서 전체 검증을 통과한 Test 모델을 지정하세요.",
    test_run_idempotency_conflict: "같은 접수 요청의 내용이 변경되었습니다. 실행 이력을 확인하세요.",
    reference_idempotency_conflict: "이미 처리한 저장 요청입니다. 답안 이력을 확인하세요.",
    payload_too_large: "HTTP 원문이 허용 크기를 초과했습니다.",
  };
  return Object.hasOwn(messages, error?.message) ? messages[error.message] : (error?.status === 401 ? "로그인 후 다시 시도하세요." : error?.status === 422 ? "입력 형식과 필수 항목을 확인하세요." : "처리 결과를 확인하지 못했습니다. 새로고침 후 변경 이력을 확인하세요.");
}

export function selectedPageIds(ids, items) {
  const available = new Set(items.filter(item => item.analysis_id !== null).map(item => item.analysis_id || item.id));
  return [...new Set(ids)].filter(id => available.has(id));
}

export function datasetImportMessage(result) {
  return `${result.added}건 추가 · ${result.duplicates}건 중복 제외 · ${result.conflicts.length}건 충돌 · ${result.rejected.length}건 입력 오류`;
}
