// Refreshes may replace values without unmounting a page's existing content.
// A different query or resource must never borrow the previous scope's data.
export const emptyScopedRead = () => ({ key: null, scopeKey: null, data: null, error: "", loading: true });

export function scopedRead(previous, scopeKey) {
  return previous.scopeKey === scopeKey ? previous : emptyScopedRead();
}

export function beginScopedRead(previous, scopeKey, key) {
  return { ...scopedRead(previous, scopeKey), key, scopeKey, error: "", loading: true };
}

export function updateScopedRead(previous, scopeKey, key, patch) {
  if (previous.scopeKey !== scopeKey || previous.key !== key) return previous;
  return { ...previous, ...patch };
}
