// Fixed diagnostic presentation only. Never render provider text or raw logs.
const JOB_KINDS = new Set(["format_discovery", "rule_generation"]);
const ACTIVE = new Set(["pending", "running", "processing"]);
const STAGES = new Set(["response", "json", "schema", "rule_validation", "authoring", "discovery", "transport"]);
const FINISH_REASONS = new Set(["stop", "length", "max_tokens", "max_output_tokens", "tool_calls", "function_call", "content_filter", "refusal", "unknown"]);
const ERROR_CODES = new Set([
  "parser_workbench_output_incomplete", "parser_workbench_json_invalid", "parser_workbench_invalid_response",
  "parser_workbench_schema_invalid", "parser_workbench_authoring_failed", "parser_workbench_invalid_rule",
  "parser_workbench_invalid_discovery", "parser_workbench_attempt_limit", "parser_workbench_context_limit",
  "parser_workbench_response_limit", "parser_workbench_revision_conflict", "parser_workbench_frozen_request_mismatch",
  "parser_workbench_refusal", "parser_workbench_completion_not_finished", "parser_workbench_request_rejected",
  "parser_workbench_permission_denied", "parser_workbench_rate_limited", "parser_workbench_provider_unavailable",
  "parser_workbench_http_error", "parser_suggestion_timeout", "parser_suggestion_connection_failed",
  "parser_suggestion_invalid_output", "parser_suggestion_failed", "parser_model_profile_changed", "parser_profile_changed",
  "parser_internal_vllm_required", "parser_verified_profile_required", "parser_vllm_target_blocked", "ok",
  "parser_model_profile_not_verified", "parser_internal_model_required", "parser_target_not_allowed",
  "parser_authoring_not_qualified", "parser_groups_invalid", "parser_group_membership_invalid",
  "parser_group_partition_required", "parser_group_confirmation_required", "parser_rule_bundle_invalid",
  "parser_regex_unavailable", "parser_regex_capture_invalid", "parser_regex_match_capture_forbidden",
  "parser_regex_invalid", "parser_regex_no_match", "parser_regex_ambiguous", "parser_regex_empty_capture",
  "parser_marker_missing", "parser_marker_ambiguous", "parser_markers_invalid", "parser_json_invalid",
  "parser_json_limit", "parser_json_duplicate_keys", "parser_json_pointer_missing", "parser_json_pointer_invalid",
  "parser_json_pointer_limit", "parser_json_value_limit", "parser_extraction_empty", "parser_extraction_not_string",
  "parser_source_mapping_invalid", "parser_source_invalid", "parser_source_encoding_invalid",
  "parser_rule_source_limit", "parser_rule_time_limit", "parser_request_not_qualified", "parser_rule_not_matched",
  "parser_prefix_mismatch", "parser_json_value_mismatch", "parser_json_string_missing", "parser_match_condition_failed",
  "parser_rule_label_invalid", "parser_rule_text_invalid", "parser_rule_id_duplicate", "source_span_invalid",
  "vllm_output_incomplete", "vllm_refusal", "vllm_completion_not_finished", "vllm_invalid_response",
]);
const ISSUE_TYPES = new Set(["missing", "extra_forbidden", "literal_error", "string_type", "string_too_long",
  "string_too_short", "list_type", "too_long", "too_short", "int_type", "bool_type", "union_tag_invalid",
  "union_tag_not_found", "value_error", "validation_error", "dict_type", "model_type",
  "json_invalid", "json_duplicate_key", "json_non_finite", "json_size_limit", "json_depth_limit",
  "json_encoding_invalid", "json_top_level_object_required"]);
const FIELD_PARTS = new Set(["$", "[]", "<unknown>", "schema_version", "groups", "group_id", "name", "waf_vendor",
  "case_ids", "description", "reason_ko", "limitations_ko", "match", "all", "any", "kind", "value", "json_pointer",
  "start_marker", "end_marker", "start_anchor", "pattern", "extract", "capture", "line_endings",
  "request_line_whitespace", "skip_leading_blank_lines", "target_rule_id"]);
const MATCH_KINDS = new Set(["starts_with", "json_equals", "json_string_exists", "marker_pair_exists", "regex_match"]);
const MATCH_FIELDS = new Set(["all", "any"].flatMap(mode => Array.from({ length: 8 }, (_, index) => `match.${mode}.${index}`)));
const MATCH_CODES = new Set(["ok", "parser_prefix_mismatch", "parser_json_value_mismatch", "parser_json_string_missing",
  "parser_match_condition_failed", "parser_extraction_empty", "parser_extraction_not_string", "parser_json_duplicate_keys",
  "parser_json_invalid", "parser_json_limit", "parser_json_pointer_missing", "parser_marker_ambiguous",
  "parser_marker_missing", "parser_regex_ambiguous", "parser_regex_capture_invalid", "parser_regex_empty_capture",
  "parser_regex_invalid", "parser_regex_match_capture_forbidden", "parser_regex_no_match", "parser_regex_unavailable",
  "parser_rule_bundle_invalid", "parser_rule_time_limit", "parser_source_invalid", "parser_source_mapping_invalid"]);
const count = value => Number.isSafeInteger(value) && value >= 0 && value <= 1_000_000_000 ? value : null;
const code = value => ERROR_CODES.has(value) ? value : "unrecorded_error";

function byAdmission(left, right) {
  const a = Date.parse(left.job.created_at), b = Date.parse(right.job.created_at);
  if (Number.isFinite(a) && Number.isFinite(b) && a !== b) return a - b;
  const revision = (count(left.job.session_revision) ?? 0) - (count(right.job.session_revision) ?? 0);
  return revision || left.index - right.index;
}

export function parserJobTimeline(jobs = []) {
  const ordered = (Array.isArray(jobs) ? jobs : []).filter(job => job && JOB_KINDS.has(job.kind))
    .map((job, index) => ({ job, index })).sort(byAdmission);
  const latest = new Map();
  for (const row of ordered) latest.set(row.job.kind, row);
  const discovery = latest.get("format_discovery"), generation = latest.get("rule_generation");
  // Rediscovery resets the groups, so an older generation is historical even
  // when it remains the last recorded generation job.
  if (discovery && generation && byAdmission(generation, discovery) < 0) latest.delete("rule_generation");
  // A new generation follows explicit group confirmation, including manual
  // confirmation after failed discovery. That failure is no longer current.
  if (discovery?.job.status === "failed" && generation && byAdmission(discovery, generation) < 0) latest.delete("format_discovery");
  const currentRows = [...latest.values()].sort(byAdmission), current = new Set(currentRows);
  return {
    latest: currentRows.map(row => row.job),
    failures: currentRows.filter(row => row.job.status === "failed").map(row => row.job),
    history: ordered.filter(row => !current.has(row)).map(row => row.job).reverse(),
  };
}

function safeIssue(issue) {
  const parts = typeof issue?.field === "string" && issue.field.length <= 256 ? issue.field.split(".") : [];
  return {
    field: parts.length ? parts.map(part => FIELD_PARTS.has(part) ? part : "<unknown>").join(".") : "<unknown>",
    type: ISSUE_TYPES.has(issue?.type) || ERROR_CODES.has(issue?.type) ? issue.type : "validation_error",
  };
}

function safeMatchCheck(check) {
  const index = count(check?.condition_index);
  if (typeof check?.sample_id !== "string" || check.sample_id.length !== 4
    || !/^c(?:00[1-9]|0[1-9][0-9]|100)$/.test(check.sample_id)
    || index === null || index > 7 || !MATCH_FIELDS.has(check?.field) || !check.field.endsWith(`.${index}`)
    || !MATCH_KINDS.has(check?.kind) || typeof check?.matched !== "boolean") return null;
  return { sample_id: check.sample_id, condition_index: index, field: check.field, kind: check.kind,
    matched: check.matched, code: MATCH_CODES.has(check.code) ? check.code : "parser_match_condition_failed" };
}

export function parserJobDiagnostics(job) {
  const diagnostics = job?.summary?.diagnostics;
  const attempts = (Array.isArray(diagnostics?.attempts) ? diagnostics.attempts : []).slice(0, 100).map(item => ({
    ...(count(item?.attempt) !== null ? { attempt: item.attempt } : {}),
    ...(typeof item?.correction === "boolean" ? { correction: item.correction } : {}),
    stage: STAGES.has(item?.stage) ? item.stage : "unrecorded",
    code: code(item?.code),
    ...(count(item?.group_index) > 0 ? { group_index: item.group_index } : {}),
    ...(FINISH_REASONS.has(item?.finish_reason) ? { finish_reason: item.finish_reason } : {}),
    ...(count(item?.output_limit) !== null ? { output_limit: item.output_limit } : {}),
    ...(item?.usage && typeof item.usage === "object" ? { usage: Object.fromEntries(
      ["input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens"]
        .filter(key => count(item.usage[key]) !== null).map(key => [key, item.usage[key]])) } : {}),
    ...(Array.isArray(item?.issues) ? { issues: item.issues.slice(0, 20).map(safeIssue) } : {}),
    ...(Array.isArray(item?.match_checks) ? { match_checks: item.match_checks.slice(0, 64).map(safeMatchCheck).filter(Boolean) } : {}),
    ...(Array.isArray(item?.failed_sample_ids) ? { failed_sample_count: item.failed_sample_ids.length } : {}),
  }));
  return {
    error_code: job?.status === "failed" ? code(job?.error_code || diagnostics?.last_error_code) : null,
    last_error_code: diagnostics?.last_error_code ? code(diagnostics.last_error_code) : null,
    error_stage: STAGES.has(diagnostics?.last_error_stage) ? diagnostics.last_error_stage : "unrecorded",
    attempt_count: count(diagnostics?.attempt_count),
    correction_count: count(diagnostics?.correction_count),
    call_reservations: count(job?.summary?.call_count),
    attempts,
  };
}

export function parserJobFailure(job, english = false) {
  const w = (ko, en) => english ? en : ko;
  const diagnostics = parserJobDiagnostics(job), value = diagnostics.error_code;
  const matchFailure = value === "parser_rule_not_matched"
    || (["parser_workbench_authoring_failed", "parser_authoring_not_qualified"].includes(value)
      && diagnostics.attempts.at(-1)?.issues?.some(issue => issue.type === "parser_rule_not_matched"));
  let message = w("작업을 완료하지 못했으며 자세한 원인은 기록되지 않았습니다.", "The job did not finish, and its cause was not recorded.");
  let action = w("다시 요청하면 새 작업 이력을 남깁니다.", "Request again to create a new job record.");
  if (["parser_workbench_output_incomplete", "vllm_output_incomplete", "parser_workbench_completion_not_finished", "vllm_completion_not_finished"].includes(value)) {
    message = w("모델이 응답을 끝까지 생성하지 못했습니다.", "The model did not complete its response.");
    action = w("모델의 출력 한도와 종료 사유를 확인한 뒤 다시 요청하세요.", "Check the model output limit and finish reason before requesting again.");
  } else if (["parser_workbench_json_invalid", "parser_workbench_invalid_response", "vllm_invalid_response"].includes(value)) {
    message = w("모델 응답을 JSON으로 읽지 못했습니다.", "The model response could not be read as JSON.");
    action = w("모델의 JSON 출력을 확인한 뒤 다시 요청하세요.", "Check the model JSON output before requesting again.");
  } else if (["parser_workbench_schema_invalid", "parser_workbench_invalid_discovery", "parser_suggestion_invalid_output", "parser_groups_invalid", "parser_group_membership_invalid", "parser_group_partition_required", "parser_rule_bundle_invalid"].includes(value)) {
    message = w("모델 응답이 형식 또는 규칙 정의와 맞지 않습니다.", "The model response does not match the format or rule definition.");
    action = w("작업 상세에서 오류 항목을 확인한 뒤 다시 요청하세요.", "Review the validation issues in job details before requesting again.");
  } else if (matchFailure) {
    message = w("형식 식별 조건이 일부 입력과 맞지 않습니다.", "The format matching conditions do not match some inputs.");
    action = w("형식 묶음과 조건을 확인하세요.", "Review the format groups and matching conditions.");
  } else if (["parser_workbench_authoring_failed", "parser_authoring_not_qualified", "parser_request_not_qualified", "parser_source_mapping_invalid", "source_span_invalid", "parser_extraction_empty", "parser_extraction_not_string", "parser_json_pointer_missing", "parser_marker_missing", "parser_marker_ambiguous", "parser_regex_no_match", "parser_regex_ambiguous", "parser_regex_empty_capture"].includes(value)) {
    message = w("제안한 규칙으로 일부 입력을 올바르게 추출하지 못했습니다.", "The proposed rule could not correctly extract some inputs.");
    action = w("형식 묶음과 제안용 입력을 대조한 뒤 다시 요청하세요.", "Review the format groups against the authoring inputs before requesting again.");
  } else if (["parser_regex_invalid", "parser_regex_capture_invalid", "parser_regex_match_capture_forbidden", "parser_markers_invalid", "parser_json_pointer_invalid", "parser_json_pointer_limit", "parser_json_value_limit", "parser_rule_label_invalid", "parser_rule_text_invalid", "parser_rule_id_duplicate"].includes(value)) {
    message = w("생성한 규칙의 문법이나 추출 설정이 올바르지 않습니다.", "The generated rule has invalid syntax or extraction settings.");
    action = w("작업 상세에서 규칙 오류를 확인한 뒤 다시 요청하세요.", "Review the rule errors in job details before requesting again.");
  } else if (["parser_json_invalid", "parser_json_duplicate_keys"].includes(value)) {
    message = w("규칙이 읽으려는 JSON 입력을 확인하지 못했습니다.", "The rule could not read the input as JSON.");
    action = w("제안용 입력의 JSON 형식과 규칙의 키 경로를 대조하세요.", "Compare the authoring input JSON format with the rule key path.");
  } else if (["parser_source_invalid", "parser_source_encoding_invalid"].includes(value)) {
    message = w("입력 형식이나 문자 인코딩을 처리하지 못했습니다.", "The input format or character encoding could not be processed.");
    action = w("선택한 입력 형식을 확인한 뒤 다시 요청하세요.", "Review the selected input format before requesting again.");
  } else if (value === "parser_group_confirmation_required") {
    message = w("규칙을 생성할 형식 구성이 확인되지 않았습니다.", "The format groups for rule generation have not been confirmed.");
    action = w("형식 구성을 확인한 뒤 규칙을 요청하세요.", "Confirm the format groups before requesting rules.");
  } else if (value === "parser_suggestion_timeout") {
    message = w("모델 응답 대기 시간이 초과되었습니다.", "The model response timed out.");
    action = w("모델 상태와 처리 시간을 확인한 뒤 다시 요청하세요.", "Check model availability and processing time before requesting again.");
  } else if (value === "parser_suggestion_connection_failed") {
    message = w("모델 서버에 연결하지 못했습니다.", "The model server could not be reached.");
    action = w("서버 주소와 연결 상태를 확인한 뒤 다시 요청하세요.", "Check the server address and connectivity before requesting again.");
  } else if (["parser_workbench_permission_denied", "parser_model_profile_not_verified", "parser_internal_model_required", "parser_target_not_allowed", "parser_internal_vllm_required", "parser_verified_profile_required", "parser_vllm_target_blocked"].includes(value)) {
    message = w("모델 호출 권한이나 승인 설정을 확인하지 못했습니다.", "The model request was blocked by authorization or approval settings.");
    action = w("내부 모델 검증 상태와 허용 대상을 확인하세요.", "Check the internal model validation and allowed target settings.");
  } else if (value === "parser_workbench_rate_limited") {
    message = w("모델 서버의 요청 한도에 도달했습니다.", "The model server request limit was reached.");
    action = w("서버 상태를 확인하고 잠시 뒤 다시 요청하세요.", "Check the server status and request again later.");
  } else if (value === "parser_workbench_provider_unavailable") {
    message = w("모델 서버가 요청을 처리하지 못했습니다.", "The model server could not process the request.");
    action = w("모델 서버 상태를 확인한 뒤 다시 요청하세요.", "Check the model server status before requesting again.");
  } else if (["parser_workbench_refusal", "vllm_refusal", "parser_workbench_request_rejected", "parser_workbench_http_error"].includes(value)) {
    message = w("모델 서버가 요청을 거절했습니다.", "The model server rejected the request.");
    action = w("모델의 지원 기능과 요청 설정을 확인하세요.", "Check the model capabilities and request settings.");
  } else if (["parser_workbench_context_limit", "parser_workbench_response_limit", "parser_rule_source_limit", "parser_rule_time_limit", "parser_json_limit"].includes(value)) {
    message = w("입력이나 처리 결과가 작업 한도를 넘었습니다.", "The input or result exceeded a workbench limit.");
    action = w("입력 수와 형식 묶음 크기를 확인한 뒤 다시 요청하세요.", "Check the input count and format group size before requesting again.");
  } else if (["parser_workbench_revision_conflict", "parser_workbench_frozen_request_mismatch", "parser_model_profile_changed", "parser_profile_changed"].includes(value)) {
    message = w("입력 또는 설정이 변경되어 작업을 완료하지 못했습니다.", "The job could not finish because its inputs or settings changed.");
    action = w("저장된 상태를 새로고침한 뒤 다시 요청하세요.", "Refresh the saved state before requesting again.");
  } else if (value === "parser_regex_unavailable") {
    message = w("정규식 처리 엔진을 사용할 수 없습니다.", "The regex processing engine is unavailable.");
    action = w("배포 이미지에 RE2가 포함됐는지 확인하세요.", "Check that RE2 is included in the deployment image.");
  } else if (value === "parser_workbench_attempt_limit") {
    message = w("이 작업의 모델 요청 한도에 도달했습니다.", "This job reached its model request limit.");
    action = w("입력과 진단을 확인한 뒤 명시적으로 다시 요청하세요.", "Review the inputs and diagnostics before explicitly requesting again.");
  }
  return { message, action, ...diagnostics };
}

export function parserJobKindLabel(kind, english = false) {
  return kind === "format_discovery" ? english ? "Format discovery" : "형식 찾기"
    : kind === "rule_generation" ? english ? "Rule generation" : "규칙 생성"
      : english ? "Model job" : "모델 작업";
}

export function parserJobStatusLabel(status, english = false) {
  const labels = { pending: ["대기", "Queued"], running: ["진행 중", "Running"], processing: ["진행 중", "Running"],
    completed: ["완료", "Completed"], failed: ["실패", "Failed"], canceled: ["취소", "Canceled"] };
  return labels[status]?.[english ? 1 : 0] || (english ? "Not recorded" : "미기록");
}

export function parserJobIsActive(job) { return ACTIVE.has(job?.status); }
