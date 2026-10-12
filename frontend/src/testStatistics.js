import { testRunQuery } from "./testRuns.js";

export const statisticFields = [
  ["signature", "시그니처", "Signature"], ["company_name", "회사", "Company"],
  ["waf_vendor", "WAF 벤더", "WAF vendor"], ["waf_action", "WAF 조치", "WAF action"],
  ["event_name", "이벤트명", "Event name"], ["difficulty", "난이도", "Difficulty"],
  ["test_category", "테스트 유형", "Category"], ["src_ip", "출발지 IP", "Source IP"], ["dest_ip", "목적지 IP", "Destination IP"],
];
export const initialStatistics = () => ({ group_by: "signature", q: "", draft: "", limit: 10, offset: 0, sort_by: "cases", sort_order: "desc" });

export function statisticsFields(state) {
  const allowed = new Set(statisticFields.map(([field]) => field));
  const fields = [...new Set(String(state?.group_by || "signature").split(",").filter(field => allowed.has(field)))].slice(0, 3);
  return fields.length ? fields : ["signature"];
}

export function changeGrouping(state, fields) {
  return { ...state, group_by: fields.join(","), q: "", draft: "", offset: 0 };
}

export function addGrouping(state) {
  const fields = statisticsFields(state);
  if (fields.length === 3) return state;
  const next = ["signature", "company_name", "waf_action", ...statisticFields.map(([field]) => field)].find(field => !fields.includes(field));
  return changeGrouping(state, [...fields, next]);
}

export function selectedGroups(filters) {
  return filters.group_filters?.length ? filters.group_filters : filters.group_field
    ? [{ field: filters.group_field, value: filters.group_missing ? null : filters.group_value }] : [];
}

export function clearGroupSelection(filters) {
  return { ...filters, ...(filters.group_field ? { group_field: "", group_value: "", group_missing: false } : {}),
    ...(filters.group_filters?.length ? { group_filters: [] } : {}) };
}

export function referenceQuery(filters) {
  const query = testRunQuery(filters);
  return Object.fromEntries(Object.entries(query).filter(([key]) => ["reference_basis", "evaluation_id"].includes(key)));
}

export function statisticsQuery(filters, state) {
  const scope = testRunQuery(filters);
  return { ...Object.fromEntries(Object.entries(scope).filter(([key]) => ["reference_basis", "evaluation_id", "difficulty", "test_category", "difficulty_missing", "test_category_missing"].includes(key))),
    ...Object.fromEntries(Object.entries(state).filter(([key, value]) => key !== "draft" && value !== "")) };
}

export function groupCases(filters, field, value) {
  const fields = Array.isArray(field) ? field : [field];
  const base = { ...clearGroupSelection(filters), view: "items", offset: 0, status: "", evaluation_outcome: "", cell: "" };
  return fields.length === 1
    ? { ...base, group_field: fields[0], group_value: value ?? "", group_missing: value == null }
    : { ...base, group_filters: fields.map(key => ({ field: key, value: value[key] ?? null })) };
}

export function percent(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}
