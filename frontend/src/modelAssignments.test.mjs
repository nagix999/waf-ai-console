import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { assignmentBlockReason, createAssignmentController, modelAssignmentPayload, roleAssigned, testModelAvailability } from "./modelAssignments.js";
import { modelProfileError } from "./llmProfiles.js";
import { testRunError } from "./testRuns.js";

const profile = extra => ({ id: "synthetic-model", name: "synthetic-model", provider: "vllm", model_name: "synthetic", status: "verified", is_test: false, can_assign: true, assignment_block_reason: null, profile_fingerprint: "a".repeat(64), ...extra });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test("role eligibility uses current matching full validation rather than status alone or the latest quick test", () => {
  assert.equal(assignmentBlockReason(profile()), ""); assert.equal(assignmentBlockReason(profile({ status: "production" })), "");
  assert.match(assignmentBlockReason(profile({ can_assign: false, assignment_block_reason: "matching_full_test_required" })), /현재 설정과 일치하는 전체 검증/);
  assert.match(assignmentBlockReason(profile({ status: "draft", can_assign: false, assignment_block_reason: "profile_not_verified" })), /먼저 통과/);
  assert.match(assignmentBlockReason(profile({ can_assign: undefined })), /빠른 테스트만으로/);
  assert.match(assignmentBlockReason(profile({ status: "disabled" })), /비활성/);
  assert.match(assignmentBlockReason(profile({ provider: "openai", external_data_approved: false })), /외부 전송 승인/);
  assert.match(assignmentBlockReason(profile(), { targetError: "허용되지 않은 내부 대상" }), /내부 대상/);
  assert.match(assignmentBlockReason(profile(), { active: true }), /진행 중/);
  assert.match(assignmentBlockReason(profile(), { error: "synthetic" }), /조회하지 못/);
});

test("either role locks profile editing and one profile can explicitly hold both roles", () => {
  assert.equal(roleAssigned(profile()), false); assert.equal(roleAssigned(profile({ status: "production" })), true); assert.equal(roleAssigned(profile({ is_test: true })), true); assert.equal(roleAssigned(profile({ status: "production", is_test: true })), true);
  assert.match(modelProfileError(new Error("test_profile_is_immutable")), /Test 지정 해제/);
  assert.match(modelProfileError(new Error("production_profile_is_immutable")), /Production/);
});

test("ordinary testing never falls back to production and only explicit stub mode bypasses test assignment", () => {
  const prod = profile({ status: "production" });
  const missing = testModelAvailability({ profiles: [prod], loading: false, error: "", agentMode: "moduagent" });
  assert.equal(missing.profile, null); assert.match(missing.blocked, /Production으로 자동 대체하지/);
  const testProfile = profile({ id: "test-profile", is_test: true });
  const assigned = testModelAvailability({ profiles: [prod, testProfile], loading: false, error: "", agentMode: "moduagent" });
  assert.equal(assigned.blocked, ""); assert.equal(assigned.profile.id, "test-profile");
  assert.equal(testModelAvailability({ profiles: [profile({ is_test: true, status: "production" })], loading: false, agentMode: "moduagent" }).blocked, "");
  assert.match(testModelAvailability({ profiles: [], error: "network", agentMode: "moduagent" }).blocked, /조회하지 못/);
  assert.match(testModelAvailability({ profiles: [testProfile, { ...testProfile, id: "other" }], agentMode: "moduagent" }).blocked, /올바르지/);
  assert.equal(testModelAvailability({ profiles: null, loading: true, error: "network", agentMode: "stub" }).blocked, "");
  assert.match(testRunError(new Error("test_model_profile_required")), /Test 모델/);
  assert.match(testRunError(new Error("matching_full_test_required")), /현재 설정과 일치하는 전체 검증/);
  assert.match(testRunError(new Error("profile_not_verified")), /전체 검증을 통과한 프로필을 Test로 지정/);
});

test("opening and cancelling assignment copies no secrets and makes no request", () => {
  let calls = 0; const controller = createAssignmentController({ api: { assignTestModelProfile() { calls++; } }, onChange() {} });
  controller.open(profile({ api_key: "NEVER_COPY" }), "test", []);
  assert.equal(controller.getState().target.action, "test"); assert.equal(Object.hasOwn(controller.getState().target, "api_key"), false); assert.equal(calls, 0);
  controller.close(); assert.equal(controller.getState().target, null); assert.equal(calls, 0);
  controller.open(profile({ can_assign: false }), "test"); assert.equal(controller.getState().target, null); controller.dispose();
});

test("explicit production and test assignment preserve role isolation and send the frozen fingerprint", async () => {
  const calls = []; const methods = Object.fromEntries(["promoteModelProfile", "assignTestModelProfile", "unassignTestModelProfile", "disableModelProfile"].map(name => [name, async (...args) => { calls.push([name, ...args]); }]));
  const controller = createAssignmentController({ api: methods, onChange() {} });
  for (const action of ["production", "test", "unassign_test", "disable"]) {
    controller.open(profile({ is_test: true }), action, [profile({ id: "old", name: "old-test", is_test: true })]);
    if (action === "test") assert.equal(controller.getState().target.previous_name, "old-test");
    assert.equal(await controller.submit(), true);
  }
  assert.deepEqual(calls, [["promoteModelProfile", "synthetic-model", "a".repeat(64)], ["assignTestModelProfile", "synthetic-model", "a".repeat(64)], ["unassignTestModelProfile", "synthetic-model", "a".repeat(64)], ["disableModelProfile", "synthetic-model"]]); controller.dispose();
});

test("assignment submission deduplicates and target cannot be changed until the response returns", async () => {
  const response = deferred(); let calls = 0; const controller = createAssignmentController({ api: { assignTestModelProfile: async () => { calls++; return response.promise; } }, onChange() {} });
  controller.open(profile(), "test"); const pending = controller.submit(); assert.equal(await controller.submit(), false); controller.close(); controller.open(profile({ id: "wrong" }), "production");
  assert.equal(controller.getState().target.id, "synthetic-model"); assert.equal(calls, 1); response.resolve({}); assert.equal(await pending, true); controller.dispose();
});

test("changed settings and ambiguous responses require fresh confirmation without automatic retries", async () => {
  for (const failure of [Object.assign(new Error("model_profile_changed_reconfirm"), { status: 409 }), new Error("SECRET_FAILURE")]) {
    let calls = 0, refreshes = 0; const controller = createAssignmentController({ api: { assignTestModelProfile: async () => { calls++; throw failure; } }, onChange() {}, onRefresh() { refreshes++; } });
    controller.open(profile(), "test"); assert.equal(await controller.submit(), false); assert.equal(controller.getState().needsReconfirm, true); assert.equal(await controller.submit(), false); assert.equal(calls, 1); assert.equal(refreshes, 1); assert.doesNotMatch(controller.getState().error, /SECRET_FAILURE/); controller.dispose();
  }
});

test("late assignment result after unmount cannot change UI or trigger callbacks", async () => {
  const response = deferred(); let committed = 0; const states = []; const controller = createAssignmentController({ api: { assignTestModelProfile: async () => response.promise }, onChange: state => states.push(state), onCommitted: () => committed++ });
  controller.open(profile(), "test"); const pending = controller.submit(); controller.dispose(); const count = states.length; response.resolve({}); await pending; assert.equal(states.length, count); assert.equal(committed, 0);
});

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./ModelAssignments.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} }; runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL });
const { ModelAssignmentCards, AssignmentDialogContent, TestModelNotice } = module.exports;
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));

test("role cards show independent names, unassigned state and read failure without claiming missing assignments", () => {
  const html = render(ModelAssignmentCards, { profiles: [profile({ status: "production", name: "prod-only" }), profile({ id: "test", is_test: true, name: "test-only" })], onUnassignTest() {} });
  assert.match(html, /prod-only/); assert.match(html, /test-only/); assert.match(html, /Test 지정 해제/); assert.match(html, /각각 지정/);
  const missing = render(ModelAssignmentCards, { profiles: [], onUnassignTest() {} }); assert.match(missing, /Production으로 자동 대체하지/);
  const failed = render(ModelAssignmentCards, { profiles: [], error: "synthetic", onUnassignTest() {} }); assert.match(failed, /조회하지 못/); assert.doesNotMatch(failed, />미지정</);
});

test("confirmation explains scope, OpenAI transmission and disable-all impact with escaped names", () => {
  const target = { ...profile({ name: "<script>synthetic</script>", provider: "openai" }), action: "test", previous_name: "previous-test" };
  const html = render(AssignmentDialogContent, { target, onCancel() {}, onConfirm() {} }); assert.match(html, /previous-test/); assert.match(html, /Production 지정은 변경하지/); assert.match(html, /OpenAI 외부 API/); assert.match(html, /API 비용/); assert.match(html, /&lt;script/); assert.doesNotMatch(html, /<script/);
  const clear = render(AssignmentDialogContent, { target: { ...target, action: "unassign_test" } }); assert.match(clear, /Test 지정만 해제/); assert.match(clear, /Production 지정과 프로필은 유지/);
  const disable = render(AssignmentDialogContent, { target: { ...target, action: "disable" } }); assert.match(disable, /Production·Test 지정을 모두 해제/); assert.match(disable, /진행 중 작업/);
});

test("ordinary test notice distinguishes selected Test, missing Test and stub without using a candidate validation profile", () => {
  const html = render(TestModelNotice, { state: { profiles: [profile({ is_test: true })], loading: false, error: "" }, agentMode: "moduagent" }); assert.match(html, /이 테스트에 사용할 Test 모델/); assert.match(html, /synthetic-model/); assert.match(html, /접수 당시 Test 모델/);
  const missing = render(TestModelNotice, { state: { profiles: [profile({ status: "production" })], loading: false }, agentMode: "moduagent" }); assert.match(missing, /Test 모델이 지정되지/); assert.match(missing, /Production으로 자동 대체하지/);
  const stub = render(TestModelNotice, { state: { profiles: [], loading: false }, agentMode: "stub" }); assert.match(stub, /실제 LLM은 호출하지/);
});

test("role APIs send only profile fingerprint and never launch tests or alter the other role", { concurrency: false }, async () => {
  const original = globalThis.fetch; const calls = []; globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response("{}", { status: 200 }); };
  try { await api.assignTestModelProfile("profile/id", "a".repeat(64)); await api.unassignTestModelProfile("profile/id", "b".repeat(64)); await api.promoteModelProfile("profile/id", "c".repeat(64)); assert.deepEqual(calls.map(call => call.url), ["/api/v1/model-profiles/profile%2Fid/assign-test", "/api/v1/model-profiles/profile%2Fid/unassign-test", "/api/v1/model-profiles/profile%2Fid/promote"]); assert.deepEqual(calls.map(call => JSON.parse(call.options.body)), ["a", "b", "c"].map(char => ({ expected_profile_fingerprint: char.repeat(64) }))); assert.throws(() => modelAssignmentPayload("invalid")); }
  finally { globalThis.fetch = original; }
});
