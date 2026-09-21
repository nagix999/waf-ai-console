import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { consoleDestination, consoleGroups, consoleLocation, consoleSearch } from "./consoleNavigation.js";
import { applyAppRoute, readAppHash, writeAppHash } from "./appRoutes.js";
import { initialListState } from "./analysisView.js";
import { messages, translate } from "./consoleMessages.js";
import { startVisiblePolling } from "./visiblePolling.js";

const initial = () => ({ page: "dashboard", listState: initialListState(), resultsPurpose: "", settingsTab: "models", testResults: { view: "runs", runId: null, items: initialListState("test") } });
function load(file) {
  const result = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty", ".md": "text" }, logLevel: "silent" });
  const module = { exports: {} }; runInNewContext(result.outputFiles[0].text, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, Date }); return module.exports;
}

test("six canonical workspaces map every leaf to a real route, preserving legacy links", () => {
  assert.deepEqual(consoleGroups.map(group => group.key), ["overview", "configure", "evaluate", "promote", "operate", "connect"]);
  for (const group of consoleGroups) for (const key of group.items) {
    const next = consoleDestination(initial(), key);
    assert.equal(consoleLocation(next).item, key);
    assert.equal(consoleLocation(next).group, group.key);
    const restored = applyAppRoute(initial(), readAppHash(writeAppHash(next)));
    assert.equal(consoleLocation(restored).item, key);
  }
  assert.equal(readAppHash("#settings/prompts").tab, "instructions");
  assert.equal(readAppHash("#analyses/test").view, "runs");
  assert.equal(readAppHash("#analyses/test/items").view, "items");
  assert.equal(readAppHash("#runtime/__proto__"), null);
});

test("individual Test history and named Test runs are separate, without clearing filters", () => {
  const state = { ...initial(), resultsPurpose: "test" }; state.testResults.items.applied.q = "private-fixture";
  const runs = consoleDestination(state, "runs"); const history = consoleDestination(runs, "history");
  assert.equal(history.testResults.view, "items"); assert.equal(history.testResults.items.applied.q, "private-fixture");
  assert.equal(consoleLocation({ ...runs, page: "detail", detailReturnPage: "testRun" }).group, "evaluate");
  assert.equal(consoleLocation({ ...history, page: "detail", detailReturnPage: "analyses" }).group, "operate");
  assert.equal(state.page, "dashboard");
});

test("global search values never enter URLs, and ID lookup strictly validates UUIDs", () => {
  for (const field of ["event_id", "src_ip", "company_name", "signature"]) {
    const { state } = consoleSearch(initial(), field, "  private-fixture  ");
    assert.equal(state.listState.applied.q, "private-fixture"); assert.equal(state.listState.applied.search_field, field);
    assert.equal(state.resultsPurpose, ""); assert.doesNotMatch(writeAppHash(state), /private-fixture/);
  }
  for (const input of ["../", "<script>", "secret"]) assert.equal(consoleSearch(initial(), "analysis_id", input).error, "invalidId");
  const id = "11111111-1111-4111-8111-111111111111";
  assert.equal(consoleSearch(initial(), "analysis_id", id).state.selectedId, id);
  const fromRun = consoleSearch({ ...initial(), resultsPurpose: "test", testResults: { view: "runs", runId: id } }, "analysis_id", id).state;
  assert.equal(fromRun.resultsPurpose, ""); assert.equal(fromRun.testResults.runId, null); assert.equal(fromRun.testResults.view, "items");
  assert.equal(consoleSearch(initial(), "__proto__", "secret").state.page, "dashboard");
});

test("locale dictionary is complete for its scope and does not translate interpolated user content", () => {
  for (const [key, pair] of Object.entries(messages)) { assert.equal(pair.length, 2); assert.ok(pair.every(value => typeof value === "string" && value.length), key); }
  assert.equal(translate("en", "closeNamed", { title: "사용자 입력" }), "Close 사용자 입력");
  assert.equal(translate("ko", "__proto__"), "__proto__");
});

test("preferences accept only explicit locale/theme values and survive denied storage", () => {
  const { readPreference } = load("./consolePreferences.jsx");
  assert.equal(readPreference({ getItem: () => "sk" }, "theme", ["light", "sk", "dark"], "light"), "sk");
  assert.equal(readPreference({ getItem: () => "system" }, "theme", ["light", "sk", "dark"], "light"), "light");
  assert.equal(readPreference({ getItem: () => { throw Error(); } }, "locale", ["ko", "en"], "ko"), "ko");
});

test("shell derives avatar from principal, does not claim Production health and omits SOC branding", () => {
  const Shell = load("./ConsoleShell.jsx").default;
  const html = renderToStaticMarkup(createElement(Shell, { screen: initial(), principal: { username: "fixture-user" }, logoutState: {} }));
  assert.match(html, /운영 상태 미측정/); assert.match(html, /aria-hidden="true">F<\/span>/);
  assert.doesNotMatch(html, /SECURITY OPERATIONS|Production 정상|INTERNAL WORKSPACE/);
  assert.match(html, /화면 언어/); assert.match(html, /테마/);
});

test("runtime loading and failure states never fabricate zero requests or healthy workers", () => {
  const { RuntimeStatus, RuntimeQuality } = load("./RuntimeViews.jsx");
  const props = { days: 7, serviceApiKeyId: "", onUnauthorized: () => {} };
  const loading = renderToStaticMarkup(createElement(RuntimeStatus, props));
  assert.match(loading, /미측정/); assert.match(loading, /조회 중/);
  assert.doesNotMatch(loading, /접수된 요청이 없습니다|Production 정상/);
  const failed = renderToStaticMarkup(createElement(RuntimeStatus, { ...props, error: "private-error" }));
  assert.match(failed, /정보를 불러오지 못했습니다/); assert.doesNotMatch(failed, /private-error|접수된 요청이 없습니다/);
  const quality = renderToStaticMarkup(createElement(RuntimeQuality, props));
  assert.match(quality, /공식 평가가 아직 연결되지/); assert.match(quality, /운영 참고 답안 비교/);
});

test("visible polling pauses in background, resumes without overlap, aborts and cleans up", async () => {
  const handlers = new Set(); const doc = { hidden: true, addEventListener: (_, fn) => handlers.add(fn), removeEventListener: (_, fn) => handlers.delete(fn) };
  let reads = 0, resolve, signal;
  const stop = startVisiblePolling(nextSignal => { reads++; signal = nextSignal; return new Promise(done => { resolve = done; }); }, { document: doc });
  assert.equal(reads, 0); doc.hidden = false; handlers.forEach(fn => fn()); assert.equal(reads, 1);
  handlers.forEach(fn => fn()); assert.equal(reads, 1);
  doc.hidden = true; handlers.forEach(fn => fn()); resolve(false); await Promise.resolve(); await Promise.resolve();
  doc.hidden = false; handlers.forEach(fn => fn()); assert.equal(reads, 2);
  stop(); assert.equal(signal.aborted, true); assert.equal(handlers.size, 0); resolve(false);
});

test("new navigation removes nested settings tabs but preserves Production assignment controls", () => {
  const { Settings, AnalysisResultsPage } = load("./App.jsx");
  const agent = renderToStaticMarkup(createElement(Settings, { standalone: true, tab: "agents" }));
  assert.doesNotMatch(agent, /aria-label="설정 항목"|aria-label="Agent 설정 구분"/); assert.match(agent, /역할별 모델/);
  const run = renderToStaticMarkup(createElement(AnalysisResultsPage, { splitNavigation: true, purpose: "test", testState: { view: "runs", runId: null }, setTestState: () => {} }));
  assert.doesNotMatch(run, /aria-label="분석 구분"/); assert.match(run, /테스트 목록/);
});
