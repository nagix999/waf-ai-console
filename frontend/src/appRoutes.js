import { feedbackUrlState, initialFeedbackState, validFeedbackDate } from "./feedbackView.js";

// Only screen names, safe date/enum filters and server-generated UUIDs belong in URLs.
// Search terms, form drafts, API responses and secrets stay out of this module.
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const settingsTabs = new Set(["models", "agents", "instructions", "concurrency", "schema", "parsers", "signature-explanations", "egress", "keys"]);
const runtimePages = new Set(["quality", "diagnostics", "deployment", "changes"]);
const detailTabs = new Set(["result", "agent", "raw", "json", "report"]);
const canonical = {
  "#overview": "#dashboard", "#configure/llm-profiles": "#settings/models",
  "#configure/agent-roles": "#settings/agents", "#configure/instructions": "#settings/instructions",
  "#configure/parsers": "#settings/parsers",
  "#configure/signature-explanations": "#settings/signature-explanations",
  "#evaluate/tests": "#analyses/test", "#evaluate/tests/new": "#test",
  "#evaluate/tests/items": "#analyses/test/items",
  "#evaluate/ground-truth": "#datasets", "#evaluate/production-evaluation": "#runtime/quality",
  "#operate/inference": "#analyses", "#operate/activity": "#runtime/changes",
  "#operate/activity/deployment": "#runtime/deployment", "#connect/production-api": "#production-api",
  "#connect/api-keys": "#settings/keys", "#configure/input-schema": "#settings/schema", "#connect/input-schema": "#settings/schema", "#connect/vllm-targets": "#settings/egress",
};

export function readAppHash(hash) {
  const [path, query] = typeof hash === "string" ? hash.split("?") : [hash];
  const route = readRoutePath(path);
  if (!route || query === undefined) return route;
  const params = new URLSearchParams(query);
  if (route.page === "feedback") {
    const defaults = initialFeedbackState();
    const state = feedbackUrlState({ ...defaults,
      start: validFeedbackDate(params.get("start")) ? params.get("start") : defaults.start,
      end: validFeedbackDate(params.get("end")) ? params.get("end") : defaults.end,
      purpose: params.get("purpose"), version: params.get("version"), category: params.get("category"),
      analysis: params.get("analysis"), selectedId: params.get("selected"),
      limit: Number(params.get("limit") || 20), offset: Number(params.get("offset") || 0),
    });
    return { ...route, feedbackState: { ...state, query: "" } };
  }
  if (route.page !== "detail") return null;
  const runId = params.get("run"), stepId = params.get("step"), view = params.get("view");
  const executionSelection = {
    runId: uuid.test(runId || "") ? runId.toLowerCase() : null,
    stepId: stepId === "result" ? "result" : uuid.test(stepId || "") ? stepId.toLowerCase() : null,
    view: ["output", "input", "metadata"].includes(view) ? view : "output",
  };
  return { ...route, executionSelection };
}

function readRoutePath(hash) {
  if (hash === "#operate/feedback") return { page: "feedback" };
  if (hash === "#configure/parsers/new/data") return { page: "settings", tab: "parsers", parserRoute: { view: "new", step: "data" } };
  if (hash === "#configure/parsers/legacy") return { page: "settings", tab: "parsers", parserRoute: { view: "legacy" } };
  const parserSession = typeof hash === "string" && hash.match(/^#configure\/parsers\/sessions\/([^/]+)\/(data|formats|verify)$/);
  if (parserSession && uuid.test(parserSession[1])) return { page: "settings", tab: "parsers", parserRoute: { view: "session", sessionId: parserSession[1].toLowerCase(), step: parserSession[2] } };
  const testView = typeof hash === "string" && hash.match(/^#evaluate\/tests\/([^/]+)\/(metrics|statistics)$/);
  if (testView && uuid.test(testView[1])) return { page: "analyses", purpose: "test", view: "runs", runId: testView[1].toLowerCase(), runView: testView[2] };
  const ungrouped = typeof hash === "string" && hash.match(/^#evaluate\/tests\/items\/([^/]+)\/(result|agent-trace|input|result-json|report)$/);
  if (ungrouped && uuid.test(ungrouped[1])) return { page: "detail", purpose: "test", id: ungrouped[1].toLowerCase(), tab: { result: "result", "agent-trace": "agent", input: "raw", "result-json": "json", report: "report" }[ungrouped[2]] };
  if (hash === "#promote") return { page: "analyses", purpose: "test", view: "runs" };
  const promotion = typeof hash === "string" && hash.match(/^#promotion\/([^/]+)$/);
  if (promotion && uuid.test(promotion[1])) return { page: "promote", promoteRunId: promotion[1].toLowerCase() };
  const fullCase = typeof hash === "string" && hash.match(/^#evaluate\/tests\/([^/]+)\/case\/([^/]+)\/(result|agent-trace|input|result-json|report)$/);
  if (fullCase && uuid.test(fullCase[1]) && uuid.test(fullCase[2])) return { page: "detail", runId: fullCase[1].toLowerCase(), id: fullCase[2].toLowerCase(), tab: { result: "result", "agent-trace": "agent", input: "raw", "result-json": "json", report: "report" }[fullCase[3]] };
  const caseRoute = typeof hash === "string" && hash.match(/^#evaluate\/tests\/([^/]+)\/case\/([^/]+)$/);
  if (caseRoute && uuid.test(caseRoute[1]) && uuid.test(caseRoute[2])) return { page: "detail", purpose: "test", runId: caseRoute[1].toLowerCase(), id: caseRoute[2].toLowerCase(), tab: "result" };
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
  if (hash === "#analyses" || hash === "#analyses/all") return { page: "analyses", purpose: "production" };
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
  const path = state.page === "feedback" ? "#operate/feedback" : writeRoutePath(state);
  const params = new URLSearchParams();
  if (state.page === "feedback" && state.feedbackState) {
    const safe = feedbackUrlState(state.feedbackState);
    for (const key of ["start", "end", "purpose", "version", "category", "analysis", "limit", "offset"]) {
      if (safe[key] !== "" && safe[key] !== null && safe[key] !== undefined) params.set(key, safe[key]);
    }
    if (safe.selectedId) params.set("selected", safe.selectedId);
  }
  if (state.page === "detail" && state.detailTab === "agent") {
    const selection = state.executionSelection || {};
    if (uuid.test(selection.runId || "")) params.set("run", selection.runId.toLowerCase());
    if (selection.stepId === "result" || uuid.test(selection.stepId || "")) params.set("step", selection.stepId.toLowerCase());
    if (["input", "metadata"].includes(selection.view)) params.set("view", selection.view);
  }
  return params.size ? `${path}?${params}` : path;
}

function writeRoutePath(state) {
  const legacy = writeLegacyHash(state);
  if (state.page === "settings" && state.settingsTab === "parsers") {
    const route = state.parserRoute;
    if (route?.view === "legacy") return "#configure/parsers/legacy";
    if (route?.view === "new") return "#configure/parsers/new/data";
    if (route?.view === "session" && uuid.test(route.sessionId || "") && ["data", "formats", "verify"].includes(route.step)) return `#configure/parsers/sessions/${route.sessionId.toLowerCase()}/${route.step}`;
    return "#configure/parsers";
  }
  if (state.page === "promote") return uuid.test(state.promoteRunId || "") ? `#promotion/${state.promoteRunId}` : "#evaluate/tests";
  if (state.page === "datasets") return uuid.test(state.datasetId || "") ? `#evaluate/ground-truth/${state.datasetId.toLowerCase()}` : "#evaluate/ground-truth";
  if (state.page === "analyses" && state.resultsPurpose === "test" && state.testResults?.view === "runs" && uuid.test(state.testResults?.runId || "") && ["metrics", "statistics"].includes(state.testResults?.filters?.view)) return `#evaluate/tests/${state.testResults.runId}/${state.testResults.filters.view}`;
  if (["runtime", "diagnostics"].includes(state.page) || state.page === "settings" && state.settingsTab === "concurrency") return "#operate/runtime";
  if (state.page === "detail" && uuid.test(state.selectedId || "")) {
    const tabs = { result: "result", agent: "agent-trace", raw: "input", json: "result-json", report: "report" };
    if (state.resultsPurpose === "test" && uuid.test(state.testResults?.runId || "")) return `#evaluate/tests/${state.testResults.runId}/case/${state.selectedId.toLowerCase()}/${tabs[state.detailTab] || "result"}`;
    if (state.resultsPurpose === "test") return `#evaluate/tests/items/${state.selectedId.toLowerCase()}/${tabs[state.detailTab] || "result"}`;
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
  if (route.page === "feedback") next.feedbackState = route.feedbackState || state.feedbackState || initialFeedbackState();
  if (route.page === "promote") next.promoteRunId = route.promoteRunId;
  if (route.page === "datasets") next.datasetId = route.datasetId;
  if (route.page === "settings") {
    next.settingsTab = route.tab;
    if (route.tab === "parsers") next.parserRoute = route.parserRoute || { view: "list" };
  }
  if (route.page === "detail") {
    next.selectedId = route.id;
    next.detailTab = route.tab;
    next.detailReturnPage = "analyses";
    next.executionSelection = route.executionSelection || null;
    if (route.purpose === "test" && !route.runId) {
      next.resultsPurpose = "test";
      next.testResults = { ...state.testResults, view: "items", runId: null, caseId: null };
    }
    if (route.runId) {
      next.resultsPurpose = "test";
      next.detailReturnPage = "testRun";
      next.testResults = { ...state.testResults, view: "runs", runId: route.runId, caseId: null };
    }
  }
  if (route.page === "analyses") {
    next.resultsPurpose = route.purpose;
    next.testResults = { ...state.testResults, view: route.view || "runs", runId: route.runId || null, caseId: route.caseId || null };
    if (route.purpose === "test" && route.runId) next.testResults.filters = { ...state.testResults.filters, view: route.runView || "items" };
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
