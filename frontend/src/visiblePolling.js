// No overlapping requests. Hidden tabs pause reads; visibility restores them.
// A cleanup aborts in-flight reads as well as removing timers/listeners.
export function startVisiblePolling(read, { document: doc = document, interval = 20000, activeInterval = 4000, onError = () => {} } = {}) {
  const controller = new AbortController();
  let timer, running = false, stopped = false, failures = 0;
  const clear = () => { clearTimeout(timer); timer = undefined; };
  async function tick() {
    clear();
    if (stopped || running || doc.hidden) return;
    running = true; let active = false;
    try { active = await read(controller.signal); failures = 0; }
    catch (error) { if (!stopped) { failures++; onError(error); } }
    finally {
      running = false;
      if (!stopped && !doc.hidden) timer = setTimeout(tick, failures ? Math.min(interval * 2 ** Math.min(failures, 3), 120000) : active ? activeInterval : interval);
    }
  }
  const visibility = () => { clear(); if (!doc.hidden) void tick(); };
  doc.addEventListener("visibilitychange", visibility); void tick();
  return () => { stopped = true; clear(); controller.abort(); doc.removeEventListener("visibilitychange", visibility); };
}
