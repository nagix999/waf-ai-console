import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "./api.js";
import { analysisQuery } from "./analysisView.js";
import { createFullValidationController, datasetEvaluationStatus, datasetListState, expectedVerdictUploadError, modelTestPayload, uploadLabelNotice } from "./modelValidation.js";

const profile = (extra = {}) => ({ id: "candidate-profile", name: "synthetic-candidate", provider: "vllm", model_name: "synthetic-model", profile_fingerprint: "a".repeat(64), ...extra });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const openNamed = (controller, value = profile()) => { controller.open(value); controller.setName("합성 검증 1차"); };

test("test request options default to no dataset and reject dataset evaluation in quick mode", () => {
  assert.deepEqual(modelTestPayload("full"), { mode: "full", include_dataset: false }); assert.deepEqual(modelTestPayload("full", true), { mode: "full", include_dataset: true }); assert.deepEqual(modelTestPayload("quick"), { mode: "quick", include_dataset: false });
  for (const args of [["quick", true], ["other", false], ["full", "true"], ["full", 1]]) assert.throws(() => modelTestPayload(...args), /invalid_model_test_options/);
  assert.deepEqual(modelTestPayload("full", true, "a".repeat(64)), { mode: "full", include_dataset: true, expected_profile_fingerprint: "a".repeat(64) });
  for (const value of [null, "", "a".repeat(63), "G".repeat(64)]) assert.throws(() => modelTestPayload("full", true, value), /invalid_model_profile_fingerprint/);
});

test("opening and cancelling the full-validation dialog sends no request; either explicit option sends one request to the selected candidate", async () => {
  const calls = [], submitted = []; const controller = createFullValidationController({ api: { runModelProfileTest: async (...args) => { calls.push(args); return { id: "synthetic-run" }; } }, onChange() {}, onSubmitted: (...args) => submitted.push(args) });
  controller.open(profile({ api_key: "NEVER_COPY_PROFILE_KEY" })); assert.equal(Object.hasOwn(controller.getState().profile, "api_key"), false); assert.deepEqual(calls, []); controller.close(); assert.equal(controller.getState().profile, null); assert.deepEqual(calls, []);
  for (const include of [false, true]) { openNamed(controller); assert.equal(await controller.submit(include), true); assert.equal(controller.getState().profile, null); }
  assert.deepEqual(calls.map(args => args.slice(0, 4)), [["candidate-profile", "full", false, "a".repeat(64)], ["candidate-profile", "full", true, "a".repeat(64)]]); assert.ok(calls.every(args => args[4].name === "합성 검증 1차" && args[4].idempotency_key.length >= 8)); assert.notEqual(calls[0][4].idempotency_key, calls[1][4].idempotency_key); assert.equal(submitted.length, 2); assert.equal(submitted[1][1].id, "candidate-profile"); controller.dispose();
});

test("a missing profile fingerprint prevents submission and requests a fresh explicit confirmation", async () => {
  let writes = 0, refreshes = 0; const controller = createFullValidationController({ api: { runModelProfileTest: async () => { writes += 1; } }, onChange() {}, onRequireRefresh: () => refreshes++ });
  controller.open(profile({ profile_fingerprint: undefined })); assert.equal(controller.getState().needsReconfirm, true); assert.equal(refreshes, 1); assert.equal(await controller.submit(true), false); assert.equal(writes, 0); assert.match(controller.getState().error, /다시 열어 확인/);
  controller.close(); openNamed(controller); assert.equal(controller.getState().needsReconfirm, false); assert.equal(await controller.submit(false), true); assert.equal(writes, 1); controller.dispose();
});

test("profile changes reject the old confirmation and cannot silently change an internal call into an OpenAI call", async () => {
  let refreshes = 0; const calls = []; let changed = true; const controller = createFullValidationController({ api: { runModelProfileTest: async (...args) => { calls.push(args); if (changed) throw Object.assign(new Error("model_profile_changed_reconfirm"), { status: 409 }); return {}; } }, onChange() {}, onRequireRefresh: () => refreshes++ });
  openNamed(controller); assert.equal(await controller.submit(true), false); assert.equal(refreshes, 1); assert.equal(controller.getState().needsReconfirm, true); assert.equal(controller.getState().profile.provider, "vllm"); assert.equal(await controller.submit(true), false); assert.equal(calls.length, 1); assert.match(controller.getState().error, /설정이 변경되어 실행하지/);
  changed = false; controller.close(); openNamed(controller, profile({ provider: "openai", profile_fingerprint: "b".repeat(64) })); assert.equal(await controller.submit(true), true); assert.deepEqual(calls[1].slice(0, 4), ["candidate-profile", "full", true, "b".repeat(64)]); controller.dispose();
});

test("full-validation submission is deduplicated and the selected profile cannot change while registering", async () => {
  const response = deferred(); let count = 0; const controller = createFullValidationController({ api: { runModelProfileTest: async () => { count += 1; return response.promise; } }, onChange() {} });
  openNamed(controller); const pending = controller.submit(true); assert.equal(await controller.submit(false), false); controller.close(); controller.open(profile({ id: "wrong-profile" })); controller.setName("변경 시도"); assert.equal(controller.getState().name, "합성 검증 1차"); assert.equal(controller.getState().profile.id, "candidate-profile"); assert.equal(count, 1); response.resolve({ id: "run" }); assert.equal(await pending, true); controller.dispose();
});

test("ambiguous model-test responses never retry or silently close the confirmation", async () => {
  let calls = 0; const controller = createFullValidationController({ api: { runModelProfileTest: async () => { calls += 1; throw new Error("SYNTHETIC_NETWORK_FAILURE"); } }, onChange() {} });
  openNamed(controller); const key = controller.getState().idempotencyKey; assert.equal(await controller.submit(true), false); assert.equal(calls, 1); assert.equal(controller.getState().idempotencyKey, key); assert.equal(controller.getState().profile.id, "candidate-profile"); assert.match(controller.getState().error, /자동으로 재요청하지/); assert.doesNotMatch(controller.getState().error, /SYNTHETIC_NETWORK_FAILURE/); controller.close(); controller.dispose();
});

test("a model-test response after unmount cannot publish or navigate", async () => {
  const response = deferred(); let submitted = 0; const states = []; const controller = createFullValidationController({ api: { runModelProfileTest: async () => response.promise }, onChange: state => states.push(state), onSubmitted: () => submitted++ });
  openNamed(controller); const pending = controller.submit(true); controller.dispose(); const count = states.length; response.resolve({}); await pending; assert.equal(states.length, count); assert.equal(submitted, 0);
});

test("dataset navigation filters exact internal source and test purpose, preserving independent view state", () => {
  const source = "waf-internal-model-test-01234567-89ab-cdef"; const state = datasetListState(source);
  assert.equal(state.applied.analysis_purpose, "test"); assert.equal(state.applied.source_system, source); assert.equal(state.draft.source_system, source); assert.equal(state.offset, 0); assert.equal(state.advanced, true); assert.deepEqual(analysisQuery(state.applied, 25, 0), { analysis_purpose: "test", source_system: source, limit: 25, offset: 0 });
  for (const invalid of [null, "admin-ui", "external", "waf-internal-model-test-x?q=secret", "waf-internal-model-test-"]) assert.equal(datasetListState(invalid), null);
  state.draft.source_system = "changed"; assert.equal(state.applied.source_system, source);
});

test("upload automatic label counts and error messages never imply completed analysis or independent accuracy", () => {
  assert.equal(uploadLabelNotice(null), ""); assert.equal(uploadLabelNotice({ accepted: 1 }), ""); assert.match(uploadLabelNotice({ label_attached: 2, label_unchanged: 1 }), /합성 기대값 연결 2건 · 기존 답안과 동일 1건/); assert.match(uploadLabelNotice({ label_attached: 0, label_unchanged: 0 }), /분석이 완료되면/);
  assert.match(expectedVerdictUploadError({ message: "expected_verdict_conflict" }), /덮어쓰지 않고/); assert.match(expectedVerdictUploadError({ code: "invalid_expected_verdict" }), /true_positive/); assert.equal(expectedVerdictUploadError({ message: "toString" }), "");
  assert.equal(datasetEvaluationStatus("completed"), "판정 평가 처리 완료"); assert.equal(datasetEvaluationStatus("toString"), "판정 평가 상태 미확인");
});

const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./FullValidationDialog.jsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
const module = { exports: {} }; runInNewContext(bundled, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL });
const { FullValidationContent, DatasetEvaluation } = module.exports;
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));

test("full-validation UI exposes exactly three choices with selected-profile and OpenAI cost notices", () => {
  const html = render(FullValidationContent, { profile: profile({ provider: "openai", name: '<script>SYNTHETIC</script>' }), busy: false, error: "", onCancel() {}, onSubmit() {} });
  assert.equal((html.match(/<button/g) || []).length, 3); assert.match(html, />취소<\/button>/); assert.match(html, />연결·기능 검증만<\/button>/); assert.match(html, />150건 판정 평가도 실행<\/button>/);
  assert.match(html, /이 화면에서 선택한 프로필/); assert.doesNotMatch(html, /현재 Production이 아니라/); assert.match(html, /자동 승격하지/); assert.match(html, /150회를 넘을 수/); assert.match(html, /OpenAI 외부 API로 전송/); assert.match(html, /API 비용/); assert.match(html, /독립적인 운영 정확도가 아닙니다/); assert.doesNotMatch(html, /<script>/); assert.match(html, /&lt;script&gt;SYNTHETIC/);
  assert.doesNotMatch(render(FullValidationContent, { profile: profile(), busy: false }), /OpenAI를 선택했습니다/);
  const reconfirm = render(FullValidationContent, { profile: profile(), busy: false, needsReconfirm: true }); assert.equal((reconfirm.match(/disabled=""/g) || []).length, 2); assert.match(reconfirm, /class="secondary">취소<\/button>/);
});

test("dataset result UI separates completed, failed, held and expected-abstention outcomes and preserves missing metadata", () => {
  const summary = { total: 150, labeled: 150, evaluable: 147, matches: 140, outcomes: { false_negative: 2, false_positive: 1, abstained: 3, expected_abstention_match: 5, expected_abstention_mismatch: 1, failed: 3 }, source_groups: [{ source_kind: "synthetic_expected", ai_visible: false, matches: 140, evaluable: 147, binary_decided: 130, binary_evaluable: 140, false_negatives: 2, false_positives: 1, abstained: 3, expected_abstention_matches: 5, expected_abstention_mismatches: 1 }] };
  const testRun = { include_dataset: true, dataset_evaluation: { dataset_version: "waf-dummy-v1", source_system: "waf-internal-model-test-synthetic", status: "completed", total: 150, pending: 0, processing: 0, completed: 147, failed: 3, summary } };
  const html = render(DatasetEvaluation, { test: testRun, onViewDataset() {} }); assert.match(html, /처리 종료 150 \/ 150건/); assert.match(html, /분석 완료 <strong>147건/); assert.match(html, /실행 실패 <strong>3건/); assert.match(html, /150건 분석 결과 보기/); assert.match(html, /미탐 방향 2 · 과탐 방향 1 · 모델 보류 3건/); assert.match(html, /기대 보류 일치 5 · 불일치 1건/); assert.match(html, /일치율로 자동 승격하지/);
  assert.equal(render(DatasetEvaluation, { test: { include_dataset: false } }), ""); assert.match(render(DatasetEvaluation, { test: { include_dataset: true, dataset_evaluation: null } }), /아직 확인할 수 없습니다/);
  assert.match(render(DatasetEvaluation, { test: { ...testRun, dataset_evaluation: { ...testRun.dataset_evaluation, status: "skipped" } } }), /실행하지 않았습니다/);
});

test("model-test API transmits one explicit dataset option without changing Production or using analysis input APIs", { concurrency: false }, async () => {
  const original = globalThis.fetch; const calls = []; globalThis.fetch = async (url, options) => { calls.push([url, options]); return new Response(JSON.stringify({ id: "run" }), { status: 202 }); };
  try { await api.runModelProfileTest("candidate/id", "full", true, "a".repeat(64)); await api.runModelProfileTest("candidate/id", "full"); await api.runModelProfileTest("candidate/id", "quick"); assert.throws(() => api.runModelProfileTest("candidate/id", "quick", true)); assert.equal(calls.length, 3); assert.ok(calls.every(([url, options]) => url === "/api/v1/model-profiles/candidate%2Fid/tests" && options.method === "POST")); assert.deepEqual(calls.map(([, options]) => JSON.parse(options.body)), [{ mode: "full", include_dataset: true, expected_profile_fingerprint: "a".repeat(64) }, { mode: "full", include_dataset: false }, { mode: "quick", include_dataset: false }]); }
  finally { globalThis.fetch = original; }
});
