import { emptyInitialAssessment, singleTestAdmission } from "./initialAssessmentInput.js";
import { emptySingleTest, singleTestEvent } from "./testRuns.js";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const baseFields = Object.keys(emptySingleTest());
const optionalFields = ["src_port", "dest_port", "signature", "event_name"];
const initialFields = Object.keys(emptyInitialAssessment());
const verdicts = new Set(["true_positive", "false_positive", "inconclusive"]);
// Admission metadata and server snapshots must never become copied event input.
// These mirror input_field_policy.SERVER_CONTROL_FIELDS and label_fields.LABEL_FIELDS.
const reservedFields = new Set([
  "signature_explanation", "signature_explanation_version_id", "signature_explanation_snapshot_ciphertext",
  "production_configuration_id", "parser_version_id", "parser_snapshot_json",
  "initial_verdict", "initial_probability", "initial_model_version", "initial_assessment", "test_purpose",
  "source_system", "analysis_purpose", "ingest_channel", "analysis_scope", "purpose", "verdict", "severity",
  "status", "result", "result_json", "confidence_score", "summary_ko", "event_fingerprint", "id", "analysis_id", "source_analysis_id",
  "prompt_version", "model_profile", "started_at", "completed_at", "created_at", "total_elapsed_ms", "queue_wait_ms",
  "processing_duration_ms", "input_truncated", "threat_category", "provenance", "is_test", "base_url", "api_key",
  "instructions", "system_prompt", "enable_thinking", "run_id", "attempt_count", "lease_owner", "lease_expires_at",
  "payload_ciphertext", "encryption_key_version", "error_code", "error_message", "prompt_policy_version_id",
  "prompt_snapshot_ciphertext", "policy_text", "policy_version_id", "fixed_rules_version", "primary_instructions",
  "verifier_instructions", "model_test_run_id", "include_dataset", "dataset_version", "dataset_hash", "test_run_id",
  "test_category", "test_difficulty", "idempotency_key", "test_name", "input_schema_version_id",
  "input_schema_snapshot_ciphertext", "input_schema_metadata", "input_schema", "field_definitions", "schema_snapshot",
  "selection_origin", "internal_only", "dataset_version_id", "api_source_system", "accepting_items",
  "candidate_configuration", "configuration_snapshot", "configuration_hash", "configuration_snapshot_json",
  "evaluation_mode", "approved_item_version_ids", "dataset_item_version_id", "official_evaluation_pending",
  "ground_truth", "evaluation_kind", "metrics_version", "analysis_ids_json", "summary_json",
  "label", "labels", "ground_truth_label", "reference_label", "expected_verdict", "expected_severity", "evaluation",
  "evaluation_label", "evaluation_result", "evaluation_outcome", "label_source_kind", "label_source_ref", "label_ai_visible",
  "is_correct", "answer", "answers", "difficulty", "case_name", "important_evidence", "verification_notes_ko",
  "rationale_ko", "reference_verdict", "reference_comment",
]);
// Only these direct extra_fields keys are reserved by AnalysisInput. Other
// same-name vendor data (for example status or event_id) is not server metadata.
const nestedReservedFields = new Set([
  "signature_explanation", "signature_explanation_version_id", "signature_explanation_snapshot_ciphertext",
  "production_configuration_id", ...initialFields,
]);
const record = value => value !== null && typeof value === "object" && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const invalid = () => { throw new Error("single_analysis_template_invalid"); };
const inputString = value => value == null ? "" : String(value);

function validJson(value, ancestors = new Set()) {
  if (value === null || ["string", "boolean"].includes(typeof value)) return true;
  if (typeof value === "number") return Number.isFinite(value);
  if ((!record(value) && !Array.isArray(value)) || ancestors.has(value)) return false;
  const branch = new Set(ancestors); branch.add(value);
  return Object.values(value).every(item => validJson(item, branch));
}

function safeEvent(event) {
  if (!record(event) || !validJson(event)) invalid();
  if (Object.keys(event).some(key => reservedFields.has(key))) invalid();
  if (record(event.extra_fields) && Object.keys(event.extra_fields).some(key => nestedReservedFields.has(key))) invalid();
  for (const key of baseFields.filter(key => !optionalFields.includes(key))) {
    if (typeof event[key] !== "string" || !event[key].length) invalid();
  }
  if (!["D", "A"].includes(event.waf_action)) invalid();
  for (const key of ["signature", "event_name"]) {
    if (event[key] != null && typeof event[key] !== "string") invalid();
  }
  for (const key of ["src_port", "dest_port"]) {
    if (event[key] != null && (!Number.isInteger(event[key]) || event[key] < 0 || event[key] > 65535)) invalid();
  }
  return JSON.parse(JSON.stringify(event));
}

function optionalText(value, maxLength) {
  if (value == null) return "";
  if (typeof value !== "string" || value.length > maxLength) invalid();
  return value;
}

/** Convert an authorized server template into editable values, in memory only. */
export function transformSingleAnalysisTemplate(template) {
  if (!record(template) || typeof template.source_analysis_id !== "string" || !uuid.test(template.source_analysis_id)
    || typeof template.internal_only !== "boolean") invalid();
  const originalEvent = safeEvent(template.event);
  const form = Object.fromEntries(baseFields.map(key => [key, inputString(originalEvent[key])]));
  const additional = Object.fromEntries(Object.entries(originalEvent).filter(([key]) => !baseFields.includes(key)));
  const initialAssessment = emptyInitialAssessment();
  if (template.initial_assessment != null) {
    if (!record(template.initial_assessment) || Object.keys(template.initial_assessment).some(key => !initialFields.includes(key))) invalid();
    const nonNull = Object.fromEntries(Object.entries(template.initial_assessment).filter(([, value]) => value != null));
    try { singleTestAdmission({}, nonNull); } catch { invalid(); }
    const probability = template.initial_assessment.initial_probability;
    if (probability != null && (typeof probability !== "number" || !Number.isFinite(probability))) invalid();
    for (const key of initialFields) initialAssessment[key] = inputString(template.initial_assessment[key]);
  }
  if (template.expected_verdict != null && !verdicts.has(template.expected_verdict)) invalid();
  return {
    sourceAnalysisId: template.source_analysis_id.toLowerCase(), internalOnly: template.internal_only,
    name: optionalText(template.name, 120), expectedVerdict: template.expected_verdict ?? "",
    difficulty: optionalText(template.difficulty, 80), category: optionalText(template.test_category, 120),
    caseName: optionalText(template.case_name, 240), form, initialAssessment,
    additionalFields: Object.keys(additional).length ? JSON.stringify(additional, null, 2) : "", originalEvent,
  };
}

/** Build edited input while retaining untouched nullable fields and extra types. */
export function buildSingleAnalysisTemplateEvent({ form, additionalFields = "", originalEvent = null }, requestId) {
  if (!record(form) || baseFields.some(key => typeof form[key] !== "string")) invalid();
  const base = Object.fromEntries(baseFields.map(key => [key, form[key]]));
  const event = singleTestEvent(base, requestId);
  if (originalEvent !== null) {
    const original = safeEvent(originalEvent);
    for (const key of ["event_id", ...optionalFields]) {
      if (base[key] !== inputString(original[key])) continue;
      if (Object.hasOwn(original, key)) event[key] = original[key];
      else delete event[key];
    }
  }
  let additional;
  try { additional = typeof additionalFields === "string" && additionalFields.trim() ? JSON.parse(additionalFields) : {}; }
  catch { throw new Error("single_analysis_template_additional_fields_invalid"); }
  // Ordinary manual tests retain their original JSON/duplicate checks. In
  // particular, singleTestAdmission owns their specific initial_* errors and
  // the server owns input-schema validation. Only copied input is restricted
  // here, so server snapshots cannot be reintroduced into the copied event.
  const duplicateField = key => originalEvent === null ? Object.hasOwn(event, key) : baseFields.includes(key);
  if (typeof additionalFields !== "string" || !record(additional)
    || Object.keys(additional).some(duplicateField)
    || originalEvent !== null && (!validJson(additional) || Object.keys(additional).some(key => reservedFields.has(key))
      || record(additional.extra_fields) && Object.keys(additional.extra_fields).some(key => nestedReservedFields.has(key)))) {
    throw new Error("single_analysis_template_additional_fields_invalid");
  }
  return { ...event, ...additional };
}
