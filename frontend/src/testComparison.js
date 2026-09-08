export const initialTestComparisonState = () => ({ open: false, queryText: "", query: { q: "", limit: 10, offset: 0 }, baselineId: "", limit: 25, offset: 0, changes_only: false });

export function testComparisonChange(state, action) {
  if (action.type === "open") return { ...state, open: action.value };
  if (action.type === "draft") return { ...state, queryText: action.value };
  if (action.type === "search") return { ...state, query: { ...state.query, q: state.queryText.trim(), offset: 0 } };
  if (action.type === "search_page") return { ...state, query: { ...state.query, offset: Math.max(0, state.query.offset + action.direction * state.query.limit) } };
  if (action.type === "baseline") return { ...state, baselineId: action.id, offset: 0 };
  if (action.type === "changes") return { ...state, changes_only: action.value, offset: 0 };
  if (action.type === "limit") return { ...state, limit: action.value, offset: 0 };
  if (action.type === "page") return { ...state, offset: Math.max(0, state.offset + action.direction * state.limit) };
  return state;
}

export function testComparisonQuery(state) {
  return { baseline_id: state.baselineId, limit: state.limit, offset: state.offset, changes_only: state.changes_only };
}

export const comparisonExclusions = {
  baseline_missing: "기준 실행에 문항 없음", candidate_missing: "후보 실행에 문항 없음",
  analysis_missing: "분석 이력 없음", item_identity_mismatch: "문항과 분석 식별 정보 불일치",
  fingerprint_missing: "입력 지문 미기록", input_mismatch: "접수한 이벤트 내용이 다름",
  reference_missing: "접수 당시 참고 답안 없음", reference_mismatch: "접수 당시 참고 답안 또는 출처 조건이 다름",
  both_not_evaluable: "양쪽 모두 평가 제외", baseline_not_evaluable: "기준 실행 평가 제외", candidate_not_evaluable: "후보 실행 평가 제외",
};
export const comparisonChanges = { improved: "답안 일치로 변경", regressed: "답안 불일치로 변경", changed: "판정 변경", unchanged: "판정 유지", not_comparable: "비교 제외" };
export const comparisonWarnings = {
  run_source_system_differs: "테스트마다 연동 시스템 식별값이 달라 실제 모델 입력의 메타데이터도 같지 않을 수 있습니다.",
  prompt_budget_may_change_submitted_input: "프롬프트 길이에 따라 입력 예산과 모델에 전달된 원문 범위가 달라질 수 있습니다.",
  comparison_is_not_causal_proof: "이 비교만으로 차이가 프롬프트 변경 때문에 발생했다고 단정할 수 없습니다.",
  performance_excludes_noncomparable_analyses: "시간·토큰은 비교 가능한 문항의 기록된 모든 시도 기준입니다. 제외 문항은 포함하지 않습니다.",
  recorded_tokens_are_not_billing_total: "기록된 토큰 합계는 실제 청구량이나 전체 실행의 비용이 아닙니다.",
  duplicate_event_rows_ignored: "중복 이벤트 식별자 행은 중복 집계하지 않았습니다.",
  reference_source_ref_different: "참고 답안의 출처 식별 정보가 다릅니다. 답안의 동일성을 확인하세요.",
  case_metadata_different: "문항명·난이도·유형 중 다른 정보가 있습니다. 문항 표는 기준 실행 정보를 우선 표시합니다.",
  model_configuration_unknown: "모델 설정 기록이 부족해 같은 조건인지 확인할 수 없습니다.",
  model_configuration_different: "모델 또는 실행 설정이 달라 프롬프트만의 비교가 아닙니다.",
  execution_mode_unknown: "실행 모드 기록을 확인할 수 없습니다.", execution_mode_different: "두 실행의 실행 모드가 다릅니다.",
  verifier_threshold_unknown: "독립 검증 호출 임계값 기록이 부족합니다.", verifier_threshold_different: "독립 검증 호출 임계값이 다릅니다.",
  input_schema_unknown: "입력 스키마 버전 기록이 부족합니다.", input_schema_different: "두 실행의 입력 스키마 정의가 다릅니다.",
  fixed_rules_unknown: "고정 시스템 규칙 기록이 부족합니다.", fixed_rules_different: "공통 시스템 규칙 버전도 다릅니다. 편집 지침만 바뀐 비교가 아닙니다.",
  input_truncation_present: "일부 문항에서 모델 입력이 생략되었습니다.", input_truncation_different: "두 실행의 입력 생략 여부가 다릅니다.",
  execution_identity_unknown: "실제 모델 실행 식별 정보를 확인할 수 없는 문항이 있습니다.", execution_identity_mismatch: "접수 설정과 실제 모델 실행 식별 정보가 맞지 않는 문항이 있습니다.",
  executed_verifier_threshold_unknown: "실제 실행의 독립 검증 임계값을 확인할 수 없는 문항이 있습니다.", executed_verifier_threshold_mismatch: "접수 시 고정한 독립 검증 임계값과 실행 기록이 다릅니다.",
  token_usage_incomplete: "토큰 사용량에 미측정 기록이 있습니다. 기록된 합계를 전체 사용량으로 해석하지 마세요.",
  timing_incomplete: "일부 소요 시간 또는 실행 이력이 미기록입니다. 미기록을 0으로 계산하지 않습니다.",
};

export function comparisonReadError(error) {
  if (error?.status === 401) return "로그인 세션이 만료되었습니다. 다시 로그인하세요.";
  if (error?.message === "test_comparison_same_run") return "같은 테스트 실행끼리는 비교할 수 없습니다. 다른 실행을 선택하세요.";
  if (error?.status === 404) return "비교할 테스트 실행을 찾지 못했습니다. 목록을 다시 조회하세요.";
  return "테스트 비교를 조회하지 못했습니다. 대상과 상태를 확인한 뒤 다시 조회하세요.";
}

export function watchComparisonRead({ read, onUpdate, onUnauthorized }) {
  let active = true;
  const controller = new AbortController();
  void Promise.resolve().then(() => active ? read({ signal: controller.signal }) : undefined).then(data => {
    if (active) onUpdate({ data, loading: false, error: "" });
  }).catch(error => {
    if (!active) return;
    onUpdate({ data: null, loading: false, error: comparisonReadError(error) });
    if (error?.status === 401) onUnauthorized?.();
  });
  return () => { active = false; controller.abort(); };
}

export function tokenCountText(counter, missingHistories = 0) {
  if (!(counter?.measured_steps > 0) || !Number.isFinite(counter.known_sum)) return "미측정";
  return `${counter.known_sum.toLocaleString()}${counter.missing_steps > 0 || missingHistories > 0 ? " · 부분 측정" : ""}`;
}
