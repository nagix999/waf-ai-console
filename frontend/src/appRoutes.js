// Only screen names, tab enums and server-generated UUIDs belong in URLs.
// Search terms, form drafts, API responses and secrets stay out of this module.
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const settingsTabs = new Set(["models", "agents", "instructions", "concurrency", "schema", "egress", "keys"]);
const runtimePages = new Set(["quality", "diagnostics", "deployment", "changes"]);
const detailTabs = new Set(["result", "agent", "raw", "json", "report"]);
const canonical = {
  "#overview": "#dashboard", "#configure/llm-profiles": "#settings/models",
  "#configure/agent-roles": "#settings/agents", "#configure/instructions": "#settings/instructions",
  "#evaluate/tests": "#analyses/test", "#evaluate/tests/new": "#test",
  "#evaluate/ground-truth": "#datasets", "#evaluate/production-evaluation": "#runtime/quality",
  "#operate/inference": "#analyses", "#operate/activity": "#runtime/changes",
  "#operate/activity/deployment": "#runtime/deployment", "#connect/production-api": "#production-api",
  "#connect/api-keys": "#settings/keys", "#connect/input-schema": "#settings/schema", "#connect/vllm-targets": "#settings/egress",
};

export function readAppHash(hash) {
  if (hash === "#promote") return { page: "analyses", purpose: "test", view: "runs" };
  const promotion = typeof hash === "string" && hash.match(/^#promotion\/([^/]+)$/);
  if (promotion && uuid.test(promotion[1])) return { page: "promote", promoteRunId: promotion[1].toLowerCase() };
  const caseRoute = typeof hash === "string" && hash.match(/^#evaluate\/tests\/([^/]+)\/case\/([^/]+)$/);
  if (caseRoute && uuid.test(caseRoute[1]) && uuid.test(caseRoute[2])) return { page: "analyses", purpose: "test", view: "runs", runId: caseRoute[1].toLowerCase(), caseId: caseRoute[2].toLowerCase() };
  if (hash === "#operate/runtime") return { page: "runtime" };
  if (hash === "#settings/concurrency" || hash === "#runtime/diagnostics") return { page: "runtime" };
  if (canonical[hash]) hash = canonical[hash];
  if (typeof hash === "string") {
    const canonicalParts = hash.slice(1).split("/");
    const [workspace, resource, identifier, tab] = canonicalParts;
    if (workspace === "evaluate" && uuid.test(identifier || "") && canonicalParts.length === 3) {
      if (resource === "tests") hash = `#test-runs/${identifier}`;
      if (resource === "ground-truth") hash = `#datasets/${identifier}`;
    }
    const tabs = { result: "result", "agent-trace": "agent", input: "raw", "result-json": "json", report: "report" };
    if (workspace === "operate" && resource === "inference" && uuid.test(identifier || "") && canonicalParts.length === 3) hash = `#analyses/${identifier}`;
    if (workspace === "operate" && resource === "inference" && uuid.test(identifier || "") && canonicalParts.length === 4 && tabs[tab]) hash = `#analyses/${identifier}/${tabs[tab]}`;
  }
  if (["", "#", "#dashboard"].includes(hash)) return { page: "dashboard" };
  if (hash === "#production-api") return { page: "apiDocs" };
  if (hash === "#test") return { page: "test" };
  if (hash === "#datasets") return { page: "datasets", datasetId: null };
  if (hash === "#analyses" || hash === "#analyses/all") return { page: "analyses", purpose: "" };
  if (hash === "#analyses/production") return { page: "analyses", purpose: "production" };
  if (hash === "#analyses/test") return { page: "analyses", purpose: "test", view: "runs" };
  if (hash === "#analyses/test/items") return { page: "analyses", purpose: "test", view: "items" };
  if (hash === "#settings") return { page: "settings", tab: "models" };
  if (hash === "#settings/prompts") return { page: "settings", tab: "instructions" };
  const parts = typeof hash === "string" ? hash.slice(1).split("/") : [];
  if (parts[0] === "runtime" && parts.length === 2 && runtimePages.has(parts[1])) return { page: parts[1] };
  if (parts[0] === "datasets" && parts.length === 2 && uuid.test(parts[1])) return { page: "datasets", datasetId: parts[1].toLowerCase() };
  if (parts[0] === "settings" && parts.length === 2 && settingsTabs.has(parts[1])) return { page: "settings", tab: parts[1] };
  if (parts[0] === "test-runs" && parts.length === 2 && uuid.test(parts[1])) return { page: "analyses", purpose: "test", view: "runs", runId: parts[1].toLowerCase() };
  if (parts[0] === "analyses" && [2, 3].includes(parts.length) && uuid.test(parts[1]) && (parts.length === 2 || detailTabs.has(parts[2]))) {
    return { page: "detail", id: parts[1].toLowerCase(), tab: parts[2] || "result" };
  }
  // Unknown fragments (including accessibility/document anchors) are not routes.
  return null;
}

export function writeAppHash(state) {
  const legacy = writeLegacyHash(state);
  if (state.page === "promote") return uuid.test(state.promoteRunId || "") ? `#promotion/${state.promoteRunId}` : "#evaluate/tests";
  if (state.page === "datasets") return "#evaluate/ground-truth";
  if (state.page === "analyses" && state.resultsPurpose === "test" && state.testResults?.view === "runs" && uuid.test(state.testResults?.runId || "") && uuid.test(state.testResults?.caseId || "")) return `#evaluate/tests/${state.testResults.runId}/case/${state.testResults.caseId}`;
  if (["runtime", "diagnostics"].includes(state.page) || state.page === "settings" && state.settingsTab === "concurrency") return "#operate/runtime";
  if (state.page === "detail" && uuid.test(state.selectedId || "")) {
    const tabs = { result: "result", agent: "agent-trace", raw: "input", json: "result-json", report: "report" };
    return `#operate/inference/${state.selectedId.toLowerCase()}/${tabs[state.detailTab] || "result"}`;
  }
  if (legacy.startsWith("#test-runs/")) return legacy.replace("#test-runs/", "#evaluate/tests/");
  if (legacy.startsWith("#datasets/")) return legacy.replace("#datasets/", "#evaluate/ground-truth/");
  return Object.keys(canonical).find(key => canonical[key] === legacy) || (legacy.startsWith("#analyses") ? "#operate/inference" : "#overview");
}

function writeLegacyHash(state) {
  if (runtimePages.has(state.page)) return `#runtime/${state.page}`;
  if (state.page === "apiDocs") return "#production-api";
  if (state.page === "test") return "#test";
  if (state.page === "datasets") return uuid.test(state.datasetId || "") ? `#datasets/${state.datasetId.toLowerCase()}` : "#datasets";
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
  if (route.page === "promote") next.promoteRunId = route.promoteRunId;
  if (route.page === "datasets") next.datasetId = route.datasetId;
  if (route.page === "settings") next.settingsTab = route.tab;
  if (route.page === "detail") {
    next.selectedId = route.id;
    next.detailTab = route.tab;
    next.detailReturnPage = "analyses";
  }
  if (route.page === "analyses") {
    next.resultsPurpose = route.purpose;
    next.testResults = { ...state.testResults, view: route.view || "runs", runId: route.runId || null, caseId: route.caseId || null };
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
