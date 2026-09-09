import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createLogoutController, loginError, logoutError, recordText, stepLabel, textMatches } from "./inspection.js";

function component(file, name = "default") {
  const bundle = buildSync({ entryPoints: [fileURLToPath(new URL(file, import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;
  const module = { exports: {} }; runInNewContext(bundle, { module, exports: module.exports, require: createRequire(import.meta.url), process, URL, URLSearchParams, setTimeout, clearTimeout });
  return module.exports[name];
}
const render = (view, props) => renderToStaticMarkup(createElement(view, props));

test("raw text search is literal, bounded and never decodes or normalizes source", () => {
  const raw = "GET /%3Cscript%3E HTTP/1.1\r\nCookie: a=+%20\r\n\r\n<script>raw</script>";
  assert.equal(recordText(raw), raw);
  assert.deepEqual(textMatches(raw, "%3Cscript%3E").positions, [5]);
  assert.equal(textMatches(raw, "cookie").positions.length, 0);
  assert.equal(textMatches("a".repeat(1000000), "a").positions.length, 200);
  assert.equal(textMatches("a".repeat(201), "a").limited, true);
  assert.equal(textMatches("a".repeat(200), "a").limited, false);
  assert.deepEqual(textMatches(raw, ""), { positions: [], limited: false });
  assert.equal(recordText(0), "0"); assert.equal(recordText(false), "false"); assert.equal(recordText(null), "");
});

test("raw inspector renders untrusted text, offers search and never creates active links", () => {
  const TextInspector = component("./TextInspector.jsx");
  const raw = "<script>alert(1)</script>\r\nhttps://example.invalid/a Cookie: sensitive";
  const html = render(TextInspector, { value: raw, label: "요청" });
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /Cookie: sensitive/); assert.match(html, /요청에서 찾기/);
  assert.doesNotMatch(html, /<script|<iframe|<a\b/);
  assert.match(html, /자동 줄바꿈/); assert.match(html, /전체 복사/);
  assert.doesNotMatch(html, /metric-help-trigger/);
  assert.match(render(TextInspector, { value: null }), /기록된 내용이 없습니다/);
});

test("agent history defaults to stage output and hides identifiers and other stage data", () => {
  const AgentHistory = component("./AgentHistory.jsx");
  const runs = [{ id: "hidden-run-canary", status: "failed", fingerprint: "hidden-fingerprint-canary", started_at: "2026-09-08T00:00:00Z", duration_ms: 1400, steps: [{ id: "step-canary", step_type: "llm_primary", name: "Primary LLM 판정", status: "failed", duration_ms: 0, input: "hidden-input-canary", output: "stored-output-canary", metadata: { private: "hidden-metadata-canary" } }] }];
  const html = render(AgentHistory, { runs });
  assert.match(html, /위협 분석/); assert.match(html, /stored-output-canary/); assert.match(html, /기술 식별정보/);
  assert.doesNotMatch(html, /hidden-fingerprint-canary|hidden-input-canary|hidden-metadata-canary/);
  assert.doesNotMatch(html, />hidden-run-canary</);
  assert.doesNotMatch(html, /metric-help-trigger/);
  assert.equal(stepLabel("finalize"), "최종 판정 정리"); assert.equal(stepLabel("custom-step"), "custom-step");
});

test("login starts empty, uses placeholders and does not disclose upstream errors", () => {
  const Login = component("./App.jsx", "Login");
  const html = render(Login, { onLogin() {} });
  assert.match(html, /placeholder="관리자 아이디"/); assert.match(html, /value=""/);
  assert.doesNotMatch(html, /value="admin"/); assert.match(html, /비밀번호 표시/);
  assert.doesNotMatch(html, /metric-help-trigger/);
  assert.match(loginError({ status: 429 }), /잠시 후/);
  assert.match(loginError({ message: "invalid_credentials" }), /아이디와 비밀번호/);
  assert.ok(!loginError({ message: "SECRET upstream body" }).includes("SECRET"));
});

for (const error of [
  { message: "csrf_origin_required" },
  { message: "csrf_origin_invalid" },
  { message: "untrusted_host" },
  { status: 403, message: "SECRET proxy response" },
  { status: 421, message: "SECRET unexpected host" },
]) {
  test(`session access rejection gives safe address guidance: ${error.message}`, () => {
    assert.match(loginError(error), /서비스 주소를 확인/);
    assert.doesNotMatch(loginError(error), /서버에 연결|SECRET|csrf_|untrusted_host/);
    assert.match(logoutError(error), /로그아웃하지 못했습니다.*서비스 주소를 확인/);
    assert.doesNotMatch(logoutError(error), /SECRET|csrf_|untrusted_host/);
  });
}

test("logout errors never disclose raw server messages", () => {
  for (const error of [undefined, { status: 401 }, { status: 429 }, new Error("SECRET network response")]) {
    assert.match(logoutError(error), /로그아웃하지 못했습니다.*다시 시도/);
    assert.doesNotMatch(logoutError(error), /SECRET/);
  }
  assert.match(loginError({ status: 401 }), /아이디와 비밀번호/);
  assert.match(loginError({ status: 429 }), /로그인 시도가 많습니다/);
});

test("logout failure preserves the session and screen and allows a successful retry", async () => {
  const changes = [];
  let fail = true;
  let attempts = 0;
  let principal = "existing-admin";
  let screen = "settings-draft";
  const controller = createLogoutController({
    async logout() { attempts += 1; if (fail) throw Object.assign(new Error("csrf_origin_invalid"), { status: 403 }); },
    onSuccess() { principal = null; screen = "login"; },
    onChange(state) { changes.push(state); },
  });
  assert.equal(await controller.submit(), false);
  assert.equal(principal, "existing-admin");
  assert.equal(screen, "settings-draft");
  assert.deepEqual(changes[0], { busy: true, error: "" });
  assert.equal(changes.at(-1).busy, false);
  assert.match(changes.at(-1).error, /로그아웃하지 못했습니다/);
  fail = false;
  assert.equal(await controller.submit(), true);
  assert.equal(attempts, 2);
  assert.equal(principal, null);
  assert.equal(screen, "login");
  assert.deepEqual(changes.slice(-2), [{ busy: true, error: "" }, { busy: false, error: "" }]);
});

test("logout waits for the server and ignores repeated clicks while pending", async () => {
  let resolve;
  let calls = 0;
  let completed = 0;
  let state;
  const controller = createLogoutController({
    logout() { calls += 1; return new Promise(done => { resolve = done; }); },
    onSuccess() { completed += 1; },
    onChange(value) { state = value; },
  });
  const first = controller.submit();
  assert.equal(state.busy, true);
  assert.equal(completed, 0);
  assert.equal(await controller.submit(), false);
  assert.equal(calls, 1);
  resolve();
  assert.equal(await first, true);
  assert.equal(completed, 1);
  assert.deepEqual(state, { busy: false, error: "" });
});

test("a delayed logout rejection is handled without switching to the login screen", async () => {
  let reject;
  let completed = false;
  let state;
  const controller = createLogoutController({
    logout: () => new Promise((_resolve, fail) => { reject = fail; }),
    onSuccess() { completed = true; },
    onChange(value) { state = value; },
  });
  const pending = controller.submit();
  reject(new Error("SECRET network detail"));
  assert.equal(await pending, false);
  assert.equal(completed, false);
  assert.equal(state.busy, false);
  assert.match(state.error, /로그아웃하지 못했습니다/);
  assert.doesNotMatch(state.error, /SECRET/);
});
