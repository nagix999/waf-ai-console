import { modelTestPayload } from "./modelValidation.js";
import { modelAssignmentPayload } from "./modelAssignments.js";

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
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.code || `HTTP ${response.status}`;
    const error = new Error(message);
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
  dashboard: (days = 7, options = {}) => request(`/api/v1/dashboard/summary?days=${days}`, options),
  analyses: (query = {}, options = {}) => request(`/api/v1/analyses?${new URLSearchParams(query)}`, options),
  analysis: (id, options = {}) => request(`/api/v1/analyses/${id}`, options),
  rawEvent: (id, options = {}) => request(`/api/v1/analyses/${id}/event`, options),
  agentRuns: (id, options = {}) => request(`/api/v1/analyses/${id}/agent-runs`, options),
  evaluationLabels: (id, options = {}) => request(`/api/v1/analyses/${id}/evaluation-labels`, options),
  previewEvaluationLabels: (body) => request("/api/v1/evaluation-labels/preview", { method: "POST", body }),
  confirmEvaluationLabels: (preview_token) => request("/api/v1/evaluation-labels/confirm", {
    method: "POST", body: JSON.stringify({ preview_token })
  }),
  createAnalysis: (payload) => request("/api/v1/test-analyses", {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  upload: (file) => {
    const body = new FormData();
    body.append("file", file);
    return request("/api/v1/test-uploads", { method: "POST", body });
  },
  testRuns: (query = {}, options = {}) => request(`/api/v1/test-runs?${new URLSearchParams(query)}`, options),
  testRun: (id, query = {}, options = {}) => request(`/api/v1/test-runs/${encodeURIComponent(id)}?${new URLSearchParams(query)}`, options),
  createTestRun: (payload) => request("/api/v1/test-runs", { method: "POST", body: JSON.stringify(payload) }),
  uploadTestRun: (file, name, idempotencyKey) => {
    const body = new FormData(); body.append("name", name); body.append("idempotency_key", idempotencyKey); body.append("file", file);
    return request("/api/v1/test-runs/uploads", { method: "POST", body });
  },
  modelProfiles: (options = {}) => request("/api/v1/model-profiles", options),
  serviceApiKeys: (options = {}) => request("/api/v1/admin/service-api-keys", { ...options, cache: "no-store" }),
  createServiceApiKey: (payload) => request("/api/v1/admin/service-api-keys", { method: "POST", body: JSON.stringify(payload), cache: "no-store" }),
  renameServiceApiKey: (id, payload) => request(`/api/v1/admin/service-api-keys/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload), cache: "no-store" }),
  revokeServiceApiKey: (id) => request(`/api/v1/admin/service-api-keys/${encodeURIComponent(id)}/revoke`, { method: "POST", cache: "no-store" }),
  internalEgress: (options = {}) => request("/api/v1/admin/internal-egress", options),
  createInternalEgress: (payload) => request("/api/v1/admin/internal-egress", { method: "POST", body: JSON.stringify(payload) }),
  updateInternalEgress: (id, payload) => request(`/api/v1/admin/internal-egress/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteInternalEgress: (id, revision) => request(`/api/v1/admin/internal-egress/${encodeURIComponent(id)}?${new URLSearchParams({ expected_revision: revision })}`, { method: "DELETE" }),
  promptPolicies: (options = {}) => request("/api/v1/admin/prompt-policies", options),
  productionApi: (options = {}) => request("/api/v1/production-api", { ...options, cache: "no-store" }),
  inputSchemas: (options = {}) => request("/api/v1/admin/input-schemas", { ...options, cache: "no-store" }),
  inputSchema: (id, options = {}) => request(`/api/v1/admin/input-schemas/${encodeURIComponent(id)}`, { ...options, cache: "no-store" }),
  inputSchemaHistory: (options = {}) => request("/api/v1/admin/input-schemas/activation-history", { ...options, cache: "no-store" }),
  createInputSchema: (payload) => request("/api/v1/admin/input-schemas", { method: "POST", body: JSON.stringify(payload) }),
  validateInputSchema: (id, event) => request(`/api/v1/admin/input-schemas/${encodeURIComponent(id)}/validate`, { method: "POST", body: JSON.stringify({ event }), cache: "no-store" }),
  activateInputSchema: (id, payload) => request(`/api/v1/admin/input-schemas/${encodeURIComponent(id)}/activate`, { method: "POST", body: JSON.stringify(payload) }),
  promptPolicy: (id, options = {}) => request(`/api/v1/admin/prompt-policies/${encodeURIComponent(id)}`, options),
  createPromptPolicy: (payload, options = {}) => request("/api/v1/admin/prompt-policies", {
    ...options, method: "POST", body: JSON.stringify(payload)
  }),
  activatePromptPolicy: (id, payload, options = {}) => request(`/api/v1/admin/prompt-policies/${encodeURIComponent(id)}/activate`, {
    ...options, method: "POST", body: JSON.stringify(payload)
  }),
  createModelProfile: (payload) => request("/api/v1/model-profiles", {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  updateModelProfile: (id, payload) => request(`/api/v1/model-profiles/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  }),
  modelProfileTests: (id) => request(`/api/v1/model-profiles/${id}/tests`),
  runModelProfileTest: (id, mode, includeDataset = false, expectedFingerprint, runOptions) => request(`/api/v1/model-profiles/${encodeURIComponent(id)}/tests`, {
    method: "POST",
    body: JSON.stringify(modelTestPayload(mode, includeDataset, expectedFingerprint, runOptions))
  }),
  promoteModelProfile: (id, fingerprint) => request(`/api/v1/model-profiles/${encodeURIComponent(id)}/promote`, { method: "POST", ...(fingerprint === undefined ? {} : { body: JSON.stringify(modelAssignmentPayload(fingerprint)) }) }),
  assignTestModelProfile: (id, fingerprint) => request(`/api/v1/model-profiles/${encodeURIComponent(id)}/assign-test`, { method: "POST", body: JSON.stringify(modelAssignmentPayload(fingerprint)) }),
  unassignTestModelProfile: (id, fingerprint) => request(`/api/v1/model-profiles/${encodeURIComponent(id)}/unassign-test`, { method: "POST", body: JSON.stringify(modelAssignmentPayload(fingerprint)) }),
  disableModelProfile: (id) => request(`/api/v1/model-profiles/${id}/disable`, { method: "POST" }),
  enableModelProfile: (id) => request(`/api/v1/model-profiles/${id}/enable`, { method: "POST" })
};
