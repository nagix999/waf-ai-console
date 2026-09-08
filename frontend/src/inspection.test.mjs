import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { loginError, recordText, stepLabel, textMatches } from "./inspection.js";

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
