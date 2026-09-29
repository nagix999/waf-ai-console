// Payload-free, same-tab signals. Returning to a page/focused tab also re-reads.
export const HOME_INVALIDATED = "waf:home-invalidated";
export function invalidateHome() {
  if (typeof document !== "undefined") document.dispatchEvent(new Event(HOME_INVALIDATED));
}
export function affectsHome(path, method = "GET") {
  return ["POST", "PUT", "PATCH", "DELETE"].includes(method.toUpperCase()) &&
    !/\/(search|selection|preview)(?:\?|$)/.test(path) && !path.startsWith("/api/v1/auth/");
}
export function testRunActive(run) {
  return ["pending", "processing"].includes(run?.status) || Boolean(run?.official_evaluation_pending);
}
