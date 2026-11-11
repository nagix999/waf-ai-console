// Transport metadata is deliberately out of band: do not add JSON fields or
// symbols to an API result, export it with the result, or infer missing sources.
const metadata = new WeakMap();
const statuses = new Set(["completed", "fallback", "skipped", "pending"]);
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);

export function analysisEvidenceMeta(value) { return record(value) ? metadata.get(value) ?? null : null; }

export function rememberEvidenceMeta(detail, value) {
  if (!record(detail) || value?.view !== "core-v1" || !statuses.has(value.status)) return null;
  const safe = Object.freeze({ view: "core-v1", status: value.status });
  metadata.set(detail, safe);
  if (record(detail.result)) metadata.set(detail.result, safe);
  return safe;
}

export function responseEvidenceMeta(detail, headers) {
  return rememberEvidenceMeta(detail, { view: headers?.get?.("X-Evidence-View"), status: headers?.get?.("X-Evidence-Status") });
}

export function isCoreEvidence(result, evidenceView) {
  return result?.schema_version === "waf-analysis-v3"
    && (evidenceView === "core-v1" || analysisEvidenceMeta(result)?.view === "core-v1");
}

// The server owns selection, ordering, interpretation and direction. Keep every
// received entry, including identical-looking entries and required context.
export function receivedEvidence(result) {
  return (Array.isArray(result?.evidence) ? result.evidence : []).map((item, index) => ({ ...item,
    number: /^e[1-9][0-9]*$/.test(item?.evidence_id) ? Number(item.evidence_id.slice(1)) : index + 1,
    roles: ["primary", "verifier"].filter(role => Array.isArray(item?.origins) && item.origins.some(origin => origin?.role === role)),
    related_numbers: [],
  }));
}
