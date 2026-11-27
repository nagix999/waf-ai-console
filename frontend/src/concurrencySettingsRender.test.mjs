import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { buildSync } from "esbuild";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { concurrencyDraft } from "./concurrencySettings.js";

const require = createRequire(import.meta.url);
const bundled = buildSync({ entryPoints: [fileURLToPath(new URL("./ConcurrencySettings.jsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic",
  external: ["./Dialog.jsx"], loader: { ".css": "empty" }, logLevel: "silent" }).outputFiles[0].text;

function render({ production = 1, test = 1, maxCalls = 1, confirm = false } = {}) {
  const catalog = { state_token: "a".repeat(64), production, test,
    servers: [{ server_key: "10.0.0.10:8000", max_calls: maxCalls, profiles: [{ name: "Synthetic model" }] }] };
  const state = { catalog, draft: concurrencyDraft(catalog), loading: false, busy: false,
    needsRefresh: false, error: "", notice: "" };
  const module = { exports: {} };
  // Seed only the existing component's state. The real hook dispatcher and
  // renderer still render inputs; the shared portal is replaced for SSR.
  const loadedRequire = name => {
    if (name === "react") return { ...React, useState: initial => React.useState(
      typeof initial === "function" ? state : confirm) };
    if (name === "./Dialog.jsx") return ({ open, children }) =>
      open ? React.createElement("section", { "data-dialog": true }, children) : null;
    return require(name);
  };
  runInNewContext(bundled, { module, exports: module.exports, require: loadedRequire, process, URL });
  return renderToStaticMarkup(React.createElement(module.exports.default));
}

test("both analysis inputs allow 512 while the server call input remains capped at 64", () => {
  const html = render({ production: 512, test: 512, maxCalls: 64 });
  assert.equal((html.match(/type="number" min="1" max="512" step="1"/g) || []).length, 2);
  assert.equal((html.match(/type="number" min="1" max="64" step="1"/g) || []).length, 1);
  assert.equal((html.match(/value="512"/g) || []).length, 2);
  assert.match(html, /1~512건/);
  assert.match(html, /실제 LLM 호출 수는 서버별 제한/);
  assert.match(html, /Console 서버의 CPU와 메모리 부하/);
  assert.doesNotMatch(html, /max="32"|1~32건|설정을 다시 조회/);
});

test("rendering and confirmation retain the saved counts without automatically selecting 512", () => {
  const html = render({ production: 1, test: 7, confirm: true });
  assert.match(html, /max="512" step="1" value="1"/);
  assert.match(html, /max="512" step="1" value="7"/);
  assert.match(html, /동시 분석: 프로덕션 1건 · 테스트 7건/);
  assert.match(html, /서버별 호출 수는 LLM 서버 부하/);
  assert.match(html, /OpenAI 비용이 더 빠르게 발생/);
  assert.doesNotMatch(html, /value="512"/);
});
