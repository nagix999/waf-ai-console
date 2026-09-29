// No overlapping requests. Hidden tabs pause reads; visibility restores them.
// A cleanup aborts in-flight reads as well as removing timers/listeners.
export function startVisiblePolling(read, { document: doc = document, interval = 20000, activeInterval = 4000, onError = () => {}, invalidationEvent } = {}) {
  const controller = new AbortController();
  let timer, running = false, stopped = false, failures = 0, invalidated = false;
  const clear = () => { clearTimeout(timer); timer = undefined; };
  async function tick() {
    clear();
    if (stopped || running || doc.hidden) return;
    running = true; let active = false;
    try { active = await read(controller.signal); failures = 0; }
    catch (error) { if (!stopped) { failures++; onError(error); } }
    finally {
      running = false;
      const delay = failures ? Math.min((interval || 30000) * 2 ** Math.min(failures, 3), 120000) : active ? activeInterval : interval;
      if (!stopped && !doc.hidden && invalidated) { invalidated = false; void tick(); }
      else if (!stopped && !doc.hidden && delay != null) timer = setTimeout(tick, delay);
    }
  }
  const visibility = () => { clear(); if (!doc.hidden) void tick(); };
  const invalidate = () => { if (running) invalidated = true; else visibility(); };
  doc.addEventListener("visibilitychange", visibility); void tick();
  if (invalidationEvent) doc.addEventListener(invalidationEvent, invalidate);
  return () => { stopped = true; clear(); controller.abort(); doc.removeEventListener("visibilitychange", visibility); if (invalidationEvent) doc.removeEventListener(invalidationEvent, invalidate); };
}
