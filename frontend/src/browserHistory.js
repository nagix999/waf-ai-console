// Browser history stores only opaque identifiers. Search text, drafts and other
// UI state live in this controller's memory, never in browser storage or URLs.
const NAMESPACE = "__wafNavigation";
const MAX_REMEMBERED_ENTRIES = 200;

export function createBrowserHistory({ browser, initialSnapshot, readHash, writeHash, applyRoute }) {
  const token = () => {
    const crypto = browser.crypto ?? globalThis.crypto;
    return crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  };
  const documentId = token();
  let sessionId = token();
  const records = new Map();
  const listeners = new Set();
  const starts = new Set();
  let stack = [];
  let cursor = -1;
  let currentId = null;
  let pendingTraversal = null;

  function freshSnapshot() {
    const fresh = initialSnapshot();
    const route = readHash(browser.location.hash);
    return route == null ? fresh : applyRoute(fresh, route);
  }

  // Construction is safe during rendering, including discarded StrictMode renders.
  let snapshot = freshSnapshot();

  function canonicalHash(value) {
    const hash = writeHash(value);
    if (typeof hash !== "string" || (hash !== "" && !hash.startsWith("#"))) {
      throw new TypeError("Navigation routes must be same-document hashes");
    }
    return hash;
  }

  function emit() {
    for (const listener of listeners) listener();
  }

  function writeEntry(method, id, hash) {
    // Do not copy arbitrary existing history.state: it may contain sensitive data.
    browser.history[method]({
      [NAMESPACE]: { document: documentId, session: sessionId, entry: id },
    }, "", `${browser.location.pathname}${browser.location.search}${hash}`);
  }

  function markerRecord() {
    const marker = browser.history.state?.[NAMESPACE];
    if (marker?.document !== documentId || marker?.session !== sessionId) return null;
    const record = records.get(marker.entry);
    // A native hash edit can retain an old state object in some environments.
    // Never associate that different URL with the old entry's remembered filters.
    return record && record.hash === browser.location.hash ? record : null;
  }

  function trimMemory() {
    for (const id of records.keys()) {
      if (records.size <= MAX_REMEMBERED_ENTRIES) break;
      if (id !== currentId) records.delete(id);
    }
    if (stack.length > MAX_REMEMBERED_ENTRIES) {
      const first = Math.max(0, cursor - MAX_REMEMBERED_ENTRIES + 1);
      stack = stack.slice(first, first + MAX_REMEMBERED_ENTRIES);
      cursor -= first;
    }
  }

  function ownCurrentEntry(value) {
    const id = token();
    const hash = canonicalHash(value);
    writeEntry("replaceState", id, hash);
    const record = { id, hash, snapshot: value };
    records.set(id, record);
    stack = [id];
    cursor = 0;
    currentId = id;
    snapshot = value;
    pendingTraversal = null;
    trimMemory();
  }

  function reconcile() {
    const record = markerRecord();
    if (record) {
      if (record.id === currentId) return;
      pendingTraversal = null;
      const position = stack.indexOf(record.id);
      if (position < 0) {
        // We crossed an unowned entry. Its browser distance is unknown, so a
        // subsequent in-app Back must not guess a distance to older entries.
        stack = [record.id];
        cursor = 0;
      } else {
        cursor = position;
      }
      currentId = record.id;
      snapshot = record.snapshot;
      emit();
      return;
    }

    const route = readHash(browser.location.hash);
    // Unknown document anchors do not select another application page. Still
    // allocate a new entry identity so the previous snapshot remains untouched.
    const next = route == null && currentId !== null
      ? snapshot
      : (route == null ? initialSnapshot() : applyRoute(initialSnapshot(), route));
    ownCurrentEntry(next);
    emit();
  }

  function remember(updateFn) {
    const next = updateFn(snapshot);
    if (next === snapshot) return;
    snapshot = next;
    const record = records.get(currentId);
    if (record) record.snapshot = next;
    emit();
  }

  function navigate(updateFn, { replace = false } = {}) {
    // Also detect URL edits made while React effects were temporarily detached.
    if (!markerRecord() || markerRecord().id !== currentId) reconcile();
    const next = updateFn(snapshot);
    const hash = canonicalHash(next);
    const current = records.get(currentId);
    if (replace || current.hash === hash) {
      writeEntry("replaceState", current.id, hash);
      current.hash = hash;
      current.snapshot = next;
    } else {
      const id = token();
      writeEntry("pushState", id, hash);
      for (const discarded of stack.slice(cursor + 1)) records.delete(discarded);
      stack = [...stack.slice(0, cursor + 1), id];
      cursor = stack.length - 1;
      currentId = id;
      records.set(id, { id, hash, snapshot: next });
      trimMemory();
    }
    pendingTraversal = null;
    snapshot = next;
    emit();
  }

  function backTo(predicate, fallbackUpdateFn) {
    if (pendingTraversal !== null) return true;
    if (!markerRecord() || markerRecord().id !== currentId) reconcile();
    for (let position = cursor - 1; position >= 0; position -= 1) {
      const record = records.get(stack[position]);
      if (record && predicate(record.snapshot)) {
        pendingTraversal = record.id;
        browser.history.go(position - cursor);
        return true;
      }
    }
    navigate(fallbackUpdateFn, { replace: true });
    return false;
  }

  function reset() {
    records.clear();
    stack = [];
    cursor = -1;
    currentId = null;
    pendingTraversal = null;
    sessionId = token();
    ownCurrentEntry(freshSnapshot());
    emit();
  }

  function start() {
    const registration = {};
    starts.add(registration);
    if (starts.size === 1) {
      browser.addEventListener("popstate", reconcile);
      browser.addEventListener("hashchange", reconcile);
      reconcile();
    }
    return () => {
      if (!starts.delete(registration) || starts.size !== 0) return;
      browser.removeEventListener("popstate", reconcile);
      browser.removeEventListener("hashchange", reconcile);
    };
  }

  return {
    getSnapshot: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    start,
    remember,
    navigate,
    backTo,
    reset,
  };
}
