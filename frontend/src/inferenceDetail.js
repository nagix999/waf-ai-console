// Only recorded execution metadata is used. Never load today's assignments to
// fill in an older execution, and never infer a Verifier from Primary.
export function recordedProfiles(detail, runs) {
  const steps = runs?.[0]?.steps || [];
  const find = role => steps.find(step => step.step_type === role || step.name === role)?.metadata;
  const saved = detail.result?.agent?.role_profiles;
  return {
    primary: saved?.primary?.model_profile || detail.model_profile || find("llm_primary")?.model_profile,
    verifier: saved?.verifier?.model_profile || find("llm_verifier")?.model_profile,
    editor: find("llm_evidence_editor")?.model_profile,
  };
}

const inputFields = ["event_id", "event_name", "company_name", "src_ip", "src_port", "dest_ip", "dest_port", "waf_vendor", "waf_action", "signature"];
export function recordedInputFields(detail) {
  return Object.fromEntries(inputFields.filter(key => Object.hasOwn(detail || {}, key)).map(key => [key, detail[key]]));
}

function ownPath(value, path) {
  for (const part of path.split(".")) {
    if (!value || typeof value !== "object" || !Object.hasOwn(value, part)) return undefined;
    value = value[part];
  }
  return value;
}

// Do not decode, fuzzy-match or search unrelated fields for an evidence jump.
export function evidenceLocation(event, detail, target) {
  if (!target || typeof target.field !== "string" || typeof target.excerpt !== "string" || !target.excerpt) return null;
  const field = target.field.replace(/^event\./, "");
  const http = field === "payload" || field === "raw_payload" || field.startsWith("payload.");
  let value;
  if (http) value = event.payload;
  else if (field.startsWith("extra_fields.")) value = ownPath(event.extra_fields, field.slice(13));
  else if (Object.hasOwn(recordedInputFields(detail), field)) value = detail[field];
  else value = ownPath(event.extra_fields, field);
  const text = typeof value === "string" ? value : typeof value === "number" || typeof value === "boolean" ? String(value) : null;
  return { section: http ? "http" : "fields", field: target.field, value: text,
    query: target.excerpt, found: text !== null && text.includes(target.excerpt) };
}
