import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  createPromptDraft, createPromptSettingsController, promptActivationPayload,
  promptCharacterCount, promptDraftPayload, promptErrorMessage, promptLineDiff,
  validatePromptDraft, visiblePromptText,
} from "./promptSettings.js";
import { api } from "./api.js";

const flush = async () => { for (let index = 0; index < 24; index += 1) await Promise.resolve(); };
const detail = (number, text = `합성 판정 지침 ${number}`, extra = {}) => ({
  id: `synthetic-version-${number}`, version_number: number, name: `합성 버전 ${number}`,
  policy_text: text, change_note: "합성 변경", parent_version_id: number > 1 ? "synthetic-version-1" : null,
  content_hash: "synthetic-hash", created_by: "synthetic-admin", created_at: "2026-09-07T00:00:00Z",
  quality_status: "not_evaluated", ...extra,
});
const catalog = (versions = [detail(1), detail(2)], active = 1, revision = 1) => ({
  items: versions.map(({ policy_text, ...metadata }) => metadata),
  active_version_id: `synthetic-version-${active}`, revision,
  fixed_instructions: "합성 고정 안전 규칙", fixed_rules_version: "synthetic-fixed-v1", max_policy_chars: 4000,
});
const validDraft = () => ({ ...createPromptDraft(detail(1)), name: "새 버전", change_note: "변경 목적", policy_text: "판정 지침" });

function harness() {
  const requests = []; const updates = []; const timers = new Map();
  let timerId = 0;
  const mockApi = Object.fromEntries(["promptPolicies", "promptPolicy", "createPromptPolicy", "activatePromptPolicy"].map((name) => [name, (...args) => new Promise((resolve, reject) => requests.push({ name, args, resolve, reject, settled: false }))]));
  const controller = createPromptSettingsController({
    api: mockApi, onChange: (state) => updates.push(state),
    setTimer(fn) { const id = ++timerId; timers.set(id, fn); return id; }, clearTimer(id) { timers.delete(id); },
  });
  return {
    controller, requests, timers, updates,
    get state() { return updates.at(-1); },
    calls(name) { return requests.filter((request) => request.name === name); },
    next(name) { return requests.find((request) => request.name === name && !request.settled); },
    async answer(name, value) { const request = this.next(name); assert.ok(request, name); request.settled = true; request.resolve(value); await flush(); },
    async fail(name, status) { const request = this.next(name); assert.ok(request, name); request.settled = true; const error = new Error("synthetic-private-error"); error.status = status; request.reject(error); await flush(); },
    async load() { const promise = controller.refresh(); await this.answer("promptPolicies", catalog()); await this.answer("promptPolicy", detail(1)); assert.equal(await promise, true); },
    async chooseSecond() { const promise = controller.select("synthetic-version-2"); await this.answer("promptPolicy", detail(2)); await promise; },
  };
}

test("cloning creates an editable draft without changing a saved version", () => {
  const source = detail(1); const before = structuredClone(source); const draft = createPromptDraft(source);
  draft.policy_text += "\n수정";
  assert.deepEqual(source, before);
  assert.equal(draft.parent_version_id, source.id);
  assert.equal(draft.source_policy_text, source.policy_text);
  assert.equal(draft.change_note, "");
  assert.deepEqual(createPromptDraft().parent_version_id, null);
});

test("validation uses Python-compatible character count including emoji", () => {
  assert.equal(promptCharacterCount("한😀글"), 3);
  const draft = validDraft(); draft.policy_text = "😀".repeat(4000);
  assert.deepEqual(validatePromptDraft(draft), []);
  draft.policy_text += "가";
  assert.equal(validatePromptDraft(draft)[0].field, "policy_text");
});

test("validation requires content and metadata within their independent limits", () => {
  for (const field of ["name", "change_note", "policy_text"]) {
    assert.ok(validatePromptDraft({ ...validDraft(), [field]: " \t " }).some((error) => error.field === field));
  }
  for (const [field, limit] of [["name", 120], ["change_note", 1000], ["policy_text", 4000]]) {
    assert.ok(validatePromptDraft({ ...validDraft(), [field]: "가".repeat(limit + 1) }).some((error) => error.field === field));
  }
});

test("metadata is single-line and forbidden Unicode controls are not submitted", () => {
  for (const [field, value] of [["name", "이름\n다음"], ["change_note", "설명\t탭"], ["policy_text", "지침\u202e숨김"], ["policy_text", "지침\r\nCR"], ["policy_text", "\ud800"]]) {
    assert.ok(validatePromptDraft({ ...validDraft(), [field]: value }).some((error) => error.field === field));
  }
  assert.deepEqual(validatePromptDraft({ ...validDraft(), policy_text: "지침\n\t다음 줄" }), []);
});

test("create payload includes no UI state, activation, model, or fixed-instruction field", () => {
  const draft = { ...validDraft(), name: " 새 버전 ", change_note: " 변경 ", policy_text: "  지침\n다음  ", active: true, fixed_instructions: "not editable" };
  assert.deepEqual(promptDraftPayload(draft), { name: "새 버전", change_note: "변경", policy_text: "  지침\n다음  ", parent_version_id: "synthetic-version-1" });
  assert.throws(() => promptDraftPayload({ ...draft, change_note: "" }), /invalid_prompt_draft/);
});

test("activation requires an explicit acknowledgement and positive integer revision", () => {
  assert.deepEqual(promptActivationPayload({ id: "synthetic", expected_revision: 4 }, true), { expected_revision: 4, acknowledge_unverified: true });
  for (const acknowledged of [false, undefined, "true", 1]) assert.throws(() => promptActivationPayload({ id: "synthetic", expected_revision: 4 }, acknowledged));
  for (const revision of [0, -1, 1.2, "1", undefined]) assert.throws(() => promptActivationPayload({ id: "synthetic", expected_revision: revision }, true));
});

test("exact line diff shows insertions/removals and preserves untrusted text", () => {
  const before = "같음\n<script>synthetic</script>\n끝";
  const after = "같음\n<img src=https://synthetic.invalid>\n끝";
  const diff = promptLineDiff(before, after);
  assert.equal(diff.mode, "lines");
  assert.deepEqual(diff.rows.map((row) => row.kind), ["same", "removed", "added", "same"]);
  assert.equal(diff.rows.filter((row) => row.kind !== "added").map((row) => row.text).join("\n"), before);
  assert.equal(diff.rows.filter((row) => row.kind !== "removed").map((row) => row.text).join("\n"), after);
  assert.equal(promptLineDiff(before, before).changed, false);
});

test("diff does not normalize whitespace, case, CRLF or trailing empty lines", () => {
  for (const [before, after] of [["a", "A"], ["a", " a"], ["a\r\n", "a\n"], ["a\n", "a"]]) assert.equal(promptLineDiff(before, after).changed, true);
  assert.equal(visiblePromptText("a\r\n\u0000<script>"), "a\\r\n\\u0000<script>");
});

test("diff resource limits fall back without pretending to compare oversized content", () => {
  const before = "\n".repeat(201); const after = before + "합성";
  const diff = promptLineDiff(before, after);
  assert.equal(diff.mode, "whole"); assert.equal(diff.rows.length, 2);
  assert.equal(diff.rows[0].text, before); assert.equal(diff.rows[1].text, after);
  assert.deepEqual(promptLineDiff("", "a".repeat(4001)), { changed: true, mode: "limit", rows: [] });
});

test("error messages never echo unknown server text or submitted prompt contents", () => {
  const error = new Error("synthetic-sensitive-prompt");
  assert.doesNotMatch(promptErrorMessage(error), /synthetic-sensitive/);
  assert.match(promptErrorMessage(error, "save"), /처리 결과.*새로고침/);
  assert.match(promptErrorMessage({ status: 409 }, "activate"), /다시 선택하고 동의/);
  assert.match(promptErrorMessage({ status: 422, message: "prompt_policy_context_budget_too_small" }, "activate"), /현재 운영 모델의 입력 공간이 부족/);
  assert.doesNotMatch(promptErrorMessage({ status: 422, message: "unrelated" }, "activate"), /입력 공간/);
});

test("initial load and immutable detail reads never write or invoke a model", async () => {
  const h = harness(); await h.load();
  assert.deepEqual(h.requests.map((request) => request.name), ["promptPolicies", "promptPolicy"]);
  assert.equal(h.state.selected.id, "synthetic-version-1");
  assert.equal(h.state.catalog.revision, 1);
  assert.equal(h.timers.size, 0);
  h.controller.dispose();
});

test("slow detail/comparison results cannot overwrite a newer selection", async () => {
  const h = harness(); await h.load();
  const pendingSelection = h.controller.select("synthetic-version-2");
  const oldRequest = h.next("promptPolicy");
  await h.controller.select("synthetic-version-1");
  oldRequest.settled = true; oldRequest.resolve(detail(2)); await pendingSelection;
  assert.equal(h.state.selected.id, "synthetic-version-1");
  const pendingComparison = h.controller.compare("synthetic-version-2");
  await h.controller.compare("synthetic-version-1");
  await h.answer("promptPolicy", detail(2)); await pendingComparison;
  assert.equal(h.state.comparison.id, "synthetic-version-1");
  h.controller.dispose();
});

test("saving creates a new immutable version but does not activate it", async () => {
  const h = harness(); await h.load(); const draft = validDraft(); const before = structuredClone(draft);
  const saving = h.controller.save(draft);
  assert.equal(h.state.busy, "save");
  await h.answer("createPromptPolicy", detail(3, draft.policy_text));
  await h.answer("promptPolicies", catalog([detail(1), detail(2), detail(3)]));
  assert.equal(await saving, true);
  assert.equal(h.state.selected.id, "synthetic-version-3");
  assert.equal(h.state.catalog.active_version_id, "synthetic-version-1");
  assert.equal(h.calls("activatePromptPolicy").length, 0);
  assert.deepEqual(draft, before);
  assert.match(h.state.notice, /운영 적용은 별도/);
  h.controller.dispose();
});

test("empty draft cannot write and failed validation retains the selected version", async () => {
  const h = harness(); await h.load();
  assert.equal(await h.controller.save(createPromptDraft()), false);
  assert.equal(h.calls("createPromptPolicy").length, 0);
  assert.equal(h.state.selected.id, "synthetic-version-1");
  h.controller.dispose();
});

test("activation freezes target/revision and requires acknowledgement for every request", async () => {
  const h = harness(); await h.load(); await h.chooseSecond();
  h.controller.beginActivation();
  assert.equal(await h.controller.activate(), false);
  assert.equal(h.calls("activatePromptPolicy").length, 0);
  h.controller.acknowledge(true);
  const applying = h.controller.activate();
  assert.equal(await h.controller.activate(), false);
  const request = h.next("activatePromptPolicy");
  assert.deepEqual(request.args.slice(0, 2), ["synthetic-version-2", { expected_revision: 1, acknowledge_unverified: true }]);
  await h.answer("activatePromptPolicy", { active_version_id: "synthetic-version-2", revision: 2 });
  await h.answer("promptPolicies", catalog([detail(1), detail(2)], 2, 2));
  assert.equal(await applying, true);
  assert.equal(h.state.confirmation, null); assert.equal(h.state.acknowledged, false);
  assert.equal(h.state.catalog.active_version_id, "synthetic-version-2");
  assert.equal(h.calls("activatePromptPolicy").length, 1);
  h.controller.dispose();
});

test("changing selection or cancelling invalidates the activation acknowledgement", async () => {
  const h = harness(); await h.load(); await h.chooseSecond();
  h.controller.beginActivation(); h.controller.acknowledge(true); h.controller.cancelActivation();
  assert.equal(await h.controller.activate(), false);
  h.controller.beginActivation(); h.controller.acknowledge(true); await h.controller.select("synthetic-version-1");
  assert.equal(h.state.confirmation, null); assert.equal(h.state.acknowledged, false);
  assert.equal(await h.controller.activate(), false);
  assert.equal(h.calls("activatePromptPolicy").length, 0);
  h.controller.dispose();
});

test("activation conflict re-reads only and never retries or reuses consent", async () => {
  const h = harness(); await h.load(); await h.chooseSecond(); h.controller.beginActivation(); h.controller.acknowledge(true);
  const applying = h.controller.activate();
  await h.fail("activatePromptPolicy", 409);
  assert.equal(h.state.confirmation, null);
  await h.answer("promptPolicies", catalog([detail(1), detail(2)], 1, 3));
  assert.equal(await applying, false);
  assert.equal(h.calls("activatePromptPolicy").length, 1);
  assert.equal(h.state.catalog.revision, 3);
  assert.equal(h.state.acknowledged, false);
  assert.match(h.state.error, /다른 요청/);
  h.controller.beginActivation();
  assert.equal(h.state.confirmation.expected_revision, 3);
  assert.equal(await h.controller.activate(), false);
  h.controller.dispose();
});

test("uncertain write outcome blocks further writes until a manual refresh", async () => {
  const h = harness(); await h.load();
  const saving = h.controller.save(validDraft()); await h.fail("createPromptPolicy", 503);
  assert.equal(await saving, false); assert.equal(h.state.needsRefresh, true);
  assert.equal(await h.controller.save(validDraft()), false);
  assert.equal(h.calls("createPromptPolicy").length, 1);
  const refreshing = h.controller.refresh(); await h.answer("promptPolicies", catalog()); await refreshing;
  assert.equal(h.state.needsRefresh, false);
  h.controller.dispose();
});

test("a known activation validation rejection keeps the prior active version without retry", async () => {
  const h = harness(); await h.load(); await h.chooseSecond(); h.controller.beginActivation(); h.controller.acknowledge(true);
  const applying = h.controller.activate(); await h.fail("activatePromptPolicy", 422);
  assert.equal(await applying, false);
  assert.equal(h.state.catalog.active_version_id, "synthetic-version-1");
  assert.equal(h.state.needsRefresh, false);
  assert.equal(h.state.confirmation, null);
  assert.equal(h.state.acknowledged, false);
  assert.equal(h.calls("activatePromptPolicy").length, 1);
  h.controller.dispose();
});

test("permission/read failure retains existing data but blocks stale activation", async () => {
  const h = harness(); await h.load(); await h.chooseSecond();
  const refreshing = h.controller.refresh(); await h.fail("promptPolicies", 403); await refreshing;
  assert.equal(h.state.selected.id, "synthetic-version-2");
  assert.equal(h.state.needsRefresh, true);
  h.controller.beginActivation(); assert.equal(h.state.confirmation, null);
  assert.match(h.state.error, /관리자/);
  h.controller.dispose();
});

test("timeouts end a request, do not retry writes, and dispose cancels pending reads", async () => {
  const h = harness(); await h.load();
  const saving = h.controller.save(validDraft());
  for (const timer of [...h.timers.values()]) timer();
  assert.equal(await saving, false); assert.equal(h.state.needsRefresh, true);
  assert.equal(h.calls("createPromptPolicy").length, 1);
  assert.equal(h.timers.size, 0);
  const count = h.updates.length; h.controller.dispose();
  h.next("createPromptPolicy").resolve(detail(3)); await flush();
  assert.equal(h.updates.length, count);
  const another = harness(); const loading = another.controller.refresh(); const pending = another.next("promptPolicies");
  another.controller.dispose(); await loading;
  assert.equal(pending.args[0].signal.aborted, true); assert.equal(another.timers.size, 0);
});

test("UI source renders plain text and exposes no mutation of saved or fixed instructions", async () => {
  const source = await readFile(new URL("./PromptSettings.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|ReactMarkdown|runModelProfileTest|createAnalysis|localStorage|sessionStorage/);
  for (const phrase of ["시스템 규칙 · 읽기 전용", "모델 품질", "정답", "공통 적용 확인", "새 버전 작성", "이후 새로 접수되는 분석", "컨텍스트가 부족", "닫기 · 초안 유지"]) assert.ok(source.includes(phrase), phrase);
  assert.match(source, /<Dialog open=\{view === "technical"/); assert.match(source, /긴 지침은 로그 입력 공간을 줄입니다/);
  assert.match(source, /컨텍스트가 부족하면 모델을 호출할 수 없습니다/); assert.doesNotMatch(source, /<HelpTooltip/);
  assert.doesNotMatch(source, /<HelpTooltip label="(?:프롬프트 버전 관리|시스템 규칙)"/);
  assert.match(source, /코드에서 관리하는 읽기 전용 안전·출력 규칙/);
  assert.match(source, /저장만으로 공통 적용 버전이 바뀌거나 LLM이 호출되지/);
});

test("prompt API methods use only the four agreed endpoints and explicit POST bodies", async () => {
  const oldFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify({ synthetic: true }), { status: 200, headers: { "Content-Type": "application/json" } }); };
  try {
    await api.promptPolicies(); await api.promptPolicy("synthetic/id");
    await api.createPromptPolicy(promptDraftPayload(validDraft()));
    await api.activatePromptPolicy("synthetic/id", { expected_revision: 1, acknowledge_unverified: true });
  } finally { globalThis.fetch = oldFetch; }
  assert.deepEqual(calls.map((call) => call.url), ["/api/v1/admin/prompt-policies", "/api/v1/admin/prompt-policies/synthetic%2Fid", "/api/v1/admin/prompt-policies", "/api/v1/admin/prompt-policies/synthetic%2Fid/activate"]);
  assert.deepEqual(calls.map((call) => call.options.method || "GET"), ["GET", "GET", "POST", "POST"]);
  assert.ok(calls.every((call) => call.options.credentials === "include"));
});
