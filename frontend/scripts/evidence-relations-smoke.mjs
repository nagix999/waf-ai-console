// Isolated real React detail, fabricated API responses, no model or remote I/O.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readdirSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";
import { relationFixture, relationMapping } from "./evidence-relations-fixture.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = mkdtempSync(join(tmpdir(), "waf-evidence-relations-"));
const archive = join(homedir(), ".cache/uv/archive-v0");
const driver = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
assert.ok(driver, "Existing local browser driver is required; this script downloads nothing.");
const { chromium } = await import(pathToFileURL(driver).href);
const harness = `import React from 'react'; import { createRoot } from 'react-dom/client';
import { Detail } from '/src/App.jsx'; import { ConsolePreferencesProvider } from '/src/consolePreferences.jsx';
import '/src/styles.css'; import '/src/console.css'; import '/src/lifecycle.css'; import '/src/r3.css';
createRoot(document.getElementById('root')).render(React.createElement(ConsolePreferencesProvider, null,
React.createElement('main', { style: { padding: '20px', maxWidth: '1440px', margin: '0 auto' } },
React.createElement(Detail, { id: 'synthetic-linked-analysis', onBack() {}, onOpen() {} }))));`;
const server = await createServer({ root, configFile: false, logLevel: "error", cacheDir: join(output, "cache"),
  plugins: [react(), { name: "isolated-source-relations", resolveId(id) { if (id === "/__relations.jsx") return "\0relations.jsx"; },
    load(id) { if (id === "\0relations.jsx") return harness; } }],
  server: { host: "127.0.0.1", port: 0, hmr: false, fs: { allow: [resolve(root, "..")] } },
});
let browser;
const result = { ...relationFixture(), agent: { framework: "moduagent" },
  threat_analysis: { severity: "UNKNOWN", category: "request_context", target: "payload.body", technique_ko: "입력값을 요청 문맥과 함께 해석했습니다.", potential_impact_ko: "실제 실행 결과는 확인되지 않았습니다." },
  verifier: { executed: true, agreement: false, output: { verdict: "false_positive" } }, primary: { verdict: "true_positive" },
  diagnostics: { inconclusive_reasons: ["verdict_disagreement"] },
};
const detail = { id: "synthetic-linked-analysis", status: "completed", event_id: "synthetic-event", event_name: "근거 연결 확인", analysis_purpose: "test",
  source_system: "synthetic", company_name: "Example", src_ip: "192.0.2.1", dest_ip: "198.51.100.1", waf_action: "D", created_at: "2026-11-11T00:00:00Z",
  total_elapsed_ms: 1200, result, evaluation: { outcome: "unlabeled" },
};
try {
  await server.listen();
  const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const html = await server.transformIndexHtml("/", '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/__relations.jsx"></script></body></html>');
  browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
  const checked = [];
  for (const theme of process.env.WAF_RELATIONS_SMOKE_QUICK === "1" ? ["light"] : ["sk", "light", "dark"]) for (const locale of process.env.WAF_RELATIONS_SMOKE_QUICK === "1" ? ["ko"] : ["ko", "en"]) {
    const context = await browser.newContext({ viewport: { width: 1360, height: 1000 }, serviceWorkers: "block" });
    const errors = [], violations = [], calls = [];
    let failRelations = false;
    await context.addInitScript(({ theme, locale }) => { localStorage.setItem("waf-console-theme", theme); localStorage.setItem("waf-console-locale", locale); }, { theme, locale });
    await context.routeWebSocket("**/*", socket => socket.close());
    await context.route("**/*", async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== origin) { violations.push("remote request"); return route.abort(); }
      if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: html });
      if (!url.pathname.startsWith("/api/")) return route.continue();
      calls.push(url.pathname);
      const json = value => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
      if (request.method() !== "GET") { violations.push("unexpected write"); return route.abort(); }
      if (url.pathname === `/api/v1/analyses/${detail.id}`) return json(detail);
      if (url.pathname.endsWith("/evaluation-labels")) return json({ items: [] });
      if (url.pathname.endsWith("/evidence-relations")) return failRelations ? route.fulfill({ status: 403, contentType: "application/json", body: '{"detail":"forbidden"}' }) : json(relationMapping());
      if (url.pathname.endsWith("/event")) return json({ payload: 'POST /api/entry HTTP/1.1\r\nContent-Type: application/json\r\n\r\n{"content":"<img src=x onerror=alert(1)>"}', extra_fields: {} });
      violations.push(url.pathname); return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(15000); page.on("pageerror", error => errors.push(error.message));
    await page.goto(origin);
    await page.locator(".decision-linked-sources").first().waitFor();
    assert.equal(await page.locator(".decision-evidence-row.has-linked-sources").count(), 2);
    assert.deepEqual(await page.locator(".decision-evidence-roles").allTextContents(), locale === "ko" ? ["1차 분석", "추가 검증"] : ["Primary analysis", "Additional verification"]);
    assert.equal(await page.locator(".decision-linked-source .direction-context").count(), 2);
    assert.equal(await page.locator(".decision-evidence-panel img").count(), 0);
    assert.equal(await page.getByText(result.evidence[1].interpretation_ko, { exact: true }).count(), 1);
    assert.equal(await page.getByText(result.evidence[3].interpretation_ko, { exact: true }).count(), 1);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    const source = page.getByRole("button", { name: locale === "ko" ? "원문과 개별 해석 보기 →" : "View source and interpretation →", exact: true }).first();
    await source.focus();
    assert.ok(await source.evaluate(element => element === document.activeElement));
    await page.screenshot({ path: join(output, `${theme}-${locale}-decision.png`), fullPage: true });
    await page.keyboard.press("Enter");
    await page.getByRole("tab", { name: locale === "ko" ? "근거·입력" : "Evidence & input", exact: true }).waitFor();
    await page.locator(".raw-event-view").waitFor();
    assert.equal(await page.locator(".decision-source-list > button").count(), 4);
    assert.equal(await page.locator('.decision-source-list button[aria-pressed="true"]').count(), 1);
    assert.ok((await page.locator(".decision-selected-explanation").innerText()).includes(result.evidence[0].interpretation_ko));
    assert.equal(calls.filter(path => path.endsWith("/evidence-relations")).length, 1);
    assert.equal(calls.filter(path => path.endsWith("/agent-runs")).length, 0);
    const decisionTab = page.getByRole("tab", { name: locale === "ko" ? "판정" : "Decision", exact: true });
    await decisionTab.click();
    if (theme === "light" && locale === "ko") {
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({ path: join(output, "light-ko-mobile.png"), fullPage: true });
      await page.setViewportSize({ width: 1360, height: 1000 });
      failRelations = true;
      await page.reload();
      const fallback = page.locator(".decision-relations-fallback"); await fallback.waitFor();
      assert.ok((await fallback.innerText()).includes("개별 근거를 표시합니다"));
      assert.equal(await page.locator(".decision-evidence-row.has-linked-sources").count(), 0);
      failRelations = false;
      await fallback.getByRole("button", { name: "다시 시도", exact: true }).click();
      await page.locator(".decision-linked-sources").first().waitFor();
    }
    assert.deepEqual(errors, []); assert.deepEqual(violations, []);
    checked.push(`${theme}/${locale}`);
    await context.close();
  }
  console.log(JSON.stringify({ output, checked, screenshots: checked.length + 1, remoteRequests: 0, writes: 0, status: "passed" }));
} finally { await browser?.close(); await server.close(); }
