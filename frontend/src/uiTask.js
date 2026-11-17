// Final boundary for React event handlers, which do not await returned promises.
// Keep the failure message fixed: upstream errors may contain sensitive text.
// setError is a state setter (or a stale-view guard around one), never an
// application callback, error formatter or operation that may itself reject.
export async function runUiTask(task, setError, message) {
  try {
    return await task();
  } catch {
    setError(message);
    return undefined;
  }
}
