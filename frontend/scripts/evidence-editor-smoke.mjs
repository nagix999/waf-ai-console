// Real components, isolated loopback Vite, fabricated API, no remote traffic.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readdirSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = mkdtempSync(join(tmpdir(), "waf-editor-ui-"));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
assert.ok(driver);
const { chromium } = await import(pathToFileURL(driver).href);
const harness = `
import React from 'react'; import { createRoot } from 'react-dom/client';
import SummaryPreview from '/src/SummaryPreview.jsx';
import { ConfusionMatrix } from '/src/EvaluationMetrics.jsx';
import { TestRunItemRows } from '/src/TestRuns.jsx';
export function mount(text) {
  document.getElementById('root').hidden = true;
  const host = document.createElement('main'); host.id = 'editor-smoke'; host.style.padding = '16px'; document.body.appendChild(host);
  window.matrixSelection = null;
  const el = React.createElement;
  createRoot(host).render(el('div', null, el('h1', null, '분석 결과'), el(SummaryPreview, { text }),
    el(TestRunItemRows, { items: [{ id: 'case', analysis_id: 'analysis', case_name: '긴 요약 확인', row_number: 1, status: 'completed', verdict: 'inconclusive', summary_ko: text }], onOpen() {} }),
    el(ConfusionMatrix, { matrix: { tp: 7, fn: 1, fp: 2, tn: 5, abstained_positive: 1, abstained_negative: 2, expected_hold_positive: 3, expected_hold_negative: 1, expected_hold_match: 6 }, onCell: key => { window.matrixSelection = key; } })));
}`;
const server = await createServer({ root, configFile: false, plugins: [react(), { name: "isolated-evidence-harness",
  resolveId(id) { if (id === "/__evidence_smoke.jsx") return "\0evidence-smoke.jsx"; },
  load(id) { if (id === "\0evidence-smoke.jsx") return harness; },
}], logLevel: "error", cacheDir: join(output, "cache"),
  server: { host: "127.0.0.1", port: 0, hmr: false } });
let browser;
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
  for (const [name, width] of [["desktop", 1360], ["mobile", 390]]) {
    const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: "block" });
    const violations = [], errors = [], writes = [];
    const profile = { id: "fixture-primary", name: "판정 모델", provider: "vllm", model_name: "fixture-model", status: "production", is_test: true, can_assign: true };
    const editor = { ...profile, id: "fixture-editor", name: "정리 모델", status: "verified", is_test: false };
    let catalog = { state_token: "a".repeat(64), revision: 0, profiles: [profile, editor], assignments: Object.fromEntries(["production", "test"].map(purpose => [purpose, { primary_profile_id: profile.id, verifier_profile_id: null, evidence_editor_enabled: false, evidence_editor_profile_id: null }])) };
    await context.routeWebSocket("**/*", socket => socket.close());
    await context.route("**/*", async route => {
      const request = route.request(), url = new URL(request.url());
      const json = body => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
      if (url.origin !== origin) { violations.push("remote"); return route.abort(); }
      if (!url.pathname.startsWith("/api/")) return route.continue();
      if (url.pathname === "/api/v1/admin/agent-settings" && request.method() === "PUT") {
        const value = request.postDataJSON(); writes.push(value);
        catalog = { ...catalog, state_token: "b".repeat(64), assignments: { production: value.production, test: value.test } };
        return json(catalog);
      }
      if (request.method() !== "GET") { violations.push("unexpected write"); return route.abort(); }
      if (url.pathname === "/api/v1/auth/me") return json({ kind: "admin_session", username: "fixture", scopes: ["admin"] });
      if (url.pathname === "/api/v1/dashboard/summary") return json({ runtime: { agent_mode: "moduagent", production_profile: profile } });
      if (url.pathname === "/api/v1/model-profiles") return json(catalog.profiles);
      if (url.pathname === "/api/v1/admin/agent-settings") return json(catalog);
      violations.push(url.pathname); return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    page.on("pageerror", error => errors.push(error.message));
    try {
      await page.goto(origin + "/#settings/agents");
      const production = page.getByRole("group", { name: "프로덕션", exact: true });
      assert.ok(await production.getByLabel("근거 정리 모델").isDisabled());
      await production.getByLabel("근거 정리 사용", { exact: true }).check();
      await production.getByLabel("근거 정리 모델").selectOption(editor.id);
      await page.getByRole("button", { name: "배정 저장", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "모델 배정 확인", exact: true });
      await dialog.getByRole("cell", { name: "정리 모델", exact: true }).waitFor();
      await dialog.getByRole("button", { name: "적용", exact: true }).click();
      await dialog.waitFor({ state: "hidden" });
      assert.equal(writes.length, 1); assert.equal(writes[0].production.evidence_editor_profile_id, editor.id);
      assert.equal(writes[0].test.evidence_editor_enabled, false);
      await page.screenshot({ path: join(output, name + "-settings.png"), fullPage: true });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));

      // Mount the same real list/matrix components with a long hostile-looking
      // summary. No parsing as HTML and no request to an analysis endpoint.
      const text = "검색 요청의 입력값을 확인했습니다. ".repeat(100) + "<img src=https://never.invalid/x onerror=alert(1)> 마지막 문장";
      await page.evaluate(async text => {
        const { mount } = await import("/__evidence_smoke.jsx"); mount(text);
      }, text);
      const summary = page.getByRole("button", { name: "분석 요약 전체 보기", exact: true }).first();
      await summary.waitFor(); assert.match(await summary.innerText(), /\.\.\.$/);
      assert.ok(Array.from(await summary.innerText()).length <= 93);
      await summary.hover();
      let tooltip = page.getByRole("tooltip"); await tooltip.waitFor(); assert.equal(await tooltip.innerText(), text);
      assert.equal(await page.locator("#editor-smoke img").count(), 0);
      const box = await tooltip.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= width + 1 && box.y >= 0 && box.y + box.height <= 951);
      await tooltip.hover(); assert.ok(await tooltip.isVisible());
      await page.keyboard.press("Escape"); await tooltip.waitFor({ state: "hidden" });
      await summary.focus(); await tooltip.waitFor();
      await page.keyboard.press("Escape"); await tooltip.waitFor({ state: "hidden" });
      const matrix = page.locator(".quality-matrix table");
      assert.equal(await matrix.locator("tbody tr").count(), 3); assert.equal(await matrix.locator("td").count(), 9);
      await matrix.getByRole("button", { name: "보류 답안 · 정탐 확정 3건 문항 보기", exact: true }).click();
      assert.equal(await page.evaluate(() => window.matrixSelection), "expected_hold_positive");
      await page.getByText("60.0%", { exact: true }).waitFor(); await page.getByText("40.0%", { exact: true }).waitFor();
      await page.screenshot({ path: join(output, name + "-results.png"), fullPage: true });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      assert.deepEqual(errors, []); assert.deepEqual(violations, []);
      console.log(`PASS ${name}: editor assignment, summary ellipsis/hover/focus/Escape, inert content, 3x3 matrix and hold metrics`);
    } finally { await context.close(); }
  }
  console.log("Screenshots: " + output);
} finally { await browser?.close(); await server.close(); }
