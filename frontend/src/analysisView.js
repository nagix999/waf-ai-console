export const searchFields = [
  ["all", "전체 검색 필드"], ["event_id", "Event ID"], ["company_name", "회사명"],
  ["source_system", "Source System"], ["src_ip", "Source IP (정확히)"],
  ["dest_ip", "Destination IP (정확히)"], ["signature", "Signature"],
  ["event_name", "이벤트명"], ["threat_category", "위협 유형"]
];

export const emptyFilters = {
  analysis_purpose: "", ingest_channel: "", q: "", search_field: "all",
  status: "", verdict: "", severity: "", waf_vendor: "", waf_action: "",
  review_state: "", model_profile: "", input_truncated: "", src_port: "", dest_port: "",
  confidence_min: "", confidence_max: "", created_from: "", created_to: "",
  event_id: "", company_name: "", source_system: "", src_ip: "", dest_ip: "",
  signature: "", event_name: "", threat_category: "",
  label_presence: "", evaluation_outcome: "", reference_label: "", label_source_kind: "", label_source_ref: "", label_ai_visible: "",
  test_run_id: "", test_difficulty: "", test_category: ""
};

export function initialListState(purpose = "") {
  const filters = { ...emptyFilters, analysis_purpose: purpose };
  return { draft: { ...filters }, applied: { ...filters }, offset: 0, limit: 25, advanced: false };
}

export function analysisRowState(status) {
  const states = {
    pending: { className: "analysis-row-pending", label: "분석 대기" },
    processing: { className: "analysis-row-processing", label: "분석 중" },
    failed: { className: "analysis-row-failed", label: "분석 실패" },
    completed: { className: "analysis-row-completed", label: "분석 완료" },
  };
  return typeof status === "string" && Object.hasOwn(states, status)
    ? states[status] : { className: "analysis-row-unknown", label: "처리 상태 미확인" };
}

export function analysisReceivedAt(value) {
  const date = dateValue(value);
  return date ? {
    dateTime: date.toISOString(), date: date.toLocaleDateString("ko-KR"),
    time: date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
  } : { dateTime: undefined, date: "—", time: "시각 미기록" };
}

export function analysisElapsedTime(item) {
  const ongoing = ["pending", "processing"].includes(item?.status);
  const value = item?.total_elapsed_ms;
  const measured = typeof value === "number" && Number.isFinite(value) && value >= 0;
  const formatted = formatDuration(value, ongoing ? "진행 중" : "측정 전 데이터");
  return ongoing && measured ? `${formatted} 경과` : formatted;
}

const filterLabels = {
  analysis_purpose: "분석 구분", ingest_channel: "유입 경로", q: "검색어", status: "처리 상태", verdict: "판정", severity: "심각도",
  waf_vendor: "WAF 벤더", waf_action: "WAF 조치", review_state: "분석가 리뷰", model_profile: "모델 프로필", input_truncated: "입력 잘림",
  src_port: "출발지 포트", dest_port: "목적지 포트", confidence_min: "최소 신뢰도", confidence_max: "최대 신뢰도",
  created_from: "접수 시작", created_to: "접수 종료", event_id: "이벤트 ID", company_name: "회사명", source_system: "Source System",
  src_ip: "출발지 IP", dest_ip: "목적지 IP", signature: "시그니처", event_name: "이벤트명", threat_category: "위협 유형",
  label_presence: "참고 답안", evaluation_outcome: "답안 비교", reference_label: "참고 판정", label_source_kind: "답안 종류",
  label_source_ref: "답안 출처 / 버전", label_ai_visible: "답안 작성 시 AI 열람",
  test_run_id: "테스트 실행 ID", test_difficulty: "테스트 난이도", test_category: "테스트 유형",
};

// Reflect applied server-query filters, not edits awaiting the Search button.
// All values remain text; unknown fields cannot be introduced as filter chips.
export function appliedFilterTags(filters, valueLabels = {}) {
  const tags = [];
  for (const key of Object.keys(filterLabels)) {
    const raw = filters?.[key];
    if (raw === undefined || raw === null || !["string", "number", "boolean"].includes(typeof raw)) continue;
    const value = typeof raw === "string" ? raw.trim() : String(raw);
    if (!value) continue;
    const named = valueLabels[key];
    // Filter values come from datetime-local or an explicitly zoned dashboard
    // query. Match analysisQuery's Date parsing; DB timestamps use a different
    // UTC fallback in dateValue and must not be reused for these input values.
    const filterDate = ["created_from", "created_to"].includes(key) ? new Date(value) : null;
    const display = named && Object.hasOwn(named, value) ? named[value]
      : filterDate ? (Number.isNaN(filterDate.getTime()) ? "시각 확인 필요" : filterDate.toLocaleString("ko-KR")) : value;
    const label = key === "q" ? searchFields.find(([field]) => field === filters.search_field)?.[1] || filterLabels.q : filterLabels[key];
    tags.push({ key, label, value: display });
  }
  return tags;
}

export function removeAppliedFilter(state, key) {
  if (!Object.hasOwn(filterLabels, key)) return state;
  const reset = key === "q" ? { q: "", search_field: "all" } : { [key]: "" };
  return { ...state, offset: 0, draft: { ...state.draft, ...reset }, applied: { ...state.applied, ...reset } };
}

export function dashboardListState(filters) {
  const state = initialListState("production");
  state.applied = { ...state.applied, ...filters };
  state.draft = { ...state.applied };
  for (const key of ["created_from", "created_to"]) {
    if (!filters[key]) continue;
    const value = new Date(filters[key]);
    const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
    state.draft[key] = local.toISOString().slice(0, 23);
  }
  state.advanced = Boolean(filters.waf_action);
  return state;
}

export function uploadErrorText(item) {
  if (item.message) return item.message;
  if (Array.isArray(item.validation) && item.validation.length) {
    return item.validation.map((issue) => `${issue.field || "입력"}: ${issue.type || "validation_failed"}`).join("\n");
  }
  return "validation_failed";
}

export function validateFilters(filters) {
  if (filters.confidence_min !== "" && filters.confidence_max !== "" && Number(filters.confidence_min) > Number(filters.confidence_max)) {
    return "신뢰도 최솟값은 최댓값보다 클 수 없습니다.";
  }
  if (filters.created_from && filters.created_to && new Date(filters.created_from) >= new Date(filters.created_to)) {
    return "접수 종료 시각은 시작 시각보다 뒤여야 합니다.";
  }
  return "";
}

export function analysisQuery(filters, limit, offset) {
  const query = { limit, offset };
  for (const [key, value] of Object.entries(filters)) {
    if (value === "" || value === undefined || value === null) continue;
    if (key === "search_field" && !filters.q.trim()) continue;
    query[key] = key === "created_from" || key === "created_to"
      ? new Date(value).toISOString()
      : typeof value === "string" ? value.trim() : value;
    if (query[key] === "") delete query[key];
  }
  return query;
}

export function formatDuration(value, fallback = "측정 전 데이터") {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) return fallback;
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(2)}초`;
  const seconds = Math.floor(value / 1000);
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
  return `${Math.floor(seconds / 3600)}시간 ${Math.floor((seconds % 3600) / 60)}분 ${seconds % 60}초`;
}

export function dateValue(value) {
  if (!value) return null;
  // Historical SQLite timestamps can lack a timezone; stored timestamps are UTC.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const time = new Date(normalized);
  return Number.isNaN(time.getTime()) ? null : time;
}

export function formatDate(value) {
  return dateValue(value)?.toLocaleString("ko-KR") || "-";
}

export function executionDuration(item, now = Date.now()) {
  if (item?.metadata?.timing_incomplete || item?.failure_id === "worker_lease_expired") return "측정 중단";
  if (item?.duration_ms !== null && item?.duration_ms !== undefined) {
    return `${formatDuration(item.duration_ms)}${["running", "processing"].includes(item.status) ? " · 진행 중" : ""}`;
  }
  const start = dateValue(item?.started_at);
  if (start && !item.completed_at && ["running", "processing"].includes(item.status)) {
    return `${formatDuration(Math.max(0, now - start.getTime()))} · 진행 중`;
  }
  return "측정 전 데이터";
}
