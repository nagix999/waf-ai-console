const version = "test-performance-summary-v1";
const scopeFields = ["evaluation_id", "reference_basis", "difficulty", "test_category", "difficulty_missing", "test_category_missing", "case_filter"];
const durationFields = ["reported_sum_ms", "min_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "mean_ms"];
export const timingKeys = ["decision_elapsed_ms", "analysis_queue_ms", "analysis_processing_ms", "result_ready_elapsed_ms"];
const accountingFields = ["recorded_cases", "unrecorded_cases", "reported_output_attempts", "reported_output_retries", "reported_citation_repairs", "reported_decision_reviews", "cases_with_output_retries", "cases_with_citation_repairs", "cases_with_decision_reviews"];
const roleFields = ["first_attempt_ms", "output_retry_ms", "citation_repair_ms", "decision_review_ms", "slot_wait_ms", "invocation_ms"];
const roleKeys = ["primary", "verifier", "legacy_evidence_editor"];
const stateKeys = ["pending", "processing", "running", "completed", "failed", "canceled", "skipped"];
const mapping = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const number = value => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= Number.MAX_SAFE_INTEGER ? value : null;
export const performanceCount = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
const counts = value => {
  const available = value && typeof value === "object" && !Array.isArray(value), source = mapping(value);
  return Object.fromEntries(stateKeys.map(key => [key, Object.hasOwn(source, key) ? performanceCount(source[key]) : available ? 0 : null]));
};

// Group search, sorting and pagination must not change the case population.
// Case filters stay in a JSON body rather than browser URLs or query strings.
export function testPerformanceScope(query = {}) {
  const source = mapping(query);
  return Object.fromEntries(scopeFields.filter(key => source[key] !== undefined && source[key] !== null && source[key] !== "").map(key => [key, source[key]]));
}

export function performanceMetric(raw) {
  const source = mapping(raw);
  return { recorded: performanceCount(source.recorded), unrecorded: performanceCount(source.unrecorded),
    ...Object.fromEntries(durationFields.map(key => [key, number(source[key])])) };
}

// Test and Runtime use the same server-computed, completed-only measurements.
// Do not fall back to a Runtime latency_summary, which includes failed work.
export function completedTimingSummary(raw) {
  const source = mapping(raw), timings = source.completed_timings;
  return {
    completed_cases: performanceCount(source.completed_cases),
    completed_timings: timings && typeof timings === "object" && !Array.isArray(timings)
      ? Object.fromEntries(timingKeys.map(key => [key, performanceMetric(timings[key])])) : null,
  };
}

const tokenMetric = raw => {
  const source = mapping(raw);
  return { recorded: performanceCount(source.recorded), unrecorded: performanceCount(source.unrecorded),
    ...Object.fromEntries(["sum", "mean", "p50", "p95", "max"].map(key => [key, number(source[key])])) };
};

const fieldNames = {
  $: ["출력 전체", "Output"], evidence: ["근거", "Evidence"], evidence_relations: ["근거 연결", "Evidence links"],
  signature_assessment: ["탐지 내용 비교", "Signature comparison"], summary_ko: ["분석 요약", "Analysis summary"],
  verdict: ["판정", "Verdict"], confidence_score: ["판정 확신도", "Confidence"], threat_analysis: ["위협 분석", "Threat analysis"],
  decision_issue: ["확정 조건", "Decision conditions"], input_truncated: ["입력 범위", "Input scope"],
  interpretation_conflict: ["해석 차이", "Interpretation conflict"], capture_assessment: ["로그 수집 범위", "Capture assessment"],
  corrections: ["근거 교정", "Evidence correction"], analyst_checks: ["확인 사항", "Checks"],
  retained_evidence_ids: ["재검토 근거", "Review evidence"],
  recommended_checks: ["확인 사항", "Checks"], tuning_recommendation: ["정책 제안", "Policy suggestion"],
};
const issueNames = {
  evidence_text_required: ["근거 설명 누락", "Missing evidence explanation"],
  evidence_source_repeated: ["근거 원문 중복", "Repeated source"],
  evidence_expansion_limit: ["근거 수 초과", "Too many evidence items"],
  evidence_support_conflict: ["연결된 근거의 분류 불일치", "Classification mismatch in linked evidence"],
  evidence_source_unavailable: ["근거 원문 연결 실패", "Source unavailable"],
  evidence_source_support_forbidden: ["허용되지 않은 근거 분류", "Unsupported evidence direction"],
  decisive_verdict_requires_directional_content: ["판정을 뒷받침할 근거 부족", "Verdict lacks supporting evidence"],
  decisive_verdict_requires_evidence: ["판정을 뒷받침할 근거 부족", "Verdict lacks supporting evidence"],
  evidence_relation_invalid: ["근거 연결 형식 오류", "Invalid evidence link"],
  evidence_relation_conflicting_supports: ["근거 연결의 분류 충돌", "Conflicting linked evidence"],
  decision_source_unselected: ["확정 조건의 원문 연결 실패", "Decision-condition source unavailable"],
  decision_source_repeated: ["확정 조건의 원문 중복", "Repeated decision-condition source"],
  evidence_id_repeated: ["근거 번호 중복", "Repeated evidence ID"],
  evidence_link_repeated: ["관련 근거 중복", "Repeated evidence link"],
  evidence_link_unknown: ["관련 근거 연결 실패", "Evidence link unavailable"],
  evidence_link_self: ["근거 자기 연결", "Self-referencing evidence"],
  evidence_link_cycle: ["근거 순환 연결", "Circular evidence links"],
  decision_evidence_unselected: ["확정 조건의 근거 연결 실패", "Decision-condition evidence unavailable"],
  decision_evidence_repeated: ["확정 조건의 근거 중복", "Repeated decision-condition evidence"],
  decision_review_context_invalid: ["근거 재검토 기준 확인 실패", "Evidence review context unavailable"],
  review_evidence_repeated: ["재검토 근거 번호 중복", "Repeated review evidence"],
  review_evidence_not_eligible: ["재검토 대상이 아닌 근거", "Evidence not eligible for review"],
  review_evidence_omitted: ["재검토 근거 누락", "Missing review evidence"],
  review_evidence_dependency_missing: ["재검토 관련 근거 누락", "Missing review dependency"],
  missing: ["필수 항목 누락", "Required value missing"], extra_forbidden: ["허용되지 않은 항목", "Unexpected field"],
  summary_must_describe_request: ["분석 요약 내용 오류", "Invalid analysis summary"],
};
const jsonIssues = new Set(["json_invalid", "json_duplicate_key", "json_non_finite", "json_size_limit", "json_depth_limit", "json_encoding_invalid", "json_top_level_object_required", "decode_error"]);
const typeIssues = new Set(["validation_error", "literal_error", "enum", "string_type", "string_too_short", "string_too_long", "list_type", "too_short", "too_long", "int_type", "int_parsing", "float_type", "float_parsing", "bool_type", "dict_type", "model_type", "none_required", "greater_than", "greater_than_equal", "less_than", "less_than_equal", "finite_number", "value_error"]);
const knownName = (names, key) => typeof key === "string" && Object.hasOwn(names, key) ? key : null;
export const performanceFieldLabel = (key, words = ko => ko) => words(...(fieldNames[knownName(fieldNames, key)] || ["출력 항목", "Output field"]));
export function performanceIssueLabel(key, words = ko => ko) {
  const label = issueNames[knownName(issueNames, key)];
  return words(...(label || (jsonIssues.has(key) ? ["JSON 형식 오류", "Invalid JSON"] : typeIssues.has(key) ? ["타입 또는 값 제한 오류", "Invalid type or value"] : ["기타 출력 검증 오류", "Other output validation issue"])));
}

function roleSummary(raw) {
  const source = mapping(raw), failure = mapping(source.output_failures), outcomes = mapping(failure.attempt_outcomes);
  return { accounting: Object.fromEntries(accountingFields.map(key => [key, performanceCount(mapping(source.accounting)[key])])),
    ...Object.fromEntries(roleFields.map(key => [key, performanceMetric(source[key])])),
    tokens: Object.fromEntries(["input_tokens", "output_tokens", "first_attempt_output_tokens"].map(key => [key, tokenMetric(mapping(source.tokens)[key])])),
    output_failures: { ...Object.fromEntries(["recorded_cases", "unrecorded_cases", "cases_with_output_failure", "completed_cases_with_output_failure", "validation_attempts_without_recorded_issues"].map(key => [key, performanceCount(failure[key])])),
      attempt_outcomes: Object.fromEntries(["completed", "output_validation", "output_limit", "timeout", "incomplete_unspecified", "unrecorded"].map(key => [key, performanceCount(outcomes[key])])),
      validation_issues: Array.isArray(failure.validation_issues) ? failure.validation_issues.map(item => ({ field: knownName(fieldNames, item?.field), type: knownName(issueNames, item?.type) || (jsonIssues.has(item?.type) || typeIssues.has(item?.type) ? item.type : null), attempts: performanceCount(item?.attempts) })).filter(item => item.attempts !== null) : [],
    } };
}

export function performanceSummary(raw) {
  if (mapping(raw).version !== version) return null;
  const source = mapping(raw), display = mapping(source.display);
  return { version, cases: performanceCount(source.cases), statuses: counts(source.statuses),
    ...completedTimingSummary(source),
    timings: Object.fromEntries(timingKeys.map(key => [key, performanceMetric(mapping(source.timings)[key])])),
    phases: Object.fromEntries(["llm_primary", "llm_verifier", "llm_evidence_editor"].map(key => { const phase = mapping(mapping(source.phases)[key]); return [key, { entered_cases: performanceCount(phase.entered_cases), not_observed_cases: performanceCount(phase.not_observed_cases), states: counts(phase.states), duration: performanceMetric(phase.duration) }]; })),
    roles: Object.fromEntries(roleKeys.map(key => [key, roleSummary(mapping(source.roles)[key])])),
    display: { no_job_cases: performanceCount(display.no_job_cases), states: counts(display.states),
      ...Object.fromEntries(["queue_ms", "processing_ms", "post_decision_elapsed_ms"].map(key => [key, performanceMetric(display[key])])) },
  };
}

export function performanceIsRunning(raw) {
  const data = performanceSummary(raw);
  return !!data && [data.statuses.pending, data.statuses.processing, data.display.states.pending, data.display.states.running].some(value => value !== null && value > 0);
}

export function durationText(ms, words = ko => ko) {
  const value = number(ms);
  if (value === null) return "—";
  return value >= 60000 ? words(`${(value / 60000).toFixed(1)}분`, `${(value / 60000).toFixed(1)} min`) : words(`${(value / 1000).toFixed(1)}초`, `${(value / 1000).toFixed(1)} s`);
}
export const performanceNumber = value => number(value) === null ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: 1 });

export function retryFraction(role) {
  const numerator = performanceCount(role?.accounting?.cases_with_output_retries), denominator = performanceCount(role?.accounting?.recorded_cases);
  return numerator !== null && denominator !== null && denominator > 0 && numerator <= denominator ? numerator / denominator : null;
}

export function topPerformanceIssues(summary, limit = 5) {
  return roleKeys.flatMap(role => summary?.roles?.[role]?.output_failures?.validation_issues?.map((item, index) => ({ ...item, role, rowKey: `${role}-${index}` })) || [])
    .filter(item => item.attempts > 0).sort((a, b) => b.attempts - a.attempts).slice(0, limit);
}
