import assert from "node:assert/strict";
import test from "node:test";
import { emptySingleTest, singleTestEvent } from "./testRuns.js";
import { emptyInitialAssessment, singleTestAdmission } from "./initialAssessmentInput.js";
import { buildSingleAnalysisTemplateEvent, transformSingleAnalysisTemplate } from "./singleAnalysisTemplate.js";

const sourceId = "ABCDEF01-2345-4678-9ABC-DEF012345678";
const event = () => ({
  event_id: "synthetic-original-event", company_name: "Synthetic company", src_ip: "192.0.2.10", dest_ip: "198.51.100.20",
  src_port: 0, dest_port: 443, waf_vendor: "synthetic-vendor", waf_action: "D", signature: "Synthetic signature",
  event_name: "Synthetic HTTP event", payload: "POST /synthetic HTTP/1.1\r\nCookie: synthetic=value\r\n\r\n{\"value\":0}\n",
  vendor_score: 0, vendor_enabled: false, vendor_note: null, vendor_details: { nested: [1, false, null, { status: "observed" }] },
  extra_fields: { company_zone: "synthetic-zone", flags: ["one", "two"], detail: { initial_verdict: "raw custom data" } },
});
const response = () => ({ source_analysis_id: sourceId, event: event(), initial_assessment: {
  initial_verdict: "false_positive", initial_probability: 0, initial_model_version: "synthetic-model-v1",
}, expected_verdict: "inconclusive", difficulty: "hard", test_category: "synthetic-category", case_name: "Synthetic case",
name: "Synthetic source Test", internal_only: true });

test("single template populates all eleven base fields and preserves custom JSON types", () => {
  const source = response(); const before = JSON.stringify(source);
  const copied = transformSingleAnalysisTemplate(source);
  assert.equal(copied.sourceAnalysisId, sourceId.toLowerCase());
  assert.equal(copied.internalOnly, true);
  assert.equal(copied.name, source.name);
  assert.equal(copied.expectedVerdict, source.expected_verdict);
  assert.equal(copied.difficulty, source.difficulty);
  assert.equal(copied.category, source.test_category);
  assert.equal(copied.caseName, source.case_name);
  assert.deepEqual(Object.keys(copied.form), Object.keys(emptySingleTest()));
  for (const [key, value] of Object.entries(copied.form)) {
    assert.equal(typeof value, "string", key);
    assert.equal(value, String(source.event[key]), key);
  }
  assert.deepEqual(copied.initialAssessment, { initial_verdict: "false_positive", initial_probability: "0", initial_model_version: "synthetic-model-v1" });
  const extras = JSON.parse(copied.additionalFields);
  assert.equal(extras.vendor_score, 0); assert.equal(extras.vendor_enabled, false); assert.equal(extras.vendor_note, null);
  assert.deepEqual(extras.vendor_details, source.event.vendor_details);
  assert.deepEqual(extras.extra_fields, source.event.extra_fields);
  assert.equal(Object.keys(emptySingleTest()).some(key => Object.hasOwn(extras, key)), false);
  assert.deepEqual(buildSingleAnalysisTemplateEvent(copied, "synthetic-request"), source.event);
  assert.equal(JSON.stringify(source), before);
  copied.originalEvent.vendor_details.nested[0] = 2;
  assert.equal(source.event.vendor_details.nested[0], 1);
});

test("nullable and omitted optional fields remain distinct when untouched", () => {
  for (const key of ["src_port", "dest_port", "signature", "event_name"]) {
    for (const original of [null, undefined, ...(["signature", "event_name"].includes(key) ? ["", "  "] : [0])]) {
      const source = response();
      if (original === undefined) delete source.event[key]; else source.event[key] = original;
      const copied = transformSingleAnalysisTemplate(source);
      const built = buildSingleAnalysisTemplateEvent(copied, "synthetic-request");
      assert.equal(Object.hasOwn(built, key), Object.hasOwn(source.event, key), key);
      assert.equal(built[key], source.event[key], key);
      assert.deepEqual(built, source.event);
    }
  }
});

test("edited optional values follow existing single-entry conversion without overriding unchanged input", () => {
  const source = response(); source.event.src_port = null; source.event.dest_port = null;
  const copied = transformSingleAnalysisTemplate(source);
  copied.form.src_port = "0"; copied.form.dest_port = "8443";
  copied.form.signature = "Changed signature"; copied.form.event_name = "";
  copied.form.payload += "preserved whitespace  \n";
  const built = buildSingleAnalysisTemplateEvent(copied, "synthetic-request");
  assert.equal(built.src_port, 0); assert.equal(built.dest_port, 8443);
  assert.equal(built.signature, "Changed signature"); assert.equal(Object.hasOwn(built, "event_name"), false);
  assert.equal(built.payload, copied.form.payload);
  assert.deepEqual(built.vendor_details, source.event.vendor_details);
  assert.equal(source.event.src_port, null);
});

test("an unchanged source event ID retains whitespace, while an edited blank ID uses normal generation", () => {
  const source = response(); source.event.event_id = "  synthetic-original-event  ";
  const copied = transformSingleAnalysisTemplate(source);
  assert.equal(buildSingleAnalysisTemplateEvent(copied, "synthetic-request").event_id, source.event.event_id);
  copied.form.event_id = "";
  assert.equal(buildSingleAnalysisTemplateEvent(copied, "synthetic-request").event_id, "event-synthetic-request");
  copied.form.event_id = "  synthetic-edited-event  ";
  assert.equal(buildSingleAnalysisTemplateEvent(copied, "synthetic-request").event_id, "synthetic-edited-event");
});

test("additional JSON can be edited or cleared without flattening extra_fields", () => {
  const copied = transformSingleAnalysisTemplate(response());
  copied.additionalFields = JSON.stringify({ extra_fields: { updated: null }, custom_count: 7 });
  const built = buildSingleAnalysisTemplateEvent(copied, "synthetic-request");
  assert.deepEqual(built.extra_fields, { updated: null }); assert.equal(built.custom_count, 7);
  assert.equal(Object.hasOwn(built, "vendor_score"), false);
  copied.additionalFields = "";
  assert.deepEqual(buildSingleAnalysisTemplateEvent(copied, "synthetic-request"), eventBase(copied.originalEvent));
});

function eventBase(input) { return Object.fromEntries(Object.entries(input).filter(([key]) => Object.hasOwn(emptySingleTest(), key))); }

test("expected answers and initial assessment come only from dedicated metadata, never model output", () => {
  const source = response(); source.initial_assessment = null; source.expected_verdict = null;
  source.verdict = "true_positive"; source.result = { verdict: "true_positive", summary_ko: "Synthetic model output" };
  source.initial_verdict = "true_positive"; source.reference_verdict = "true_positive";
  const copied = transformSingleAnalysisTemplate(source);
  assert.equal(copied.expectedVerdict, ""); assert.deepEqual(copied.initialAssessment, emptyInitialAssessment());
  const built = buildSingleAnalysisTemplateEvent(copied, "synthetic-request");
  assert.equal(Object.hasOwn(built, "expected_verdict"), false);
  assert.equal(Object.hasOwn(built, "verdict"), false); assert.equal(Object.hasOwn(built, "initial_verdict"), false);
  assert.deepEqual(singleTestAdmission(built, copied.initialAssessment), { event: source.event });
  const initial = transformSingleAnalysisTemplate(response());
  assert.equal(singleTestAdmission(buildSingleAnalysisTemplateEvent(initial, "synthetic-request"), initial.initialAssessment).initial_probability, 0);
});

test("missing optional template metadata stays empty rather than being invented", () => {
  const copied = transformSingleAnalysisTemplate({ source_analysis_id: sourceId, event: event(), internal_only: false });
  assert.equal(copied.name, ""); assert.equal(copied.expectedVerdict, ""); assert.equal(copied.difficulty, "");
  assert.equal(copied.category, ""); assert.equal(copied.caseName, ""); assert.equal(copied.internalOnly, false);
  assert.deepEqual(copied.initialAssessment, emptyInitialAssessment());
  const emptyInitial = transformSingleAnalysisTemplate({ ...response(), initial_assessment: {
    initial_verdict: null, initial_probability: null, initial_model_version: null,
  } });
  assert.deepEqual(emptyInitial.initialAssessment, emptyInitialAssessment());
  const optionalVersion = transformSingleAnalysisTemplate({ ...response(), initial_assessment: {
    initial_verdict: "true_positive", initial_probability: 1, initial_model_version: null,
  } });
  assert.deepEqual(optionalVersion.initialAssessment, { initial_verdict: "true_positive", initial_probability: "1", initial_model_version: "" });
});

test("malformed server templates fail with a fixed code and do not expose input", () => {
  const invalids = [null, [], "PRIVATE_INPUT", {}, { ...response(), event: [] }, { ...response(), internal_only: "true" },
    { ...response(), source_analysis_id: "PRIVATE_INPUT" }, { ...response(), expected_verdict: "attack" },
    { ...response(), name: {} }, { ...response(), test_category: "x".repeat(121) },
    { ...response(), initial_assessment: [] }, { ...response(), initial_assessment: { initial_verdict: "true_positive" } },
    { ...response(), initial_assessment: { initial_verdict: "true_positive", initial_probability: "0.5" } },
    { ...response(), initial_assessment: { initial_verdict: "inconclusive", initial_probability: 0.5 } },
    { ...response(), initial_assessment: { initial_verdict: "true_positive", initial_probability: 0.5, result: "PRIVATE_INPUT" } },
  ];
  for (const source of invalids) assert.throws(() => transformSingleAnalysisTemplate(source), /^Error: single_analysis_template_invalid$/);
  for (const patch of [{ event_id: null }, { payload: null }, { src_port: "443" }, { dest_port: -1 }, { signature: {} }, { waf_action: "deny" }, { vendor_score: NaN }, { vendor_score: undefined }]) {
    assert.throws(() => transformSingleAnalysisTemplate({ ...response(), event: { ...event(), ...patch } }), /^Error: single_analysis_template_invalid$/);
  }
});

test("top-level server-control and answer fields cannot be copied or injected as additional input", () => {
  for (const key of ["analysis_id", "source_analysis_id", "verdict", "expected_verdict", "reference_verdict", "initial_verdict", "candidate_configuration", "signature_explanation", "production_configuration_id", "internal_only", "parser_version_id", "api_key", "instructions"]) {
    const source = response(); source.event[key] = "PRIVATE_INPUT";
    assert.throws(() => transformSingleAnalysisTemplate(source), /^Error: single_analysis_template_invalid$/, key);
    const copied = transformSingleAnalysisTemplate(response());
    copied.additionalFields = JSON.stringify({ [key]: "PRIVATE_INPUT" });
    assert.throws(() => buildSingleAnalysisTemplateEvent(copied, "synthetic-request"), /^Error: single_analysis_template_additional_fields_invalid$/);
  }
});

test("direct extra_fields restrictions match AnalysisInput without rejecting same-name business data", () => {
  for (const key of ["signature_explanation", "signature_explanation_version_id", "signature_explanation_snapshot_ciphertext", "production_configuration_id", "initial_verdict", "initial_probability", "initial_model_version"]) {
    const source = response(); source.event.extra_fields[key] = "PRIVATE_INPUT";
    assert.throws(() => transformSingleAnalysisTemplate(source), /^Error: single_analysis_template_invalid$/, key);
    const copied = transformSingleAnalysisTemplate(response());
    copied.additionalFields = JSON.stringify({ extra_fields: { [key]: "PRIVATE_INPUT" } });
    assert.throws(() => buildSingleAnalysisTemplateEvent(copied, "synthetic-request"), /^Error: single_analysis_template_additional_fields_invalid$/);
  }
  const business = { status: "vendor-status", company_name: "Vendor's own company field", source_analysis_id: "vendor-owned value",
    event_id: "vendor-event", src_ip: "vendor-source", dest_ip: "vendor-destination", signature: "vendor-signature",
    verdict: "vendor-data", expected_verdict: "vendor-data", result: { code: 0 }, nested: { initial_verdict: "raw vendor text" } };
  const source = response(); source.event.extra_fields = business;
  const copied = transformSingleAnalysisTemplate(source);
  assert.deepEqual(JSON.parse(copied.additionalFields).extra_fields, business);
  assert.deepEqual(buildSingleAnalysisTemplateEvent(copied, "synthetic-request").extra_fields, business);
  copied.additionalFields = JSON.stringify({ extra_fields: { ...business, status: "edited vendor-status" } });
  assert.deepEqual(buildSingleAnalysisTemplateEvent(copied, "synthetic-request").extra_fields, { ...business, status: "edited vendor-status" });
  assert.equal(copied.expectedVerdict, "inconclusive");
  assert.equal(copied.sourceAnalysisId, sourceId.toLowerCase());
});

test("malformed additional JSON and duplicate base fields are rejected without reflecting values", () => {
  const copied = transformSingleAnalysisTemplate(response());
  for (const additionalFields of ["{PRIVATE_INPUT", "[]", "null", '"PRIVATE_INPUT"', "1", JSON.stringify({ payload: "PRIVATE_INPUT" }), {}, null]) {
    assert.throws(() => buildSingleAnalysisTemplateEvent({ ...copied, additionalFields }, "synthetic-request"), /^Error: single_analysis_template_additional_fields_invalid$/);
  }
});

test("the builder also supports an ordinary empty-source form using existing conversion", () => {
  const form = { ...emptySingleTest(), ...Object.fromEntries(Object.entries(eventBase(event())).map(([key, value]) => [key, String(value)])) };
  assert.deepEqual(buildSingleAnalysisTemplateEvent({ form }, "synthetic-request"), singleTestEvent(form, "synthetic-request"));
  assert.throws(() => buildSingleAnalysisTemplateEvent({ form: { ...form, src_port: 0 } }, "synthetic-request"), /^Error: single_analysis_template_invalid$/);
});

test("ordinary manual tests retain initial-metadata JSON support and existing omitted-field conflicts", () => {
  const form = { ...emptySingleTest(), ...Object.fromEntries(Object.entries(eventBase(event())).map(([key, value]) => [key, String(value)])), signature: "" };
  const initial = { initial_verdict: "true_positive", initial_probability: 0 };
  const additionalFields = JSON.stringify({ ...initial, signature: "Additional signature", custom_flag: false });
  const built = buildSingleAnalysisTemplateEvent({ form, additionalFields, originalEvent: undefined }, "synthetic-request");
  const admission = singleTestAdmission(built, emptyInitialAssessment());
  assert.equal(admission.initial_probability, 0); assert.equal(admission.initial_verdict, "true_positive");
  assert.equal(Object.hasOwn(admission.event, "initial_verdict"), false);
  assert.equal(admission.event.signature, "Additional signature"); assert.equal(admission.event.custom_flag, false);
  assert.throws(() => singleTestAdmission(built, { ...initial, initial_probability: "0" }), /duplicate_test_metadata_location/);
  assert.throws(() => buildSingleAnalysisTemplateEvent({ form, additionalFields: JSON.stringify({ company_name: "Duplicate" }) }, "synthetic-request"), /^Error: single_analysis_template_additional_fields_invalid$/);
  const nested = buildSingleAnalysisTemplateEvent({ form, additionalFields: JSON.stringify({ extra_fields: initial }) }, "synthetic-request");
  assert.throws(() => singleTestAdmission(nested, emptyInitialAssessment()), /initial_assessment_location_invalid/);
  const deferred = buildSingleAnalysisTemplateEvent({ form, additionalFields: JSON.stringify({ expected_verdict: "true_positive", result: "Synthetic invalid input" }) }, "synthetic-request");
  assert.equal(deferred.expected_verdict, "true_positive");
  assert.equal(deferred.result, "Synthetic invalid input");
});
