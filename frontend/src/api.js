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
  productionConfiguration: (options = {}) => request("/api/v1/admin/production-configurations", { ...options, cache: "no-store" }),
  productionEvaluations: (options = {}) => request("/api/v1/admin/production-configurations/evaluations", { ...options, cache: "no-store" }),
  promotionPreflight: (id, options = {}) => request(`/api/v1/admin/production-configurations/preflight/${encodeURIComponent(id)}`, { ...options, cache: "no-store" }),
  promoteConfiguration: payload => request("/api/v1/admin/production-configurations/promote", { method: "POST", body: JSON.stringify(payload), cache: "no-store" }),
  runtimeStatus: (window = "24h", options = {}, filters = {}) => request(`/api/v1/admin/runtime/status?${new URLSearchParams({ window, ...filters })}`, { ...options, cache: "no-store" }),
  activity: (query = {}, options = {}) => request(`/api/v1/admin/activity?${new URLSearchParams(query)}`, { ...options, cache: "no-store" }),
  deployment: (options = {}) => request("/api/v1/admin/runtime/deployment", { ...options, cache: "no-store" }),
  validationDatasets: (query = {}, options = {}) => request(`/api/v1/validation-datasets?${new URLSearchParams(query)}`, { ...options, cache: "no-store" }),
  validationDataset: (id, query = {}, options = {}) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}?${new URLSearchParams(query)}`, { ...options, cache: "no-store" }),
  createValidationDataset: payload => request("/api/v1/validation-datasets", { method: "POST", body: JSON.stringify(payload) }),
  updateValidationDataset: (id, payload) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteValidationDataset: (id, revision) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}`, { method: "DELETE", body: JSON.stringify({ expected_revision: revision }) }),
  datasetItem: (id, item, version) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}/items/${encodeURIComponent(item)}${version ? `?version_id=${encodeURIComponent(version)}` : ""}`, { cache: "no-store" }),
  saveDatasetItem: (id, item, payload) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}/items${item ? `/${encodeURIComponent(item)}` : ""}`, { method: item ? "PUT" : "POST", body: JSON.stringify(payload) }),
  deleteDatasetItem: (id, item, revision) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}/items/${encodeURIComponent(item)}`, { method: "DELETE", body: JSON.stringify({ expected_revision: revision }) }),
  reviewDatasetItem: (id, item, payload) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}/items/${encodeURIComponent(item)}/reviews`, { method: "POST", body: JSON.stringify(payload) }),
  importDatasetAnalyses: (id, payload) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}/imports`, { method: "POST", body: JSON.stringify(payload) }),
  runValidationDataset: (id, payload) => request(`/api/v1/validation-datasets/${encodeURIComponent(id)}/runs`, { method: "POST", body: JSON.stringify(payload) }),
  referenceSelection: ids => request("/api/v1/evaluation-labels/selection", { method: "POST", body: JSON.stringify({ analysis_ids: ids }) }),
  saveReferences: payload => request("/api/v1/evaluation-labels/bulk", { method: "POST", body: JSON.stringify(payload) }),
  testEvaluations: id => request(`/api/v1/test-runs/${encodeURIComponent(id)}/evaluations`, { cache: "no-store" }),
  rescoreTest: (id, key) => request(`/api/v1/test-runs/${encodeURIComponent(id)}/evaluations`, { method: "POST", body: JSON.stringify({ idempotency_key: key }) }),
  agentSettings: (options = {}) => request("/api/v1/admin/agent-settings", { ...options, cache: "no-store" }),
  searchValidationDatasets: (payload, options = {}) => request("/api/v1/validation-datasets/search", { ...options, method: "POST", body: JSON.stringify(payload), cache: "no-store" }),
  concurrencySettings: (options = {}) => request("/api/v1/admin/agent-settings/concurrency", { ...options, cache: "no-store" }),
  updateConcurrencySettings: payload => request("/api/v1/admin/agent-settings/concurrency", { method: "PUT", body: JSON.stringify(payload), cache: "no-store" }),
  updateAgentSettings: payload => request("/api/v1/admin/agent-settings", { method: "PUT", body: JSON.stringify(payload), cache: "no-store" }),
  agentDiagnostics: (query, options = {}) => request(`/api/v1/admin/agent-settings/diagnostics?${new URLSearchParams(query)}`, { ...options, cache: "no-store" }),
  login: (username, password) => request("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password })
  }),
  logout: () => request("/api/v1/auth/logout", { method: "POST" }),
  me: () => request("/api/v1/auth/me"),
  dashboard: (days = 7, options = {}, serviceApiKeyId = "") => request(`/api/v1/dashboard/summary?${new URLSearchParams({ days, ...(serviceApiKeyId ? { service_api_key_id: serviceApiKeyId } : {}) })}`, { ...options, cache: "no-store" }),
  analyses: (query = {}, options = {}) => request(`/api/v1/analyses?${new URLSearchParams(query)}`, options),
  analysis: (id, options = {}) => request(`/api/v1/analyses/${id}`, options),
  retryEligibility: (id, options = {}) => request(`/api/v1/analyses/${encodeURIComponent(id)}/retry-eligibility`, { ...options, cache: "no-store" }),
  retryAnalysis: (id, payload) => request(`/api/v1/analyses/${encodeURIComponent(id)}/retry`, { method: "POST", body: JSON.stringify(payload), cache: "no-store" }),
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
  testRetryEligibility: (id, options = {}) => request(`/api/v1/test-runs/${encodeURIComponent(id)}/retry-eligibility`, { ...options, cache: "no-store" }),
  retryTestFailures: (id, payload) => request(`/api/v1/test-runs/${encodeURIComponent(id)}/retry-failed`, { method: "POST", body: JSON.stringify(payload), cache: "no-store" }),
  compareTestRuns: (id, query, options = {}) => request(`/api/v1/test-runs/${encodeURIComponent(id)}/comparison?${new URLSearchParams(query)}`, { ...options, cache: "no-store" }),
  createTestRun: (payload) => request("/api/v1/test-runs", { method: "POST", body: JSON.stringify(payload) }),
  uploadTestRun: (file, name, idempotencyKey, candidateConfiguration) => {
    const body = new FormData(); body.append("name", name); body.append("idempotency_key", idempotencyKey); body.append("file", file);
    if (candidateConfiguration) body.append("candidate_configuration", JSON.stringify(candidateConfiguration));
    return request("/api/v1/test-runs/uploads", { method: "POST", body });
  },
  modelProfiles: (options = {}) => request("/api/v1/model-profiles", options),
  serviceApiKeys: (options = {}) => request("/api/v1/admin/service-api-keys", { ...options, cache: "no-store" }),
  createServiceApiKey: (payload) => request("/api/v1/admin/service-api-keys", { method: "POST", body: JSON.stringify(payload), cache: "no-store" }),
  renameServiceApiKey: (id, payload) => request(`/api/v1/admin/service-api-keys/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload), cache: "no-store" }),
  revokeServiceApiKey: (id) => request(`/api/v1/admin/service-api-keys/${encodeURIComponent(id)}/revoke`, { method: "POST", cache: "no-store" }),
  serviceApiKeyDeletionPreview: (id, options = {}) => request(`/api/v1/admin/service-api-keys/${encodeURIComponent(id)}/deletion-preview`, { ...options, cache: "no-store" }),
  deleteServiceApiKey: (id, payload) => request(`/api/v1/admin/service-api-keys/${encodeURIComponent(id)}`, { method: "DELETE", ...(payload ? { body: JSON.stringify(payload) } : {}), cache: "no-store" }),
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
