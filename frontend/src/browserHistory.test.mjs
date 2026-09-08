import assert from "node:assert/strict";
import test from "node:test";
import { createBrowserHistory } from "./browserHistory.js";

function fakeBrowser(hash = "#dashboard", preceding = []) {
  const entries = [...preceding, { url: `http://console.test/${hash}`, state: null }];
  let position = entries.length - 1;
  const handlers = new Map();
  const pending = [];
  const writes = [];
  const moves = [];
  const copy = value => structuredClone(value);
  const browser = {
    get location() { return new URL(entries[position].url); },
    get localStorage() { throw new Error("Navigation must not access persistent storage"); },
    get sessionStorage() { throw new Error("Navigation must not access persistent storage"); },
    addEventListener(name, listener) {
      if (!handlers.has(name)) handlers.set(name, new Set());
      handlers.get(name).add(listener);
    },
    removeEventListener(name, listener) { handlers.get(name)?.delete(listener); },
    dispatch(name) { for (const listener of handlers.get(name) ?? []) listener({ state: copy(entries[position].state) }); },
    listenerCount(name) { return handlers.get(name)?.size ?? 0; },
    get entries() { return copy(entries); },
    get position() { return position; },
    writes,
    moves,
    history: {
      get state() { return copy(entries[position].state); },
      get length() { return entries.length; },
      pushState(state, _title, url) {
        entries.splice(position + 1);
        entries.push({ state: copy(state), url: new URL(url, entries[position].url).href });
        position += 1;
        writes.push("push");
      },
      replaceState(state, _title, url) {
        entries[position] = { state: copy(state), url: new URL(url, entries[position].url).href };
        writes.push("replace");
      },
      go(delta) { moves.push(delta); pending.push(delta); },
      back() { this.go(-1); },
      forward() { this.go(1); },
    },
    flush() {
      while (pending.length) {
        const next = position + pending.shift();
        if (next < 0 || next >= entries.length || next === position) continue;
        const oldHash = browser.location.hash;
        position = next;
        browser.dispatch("popstate");
        if (oldHash !== browser.location.hash) browser.dispatch("hashchange");
      }
    },
    editHash(nextHash, { retainState = false } = {}) {
      const state = retainState ? entries[position].state : null;
      const url = new URL(entries[position].url);
      url.hash = nextHash;
      entries.splice(position + 1);
      entries.push({ state: copy(state), url: url.href });
      position += 1;
      browser.dispatch("hashchange");
    },
    replaceForeign(state, hash = browser.location.hash) {
      browser.history.replaceState(state, "", hash);
      browser.dispatch("popstate");
    },
  };
  return browser;
}

function initialSnapshot() {
  return { page: "dashboard", id: null, tab: "result", filters: { query: "", page: 0 }, raw: null, apiKey: "" };
}

function readHash(hash) {
  if (hash === "" || hash === "#dashboard") return { page: "dashboard" };
  if (hash === "#results") return { page: "results" };
  if (hash === "#settings") return { page: "settings" };
  const match = /^#analysis\/([a-z0-9-]+)(?:\/(result|raw|report))?$/.exec(hash);
  return match ? { page: "analysis", id: match[1], tab: match[2] ?? "result" } : null;
}

function writeHash(snapshot) {
  return snapshot.page === "analysis" ? `#analysis/${snapshot.id}/${snapshot.tab}` : `#${snapshot.page}`;
}

const applyRoute = (snapshot, route) => ({ ...snapshot, ...route });
const makeController = (browser, overrides = {}) => createBrowserHistory({
  browser, initialSnapshot, readHash, writeHash, applyRoute, ...overrides,
});
const page = name => snapshot => ({ ...snapshot, page: name });
const detail = id => snapshot => ({ ...snapshot, page: "analysis", id, tab: "result" });
const query = (value, number = 0) => snapshot => ({ ...snapshot, filters: { query: value, page: number } });

test("construction is read-only and bootstraps the current URL without another history entry", () => {
  const browser = fakeBrowser("#analysis/synthetic-1/report");
  const controller = makeController(browser);
  assert.equal(controller.getSnapshot().page, "analysis");
  assert.equal(controller.getSnapshot().tab, "report");
  assert.deepEqual(browser.writes, []);
  assert.equal(browser.listenerCount("popstate"), 0);
  controller.start();
  assert.deepEqual(browser.writes, ["replace"]);
  assert.equal(browser.history.length, 1);
  assert.deepEqual(Object.keys(browser.history.state), ["__wafNavigation"]);
  assert.deepEqual(Object.keys(browser.history.state.__wafNavigation).sort(), ["document", "entry", "session"]);
  assert.ok(Object.values(browser.history.state.__wafNavigation).every(value => typeof value === "string" && value.length > 10));
});

test("Back and Forward restore each entry's remembered filters and tab without pushing", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.remember(query("synthetic filter", 3));
  controller.navigate(detail("synthetic-1"));
  controller.navigate(snapshot => ({ ...snapshot, tab: "report" }));
  const beforeTraversal = browser.writes.length;
  browser.history.back();
  browser.flush();
  assert.equal(controller.getSnapshot().tab, "result");
  browser.history.back();
  browser.flush();
  assert.equal(controller.getSnapshot().page, "results");
  assert.deepEqual(controller.getSnapshot().filters, { query: "synthetic filter", page: 3 });
  browser.history.forward();
  browser.flush();
  browser.history.forward();
  browser.flush();
  assert.equal(controller.getSnapshot().id, "synthetic-1");
  assert.equal(controller.getSnapshot().tab, "report");
  assert.equal(browser.writes.length, beforeTraversal);
});

test("remember updates memory only and emits exactly once for a changed snapshot", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  let notifications = 0;
  const unsubscribe = controller.subscribe(() => { notifications += 1; });
  const before = JSON.stringify(browser.entries);
  controller.remember(query("synthetic private search", 8));
  controller.remember(snapshot => snapshot);
  assert.equal(notifications, 1);
  assert.equal(JSON.stringify(browser.entries), before);
  unsubscribe();
  controller.remember(query("another synthetic query"));
  assert.equal(notifications, 1);
});

test("navigating to the same canonical route replaces state without duplicate history", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.navigate(query("synthetic search", 2));
  controller.navigate(page("results"));
  assert.equal(browser.history.length, 1);
  assert.deepEqual(browser.writes, ["replace", "replace", "replace"]);
  controller.navigate(detail("synthetic-1"));
  controller.navigate(page("settings"), { replace: true });
  assert.equal(browser.history.length, 2);
  browser.history.back();
  browser.flush();
  assert.equal(controller.getSnapshot().page, "results");
  assert.equal(controller.getSnapshot().filters.query, "synthetic search");
});

test("a new navigation after Back drops the forward branch", () => {
  const browser = fakeBrowser();
  const controller = makeController(browser);
  controller.start();
  controller.navigate(page("results"));
  controller.navigate(detail("synthetic-old"));
  browser.history.back();
  browser.flush();
  controller.navigate(detail("synthetic-new"));
  browser.history.forward();
  browser.flush();
  assert.equal(controller.getSnapshot().id, "synthetic-new");
  assert.equal(browser.history.length, 3);
  assert.doesNotMatch(JSON.stringify(browser.entries), /synthetic-old/);
});

test("backTo traverses to the nearest known matching entry, preserving its original filters", () => {
  const browser = fakeBrowser();
  const controller = makeController(browser);
  controller.start();
  controller.navigate(page("results"));
  controller.remember(query("synthetic first", 1));
  controller.navigate(detail("synthetic-1"));
  controller.navigate(page("results"));
  controller.remember(query("synthetic second", 5));
  controller.navigate(detail("synthetic-2"));
  controller.navigate(snapshot => ({ ...snapshot, tab: "report" }));
  assert.equal(controller.backTo(snapshot => snapshot.page === "results", page("results")), true);
  // Double clicks must not enqueue a second traversal and overshoot the target.
  browser.dispatch("popstate");
  browser.dispatch("hashchange");
  controller.backTo(snapshot => snapshot.page === "results", page("results"));
  assert.deepEqual(browser.moves, [-2]);
  browser.flush();
  assert.equal(controller.getSnapshot().page, "results");
  assert.deepEqual(controller.getSnapshot().filters, { query: "synthetic second", page: 5 });
});

test("backTo on a direct link replaces with its fallback and never leaves the app", () => {
  const browser = fakeBrowser("#analysis/synthetic-1/report", [{ url: "https://external.test/", state: null }]);
  const controller = makeController(browser);
  controller.start();
  assert.equal(controller.backTo(snapshot => snapshot.page === "results", page("results")), false);
  assert.equal(browser.location.origin, "http://console.test");
  assert.equal(browser.location.hash, "#results");
  assert.equal(browser.history.length, 2);
  assert.deepEqual(browser.moves, []);
});

test("StrictMode start/cleanup/start retains state and has no duplicate subscriptions or bootstrap", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  const cleanup = controller.start();
  controller.remember(query("synthetic remembered"));
  cleanup();
  cleanup();
  assert.equal(browser.listenerCount("popstate"), 0);
  const cleanupAgain = controller.start();
  const extraCleanup = controller.start();
  assert.equal(browser.listenerCount("popstate"), 1);
  assert.equal(browser.listenerCount("hashchange"), 1);
  assert.equal(controller.getSnapshot().filters.query, "synthetic remembered");
  assert.deepEqual(browser.writes, ["replace"]);
  cleanupAgain();
  assert.equal(browser.listenerCount("popstate"), 1);
  extraCleanup();
  assert.equal(browser.listenerCount("popstate"), 0);
});

test("duplicate popstate/hashchange notifications are ignored", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.navigate(detail("synthetic-1"));
  let notifications = 0;
  controller.subscribe(() => { notifications += 1; });
  browser.history.back();
  browser.flush();
  browser.dispatch("popstate");
  browser.dispatch("hashchange");
  assert.equal(notifications, 1);
  assert.equal(browser.writes.length, 2);
});

test("reload uses URL routing and fresh defaults, not another document's memory", () => {
  const browser = fakeBrowser("#results");
  const original = makeController(browser);
  const cleanup = original.start();
  original.remember(query("synthetic private filter", 7));
  original.navigate(detail("synthetic-1"));
  original.remember(snapshot => ({ ...snapshot, raw: "synthetic raw HTTP", apiKey: "synthetic API key" }));
  cleanup();
  const reloaded = makeController(browser);
  assert.equal(reloaded.getSnapshot().id, "synthetic-1");
  reloaded.start();
  assert.deepEqual(reloaded.getSnapshot().filters, { query: "", page: 0 });
  assert.equal(reloaded.getSnapshot().raw, null);
  browser.history.back();
  browser.flush();
  assert.equal(reloaded.getSnapshot().page, "results");
  assert.equal(reloaded.getSnapshot().filters.query, "");
  assert.equal(reloaded.getSnapshot().apiKey, "");
});

test("reset rotates the session and old Back/Forward entries cannot resurrect sensitive snapshots", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.remember(query("synthetic previous session", 9));
  controller.navigate(detail("synthetic-1"));
  controller.remember(snapshot => ({ ...snapshot, raw: "synthetic secret HTTP", apiKey: "synthetic session API key" }));
  const previous = browser.history.state.__wafNavigation;
  controller.reset();
  const next = browser.history.state.__wafNavigation;
  assert.equal(previous.document, next.document);
  assert.notEqual(previous.session, next.session);
  assert.notEqual(previous.entry, next.entry);
  assert.equal(controller.getSnapshot().id, "synthetic-1");
  assert.deepEqual(controller.getSnapshot().filters, { query: "", page: 0 });
  assert.equal(controller.getSnapshot().raw, null);
  browser.history.back();
  browser.flush();
  assert.equal(controller.getSnapshot().page, "results");
  assert.equal(controller.getSnapshot().filters.query, "");
  browser.history.forward();
  browser.flush();
  assert.equal(controller.getSnapshot().raw, null);
  assert.equal(controller.getSnapshot().apiKey, "");
});

test("unknown document anchors keep the current route harmlessly without overwriting its previous entry", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.remember(query("synthetic anchor search", 4));
  const previousEntry = browser.history.state.__wafNavigation.entry;
  browser.editHash("#workspace-content", { retainState: true });
  assert.equal(controller.getSnapshot().page, "results");
  assert.equal(controller.getSnapshot().filters.query, "synthetic anchor search");
  assert.equal(browser.location.hash, "#results");
  assert.notEqual(browser.history.state.__wafNavigation.entry, previousEntry);
  controller.remember(query("synthetic anchor new value"));
  browser.history.back();
  browser.flush();
  assert.equal(controller.getSnapshot().filters.query, "synthetic anchor search");
});

test("external hash edits retaining old state never associate new routes with previous snapshots", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.remember(query("synthetic results-only", 6));
  const previousEntry = browser.history.state.__wafNavigation.entry;
  browser.editHash("#analysis/synthetic-2/report", { retainState: true });
  assert.equal(controller.getSnapshot().id, "synthetic-2");
  assert.equal(controller.getSnapshot().filters.query, "");
  assert.notEqual(browser.history.state.__wafNavigation.entry, previousEntry);
  browser.history.back();
  browser.flush();
  assert.equal(controller.getSnapshot().page, "results");
  assert.equal(controller.getSnapshot().filters.query, "synthetic results-only");
});

test("unowned entries break distance assumptions, so in-app Back uses a safe replacement", () => {
  const browser = fakeBrowser("#results", [{ url: "https://external.test/", state: null }]);
  const controller = makeController(browser);
  controller.start();
  controller.navigate(detail("synthetic-1"));
  browser.editHash("#settings");
  controller.navigate(detail("synthetic-2"));
  assert.equal(controller.backTo(snapshot => snapshot.page === "results", page("results")), false);
  assert.deepEqual(browser.moves, []);
  assert.equal(browser.location.hash, "#results");
});

test("foreign or corrupt state is replaced and cannot restore cached data by entry ID alone", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.remember(query("synthetic protected memory"));
  const marker = browser.history.state.__wafNavigation;
  browser.replaceForeign({ __wafNavigation: { ...marker, session: "foreign-session" }, raw: "synthetic foreign payload" });
  assert.equal(controller.getSnapshot().filters.query, "");
  assert.doesNotMatch(JSON.stringify(browser.history.state), /synthetic foreign payload/);
  assert.notEqual(browser.history.state.__wafNavigation.entry, marker.entry);
});

test("restart reconciles a URL changed while listeners were detached", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  const cleanup = controller.start();
  controller.remember(query("synthetic detached"));
  cleanup();
  browser.editHash("#settings", { retainState: true });
  controller.start();
  assert.equal(controller.getSnapshot().page, "settings");
  assert.equal(controller.getSnapshot().filters.query, "");
});

test("sensitive memory fields never appear in browser history or URL, including after reset", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.remember(snapshot => ({
    ...snapshot, filters: { query: "synthetic-private-query", page: 10 },
    raw: "synthetic-HTTP-Cookie-value", apiKey: "synthetic-api-key-value",
  }));
  controller.navigate(detail("synthetic-1"));
  controller.navigate(page("settings"));
  controller.reset();
  const persisted = JSON.stringify(browser.entries);
  assert.doesNotMatch(persisted, /synthetic-private-query|synthetic-HTTP-Cookie-value|synthetic-api-key-value|filters|raw|apiKey/);
  for (const entry of browser.entries) {
    assert.deepEqual(Object.keys(entry.state), ["__wafNavigation"]);
    assert.deepEqual(Object.keys(entry.state.__wafNavigation).sort(), ["document", "entry", "session"]);
  }
});

test("navigation refuses a route writer that returns an external or path URL", () => {
  for (const badRoute of ["https://external.test/", "//external.test/", "/settings"]) {
    const browser = fakeBrowser();
    const controller = makeController(browser, { writeHash: snapshot => snapshot.page === "settings" ? badRoute : "#dashboard" });
    controller.start();
    assert.throws(() => controller.navigate(page("settings")), /same-document hashes/);
    assert.equal(browser.history.length, 1);
    assert.equal(browser.location.hash, "#dashboard");
    assert.equal(controller.getSnapshot().page, "dashboard");
  }
});

test("long navigation sessions bound remembered snapshots and evicted entries recover from URL", () => {
  const browser = fakeBrowser("#results");
  const controller = makeController(browser);
  controller.start();
  controller.remember(query("synthetic old filter", 99));
  for (let index = 0; index < 205; index += 1) controller.navigate(detail(`synthetic-${index}`));
  browser.history.go(-205);
  browser.flush();
  assert.equal(controller.getSnapshot().page, "results");
  assert.deepEqual(controller.getSnapshot().filters, { query: "", page: 0 });
  browser.history.go(205);
  browser.flush();
  assert.equal(controller.getSnapshot().id, "synthetic-204");
  const moves = browser.moves.length;
  assert.equal(controller.backTo(snapshot => snapshot.page === "results", page("results")), false);
  assert.equal(browser.moves.length, moves);
});
