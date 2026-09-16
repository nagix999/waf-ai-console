import { retryPayload } from "./analysisRetry.js";

export function testRetryPayload(preview, requestKey, approved) {
  const ids = preview?.eligible_ids;
  if (!Array.isArray(ids) || !ids.length || ids.length > 5000 || ids.some(id => typeof id !== "string" || !id) || new Set(ids).size !== ids.length) throw new Error("invalid_retry_targets");
  return { ...retryPayload(requestKey, approved), analysis_ids: [...ids] };
}

export function testRetryNotice(result) {
  return `${result.duplicate ? "이미 접수한" : "재실행을 접수한"} 항목은 ${result.enqueued}건입니다.${result.skipped ? ` ${result.skipped}건은 재실행할 수 없어 제외했습니다.` : ""} 완료되면 결과와 평가 지표에 자동 반영됩니다.`;
}

export function afterTestRetry(filters) {
  return { ...filters, difficulty: "", test_category: "", difficulty_missing: false, test_category_missing: false,
    status: "", evaluation_outcome: "", cell: "", evaluation_id: "latest", offset: 0 };
}
