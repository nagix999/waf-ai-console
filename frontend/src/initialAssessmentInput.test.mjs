import assert from "node:assert/strict";
import test from "node:test";
import { singleTestAdmission, emptyInitialAssessment, initialAssessmentError } from "./initialAssessmentInput.js";
import { testRunError } from "./testRuns.js";

const initial = { initial_verdict: "true_positive", initial_probability: 0, initial_model_version: "fixture-v1" };
const event = { event_id: "fixture", payload: "initial_verdict remains raw HTTP text", vendor_score: 2 };

test("single input removes reserved initial metadata from the event without rewriting payload or references", () => {
  const input = { ...event, ...initial };
  assert.deepEqual(singleTestAdmission(input), { event, ...initial });
  assert.deepEqual(input, { ...event, ...initial });
  assert.deepEqual(singleTestAdmission(event, { ...initial, initial_probability: "0" }), { event, ...initial });
  assert.deepEqual(singleTestAdmission(event, emptyInitialAssessment()), { event });
  assert.equal(Object.hasOwn(singleTestAdmission(input), "expected_verdict"), false);
});

test("dedicated input and additional JSON cannot silently overwrite each other", () => {
  assert.throws(() => singleTestAdmission({ ...event, ...initial }, initial), /duplicate_test_metadata_location/);
  assert.throws(() => singleTestAdmission({ ...event, extra_fields: initial }), /initial_assessment_location_invalid/);
});

test("initial confidence is finite and numeric, with a paired two-class verdict", () => {
  for (const value of [true, "0.9", -1, 90, NaN, Infinity]) assert.throws(() => singleTestAdmission({ ...event, ...initial, initial_probability: value }), /initial_probability_invalid/);
  assert.throws(() => singleTestAdmission({ ...event, initial_verdict: "true_positive" }), /initial_assessment_pair_required/);
  assert.throws(() => singleTestAdmission({ ...event, initial_model_version: "alone" }), /initial_assessment_pair_required/);
  assert.throws(() => singleTestAdmission({ ...event, ...initial, initial_verdict: "inconclusive" }), /initial_verdict_invalid/);
  assert.throws(() => singleTestAdmission({ ...event, ...initial, initial_model_version: "x".repeat(256) }), /initial_model_version_invalid/);
  assert.equal(singleTestAdmission({ ...event, ...initial, initial_probability: 1 }).initial_probability, 1);
});

test("safe error messages explain the field without displaying arbitrary server content", () => {
  const error = { message: "HTTP 422", issues: [{ field: "body.initial_probability", type: "float_type" }] };
  assert.match(testRunError(error), /0~1/);
  assert.match(initialAssessmentError(error, (_, en) => en), /0 to 1/);
  assert.match(testRunError({ message: "initial_assessment_conflict" }), /기존 값/);
  assert.match(initialAssessmentError({ issues: [{ field: "body", code: "initial_assessment_pair_required" }] }), /함께/);
  assert.equal(initialAssessmentError({ message: "PRIVATE_RAW_EVENT", issues: [{ field: "body.PRIVATE_RAW_EVENT" }] }), null);
});
