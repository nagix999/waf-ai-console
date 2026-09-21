import { initialListState } from "./analysisView.js";

export const consoleGroups = [
  { key: "overview", icon: "dashboard", items: ["status"] },
  { key: "configure", icon: "settings", items: ["profiles", "agents", "instructions"] },
  { key: "evaluate", icon: "test", items: ["runs", "groundTruth", "quality"] },
  { key: "promote", icon: "arrow", items: ["promote"] },
  { key: "operate", icon: "analyses", items: ["runtime", "history", "changes"] },
  { key: "connect", icon: "server", items: ["api", "keys", "schema", "targets"] },
];
const settings = { profiles: "models", agents: "agents", instructions: "instructions", concurrency: "concurrency", keys: "keys", schema: "schema", targets: "egress" };
export function consoleDestination(state, key) {
  if (["promote", "runtime"].includes(key)) return { ...state, page: key };
  if (["concurrency", "diagnostics"].includes(key)) return { ...state, page: "runtime" };
  if (Object.hasOwn(settings, key)) return { ...state, page: "settings", settingsTab: settings[key] };
  if (["quality", "diagnostics", "deployment", "changes"].includes(key)) return { ...state, page: key };
  if (key === "runs") return { ...state, page: "analyses", resultsPurpose: "test", testResults: { ...state.testResults, view: "runs", runId: null } };
  if (key === "history") return { ...state, page: "analyses", testResults: { ...state.testResults, view: "items", runId: null } };
  if (key === "groundTruth") return { ...state, page: "datasets", datasetId: null };
  return { ...state, page: { status: "dashboard", run: "test", api: "apiDocs" }[key] || state.page };
}
export function consoleLocation(state) {
  let item;
  if (state.page === "detail") item = state.detailReturnPage === "testRun" ? "runs" : state.detailReturnPage === "test" ? "run" : "history";
  else if (state.page === "analyses") item = state.resultsPurpose === "test" && state.testResults?.view !== "items" ? "runs" : "history";
  else if (state.page === "settings") item = Object.keys(settings).find(key => settings[key] === state.settingsTab) || "profiles";
  else item = { dashboard: "status", test: "run", apiDocs: "api", datasets: "groundTruth" }[state.page] || state.page;
  const parent = item === "run" ? "runs" : item === "deployment" ? "changes" : ["diagnostics", "concurrency"].includes(item) ? "runtime" : item;
  return { item: parent, group: consoleGroups.find(group => group.items.includes(parent))?.key || "overview", title: state.page === "detail" ? "detail" : item };
}
export const globalSearchFields = ["analysis_id", "event_id", "src_ip", "company_name", "signature"];
export function consoleSearch(state, field, text) {
  const term = text.trim();
  if (!term || !globalSearchFields.includes(field)) return { state };
  if (field === "analysis_id") {
    if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(term)) return { error: "invalidId" };
    return { state: { ...state, page: "detail", selectedId: term.toLowerCase(), detailTab: "result", detailReturnPage: "analyses",
      resultsPurpose: "", listState: initialListState(), testResults: { ...state.testResults, view: "items", runId: null } } };
  }
  const list = initialListState();
  list.draft = { ...list.draft, search_field: field, q: term }; list.applied = { ...list.draft };
  return { state: { ...state, page: "analyses", resultsPurpose: "", listState: list, testResults: { ...state.testResults, view: "items", runId: null } } };
}
