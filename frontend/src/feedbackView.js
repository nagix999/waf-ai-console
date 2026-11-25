export const feedbackCategories = [
  ["insufficient_evidence", "근거 부족", "Insufficient evidence"],
  ["evidence_mismatch", "근거 불일치", "Evidence mismatch"],
  ["explanation_error", "설명 오류", "Explanation error"],
  ["exaggerated_impact", "영향 과장", "Exaggerated impact"],
  ["inappropriate_severity", "심각도 부적절", "Inappropriate severity"],
  ["other", "기타", "Other"],
];

export const unrecordedFeedbackVersion = "__unrecorded__";
const categoryIds = new Set(feedbackCategories.map(([id]) => id));
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const string = value => typeof value === "string" ? value : "";
export const feedbackCount = value => Number.isSafeInteger(value) && value >= 0 ? value : null;

export function validFeedbackDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  if (value.startsWith("0000-")) return false;
  const instant = Date.parse(`${value}T00:00:00.000Z`);
  return Number.isFinite(instant) && new Date(instant).toISOString().slice(0, 10) === value;
}

// Calendar dates are calculated in KST, independently of the browser timezone.
export function recentFeedbackRange(days = 30, now = new Date()) {
  const instant = new Date(now);
  if (!Number.isFinite(instant.getTime())) throw new RangeError("feedback_now_invalid");
  const end = new Date(instant.getTime() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const start = new Date(`${end}T00:00:00.000Z`);
  start.setUTCDate(start.getUTCDate() - (days === 7 ? 6 : 29));
  return { start: start.toISOString().slice(0, 10), end };
}

export function initialFeedbackState(now = new Date()) {
  return { ...recentFeedbackRange(30, now), purpose: "production", version: "", category: "", analysis: "", query: "", limit: 20, offset: 0, selectedId: null };
}

// An analysis link must not hide old feedback behind the default 30-day range.
// Start at admission in KST; never put event text or a comment in navigation.
export function analysisFeedbackState(detail, now = new Date()) {
  const state = initialFeedbackState(now);
  const admitted = typeof detail?.created_at === "string" ? Date.parse(detail.created_at) : NaN;
  const date = Number.isFinite(admitted) ? new Date(admitted + 9 * 60 * 60 * 1000).toISOString().slice(0, 10) : null;
  return normalizeFeedbackState({ ...state,
    start: date && validFeedbackDate(date) && date <= state.end ? date : state.start,
    purpose: ["test", "production"].includes(detail?.analysis_purpose) ? detail.analysis_purpose : "all", analysis: detail?.id,
  }, now);
}

export function normalizeFeedbackState(value = {}, now = new Date()) {
  const defaults = initialFeedbackState(now);
  const purpose = ["production", "test", "all"].includes(value.purpose) ? value.purpose : "production";
  const requestedVersion = string(value.version);
  const version = purpose !== "test" && (requestedVersion === unrecordedFeedbackVersion || uuid.test(requestedVersion)) ? requestedVersion.toLowerCase() : "";
  const limit = [20, 50, 100].includes(value.limit) ? value.limit : 20;
  return {
    start: typeof value.start === "string" ? value.start : defaults.start,
    end: typeof value.end === "string" ? value.end : defaults.end,
    purpose: version ? "production" : purpose, version,
    category: categoryIds.has(value.category) ? value.category : "",
    analysis: uuid.test(value.analysis || "") ? value.analysis.toLowerCase() : "",
    query: string(value.query).slice(0, 200), limit,
    offset: Number.isSafeInteger(value.offset) && value.offset >= 0 ? Math.floor(value.offset / limit) * limit : 0,
    selectedId: uuid.test(value.selectedId || "") ? value.selectedId.toLowerCase() : null,
  };
}

export function feedbackDateIssue(state) {
  if (!validFeedbackDate(state?.start) || !validFeedbackDate(state?.end)) return "date_required";
  return state.start > state.end ? "date_order" : "";
}

// Dates stay as calendar dates: the API applies inclusive KST boundaries.
// Raw comments are never searched or copied into query parameters.
export function feedbackQuery(value, paginated = false) {
  const state = normalizeFeedbackState(value);
  if (feedbackDateIssue(state)) return null;
  const query = { date_from: state.start, date_to: state.end, analysis_purpose: state.purpose };
  if (state.version) query.production_configuration_id = state.version;
  if (state.category) query.category = state.category;
  if (state.analysis) query.analysis_id = state.analysis;
  if (state.query.trim()) query.q = state.query.trim();
  if (paginated) { query.page = Math.floor(state.offset / state.limit) + 1; query.page_size = state.limit; }
  return query;
}

// URL state contains only dates, known enum values and server-generated IDs.
// Search text is kept in memory and may contain sensitive event metadata.
export function feedbackUrlState(value) {
  const state = normalizeFeedbackState(value);
  const { query, ...safe } = state;
  if (!validFeedbackDate(safe.start)) delete safe.start;
  if (!validFeedbackDate(safe.end)) delete safe.end;
  return safe;
}

export function feedbackCategoryLabel(value, locale = "ko") {
  const category = feedbackCategories.find(([id]) => id === value);
  return category ? category[locale === "en" ? 2 : 1] : (locale === "en" ? "Unrecognized category" : "알 수 없는 유형");
}

export function feedbackCategoriesOf(row) {
  return [...new Set((Array.isArray(row?.feedback_categories) ? row.feedback_categories : []).filter(category => categoryIds.has(category)))];
}

export function feedbackVersionLabel(row, locale = "ko") {
  const w = (ko, en) => locale === "en" ? en : ko;
  if (row?.analysis_purpose === "test") return w("Test / 적용 안 함", "Test / not applicable");
  if (row?.analysis_purpose && row.analysis_purpose !== "production") return w("용도 미기록", "Purpose not recorded");
  const id = row && Object.hasOwn(row, "production_configuration_id") ? row.production_configuration_id : row?.id;
  if (!id || id === unrecordedFeedbackVersion) return w("버전 미기록", "Version not recorded");
  const label = string(row?.production_configuration_label || row?.label).trim();
  return label === "버전 정보 미기록" ? w("버전 정보 미기록", "Version details not recorded") : label || w("등록 정보가 없는 버전", "Version details unavailable");
}

export function feedbackDateTime(value, locale = "ko") {
  const missing = locale === "en" ? "Not recorded" : "미기록";
  if (typeof value !== "string") return missing;
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/i.exec(value);
  if (!match || !validFeedbackDate(match[1]) || Number(match[2]) > 23 || Number(match[3]) > 59 || Number(match[4]) > 59) return missing;
  const instant = new Date(value);
  return Number.isFinite(instant.getTime()) ? new Intl.DateTimeFormat(locale === "en" ? "en-GB" : "ko-KR", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(instant) : missing;
}

export function validFeedbackSummary(value) {
  if (!value || ["registration_count", "analysis_count", "multi_category_count", "comment_only_count"].some(key => feedbackCount(value[key]) === null)) return false;
  return Array.isArray(value.category_counts) && feedbackCategories.every(([id]) => {
    const count = value.category_counts.find(row => row.category === id)?.count;
    return feedbackCount(count) !== null && count <= value.registration_count;
  });
}
