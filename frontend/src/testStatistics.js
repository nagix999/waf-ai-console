import { testRunQuery } from "./testRuns.js";

export const statisticFields = [
  ["signature", "시그니처", "Signature"], ["company_name", "회사", "Company"],
  ["waf_vendor", "WAF 벤더", "WAF vendor"], ["waf_action", "WAF 조치", "WAF action"],
  ["event_name", "이벤트명", "Event name"], ["difficulty", "난이도", "Difficulty"],
  ["test_category", "테스트 유형", "Category"], ["src_ip", "출발지 IP", "Source IP"], ["dest_ip", "목적지 IP", "Destination IP"],
];
export const initialStatistics = () => ({ group_by: "signature", q: "", draft: "", limit: 10, offset: 0, sort_by: "cases", sort_order: "desc" });

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
  return { ...filters, view: "items", offset: 0, status: "", evaluation_outcome: "", cell: "",
    group_field: field, group_value: value ?? "", group_missing: value == null };
}

export function percent(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}
