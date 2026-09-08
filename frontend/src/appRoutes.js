// Only screen names, tab enums and server-generated UUIDs belong in URLs.
// Search terms, form drafts, API responses and secrets stay out of this module.
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const settingsTabs = new Set(["models", "prompts", "schema", "egress", "keys"]);
const detailTabs = new Set(["result", "raw", "report"]);

export function readAppHash(hash) {
  if (["", "#", "#dashboard"].includes(hash)) return { page: "dashboard" };
  if (hash === "#production-api") return { page: "apiDocs" };
  if (hash === "#test") return { page: "test" };
  if (hash === "#analyses") return { page: "analyses", purpose: "" };
  if (hash === "#analyses/production") return { page: "analyses", purpose: "production" };
  if (hash === "#analyses/test") return { page: "analyses", purpose: "test", view: "runs" };
  if (hash === "#analyses/test/items") return { page: "analyses", purpose: "test", view: "items" };
  if (hash === "#settings") return { page: "settings", tab: "models" };
  const parts = typeof hash === "string" ? hash.slice(1).split("/") : [];
  if (parts[0] === "settings" && parts.length === 2 && settingsTabs.has(parts[1])) return { page: "settings", tab: parts[1] };
  if (parts[0] === "test-runs" && parts.length === 2 && uuid.test(parts[1])) return { page: "analyses", purpose: "test", view: "runs", runId: parts[1].toLowerCase() };
  if (parts[0] === "analyses" && [2, 3].includes(parts.length) && uuid.test(parts[1]) && (parts.length === 2 || detailTabs.has(parts[2]))) {
    return { page: "detail", id: parts[1].toLowerCase(), tab: parts[2] || "result" };
  }
  // Unknown fragments (including accessibility/document anchors) are not routes.
  return null;
}

export function writeAppHash(state) {
  if (state.page === "apiDocs") return "#production-api";
  if (state.page === "test") return "#test";
  if (state.page === "settings") return `#settings/${settingsTabs.has(state.settingsTab) ? state.settingsTab : "models"}`;
  if (state.page === "detail") {
    if (!uuid.test(state.selectedId || "")) return "#analyses";
    const tab = detailTabs.has(state.detailTab) ? state.detailTab : "result";
    return `#analyses/${state.selectedId.toLowerCase()}${tab === "result" ? "" : `/${tab}`}`;
  }
  if (state.page === "analyses") {
    if (state.resultsPurpose === "test") {
      if (uuid.test(state.testResults?.runId || "")) return `#test-runs/${state.testResults.runId.toLowerCase()}`;
      return state.testResults?.view === "items" ? "#analyses/test/items" : "#analyses/test";
    }
    return state.resultsPurpose === "production" ? "#analyses/production" : "#analyses";
  }
  return "#dashboard";
}

export function applyAppRoute(state, route) {
  if (!route) return state;
  const next = { ...state, page: route.page };
  if (route.page === "settings") next.settingsTab = route.tab;
  if (route.page === "detail") {
    next.selectedId = route.id;
    next.detailTab = route.tab;
    next.detailReturnPage = "analyses";
  }
  if (route.page === "analyses") {
    next.resultsPurpose = route.purpose;
    next.testResults = { ...state.testResults, view: route.view || "runs", runId: route.runId || null };
    if (route.purpose !== "test") next.listState = {
      ...state.listState,
      draft: { ...state.listState.draft, analysis_purpose: route.purpose },
      applied: { ...state.listState.applied, analysis_purpose: route.purpose },
    };
  }
  return next;
}

export function isDetailOrigin(detail, candidate) {
  const targetPage = detail.detailReturnPage === "testRun" ? "analyses" : detail.detailReturnPage;
  if (candidate.page !== targetPage) return false;
  if (targetPage !== "analyses") return true;
  if (candidate.resultsPurpose !== detail.resultsPurpose) return false;
  // Remembered Test filters must not affect a Production/all-list return path.
  if (detail.resultsPurpose !== "test") return true;
  return detail.detailReturnPage === "testRun"
    ? candidate.testResults.runId === detail.testResults.runId
    : !candidate.testResults.runId && candidate.testResults.view === detail.testResults.view;
}
