async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {})
    }
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return body;
}

export const api = {
  login: (username, password) => request("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password })
  }),
  logout: () => request("/api/v1/auth/logout", { method: "POST" }),
  me: () => request("/api/v1/auth/me"),
  analyses: () => request("/api/v1/analyses?limit=100"),
  analysis: (id) => request(`/api/v1/analyses/${id}`),
  rawEvent: (id) => request(`/api/v1/analyses/${id}/event`),
  agentRuns: (id) => request(`/api/v1/analyses/${id}/agent-runs`),
  createAnalysis: (payload) => request("/api/v1/analyses", {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  upload: (file) => {
    const body = new FormData();
    body.append("file", file);
    return request("/api/v1/uploads", { method: "POST", body });
  },
  modelProfiles: () => request("/api/v1/model-profiles"),
  createModelProfile: (payload) => request("/api/v1/model-profiles", {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  updateModelProfile: (id, payload) => request(`/api/v1/model-profiles/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  }),
  modelProfileTests: (id) => request(`/api/v1/model-profiles/${id}/tests`),
  runModelProfileTest: (id, mode) => request(`/api/v1/model-profiles/${id}/tests`, {
    method: "POST",
    body: JSON.stringify({ mode })
  }),
  promoteModelProfile: (id) => request(`/api/v1/model-profiles/${id}/promote`, { method: "POST" }),
  disableModelProfile: (id) => request(`/api/v1/model-profiles/${id}/disable`, { method: "POST" }),
  enableModelProfile: (id) => request(`/api/v1/model-profiles/${id}/enable`, { method: "POST" })
};
