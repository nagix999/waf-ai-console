import { emptyFilters } from "./analysisView.js";
import { completedTimingSummary, performanceCount } from "./testPerformance.js";

const scopeFields = [...Object.keys(emptyFilters), "include_retries"];
const states = ["pending", "processing", "completed", "failed", "canceled"];

// Match the server's AnalysisFilters without page-local pagination/sort state.
// Search values stay in the authenticated POST body, not URL query parameters.
export function analysisPerformanceScope(filters = {}) {
  if (!filters || typeof filters !== "object" || Array.isArray(filters)) return {};
  return Object.fromEntries(scopeFields.filter(key => filters[key] !== undefined && filters[key] !== null && filters[key] !== "").map(key => [key, filters[key]]));
}

export function analysisPerformanceSummary(raw) {
  if (!raw || raw.version !== "analysis-performance-summary-v1") return null;
  return {
    version: raw.version,
    cases: performanceCount(raw.cases),
    statuses: Object.fromEntries(states.map(key => [key, performanceCount(raw.statuses?.[key])])),
    ...completedTimingSummary(raw),
  };
}

export function analysisPerformanceIsRunning(raw) {
  const data = analysisPerformanceSummary(raw);
  return !!data && [data.statuses.pending, data.statuses.processing].some(value => value !== null && value > 0);
}
