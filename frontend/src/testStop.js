export function isTestStopped(run) {
  return Boolean(run?.stopped_at) || run?.status === "stopped";
}

export function canStopTest(run) {
  if (!run || isTestStopped(run) || run.model_test_run_id) return false;
  return run.can_stop ?? (["pending", "processing"].includes(run.status) || Boolean(run.accepting_items));
}

export function stopError(error, w) {
  const messages = {
    test_run_not_active: ["이미 실행이 끝났습니다. 새로고침해서 결과를 확인하세요.", "This test has finished. Refresh to see the results."],
    test_run_stop_not_supported_for_model_validation: ["150건 모델 검증은 이 화면에서 중지할 수 없습니다.", "150-case model validation cannot be stopped here."],
    test_run_not_found: ["테스트를 찾을 수 없습니다. 목록을 새로고침하세요.", "Test not found. Refresh the list."],
  };
  return w(...(messages[error?.message] || ["중지 여부를 확인하지 못했습니다. 자동 재전송하지 않습니다. 창을 닫고 새로고침해 상태를 확인하세요.", "Stop could not be confirmed. Nothing is automatically resent. Close this dialog and refresh to check the status."]));
}
