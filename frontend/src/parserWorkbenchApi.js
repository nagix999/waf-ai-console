import * as transport from "./api.js";

const request = (path, options) => transport.request(path, options);

const base = "/api/v1/admin/parser-workbench";
const segment = value => encodeURIComponent(value);
const post = (path, payload = {}) => request(path, { method: "POST", body: JSON.stringify(payload), cache: "no-store" });

export const parserWorkbenchApi = {
  catalog: (options = {}) => request(`${base}/catalog`, { ...options, cache: "no-store" }),
  sourcePreview: (payload, options = {}) => request(`${base}/source-preview`, { ...options, method: "POST", body: JSON.stringify(payload), cache: "no-store" }),
  createSession: payload => post(`${base}/sessions`, payload),
  session: (id, options = {}) => request(`${base}/sessions/${segment(id)}`, { ...options, cache: "no-store" }),
  jobs: (id, options = {}) => request(`${base}/sessions/${segment(id)}/jobs`, { ...options, cache: "no-store" }),
  discover: (id, payload) => post(`${base}/sessions/${segment(id)}/discover`, payload),
  groups: (id, groups, expected_revision) => post(`${base}/sessions/${segment(id)}/groups`, { groups, expected_revision }),
  generate: (id, payload) => post(`${base}/sessions/${segment(id)}/generate`, payload),
  expectations: (id, items, expected_revision) => request(`${base}/sessions/${segment(id)}/expectations`, { method: "PUT", body: JSON.stringify({ items, expected_revision }), cache: "no-store" }),
  validate: (id, expected_revision) => post(`${base}/sessions/${segment(id)}/validate`, { expected_revision }),
  saveCandidate: (id, payload) => post(`${base}/sessions/${segment(id)}/save-candidate`, payload),
};
