// Isolated API-authoritative evidence checks. Only fabricated data, no external
// requests, writes, deployment changes or model calls.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, readdirSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

const root = resolve(dirname(fileURLToPath(import.meta.url)), ".."), output = mkdtempSync(join(tmpdir(), "waf-core-evidence-"));
const fixture = JSON.parse(readFileSync(join(root, "../backend/tests/fixtures/evidence_display_plan.json"), "utf8"))[0];
const archive = join(homedir(), ".cache/uv/archive-v0"), driver = readdirSync(archive).map(name => join(archive, name, "playwright/driver/package/index.mjs")).find(existsSync);
assert.ok(driver, "Existing local browser driver required; nothing is downloaded.");
const { chromium } = await import(pathToFileURL(driver).href);
const harness = `import React from 'react'; import { createRoot } from 'react-dom/client';
import { Detail } from '/src/App.jsx'; import { ConsolePreferencesProvider } from '/src/consolePreferences.jsx';
import '/src/styles.css'; import '/src/console.css'; import '/src/lifecycle.css'; import '/src/r3.css';
createRoot(document.getElementById('root')).render(React.createElement(ConsolePreferencesProvider, null,
React.createElement('main', { style: { padding: '20px', maxWidth: '1440px', margin: '0 auto' } },
React.createElement(Detail, { id: 'synthetic-core-analysis', onBack() {}, onOpen() {} }))));`;
const server = await createServer({ root, configFile: false, logLevel: "error", cacheDir: join(output, "cache"),
  plugins: [react(), { name: "isolated-core-evidence", resolveId(id) { if (id === "/__core.jsx") return "\0core.jsx"; }, load(id) { if (id === "\0core.jsx") return harness; } }],
  server: { host: "127.0.0.1", port: 0, hmr: false, fs: { allow: [resolve(root, "..")] } },
});
const result = { ...structuredClone(fixture.result), summary_ko: "요청 구문과 입력값의 쓰임을 함께 확인했습니다.", agent: { framework: "moduagent" },
  threat_analysis: { severity: "HIGH", category: "request_context", target: "payload", technique_ko: "입력값을 요청 문맥과 함께 해석했습니다.", potential_impact_ko: "실제 실행 결과는 확인되지 않았습니다." },
  primary: { verdict: "true_positive" }, verifier: { executed: false, agreement: null }, diagnostics: { inconclusive_reasons: [] },
};
result.evidence = ["e3", "e6", "e4", "e1"].map(id => result.evidence.find(item => item.evidence_id === id));
const detail = { id: "synthetic-core-analysis", status: "completed", event_id: "synthetic-event", event_name: "API 핵심 근거 확인", analysis_purpose: "test", company_name: "Example", src_ip: "192.0.2.1", dest_ip: "198.51.100.1", waf_action: "D", created_at: "2026-11-11T00:00:00Z", total_elapsed_ms: 1400, result, evaluation: { outcome: "unlabeled" } };
let browser;
try {
  await server.listen(); const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  const html = await server.transformIndexHtml("/", '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/__core.jsx"></script></body></html>');
  browser = await chromium.launch({ executablePath: join(homedir(), ".cache/ms-playwright/chromium-1208/chrome-linux64/chrome"), headless: true, args: ["--no-sandbox"] });
  const checked = []; let screenshots = 0;
  for (const theme of process.env.WAF_DISPLAY_SMOKE_QUICK === "1" ? ["light"] : ["sk", "light", "dark"]) for (const locale of process.env.WAF_DISPLAY_SMOKE_QUICK === "1" ? ["ko"] : ["ko", "en"]) {
    const context = await browser.newContext({ viewport: { width: 1360, height: 1000 }, serviceWorkers: "block" });
    const errors = [], violations = [], calls = []; let reads = 0, fallback = false;
    await context.addInitScript(({ theme, locale }) => { localStorage.setItem("waf-console-theme", theme); localStorage.setItem("waf-console-locale", locale); }, { theme, locale });
    await context.routeWebSocket("**/*", socket => socket.close());
    await context.route("**/*", async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== origin) { violations.push("remote request"); return route.abort(); }
      if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: html });
      if (!url.pathname.startsWith("/api/")) return route.continue();
      calls.push(url.pathname); const json = (value, headers = {}) => route.fulfill({ contentType: "application/json", headers, body: JSON.stringify(value) });
      if (request.method() !== "GET") { violations.push("unexpected write"); return route.abort(); }
      if (url.pathname === `/api/v1/analyses/${detail.id}`) {
        const pending = ++reads === 1;
        const value = pending ? { ...detail, status: "processing", result: null, completed_at: null }
          : fallback ? { ...detail, result: { ...result, evidence: result.evidence.slice(0, 1), decision_issues: [] } } : detail;
        return json(value, { "X-Evidence-View": "core-v1", "X-Evidence-Status": pending ? "pending" : fallback ? "fallback" : "completed" });
      }
      if (url.pathname.endsWith("/evaluation-labels")) return json({ items: [] });
      if (url.pathname.endsWith("/agent-runs")) return json([{ status: "completed", steps: [{ step_type: "llm_primary", status: "completed", metadata: {}, duration_ms: 900 }] }]);
      if (url.pathname.endsWith("/event")) return json({ payload: "POST /synthetic HTTP/1.1\r\n\r\n" + result.evidence.map(item => item.excerpt).join("\r\n"), extra_fields: {} });
      violations.push(url.pathname); return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(15000); page.on("pageerror", error => errors.push(error.message));
    const capture = async name => { await page.screenshot({ path: join(output, `${name}.png`), fullPage: true }); screenshots++; };
    await page.goto(origin); await page.locator(".decision-awaiting").waitFor();
    assert.equal(await page.locator(".decision-core-evidence").count(), 0);
    await page.locator(".decision-core-evidence-panel").waitFor();
    assert.equal(await page.locator(".decision-core-evidence").count(), 4);
    assert.deepEqual((await page.locator(".decision-core-evidence .decision-evidence-label > strong").allTextContents()).map(text => Number(/\d+/.exec(text)[0])), [3,6,4,1]);
    assert.equal(await page.locator(".decision-additional-toggle").count(), 0);
    assert.equal(calls.filter(path => path.endsWith("/agent-runs") || path.endsWith("/evidence-display") || path.endsWith("/evidence-relations")).length, 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await capture(`${theme}-${locale}-decision`);
    const source = page.getByRole("button", { name: locale === "ko" ? "근거 3 원문 보기" : "View evidence 3 source", exact: true });
    await source.focus(); await page.keyboard.press("Enter"); await page.locator(".raw-event-view").waitFor();
    assert.equal(await page.locator(".decision-source-list > button").count(), 4);
    assert.ok((await page.locator(".decision-selected-explanation").innerText()).includes(result.evidence[0].interpretation_ko)); await capture(`${theme}-${locale}-source`);
    await page.getByRole("tab", { name: locale === "ko" ? "보고서" : "Report", exact: true }).click(); await page.locator(".report-paper").waitFor();
    assert.deepEqual((await page.locator(".report-evidence-group > h4").allTextContents()).map(text => Number(/근거 (\d+)/.exec(text)[1])), [3,6,4,1]); await capture(`${theme}-${locale}-report`);
    if (theme === "light" && locale === "ko") {
      await page.getByRole("tab", { name: "실행", exact: true }).click(); await page.locator(".decision-stage-summary").waitFor();
      assert.ok((await page.locator(".decision-display-execution").innerText()).includes("화면과 API는 같은 근거")); await capture("light-ko-execution");
      await page.getByRole("tab", { name: "판정", exact: true }).click(); await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); await capture("light-ko-mobile");
      await page.setViewportSize({ width: 1360, height: 1000 }); fallback = true; await page.reload();
      await page.locator(".decision-core-evidence-panel").waitFor(); assert.equal(await page.locator(".decision-core-evidence").count(), 1);
      assert.ok((await page.locator(".decision-core-evidence-panel").innerText()).includes("정리 미적용 — 전달된 근거 1개")); await capture("light-ko-received-fallback");
    }
    assert.deepEqual(errors, []); assert.deepEqual(violations, []); checked.push(`${theme}/${locale}`); await context.close();
  }
  console.log(JSON.stringify({ output, checked, screenshots, remoteRequests: 0, writes: 0, originalSidecarRequests: 0, status: "passed" }));
} finally { await browser?.close(); await server.close(); }
